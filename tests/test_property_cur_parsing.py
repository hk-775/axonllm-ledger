"""Property-based tests for CUR parsing completeness.

Feature: axonllm-ledger, Property 1: CUR Parsing Produces Complete UsageRecords for GenAI Line Items

Validates: Requirements 1.2, 1.3
"""

from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.cur_ingestion import parse_line_item
from axonllm_ledger.models import UsageRecord


# --- Strategies ---

_genai_service_codes = st.sampled_from(["AmazonBedrock", "AmazonSageMaker"])

_non_genai_service_codes = st.sampled_from([
    "AmazonEC2",
    "AmazonS3",
    "AmazonRDS",
    "AWSLambda",
    "AmazonDynamoDB",
    "AmazonCloudWatch",
])

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
def non_genai_line_item(draw):
    """Generate a CUR line item dict with a non-GenAI service code."""
    account_id = draw(_account_ids)
    model_id = draw(_model_ids)
    region = draw(_regions)
    resource_arn = f"arn:aws:bedrock:{region}:{account_id}:foundation-model/{model_id}"

    return {
        "product/servicecode": draw(_non_genai_service_codes),
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


# --- Property Tests ---


class TestCURParsingCompleteness:
    """Property 1: CUR Parsing Produces Complete UsageRecords for GenAI Line Items.

    For any CUR line item with a GenAI service code (AmazonBedrock or
    AmazonSageMaker) and all required fields present, parsing that line item
    should produce a UsageRecord containing the correct user identifier,
    account identifier, model identifier, timestamp, token counts, and cost
    matching the source line item. For any CUR line item with a non-GenAI
    service code, parsing should produce no UsageRecord.

    **Validates: Requirements 1.2, 1.3**
    """

    @settings(max_examples=100)
    @given(raw_item=genai_line_item())
    def test_genai_line_item_produces_usage_record(self, raw_item: dict):
        """Valid GenAI line items produce a UsageRecord with correct fields.

        Feature: axonllm-ledger, Property 1: CUR Parsing Produces Complete UsageRecords for GenAI Line Items
        """
        # **Validates: Requirements 1.2, 1.3**
        result = parse_line_item(raw_item)

        assert result is not None, "GenAI line item should produce a UsageRecord"
        assert isinstance(result, UsageRecord)

        # Verify user identifier matches
        assert result.userId == raw_item["resourceTags/user:UserId"]

        # Verify account identifier matches
        assert result.accountId == raw_item["lineItem/UsageAccountId"]

        # Verify model identifier is extracted from the resource ARN
        expected_model_id = raw_item["lineItem/ResourceId"].rsplit("/", 1)[-1]
        assert result.modelId == expected_model_id

        # Verify service name matches
        assert result.serviceName == raw_item["product/servicecode"]

        # Verify timestamp is parsed correctly from the ISO string
        ts_str = raw_item["lineItem/UsageStartDate"].rstrip("Z")
        expected_start = datetime.fromisoformat(ts_str)
        assert result.usageStartDate == expected_start

        # Verify cost matches
        assert result.cost == Decimal(raw_item["lineItem/UnblendedCost"])

        # Verify token counts match
        assert result.inputTokens == int(float(raw_item["lineItem/UsageAmount"]))
        assert result.outputTokens == int(float(raw_item["product/outputTokens"]))

        # Verify invocation count
        assert result.invocationCount == int(float(raw_item["product/invocationCount"]))

        # Verify line item ID is preserved
        assert result.lineItemId == raw_item["identity/LineItemId"]

    @settings(max_examples=100)
    @given(raw_item=non_genai_line_item())
    def test_non_genai_line_item_produces_none(self, raw_item: dict):
        """Non-GenAI line items produce no UsageRecord.

        Feature: axonllm-ledger, Property 1: CUR Parsing Produces Complete UsageRecords for GenAI Line Items
        """
        # **Validates: Requirements 1.2, 1.3**
        result = parse_line_item(raw_item)

        assert result is None, (
            f"Non-GenAI line item with service code "
            f"'{raw_item['product/servicecode']}' should produce None"
        )
