"""S3 event and polling adapter for production CUR ingestion."""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable
from urllib.parse import unquote_plus

from axonllm_ledger.cur_ingestion import (
    DeduplicationStore,
    IngestionResult,
    ingest_line_items,
)

logger = logging.getLogger(__name__)

_SUPPORTED_EXPORT_SUFFIXES = (
    ".csv",
    ".csv.gz",
    ".json",
    ".json.gz",
    ".jsonl",
    ".jsonl.gz",
    ".ndjson",
    ".ndjson.gz",
    ".parquet",
)


@runtime_checkable
class S3CURClient(Protocol):
    """S3 operations required by the CUR ingestion trigger."""

    def get_paginator(self, operation_name: str) -> Any:
        """Return an S3 paginator."""
        ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        """Read an S3 object."""
        ...


class CURIngestionTrigger:
    """Detects and ingests new CUR exports from S3.

    Supports two ingestion paths:
    1. Event-driven — ``handle_s3_event`` processes an S3 event notification.
    2. Polling — ``poll_for_new_exports`` lists and processes unprocessed exports.

    The S3 client is injectable for tests. Use :meth:`from_boto3` in a
    production process to use the standard AWS credential chain.
    """

    #: Polling interval in seconds (30 minutes).  Configurable.
    POLL_INTERVAL_SECONDS: int = 1800

    def __init__(
        self,
        bucket: str,
        prefix: str,
        dedup_store: DeduplicationStore,
        *,
        s3_client: S3CURClient | None = None,
        validate_event_bucket: bool = False,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.dedup_store = dedup_store
        self._s3 = s3_client
        self._validate_event_bucket = validate_event_bucket
        self._processed_keys: set[str] = set()

    @classmethod
    def from_boto3(
        cls,
        *,
        bucket: str,
        prefix: str,
        dedup_store: DeduplicationStore,
        region_name: str | None = None,
        profile_name: str | None = None,
    ) -> CURIngestionTrigger:
        """Create a production trigger using boto3's standard credential chain."""
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError(
                'Install AWS support with: pip install "axonllm-ledger[aws]"'
            ) from exc

        session_kwargs: dict[str, str] = {}
        if region_name is not None:
            session_kwargs["region_name"] = region_name
        if profile_name is not None:
            session_kwargs["profile_name"] = profile_name
        session = boto3.Session(**session_kwargs)
        client = session.client(
            "s3",
            config=Config(
                retries={"mode": "standard", "total_max_attempts": 4},
                connect_timeout=5,
                read_timeout=90,
            ),
        )
        return cls(
            bucket,
            prefix,
            dedup_store,
            s3_client=client,
            validate_event_bucket=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_s3_event(self, event: dict) -> IngestionResult:
        """Handle an S3 event notification for a new CUR export.

        Extracts the S3 key from the event payload, reads the raw line
        items, ingests them, and marks the key as processed.

        Parameters
        ----------
        event:
            An S3 event notification dict.  Expected structure::

                {
                    "Records": [
                        {
                            "s3": {
                                "bucket": {"name": "..."},
                                "object": {"key": "..."}
                            }
                        }
                    ]
                }

        Returns
        -------
        IngestionResult from processing the export.
        """
        event_bucket, s3_key = self._extract_s3_location(event)
        if (
            self._validate_event_bucket
            and self.bucket
            and event_bucket != self.bucket
        ):
            raise ValueError(
                f"S3 event bucket {event_bucket!r} does not match "
                f"configured bucket {self.bucket!r}"
            )
        logger.info("Handling S3 event for key: %s", s3_key)

        raw_items = self._read_export(s3_key)
        result = ingest_line_items(raw_items, self.dedup_store, s3_key=s3_key)
        self._processed_keys.add(s3_key)

        logger.info(
            "Ingested %d new records from %s (skipped=%d, duplicates=%d)",
            len(result.new_records),
            s3_key,
            result.skipped_count,
            result.duplicate_count,
        )
        return result

    def poll_for_new_exports(self) -> list[IngestionResult]:
        """Poll S3 for new CUR exports and ingest any that haven't been processed.

        This is the fallback mechanism that runs every ``POLL_INTERVAL_SECONDS``
        to guarantee the 60-minute SLA even if S3 event notifications are missed.

        Returns
        -------
        A list of IngestionResult objects, one per newly processed export.
        """
        new_keys = self._list_new_exports()
        results: list[IngestionResult] = []

        for s3_key in new_keys:
            if s3_key in self._processed_keys:
                logger.debug("Skipping already-processed key: %s", s3_key)
                continue

            logger.info("Polling: ingesting new export %s", s3_key)
            raw_items = self._read_export(s3_key)
            result = ingest_line_items(raw_items, self.dedup_store, s3_key=s3_key)
            self._processed_keys.add(s3_key)
            results.append(result)

            logger.info(
                "Poll ingested %d new records from %s",
                len(result.new_records),
                s3_key,
            )

        return results

    @property
    def processed_keys(self) -> frozenset[str]:
        """Return the set of S3 keys that have already been processed."""
        return frozenset(self._processed_keys)

    # ------------------------------------------------------------------
    # S3 interactions
    # ------------------------------------------------------------------

    def _list_new_exports(self) -> list[str]:
        """List supported, non-empty export objects under the configured prefix."""
        client = self._require_s3_client()
        paginator = client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get("Contents", []):
                key = item.get("Key", "")
                if item.get("Size", 0) > 0 and _is_supported_export_key(key):
                    keys.append(key)
        return sorted(set(keys))

    def _read_export(self, s3_key: str) -> list[dict]:
        """Download and parse CSV, JSON, JSON Lines, or Parquet billing data."""
        client = self._require_s3_client()
        response = client.get_object(Bucket=self.bucket, Key=s3_key)
        body = response["Body"]
        try:
            payload = body.read()
        finally:
            body.close()
        return parse_export_payload(
            s3_key,
            payload,
            content_encoding=response.get("ContentEncoding"),
        )

    def _require_s3_client(self) -> S3CURClient:
        if self._s3 is None:
            raise RuntimeError(
                "S3 client is not configured; use CURIngestionTrigger.from_boto3 "
                "or inject s3_client"
            )
        return self._s3

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_s3_key(event: dict) -> str:
        """Extract the S3 object key from an S3 event notification."""
        return CURIngestionTrigger._extract_s3_location(event)[1]

    @staticmethod
    def _extract_s3_location(event: dict) -> tuple[str, str]:
        """Extract and URL-decode the bucket and object key from an S3 event."""
        try:
            record = event["Records"][0]["s3"]
            return (
                record["bucket"]["name"],
                unquote_plus(record["object"]["key"]),
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Invalid S3 event structure: {exc}") from exc


def parse_export_payload(
    s3_key: str,
    payload: bytes,
    *,
    content_encoding: str | None = None,
) -> list[dict[str, Any]]:
    """Parse one downloaded billing export payload."""
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if _is_gzip_encoded(s3_key, content_encoding):
        payload = gzip.decompress(payload)

    lower_key = s3_key.lower()
    if lower_key.endswith((".csv", ".csv.gz")):
        text = payload.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None:
            raise ValueError(f"{s3_key} does not contain a CSV header")
        return [dict(row) for row in reader]
    if lower_key.endswith((".jsonl", ".jsonl.gz", ".ndjson", ".ndjson.gz")):
        rows = [
            json.loads(line)
            for line in payload.decode("utf-8-sig").splitlines()
            if line.strip()
        ]
        return _validate_record_collection(rows, s3_key)
    if lower_key.endswith((".json", ".json.gz")):
        decoded = json.loads(payload.decode("utf-8-sig"))
        if isinstance(decoded, Mapping):
            decoded = decoded.get("records", decoded.get("data"))
        return _validate_record_collection(decoded, s3_key)
    if lower_key.endswith(".parquet"):
        try:
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise RuntimeError(
                'Parquet ingestion requires: pip install "axonllm-ledger[parquet]"'
            ) from exc
        table = parquet.read_table(io.BytesIO(payload))
        return _validate_record_collection(table.to_pylist(), s3_key)
    raise ValueError(f"unsupported billing export format: {s3_key}")


def _validate_record_collection(value: Any, source: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{source} must contain a JSON array of records")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ValueError(f"{source} record {index} must be an object")
        rows.append(dict(row))
    return rows


def _is_supported_export_key(key: str) -> bool:
    lower_key = key.lower()
    filename = lower_key.rsplit("/", 1)[-1]
    if filename == "manifest.json" or filename.endswith(".manifest.json"):
        return False
    return lower_key.endswith(_SUPPORTED_EXPORT_SUFFIXES)


def _is_gzip_encoded(s3_key: str, content_encoding: str | None) -> bool:
    return s3_key.lower().endswith(".gz") or (
        content_encoding is not None and content_encoding.lower() == "gzip"
    )
