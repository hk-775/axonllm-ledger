"""Unit tests for the CUR Ingestion Service line item parser.

Tests cover:
- Parsing valid GenAI line items (Bedrock, SageMaker)
- Filtering out non-GenAI line items
- Handling missing required fields (log + skip)
- Model ID extraction from ARNs
- Edge cases: invalid timestamps, invalid costs
"""

import logging
from datetime import datetime
from decimal import Decimal

import pytest

from axonllm_ledger.cur_ingestion import extract_model_id_from_arn, parse_line_item
from axonllm_ledger.models import UsageRecord


def _make_raw_item(**overrides) -> dict:
    """Build a valid Bedrock CUR line item dict with sensible defaults."""
    defaults = {
        "product/servicecode": "AmazonBedrock",
        "identity/LineItemId": "li-abc-123",
        "lineItem/UsageAccountId": "123456789012",
        "resourceTags/user:UserId": "user-alice",
        "lineItem/ResourceId": "arn:aws:bedrock:us-east-1:123456789012:foundation-model/anthropic.claude-v2",
        "lineItem/UsageStartDate": "2024-06-15T10:00:00Z",
        "lineItem/UsageEndDate": "2024-06-15T11:00:00Z",
        "lineItem/UnblendedCost": "0.0250",
        "lineItem/UsageAmount": "1500",
        "product/outputTokens": "600",
        "product/invocationCount": "5",
    }
    defaults.update(overrides)
    return defaults


class TestExtractModelIdFromArn:
    def test_bedrock_foundation_model(self):
        arn = "arn:aws:bedrock:us-east-1:123456789012:foundation-model/anthropic.claude-v2"
        assert extract_model_id_from_arn(arn) == "anthropic.claude-v2"

    def test_bedrock_inference_profile(self):
        arn = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/anthropic.claude-v2"
        assert extract_model_id_from_arn(arn) == "anthropic.claude-v2"

    def test_sagemaker_endpoint(self):
        arn = "arn:aws:sagemaker:us-west-2:123456789012:endpoint/my-llm-endpoint"
        assert extract_model_id_from_arn(arn) == "my-llm-endpoint"

    def test_empty_string(self):
        assert extract_model_id_from_arn("") is None

    def test_none_input(self):
        assert extract_model_id_from_arn(None) is None

    def test_invalid_arn(self):
        assert extract_model_id_from_arn("not-an-arn") is None

    def test_arn_without_slash(self):
        arn = "arn:aws:bedrock:us-east-1:123456789012:some-resource"
        assert extract_model_id_from_arn(arn) == "some-resource"


class TestParseLineItemValid:
    def test_valid_bedrock_item(self):
        raw = _make_raw_item()
        result = parse_line_item(raw)

        assert isinstance(result, UsageRecord)
        assert result.userId == "user-alice"
        assert result.accountId == "123456789012"
        assert result.modelId == "anthropic.claude-v2"
        assert result.serviceName == "AmazonBedrock"
        assert result.usageStartDate == datetime(2024, 6, 15, 10, 0, 0)
        assert result.cost == Decimal("0.0250")
        assert result.inputTokens == 1500
        assert result.outputTokens == 600
        assert result.invocationCount == 5

    def test_valid_sagemaker_item(self):
        raw = _make_raw_item(
            **{
                "product/servicecode": "AmazonSageMaker",
                "lineItem/ResourceId": "arn:aws:sagemaker:us-west-2:123456789012:endpoint/my-endpoint",
            }
        )
        result = parse_line_item(raw)

        assert isinstance(result, UsageRecord)
        assert result.serviceName == "AmazonSageMaker"
        assert result.modelId == "my-endpoint"

    def test_user_id_fallback_to_identity(self):
        """When resourceTags/user:UserId is absent, fall back to identity/lineItemId."""
        raw = _make_raw_item()
        del raw["resourceTags/user:UserId"]
        raw["identity/lineItemId"] = "fallback-user-id"

        result = parse_line_item(raw)
        assert result is not None
        assert result.userId == "fallback-user-id"

    def test_record_id_is_uuid(self):
        result = parse_line_item(_make_raw_item())
        assert result is not None
        assert len(result.recordId) == 36  # UUID format

    def test_missing_end_date_defaults_to_start(self):
        raw = _make_raw_item()
        del raw["lineItem/UsageEndDate"]
        result = parse_line_item(raw)
        assert result is not None
        assert result.usageEndDate == result.usageStartDate


class TestParseLineItemFiltering:
    def test_non_genai_service_returns_none(self):
        raw = _make_raw_item(**{"product/servicecode": "AmazonEC2"})
        assert parse_line_item(raw) is None

    def test_empty_service_code_returns_none(self):
        raw = _make_raw_item(**{"product/servicecode": ""})
        assert parse_line_item(raw) is None

    def test_missing_service_code_returns_none(self):
        raw = _make_raw_item()
        del raw["product/servicecode"]
        assert parse_line_item(raw) is None


class TestParseLineItemMissingFields:
    def test_missing_user_id_logs_and_skips(self, caplog):
        raw = _make_raw_item()
        del raw["resourceTags/user:UserId"]
        # Also remove fallback
        with caplog.at_level(logging.WARNING):
            result = parse_line_item(raw)

        assert result is None
        assert "missing required fields" in caplog.text
        assert "user_id" in caplog.text

    def test_missing_account_id_logs_and_skips(self, caplog):
        raw = _make_raw_item(**{"lineItem/UsageAccountId": ""})
        with caplog.at_level(logging.WARNING):
            result = parse_line_item(raw)

        assert result is None
        assert "account_id" in caplog.text

    def test_missing_resource_arn_logs_and_skips(self, caplog):
        raw = _make_raw_item(**{"lineItem/ResourceId": ""})
        with caplog.at_level(logging.WARNING):
            result = parse_line_item(raw)

        assert result is None
        assert "model_id" in caplog.text

    def test_missing_timestamp_logs_and_skips(self, caplog):
        raw = _make_raw_item(**{"lineItem/UsageStartDate": ""})
        with caplog.at_level(logging.WARNING):
            result = parse_line_item(raw)

        assert result is None
        assert "timestamp" in caplog.text

    def test_missing_cost_logs_and_skips(self, caplog):
        raw = _make_raw_item(**{"lineItem/UnblendedCost": ""})
        with caplog.at_level(logging.WARNING):
            result = parse_line_item(raw)

        assert result is None
        assert "cost" in caplog.text

    def test_invalid_timestamp_logs_and_skips(self, caplog):
        raw = _make_raw_item(**{"lineItem/UsageStartDate": "not-a-date"})
        with caplog.at_level(logging.WARNING):
            result = parse_line_item(raw)

        assert result is None
        assert "invalid timestamp" in caplog.text

    def test_invalid_cost_logs_and_skips(self, caplog):
        raw = _make_raw_item(**{"lineItem/UnblendedCost": "not-a-number"})
        with caplog.at_level(logging.WARNING):
            result = parse_line_item(raw)

        assert result is None
        assert "invalid cost" in caplog.text

    def test_unparseable_arn_logs_model_id_missing(self, caplog):
        raw = _make_raw_item(**{"lineItem/ResourceId": "not-an-arn"})
        with caplog.at_level(logging.WARNING):
            result = parse_line_item(raw)

        assert result is None
        assert "model_id" in caplog.text

    def test_line_item_id_in_log_message(self, caplog):
        raw = _make_raw_item(
            **{
                "identity/LineItemId": "li-specific-id",
                "lineItem/UsageAccountId": "",
            }
        )
        with caplog.at_level(logging.WARNING):
            parse_line_item(raw)

        assert "li-specific-id" in caplog.text


# ---------------------------------------------------------------------------
# Deduplication & Ingestion tests
# ---------------------------------------------------------------------------

from axonllm_ledger.cur_ingestion import (
    DeduplicationStore,
    deduplicate_record,
    ingest_line_items,
)


def _make_raw_item_with_key(line_item_id: str, account_id: str = "123456789012", start_date: str = "2024-06-15T10:00:00Z") -> dict:
    """Build a valid raw CUR item with a specific composite key."""
    return _make_raw_item(**{
        "identity/LineItemId": line_item_id,
        "lineItem/UsageAccountId": account_id,
        "lineItem/UsageStartDate": start_date,
    })


class TestDeduplicationStore:
    def test_new_key_not_contained(self):
        store = DeduplicationStore()
        key = ("li-1", datetime(2024, 1, 1), "acct-1")
        assert not store.contains(key)

    def test_added_key_is_contained(self):
        store = DeduplicationStore()
        key = ("li-1", datetime(2024, 1, 1), "acct-1")
        store.add(key)
        assert store.contains(key)

    def test_len_tracks_unique_keys(self):
        store = DeduplicationStore()
        store.add(("li-1", datetime(2024, 1, 1), "acct-1"))
        store.add(("li-2", datetime(2024, 1, 1), "acct-1"))
        store.add(("li-1", datetime(2024, 1, 1), "acct-1"))  # duplicate
        assert len(store) == 2


class TestDeduplicateRecord:
    def test_new_record_returns_true(self):
        store = DeduplicationStore()
        record = parse_line_item(_make_raw_item())
        assert record is not None
        assert deduplicate_record(record, store) is True

    def test_duplicate_record_returns_false(self):
        store = DeduplicationStore()
        record = parse_line_item(_make_raw_item())
        assert record is not None
        deduplicate_record(record, store)
        # Parse again — same composite key, different recordId
        record2 = parse_line_item(_make_raw_item())
        assert deduplicate_record(record2, store) is False

    def test_different_keys_both_new(self):
        store = DeduplicationStore()
        r1 = parse_line_item(_make_raw_item_with_key("li-1"))
        r2 = parse_line_item(_make_raw_item_with_key("li-2"))
        assert deduplicate_record(r1, store) is True
        assert deduplicate_record(r2, store) is True


class TestIngestLineItems:
    def test_all_unique_items(self):
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1"),
            _make_raw_item_with_key("li-2"),
            _make_raw_item_with_key("li-3"),
        ]
        result = ingest_line_items(items, store)
        assert len(result.new_records) == 3
        assert result.duplicate_count == 0
        assert result.skipped_count == 0

    def test_duplicate_items_in_same_batch(self):
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1"),
            _make_raw_item_with_key("li-1"),  # same composite key
            _make_raw_item_with_key("li-2"),
        ]
        result = ingest_line_items(items, store)
        assert len(result.new_records) == 2
        assert result.duplicate_count == 1

    def test_ingesting_same_collection_twice_produces_zero_new(self):
        """Requirement 1.5 / 11.2: redelivered exports produce no duplicates."""
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1"),
            _make_raw_item_with_key("li-2"),
        ]
        first = ingest_line_items(items, store)
        assert len(first.new_records) == 2

        second = ingest_line_items(items, store)
        assert len(second.new_records) == 0
        assert second.duplicate_count == 2

    def test_mixed_duplicates_and_skipped(self):
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1"),
            _make_raw_item(**{"product/servicecode": "AmazonEC2"}),  # non-GenAI → skipped
            _make_raw_item_with_key("li-1"),  # duplicate
        ]
        result = ingest_line_items(items, store)
        assert len(result.new_records) == 1
        assert result.skipped_count == 1
        assert result.duplicate_count == 1

    def test_ingestion_log_created(self):
        store = DeduplicationStore()
        items = [_make_raw_item_with_key("li-1")]
        result = ingest_line_items(items, store, s3_key="s3://bucket/cur/export.parquet")
        log = result.log
        assert log.source == "CUR"
        assert log.s3Key == "s3://bucket/cur/export.parquet"
        assert log.recordCount == 1
        assert log.duplicateCount == 0
        assert log.skippedCount == 0

    def test_different_account_same_line_item_not_duplicate(self):
        """Different accountId means different composite key."""
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1", account_id="111111111111"),
            _make_raw_item_with_key("li-1", account_id="222222222222"),
        ]
        result = ingest_line_items(items, store)
        assert len(result.new_records) == 2
        assert result.duplicate_count == 0

    def test_different_start_date_same_line_item_not_duplicate(self):
        """Different usageStartDate means different composite key."""
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1", start_date="2024-06-15T10:00:00Z"),
            _make_raw_item_with_key("li-1", start_date="2024-06-16T10:00:00Z"),
        ]
        result = ingest_line_items(items, store)
        assert len(result.new_records) == 2
        assert result.duplicate_count == 0



# ---------------------------------------------------------------------------
# AccessRecord creation tests (Task 2.6 — Requirement 9.1)
# ---------------------------------------------------------------------------

from axonllm_ledger.cur_ingestion import create_access_record
from axonllm_ledger.models import AccessRecord


class TestCreateAccessRecord:
    def test_produces_correct_fields_from_usage_record(self):
        """create_access_record maps userId, modelId, accountId, timestamp correctly."""
        raw = _make_raw_item()
        usage = parse_line_item(raw)
        assert usage is not None

        access = create_access_record(usage)

        assert isinstance(access, AccessRecord)
        assert access.userId == usage.userId
        assert access.modelId == usage.modelId
        assert access.accountId == usage.accountId
        assert access.timestamp == usage.usageStartDate
        assert access.sourceRecordId == usage.recordId

    def test_access_id_is_unique_uuid(self):
        usage = parse_line_item(_make_raw_item())
        assert usage is not None
        a1 = create_access_record(usage)
        a2 = create_access_record(usage)
        assert len(a1.accessId) == 36
        assert a1.accessId != a2.accessId

    def test_sagemaker_usage_record(self):
        raw = _make_raw_item(**{
            "product/servicecode": "AmazonSageMaker",
            "lineItem/ResourceId": "arn:aws:sagemaker:us-west-2:123456789012:endpoint/my-endpoint",
        })
        usage = parse_line_item(raw)
        assert usage is not None

        access = create_access_record(usage)
        assert access.modelId == "my-endpoint"
        assert access.accountId == "123456789012"


class TestIngestLineItemsAccessRecords:
    def test_creates_access_records_for_each_new_usage_record(self):
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1"),
            _make_raw_item_with_key("li-2"),
            _make_raw_item_with_key("li-3"),
        ]
        result = ingest_line_items(items, store)

        assert len(result.access_records) == 3
        assert len(result.access_records) == len(result.new_records)
        # Each access record should reference its corresponding usage record
        for usage, access in zip(result.new_records, result.access_records):
            assert access.sourceRecordId == usage.recordId
            assert access.userId == usage.userId
            assert access.modelId == usage.modelId
            assert access.accountId == usage.accountId
            assert access.timestamp == usage.usageStartDate

    def test_duplicate_usage_records_dont_produce_access_records(self):
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1"),
            _make_raw_item_with_key("li-1"),  # duplicate
            _make_raw_item_with_key("li-2"),
        ]
        result = ingest_line_items(items, store)

        assert len(result.new_records) == 2
        assert len(result.access_records) == 2
        assert result.duplicate_count == 1

    def test_reingested_collection_produces_no_access_records(self):
        """Duplicates from a second ingestion should not create AccessRecords."""
        store = DeduplicationStore()
        items = [
            _make_raw_item_with_key("li-1"),
            _make_raw_item_with_key("li-2"),
        ]
        first = ingest_line_items(items, store)
        assert len(first.access_records) == 2

        second = ingest_line_items(items, store)
        assert len(second.access_records) == 0
        assert len(second.new_records) == 0

    def test_skipped_items_dont_produce_access_records(self):
        store = DeduplicationStore()
        items = [
            _make_raw_item(**{"product/servicecode": "AmazonEC2"}),  # non-GenAI
            _make_raw_item(**{"lineItem/UnblendedCost": ""}),  # missing cost
        ]
        result = ingest_line_items(items, store)

        assert len(result.access_records) == 0
        assert len(result.new_records) == 0

    def test_empty_batch_produces_no_access_records(self):
        store = DeduplicationStore()
        result = ingest_line_items([], store)
        assert len(result.access_records) == 0