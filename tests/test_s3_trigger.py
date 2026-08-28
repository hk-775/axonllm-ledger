"""Unit tests for the S3 trigger and polling mechanism (Task 2.8).

Tests cover:
- handle_s3_event extracts S3 key and processes items
- poll_for_new_exports processes only new exports
- Already-processed exports are skipped
- IngestionLog is created for each run
- POLL_INTERVAL_SECONDS is configurable
- Invalid S3 event structures raise ValueError
"""

from __future__ import annotations

from axonllm_ledger.cur_ingestion import DeduplicationStore
from axonllm_ledger.models import IngestionStatus
from axonllm_ledger.s3_trigger import CURIngestionTrigger

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_item(line_item_id: str = "li-1", account_id: str = "123456789012") -> dict:
    """Build a valid Bedrock CUR line item dict."""
    return {
        "product/servicecode": "AmazonBedrock",
        "identity/LineItemId": line_item_id,
        "lineItem/UsageAccountId": account_id,
        "resourceTags/user:UserId": "user-alice",
        "lineItem/ResourceId": (
            "arn:aws:bedrock:us-east-1:123456789012:"
            "foundation-model/anthropic.claude-v2"
        ),
        "lineItem/UsageStartDate": "2024-06-15T10:00:00Z",
        "lineItem/UsageEndDate": "2024-06-15T11:00:00Z",
        "lineItem/UnblendedCost": "0.0250",
        "lineItem/UsageAmount": "1500",
    }


def _make_s3_event(key: str, bucket: str = "my-bucket") -> dict:
    """Build a minimal S3 event notification."""
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
                }
            }
        ]
    }


class _FakeTrigger(CURIngestionTrigger):
    """Subclass that stubs out S3 interactions for testing."""

    def __init__(self, bucket: str, prefix: str, dedup_store: DeduplicationStore):
        super().__init__(bucket, prefix, dedup_store)
        # Map of s3_key -> list of raw line item dicts
        self.exports: dict[str, list[dict]] = {}
        # Keys returned by _list_new_exports
        self.available_keys: list[str] = []

    def _read_export(self, s3_key: str) -> list[dict]:
        return self.exports.get(s3_key, [])

    def _list_new_exports(self) -> list[str]:
        return list(self.available_keys)


# ---------------------------------------------------------------------------
# handle_s3_event tests
# ---------------------------------------------------------------------------


class TestHandleS3Event:
    def test_extracts_key_and_processes_items(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [
            _make_raw_item("li-1"),
            _make_raw_item("li-2"),
        ]

        event = _make_s3_event("cur/export-001.parquet")
        result = trigger.handle_s3_event(event)

        assert len(result.new_records) == 2
        assert result.skipped_count == 0
        assert result.duplicate_count == 0

    def test_marks_key_as_processed(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]

        trigger.handle_s3_event(_make_s3_event("cur/export-001.parquet"))

        assert "cur/export-001.parquet" in trigger.processed_keys

    def test_creates_ingestion_log(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]

        result = trigger.handle_s3_event(_make_s3_event("cur/export-001.parquet"))
        log = result.log

        assert log.source == "CUR"
        assert log.s3Key == "cur/export-001.parquet"
        assert log.recordCount == 1
        assert log.skippedCount == 0
        assert log.duplicateCount == 0
        assert log.status in (IngestionStatus.SUCCESS, IngestionStatus.PARTIAL)

    def test_invalid_event_raises_value_error(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)

        with pytest.raises(ValueError, match="Invalid S3 event"):
            trigger.handle_s3_event({})

    def test_invalid_event_missing_records(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)

        with pytest.raises(ValueError):
            trigger.handle_s3_event({"Records": []})

    def test_deduplicates_across_events(self):
        """Processing the same export twice via events deduplicates at record level."""
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]

        r1 = trigger.handle_s3_event(_make_s3_event("cur/export-001.parquet"))
        assert len(r1.new_records) == 1

        # Same key again — records are deduplicated by the store
        r2 = trigger.handle_s3_event(_make_s3_event("cur/export-001.parquet"))
        assert len(r2.new_records) == 0
        assert r2.duplicate_count == 1


# ---------------------------------------------------------------------------
# poll_for_new_exports tests
# ---------------------------------------------------------------------------


class TestPollForNewExports:
    def test_processes_only_new_exports(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]
        trigger.exports["cur/export-002.parquet"] = [_make_raw_item("li-2")]
        trigger.available_keys = [
            "cur/export-001.parquet",
            "cur/export-002.parquet",
        ]

        results = trigger.poll_for_new_exports()

        assert len(results) == 2
        assert len(results[0].new_records) == 1
        assert len(results[1].new_records) == 1

    def test_skips_already_processed_exports(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]
        trigger.exports["cur/export-002.parquet"] = [_make_raw_item("li-2")]

        # Process one via event first
        trigger.handle_s3_event(_make_s3_event("cur/export-001.parquet"))

        # Now poll — should only pick up export-002
        trigger.available_keys = [
            "cur/export-001.parquet",
            "cur/export-002.parquet",
        ]
        results = trigger.poll_for_new_exports()

        assert len(results) == 1
        assert results[0].log.s3Key == "cur/export-002.parquet"

    def test_returns_empty_when_no_new_exports(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.available_keys = []

        results = trigger.poll_for_new_exports()
        assert results == []

    def test_marks_polled_keys_as_processed(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]
        trigger.available_keys = ["cur/export-001.parquet"]

        trigger.poll_for_new_exports()

        assert "cur/export-001.parquet" in trigger.processed_keys

    def test_second_poll_skips_previously_polled(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]
        trigger.available_keys = ["cur/export-001.parquet"]

        first = trigger.poll_for_new_exports()
        assert len(first) == 1

        second = trigger.poll_for_new_exports()
        assert len(second) == 0

    def test_creates_ingestion_log_for_each_export(self):
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]
        trigger.exports["cur/export-002.parquet"] = [
            _make_raw_item("li-2"),
            _make_raw_item("li-3"),
        ]
        trigger.available_keys = [
            "cur/export-001.parquet",
            "cur/export-002.parquet",
        ]

        results = trigger.poll_for_new_exports()

        assert len(results) == 2
        assert results[0].log.s3Key == "cur/export-001.parquet"
        assert results[0].log.recordCount == 1
        assert results[1].log.s3Key == "cur/export-002.parquet"
        assert results[1].log.recordCount == 2

    def test_poll_deduplicates_records_across_exports(self):
        """Same line item in two exports — only ingested once."""
        store = DeduplicationStore()
        trigger = _FakeTrigger("my-bucket", "cur/", store)
        trigger.exports["cur/export-001.parquet"] = [_make_raw_item("li-1")]
        trigger.exports["cur/export-002.parquet"] = [_make_raw_item("li-1")]  # same record
        trigger.available_keys = [
            "cur/export-001.parquet",
            "cur/export-002.parquet",
        ]

        results = trigger.poll_for_new_exports()

        assert len(results) == 2
        assert len(results[0].new_records) == 1
        assert len(results[1].new_records) == 0
        assert results[1].duplicate_count == 1


# ---------------------------------------------------------------------------
# Configuration and class attribute tests
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_poll_interval_default(self):
        assert CURIngestionTrigger.POLL_INTERVAL_SECONDS == 1800

    def test_poll_interval_is_configurable(self):
        store = DeduplicationStore()
        trigger = CURIngestionTrigger("bucket", "prefix/", store)
        trigger.POLL_INTERVAL_SECONDS = 900
        assert trigger.POLL_INTERVAL_SECONDS == 900

    def test_constructor_stores_bucket_and_prefix(self):
        store = DeduplicationStore()
        trigger = CURIngestionTrigger("my-bucket", "cur/exports/", store)
        assert trigger.bucket == "my-bucket"
        assert trigger.prefix == "cur/exports/"

    def test_processed_keys_initially_empty(self):
        store = DeduplicationStore()
        trigger = CURIngestionTrigger("bucket", "prefix/", store)
        assert trigger.processed_keys == frozenset()
