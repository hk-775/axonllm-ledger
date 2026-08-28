"""Property-based tests for CUR deduplication idempotence via the ingestion pipeline.

Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent

Validates: Requirements 1.5, 11.2

Tests the full ingestion pipeline (ingest_line_items + DeduplicationStore)
to verify that deduplication is idempotent across repeated ingestions.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.cur_ingestion import (
    DeduplicationStore,
    ingest_line_items,
)

# --- Strategies (reuse pattern from test_property_cur_parsing.py) ---

_genai_service_codes = st.sampled_from(["AmazonBedrock", "AmazonSageMaker"])

_line_item_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=20,
)

_account_ids = st.from_regex(r"[0-9]{12}", fullmatch=True)

_user_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=20,
)

_model_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_."),
    min_size=1,
    max_size=30,
)

_regions = st.sampled_from(["us-east-1", "us-west-2", "eu-west-1"])

_iso_timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
).map(lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ"))

_costs = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("99999.99"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
).map(str)

_token_counts = st.integers(min_value=0, max_value=10_000_000).map(str)

_invocation_counts = st.integers(min_value=1, max_value=100_000).map(str)


@st.composite
def genai_line_item(draw):
    """Generate a valid GenAI CUR line item dict with all required fields."""
    service_code = draw(_genai_service_codes)
    account_id = draw(_account_ids)
    model_id = draw(_model_ids)
    region = draw(_regions)
    resource_arn = f"arn:aws:bedrock:{region}:{account_id}:foundation-model/{model_id}"

    return {
        "product/servicecode": service_code,
        "identity/LineItemId": draw(_line_item_ids),
        "lineItem/UsageAccountId": account_id,
        "resourceTags/user:UserId": draw(_user_ids),
        "lineItem/ResourceId": resource_arn,
        "lineItem/UsageStartDate": draw(_iso_timestamps),
        "lineItem/UsageEndDate": draw(_iso_timestamps),
        "lineItem/UnblendedCost": draw(_costs),
        "lineItem/UsageAmount": draw(_token_counts),
        "product/outputTokens": draw(_token_counts),
        "product/invocationCount": draw(_invocation_counts),
    }


def _dedup_key_from_raw(item: dict) -> tuple:
    """Extract the deduplication key from a raw line item dict.

    Mirrors the composite key: (lineItemId, usageStartDate, accountId).
    """
    ts_str = item["lineItem/UsageStartDate"].strip().rstrip("Z")
    return (
        item["identity/LineItemId"],
        datetime.fromisoformat(ts_str),
        item["lineItem/UsageAccountId"],
    )


# --- Property Tests ---


class TestDeduplicationIngestionIdempotent:
    """Property 3: CUR Deduplication Is Idempotent (full ingestion pipeline).

    For any collection of CUR line items where multiple items share the same
    deduplication key (lineItemId, usageStartDate, accountId), ingesting the
    entire collection should produce exactly one UsageRecord per unique
    deduplication key. Ingesting the same collection a second time should
    produce zero new records.

    **Validates: Requirements 1.5, 11.2**
    """

    @settings(max_examples=100)
    @given(items=st.lists(genai_line_item(), min_size=0, max_size=30))
    def test_one_record_per_unique_key(self, items: list[dict]):
        """Ingesting a collection produces exactly one record per unique dedup key.

        Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent
        """
        # **Validates: Requirements 1.5, 11.2**
        store = DeduplicationStore()
        result = ingest_line_items(items, store, s3_key="test.csv")

        expected_unique_keys = {_dedup_key_from_raw(item) for item in items}
        actual_keys = {r.deduplication_key for r in result.new_records}

        assert len(result.new_records) == len(expected_unique_keys)
        assert actual_keys == expected_unique_keys

    @settings(max_examples=100)
    @given(items=st.lists(genai_line_item(), min_size=1, max_size=30))
    def test_second_ingestion_produces_zero_new(self, items: list[dict]):
        """Ingesting the same collection a second time produces zero new records.

        Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent
        """
        # **Validates: Requirements 1.5, 11.2**
        store = DeduplicationStore()

        # First ingestion
        first_result = ingest_line_items(items, store, s3_key="test.csv")
        first_count = len(first_result.new_records)
        assert first_count > 0, "First ingestion should produce at least one record"

        # Second ingestion with the same store
        second_result = ingest_line_items(items, store, s3_key="test.csv")

        assert len(second_result.new_records) == 0
        assert second_result.duplicate_count == len(items)

    @settings(max_examples=100)
    @given(items=st.lists(genai_line_item(), min_size=0, max_size=30))
    def test_deduplication_is_idempotent(self, items: list[dict]):
        """Deduplicating already-deduplicated results produces the same set.

        Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent
        """
        # **Validates: Requirements 1.5, 11.2**
        store = DeduplicationStore()

        # First ingestion
        first_result = ingest_line_items(items, store, s3_key="test.csv")
        first_keys = {r.deduplication_key for r in first_result.new_records}

        # Re-ingest the same items — should get zero new, same key set in store
        second_result = ingest_line_items(items, store, s3_key="test.csv")
        assert len(second_result.new_records) == 0

        # The store should still contain exactly the same unique keys
        combined_keys = first_keys  # no new keys added
        assert len(store) == len(first_keys)

        # Third ingestion — still idempotent
        third_result = ingest_line_items(items, store, s3_key="test.csv")
        assert len(third_result.new_records) == 0
        assert len(store) == len(combined_keys)
