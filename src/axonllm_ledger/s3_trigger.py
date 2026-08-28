"""S3 trigger and polling mechanism for CUR ingestion.

Detects new CUR exports via S3 event notifications and implements
a fallback polling mechanism every 30 minutes to guarantee the
60-minute SLA (Requirement 1.1).

S3 interaction methods are stubs that can be overridden or backed
by boto3 later — no boto3 dependency is introduced here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from axonllm_ledger.cur_ingestion import (
    DeduplicationStore,
    IngestionResult,
    ingest_line_items,
)

logger = logging.getLogger(__name__)


class CURIngestionTrigger:
    """Detects and ingests new CUR exports from S3.

    Supports two ingestion paths:
    1. Event-driven — ``handle_s3_event`` processes an S3 event notification.
    2. Polling — ``poll_for_new_exports`` lists and processes unprocessed exports.

    S3 reads are delegated to ``_read_export`` and ``_list_new_exports``,
    which are stubs meant to be overridden or mocked for testing.
    """

    #: Polling interval in seconds (30 minutes).  Configurable.
    POLL_INTERVAL_SECONDS: int = 1800

    def __init__(
        self,
        bucket: str,
        prefix: str,
        dedup_store: DeduplicationStore,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.dedup_store = dedup_store
        self._processed_keys: set[str] = set()

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
        s3_key = self._extract_s3_key(event)
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
    # S3 interaction stubs (override or mock for real S3 access)
    # ------------------------------------------------------------------

    def _list_new_exports(self) -> list[str]:
        """List S3 keys under the configured prefix that may need ingestion.

        This is a stub.  A real implementation would call
        ``s3.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix)``
        and return the keys.  Override this method or inject a callable
        to provide actual S3 listing.

        Returns
        -------
        A list of S3 object keys.
        """
        return []

    def _read_export(self, s3_key: str) -> list[dict]:
        """Read raw CUR line items from an S3 export file.

        This is a stub.  A real implementation would download the
        Parquet/CSV file from S3 and parse it into a list of dicts.
        Override this method or inject a callable to provide actual
        S3 reads.

        Parameters
        ----------
        s3_key:
            The S3 object key to read.

        Returns
        -------
        A list of raw CUR line item dicts.
        """
        return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_s3_key(event: dict) -> str:
        """Extract the S3 object key from an S3 event notification."""
        try:
            return event["Records"][0]["s3"]["object"]["key"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Invalid S3 event structure: {exc}") from exc
