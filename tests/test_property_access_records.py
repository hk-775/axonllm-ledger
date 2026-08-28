"""Property-based tests for AccessRecord creation.

Feature: axonllm-ledger, Property 11: Access Records Are Created for Every GenAI Usage

Validates: Requirements 9.1
"""

from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.cur_ingestion import (
    DeduplicationStore,
    create_access_record,
    ingest_line_items,
    parse_line_item,
)
from axonllm_ledger.models import AccessRecord, UsageRecord


# --- Strategies (same pattern as test_property_cur_parsing.py) ---

_genai_service_codes = st.sampled_from(["AmazonBedrock", "AmazonSageMaker"])

_line_item_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)

_account_ids = st.from_regex(r"[0-9]{12}", fullmatch=True)

_user_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)

_model_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_."),
    min_size=1,
    max_size=40,
)

_regions = st.sampled_from([
    "us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1",
])

_resource_types = st.sampled_from([
    "foundation-model", "inference-profile", "endpoint",
])

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
    resource_type = draw(_resource_types)
    resource_arn = f"arn:aws:bedrock:{region}:{account_id}:{resource_type}/{model_id}"

    start_ts = draw(_iso_timestamps)
    end_ts = draw(_iso_timestamps)

    return {
        "product/servicecode": service_code,
        "identity/LineItemId": draw(_line_item_ids),
        "lineItem/UsageAccountId": account_id,
        "resourceTags/user:UserId": draw(_user_ids),
        "lineItem/ResourceId": resource_arn,
        "lineItem/UsageStartDate": start_ts,
        "lineItem/UsageEndDate": end_ts,
        "lineItem/UnblendedCost": draw(_costs),
        "lineItem/UsageAmount": draw(_token_counts),
        "product/outputTokens": draw(_token_counts),
        "product/invocationCount": draw(_invocation_counts),
    }


@st.composite
def genai_line_item_with_unique_key(draw):
    """Generate a valid GenAI CUR line item with a unique dedup key component."""
    item = draw(genai_line_item())
    # Append a unique suffix to lineItemId to ensure uniqueness across draws
    item["identity/LineItemId"] = item["identity/LineItemId"] + "-" + draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=8,
            max_size=12,
        )
    )
    return item


# --- Property Tests ---


class TestAccessRecordCreation:
    """Property 11: Access Records Are Created for Every GenAI Usage.

    For any valid GenAI CUR line item that produces a UsageRecord, an
    AccessRecord should also be created containing the correct user
    identifier, model identifier, account identifier, and timestamp from
    the source line item.

    **Validates: Requirements 9.1**
    """

    @settings(max_examples=100)
    @given(raw_item=genai_line_item())
    def test_create_access_record_has_correct_fields(self, raw_item: dict):
        """create_access_record produces an AccessRecord with matching userId, modelId, accountId, and timestamp.

        Feature: axonllm-ledger, Property 11: Access Records Are Created for Every GenAI Usage
        """
        # **Validates: Requirements 9.1**
        usage = parse_line_item(raw_item)
        assert usage is not None, "Valid GenAI line item must produce a UsageRecord"

        access = create_access_record(usage)

        assert isinstance(access, AccessRecord)
        assert access.userId == usage.userId
        assert access.modelId == usage.modelId
        assert access.accountId == usage.accountId
        assert access.timestamp == usage.usageStartDate
        assert access.sourceRecordId == usage.recordId
        # accessId should be a non-empty string (UUID)
        assert access.accessId and isinstance(access.accessId, str)

    @settings(max_examples=100)
    @given(raw_items=st.lists(genai_line_item_with_unique_key(), min_size=1, max_size=20))
    def test_ingest_produces_one_access_record_per_usage_record(self, raw_items: list[dict]):
        """ingest_line_items produces exactly one AccessRecord per new UsageRecord with correct field mappings.

        Feature: axonllm-ledger, Property 11: Access Records Are Created for Every GenAI Usage
        """
        # **Validates: Requirements 9.1**
        store = DeduplicationStore()
        result = ingest_line_items(raw_items, store)

        # One AccessRecord per new UsageRecord
        assert len(result.access_records) == len(result.new_records)

        # Each AccessRecord maps correctly to its corresponding UsageRecord
        for usage, access in zip(result.new_records, result.access_records):
            assert access.userId == usage.userId
            assert access.modelId == usage.modelId
            assert access.accountId == usage.accountId
            assert access.timestamp == usage.usageStartDate
            assert access.sourceRecordId == usage.recordId
