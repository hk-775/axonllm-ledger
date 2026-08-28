"""Property-based tests for access query distinct sets.

Feature: axonllm-ledger, Property 12: Access Queries Return Correct Distinct Sets

Validates: Requirements 9.2, 9.3
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.aggregation import AggregationEngine, TimeRange
from axonllm_ledger.models import AccessRecord


# --- Strategies (reuse naming conventions from test_property_aggregation) ---

_BASE_DT = datetime(2024, 1, 1)
_RANGE_DAYS = 30

_datetimes = st.integers(min_value=0, max_value=_RANGE_DAYS * 24 * 60 - 1).map(
    lambda minutes: _BASE_DT + timedelta(minutes=minutes)
)

_user_ids = st.sampled_from(["user-a", "user-b", "user-c", "user-d"])
_model_ids = st.sampled_from(["bedrock/claude-v3", "bedrock/titan", "sagemaker/custom-llm"])
_account_ids = st.sampled_from(["111111111111", "222222222222", "333333333333"])


@st.composite
def time_range(draw):
    """Generate a random TimeRange within the datetime window."""
    a = draw(_datetimes)
    b = draw(_datetimes)
    if a >= b:
        a, b = _BASE_DT, _BASE_DT + timedelta(days=_RANGE_DAYS)
    return TimeRange(start=a, end=b)


@st.composite
def access_record(draw):
    """Generate a random AccessRecord."""
    return AccessRecord(
        accessId=draw(st.uuids()).hex,
        userId=draw(_user_ids),
        modelId=draw(_model_ids),
        accountId=draw(_account_ids),
        timestamp=draw(_datetimes),
        sourceRecordId=draw(st.uuids()).hex,
    )


_access_records = st.lists(access_record(), min_size=0, max_size=30)


# --- Property Tests ---


class TestAccessQueryDistinctSets:
    """Property 12: Access Queries Return Correct Distinct Sets.

    For any set of AccessRecords and any time range: querying by user should
    return the distinct set of models that user accessed within the range, and
    querying by model should return the distinct set of users who accessed that
    model within the range. The returned sets should contain no duplicates and
    should match exactly the distinct values from the underlying AccessRecords.

    **Validates: Requirements 9.2, 9.3**
    """

    @settings(max_examples=100)
    @given(records=_access_records, tr=time_range(), uid=_user_ids)
    def test_user_access_returns_correct_distinct_models(
        self, records: list[AccessRecord], tr: TimeRange, uid: str
    ):
        """Querying by user returns the correct distinct set of model IDs.

        Feature: axonllm-ledger, Property 12: Access Queries Return Correct Distinct Sets
        """
        # **Validates: Requirements 9.2**
        engine = AggregationEngine(records=[], access_records=records)
        result = engine.get_access_report_for_user(uid, tr)

        # Manually compute expected distinct models
        expected = sorted(
            {
                r.modelId
                for r in records
                if r.userId == uid and tr.start <= r.timestamp < tr.end
            }
        )

        # No duplicates
        assert len(result) == len(set(result)), (
            f"Duplicates in user access result: {result}"
        )
        # Results are sorted
        assert result == sorted(result), (
            f"User access result not sorted: {result}"
        )
        # Matches expected
        assert result == expected, (
            f"User {uid}: got {result}, expected {expected}"
        )

    @settings(max_examples=100)
    @given(records=_access_records, tr=time_range(), mid=_model_ids)
    def test_model_access_returns_correct_distinct_users(
        self, records: list[AccessRecord], tr: TimeRange, mid: str
    ):
        """Querying by model returns the correct distinct set of user IDs.

        Feature: axonllm-ledger, Property 12: Access Queries Return Correct Distinct Sets
        """
        # **Validates: Requirements 9.3**
        engine = AggregationEngine(records=[], access_records=records)
        result = engine.get_access_report_for_model(mid, tr)

        # Manually compute expected distinct users
        expected = sorted(
            {
                r.userId
                for r in records
                if r.modelId == mid and tr.start <= r.timestamp < tr.end
            }
        )

        # No duplicates
        assert len(result) == len(set(result)), (
            f"Duplicates in model access result: {result}"
        )
        # Results are sorted
        assert result == sorted(result), (
            f"Model access result not sorted: {result}"
        )
        # Matches expected
        assert result == expected, (
            f"Model {mid}: got {result}, expected {expected}"
        )
