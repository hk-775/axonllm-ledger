"""Property-based tests for incomplete CUR line item handling.

Feature: axonllm-ledger, Property 2: Incomplete CUR Line Items Are Skipped and Logged

Validates: Requirements 1.4
"""

import logging
from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from axonllm_ledger.cur_ingestion import parse_line_item


# --- Strategies ---

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


# The required field keys and how to "break" them.
# Each entry is (raw_item_key, empty_value).
# For user_id we must also remove the fallback key.
REQUIRED_FIELD_KEYS = [
    "resourceTags/user:UserId",
    "lineItem/UsageAccountId",
    "lineItem/ResourceId",
    "lineItem/UsageStartDate",
    "lineItem/UnblendedCost",
]


@st.composite
def _complete_genai_line_item(draw):
    """Generate a fully valid GenAI CUR line item dict."""
    service_code = draw(_genai_service_codes)
    account_id = draw(_account_ids)
    model_id = draw(_model_ids)
    region = draw(_regions)
    resource_type = draw(_resource_types)
    resource_arn = f"arn:aws:bedrock:{region}:{account_id}:{resource_type}/{model_id}"

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


@st.composite
def incomplete_genai_line_item(draw):
    """Generate a GenAI CUR line item with one or more required fields removed or emptied.

    Returns a tuple of (raw_item, set_of_removed_field_keys) so the test can
    verify which fields were broken.
    """
    item = draw(_complete_genai_line_item())

    # Choose a non-empty subset of required fields to break
    fields_to_break = draw(
        st.lists(
            st.sampled_from(REQUIRED_FIELD_KEYS),
            min_size=1,
            max_size=len(REQUIRED_FIELD_KEYS),
            unique=True,
        )
    )

    for key in fields_to_break:
        # Randomly choose to delete the key or set it to empty string
        if draw(st.booleans()):
            item.pop(key, None)
        else:
            item[key] = ""

    # When breaking user ID, also ensure the fallback key is absent
    if "resourceTags/user:UserId" in fields_to_break:
        item.pop("identity/lineItemId", None)

    return item, fields_to_break


# --- Property Tests ---


class TestIncompleteCURLineItemHandling:
    """Property 2: Incomplete CUR Line Items Are Skipped and Logged.

    For any CUR line item missing one or more required fields (user ID,
    account ID, model ID, timestamp, cost), the parser should skip the
    record (produce no UsageRecord) and produce a log entry containing
    the affected line item identifier.

    **Validates: Requirements 1.4**
    """

    @settings(max_examples=100)
    @given(data=incomplete_genai_line_item())
    def test_incomplete_line_item_is_skipped_and_logged(self, data):
        """Incomplete GenAI line items return None and produce a warning log.

        Feature: axonllm-ledger, Property 2: Incomplete CUR Line Items Are Skipped and Logged
        """
        # **Validates: Requirements 1.4**
        raw_item, broken_fields = data

        line_item_id = raw_item.get("identity/LineItemId", "")

        cur_logger = logging.getLogger("axonllm_ledger.cur_ingestion")
        with _LogCapture(cur_logger) as captured:
            result = parse_line_item(raw_item)

        # The parser must skip the record
        assert result is None, (
            f"Expected None for line item missing {broken_fields}, got {result}"
        )

        # The parser must produce a warning log containing the line item identifier
        log_text = "\n".join(captured.messages)
        if line_item_id:
            assert line_item_id in log_text, (
                f"Expected line item id '{line_item_id}' in log output, "
                f"got: {log_text}"
            )


# --- Helpers ---


class _CaptureHandler(logging.Handler):
    """Handler that stores formatted log messages in a list."""

    def __init__(self, store: list[str]):
        super().__init__()
        self.store = store

    def emit(self, record: logging.LogRecord):
        self.store.append(self.format(record))


class _LogCapture:
    """Context manager that captures log messages from a specific logger."""

    def __init__(self, target_logger: logging.Logger, level: int = logging.WARNING):
        self.target_logger = target_logger
        self.level = level
        self.messages: list[str] = []
        self._handler: logging.Handler | None = None

    def __enter__(self):
        self.messages.clear()
        self._handler = _CaptureHandler(self.messages)
        self._handler.setLevel(self.level)
        self.target_logger.addHandler(self._handler)
        self.target_logger.setLevel(min(self.target_logger.level or self.level, self.level))
        return self

    def __exit__(self, *exc):
        if self._handler:
            self.target_logger.removeHandler(self._handler)
        return False
