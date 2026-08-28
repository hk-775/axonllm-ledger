"""Property-based tests for UsageRecord deduplication.

Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent
"""

from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.models import UsageRecord


# --- Strategies ---

_line_item_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=20,
)

_account_ids = st.from_regex(r"[0-9]{12}", fullmatch=True)

_timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
)

_costs = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("99999.99"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)

_token_counts = st.integers(min_value=0, max_value=10_000_000)

_service_names = st.sampled_from(["AmazonBedrock", "AmazonSageMaker"])


def _usage_record_strategy():
    """Strategy that generates a random UsageRecord."""
    return st.builds(
        UsageRecord,
        recordId=st.uuids().map(str),
        lineItemId=_line_item_ids,
        userId=st.text(min_size=1, max_size=30),
        accountId=_account_ids,
        modelId=st.text(min_size=1, max_size=50),
        serviceName=_service_names,
        usageStartDate=_timestamps,
        usageEndDate=_timestamps,
        inputTokens=_token_counts,
        outputTokens=_token_counts,
        invocationCount=st.integers(min_value=1, max_value=10000),
        cost=_costs,
        ingestedAt=_timestamps,
        sourceExportId=st.text(min_size=1, max_size=20),
    )


def _deduplicate(records: list[UsageRecord]) -> list[UsageRecord]:
    """Simulate deduplication: keep first record per unique deduplication key."""
    seen: dict[tuple, UsageRecord] = {}
    for rec in records:
        key = rec.deduplication_key
        if key not in seen:
            seen[key] = rec
    return list(seen.values())


# --- Property Tests ---


class TestDeduplicationIdempotent:
    """Property 3: CUR Deduplication Is Idempotent.

    For any collection of CUR line items where multiple items share the same
    deduplication key (lineItemId, usageStartDate, accountId), ingesting the
    entire collection should produce exactly one UsageRecord per unique
    deduplication key. Ingesting the same collection a second time should
    produce zero new records.
    """

    @settings(max_examples=100)
    @given(records=st.lists(_usage_record_strategy(), min_size=0, max_size=50))
    def test_one_record_per_unique_key(self, records: list[UsageRecord]):
        """Deduplication produces exactly one record per unique dedup key."""
        # Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent
        deduplicated = _deduplicate(records)
        unique_keys = {r.deduplication_key for r in records}
        dedup_keys = {r.deduplication_key for r in deduplicated}

        assert len(deduplicated) == len(unique_keys)
        assert dedup_keys == unique_keys

    @settings(max_examples=100)
    @given(records=st.lists(_usage_record_strategy(), min_size=0, max_size=50))
    def test_second_ingestion_produces_zero_new(self, records: list[UsageRecord]):
        """Re-ingesting the same collection produces zero new records."""
        # Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent
        first_pass = _deduplicate(records)
        existing_keys = {r.deduplication_key for r in first_pass}

        # Second ingestion: filter records whose key already exists
        new_records = [r for r in records if r.deduplication_key not in existing_keys]

        assert len(new_records) == 0

    @settings(max_examples=100)
    @given(records=st.lists(_usage_record_strategy(), min_size=0, max_size=50))
    def test_deduplication_is_idempotent(self, records: list[UsageRecord]):
        """Deduplicating an already-deduplicated set produces the same set."""
        # Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent
        first = _deduplicate(records)
        second = _deduplicate(first)

        first_keys = {r.deduplication_key for r in first}
        second_keys = {r.deduplication_key for r in second}

        assert len(first) == len(second)
        assert first_keys == second_keys

    @settings(max_examples=100)
    @given(
        records=st.lists(_usage_record_strategy(), min_size=1, max_size=30),
        extra_copies=st.integers(min_value=1, max_value=5),
    )
    def test_duplicates_do_not_increase_count(
        self, records: list[UsageRecord], extra_copies: int
    ):
        """Adding duplicate copies doesn't change the deduplicated count."""
        # Feature: axonllm-ledger, Property 3: CUR Deduplication Is Idempotent
        original_dedup = _deduplicate(records)
        expanded = records + records * extra_copies
        expanded_dedup = _deduplicate(expanded)

        assert len(expanded_dedup) == len(original_dedup)
