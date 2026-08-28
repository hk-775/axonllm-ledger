"""Property-based tests for data integrity cross-dimension consistency.

Feature: axonllm-ledger, Property 16: Cross-Dimension Cost Consistency

Validates: Requirements 11.1
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.aggregation import AggregationEngine, TimeRange
from axonllm_ledger.data_integrity import DataIntegrityService
from axonllm_ledger.models import UsageRecord


# --- Strategies (reused patterns from test_property_aggregation.py) ---

_BASE_DT = datetime(2024, 1, 1)
_RANGE_DAYS = 30

_datetimes = st.integers(min_value=0, max_value=_RANGE_DAYS * 24 * 60 - 1).map(
    lambda minutes: _BASE_DT + timedelta(minutes=minutes)
)

_user_ids = st.sampled_from(["user-a", "user-b", "user-c", "user-d"])
_account_ids = st.sampled_from(["111111111111", "222222222222", "333333333333"])
_model_ids = st.sampled_from(["bedrock/claude-v3", "bedrock/titan", "sagemaker/custom-llm"])
_service_names = st.sampled_from(["AmazonBedrock", "AmazonSageMaker"])

_costs = st.decimals(
    min_value=Decimal("0.00"),
    max_value=Decimal("9999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

_token_counts = st.integers(min_value=0, max_value=100_000)
_invocation_counts = st.integers(min_value=0, max_value=1000)


@st.composite
def usage_record(draw):
    """Generate a random UsageRecord."""
    start = draw(_datetimes)
    end = start + timedelta(hours=1)
    return UsageRecord(
        recordId=draw(st.uuids()).hex,
        lineItemId=draw(st.uuids()).hex,
        userId=draw(_user_ids),
        accountId=draw(_account_ids),
        modelId=draw(_model_ids),
        serviceName=draw(_service_names),
        usageStartDate=start,
        usageEndDate=end,
        inputTokens=draw(_token_counts),
        outputTokens=draw(_token_counts),
        invocationCount=draw(_invocation_counts),
        cost=draw(_costs),
        ingestedAt=datetime.now(tz=None),
        sourceExportId="export-001",
    )


@st.composite
def time_range(draw):
    """Generate a random TimeRange within the datetime window.

    Ensures start < end so the range is non-empty.
    """
    a = draw(_datetimes)
    b = draw(_datetimes)
    if a >= b:
        a, b = _BASE_DT, _BASE_DT + timedelta(days=_RANGE_DAYS)
    return TimeRange(start=a, end=b)


_usage_records = st.lists(usage_record(), min_size=0, max_size=30)


class _FakeNotifier:
    """No-op notifier for property tests."""

    def send_alert(self, subject: str, message: str) -> None:
        pass


class TestCrossDimensionCostConsistency:
    """Property 16: Cross-Dimension Cost Consistency.

    For any set of UsageRecords and any time range, the sum of all per-user
    costs should equal the sum of all per-account costs. This invariant must
    hold regardless of how records are distributed across users and accounts.

    **Validates: Requirements 11.1**
    """

    # Feature: axonllm-ledger, Property 16: Cross-Dimension Cost Consistency

    @settings(max_examples=100)
    @given(records=_usage_records, tr=time_range())
    def test_user_total_equals_account_total(
        self, records: list[UsageRecord], tr: TimeRange
    ):
        """Sum of per-user costs equals sum of per-account costs for any time range.

        Feature: axonllm-ledger, Property 16: Cross-Dimension Cost Consistency
        """
        # **Validates: Requirements 11.1**
        engine = AggregationEngine(records)
        service = DataIntegrityService(engine, _FakeNotifier())

        result = service.validate_cross_dimension_consistency(tr)

        assert result.is_consistent is True
        assert result.user_total == result.account_total
        assert result.discrepancy == Decimal("0")


# ---------------------------------------------------------------------------
# Property 17: Data Gap Detection
# ---------------------------------------------------------------------------

# Strategy: sorted list of timestamps where gaps between consecutive pairs
# are drawn from a strategy.  Some gaps exceed the expected interval (1 hour)
# and some do not.

_GAP_BASE_DT = datetime(2024, 6, 1)
_EXPECTED_INTERVAL = timedelta(hours=1)

# Gap sizes in minutes – some within the expected interval, some exceeding it
_gap_minutes_within = st.integers(min_value=1, max_value=60)
_gap_minutes_exceeding = st.integers(min_value=61, max_value=600)


@st.composite
def _timestamps_with_gaps(draw):
    """Generate a sorted list of timestamps that contain at least one gap.

    Returns (timestamps, expected_gap_count) where expected_gap_count is the
    number of consecutive pairs whose gap exceeds _EXPECTED_INTERVAL.
    """
    # Number of intervals (at least 2 timestamps → at least 1 interval)
    n_intervals = draw(st.integers(min_value=1, max_value=15))

    # For each interval decide whether it's a gap or not.
    # Ensure at least one gap exists.
    has_gap = draw(
        st.lists(st.booleans(), min_size=n_intervals, max_size=n_intervals).filter(
            lambda bools: any(bools)
        )
    )

    timestamps = [_GAP_BASE_DT]
    for is_gap in has_gap:
        if is_gap:
            delta = timedelta(minutes=draw(_gap_minutes_exceeding))
        else:
            delta = timedelta(minutes=draw(_gap_minutes_within))
        timestamps.append(timestamps[-1] + delta)

    expected_gap_count = sum(1 for g in has_gap if g)
    return timestamps, expected_gap_count


@st.composite
def _timestamps_without_gaps(draw):
    """Generate a sorted list of timestamps with NO gaps (all within expected interval)."""
    n_intervals = draw(st.integers(min_value=1, max_value=15))
    timestamps = [_GAP_BASE_DT]
    for _ in range(n_intervals):
        delta = timedelta(minutes=draw(_gap_minutes_within))
        timestamps.append(timestamps[-1] + delta)
    return timestamps


_source_names = st.sampled_from(["CUR", "Budgets", "Organizations", "COH"])


class TestDataGapDetection:
    """Property 17: Data Gap Detection.

    For any sequence of ingestion runs from a data source where there is a
    missing time period (a gap between consecutive ingestion windows), the
    system should detect the gap and produce a log entry containing the
    affected time range and source identifier.

    **Validates: Requirements 11.3**
    """

    # Feature: axonllm-ledger, Property 17: Data Gap Detection

    @settings(max_examples=100)
    @given(data=_timestamps_with_gaps(), source=_source_names)
    def test_gaps_detected_when_present(self, data, source: str):
        """Every gap larger than the expected interval is detected with correct fields.

        Feature: axonllm-ledger, Property 17: Data Gap Detection
        """
        # **Validates: Requirements 11.3**
        timestamps, expected_gap_count = data

        engine = AggregationEngine([])
        service = DataIntegrityService(engine, _FakeNotifier())

        gaps = service.detect_data_gaps(source, timestamps, _EXPECTED_INTERVAL)

        # The number of detected gaps must match the expected count
        assert len(gaps) == expected_gap_count

        # Each gap must reference the correct source
        for gap in gaps:
            assert gap.source == source

        # Verify gap_start and gap_end correspond to actual consecutive pairs
        sorted_ts = sorted(timestamps)
        expected_gaps = []
        for prev, curr in zip(sorted_ts, sorted_ts[1:]):
            if curr - prev > _EXPECTED_INTERVAL:
                expected_gaps.append((prev, curr))

        assert len(gaps) == len(expected_gaps)
        for gap, (exp_start, exp_end) in zip(gaps, expected_gaps):
            assert gap.gap_start == exp_start
            assert gap.gap_end == exp_end

    @settings(max_examples=100)
    @given(timestamps=_timestamps_without_gaps(), source=_source_names)
    def test_no_gaps_detected_when_none_present(self, timestamps, source: str):
        """No gaps are detected when all consecutive timestamps are within expected interval.

        Feature: axonllm-ledger, Property 17: Data Gap Detection
        """
        # **Validates: Requirements 11.3**
        engine = AggregationEngine([])
        service = DataIntegrityService(engine, _FakeNotifier())

        gaps = service.detect_data_gaps(source, timestamps, _EXPECTED_INTERVAL)

        assert gaps == []


# ---------------------------------------------------------------------------
# Property 18: CUR vs Budget Reconciliation Threshold
# ---------------------------------------------------------------------------

# Strategies: positive Decimals for CUR total and Budget actual spend.
# We avoid zero for budget_actual_spend in the main test to keep the
# percentage formula straightforward (zero is handled in a dedicated test).

_positive_decimals = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("999999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


class TestReconciliationThreshold:
    """Property 18: CUR vs Budget Reconciliation Threshold.

    For any pair of CUR-derived cost total and Budgets_Source actual spend
    for the same time period, if the absolute percentage discrepancy exceeds
    1%, the system should log the discrepancy. If the discrepancy is 1% or
    less, no discrepancy log should be produced.

    **Validates: Requirements 11.4**
    """

    # Feature: axonllm-ledger, Property 18: CUR vs Budget Reconciliation Threshold

    @settings(max_examples=100)
    @given(
        cur_total=_positive_decimals,
        budget_actual_spend=_positive_decimals,
        tr=time_range(),
    )
    def test_reconciliation_threshold_classification(
        self,
        cur_total: Decimal,
        budget_actual_spend: Decimal,
        tr: TimeRange,
    ):
        """Discrepancy > 1% → not within threshold; ≤ 1% → within threshold.

        Feature: axonllm-ledger, Property 18: CUR vs Budget Reconciliation Threshold
        """
        # **Validates: Requirements 11.4**
        engine = AggregationEngine([])
        service = DataIntegrityService(engine, _FakeNotifier())

        result = service.reconcile_cur_vs_budgets(cur_total, budget_actual_spend, tr)

        # Independently compute expected discrepancy percentage
        expected_pct = (
            abs(cur_total - budget_actual_spend)
            / budget_actual_spend
            * Decimal("100")
        )

        assert result.cur_total == cur_total
        assert result.budget_actual_spend == budget_actual_spend
        assert result.discrepancy_pct == expected_pct

        if expected_pct > Decimal("1"):
            assert result.is_within_threshold is False
        else:
            assert result.is_within_threshold is True

    @settings(max_examples=100)
    @given(
        budget_actual_spend=_positive_decimals,
        tr=time_range(),
    )
    def test_boundary_at_one_percent(
        self,
        budget_actual_spend: Decimal,
        tr: TimeRange,
    ):
        """When CUR total is exactly 1% away from budget, result is within threshold.

        Feature: axonllm-ledger, Property 18: CUR vs Budget Reconciliation Threshold
        """
        # **Validates: Requirements 11.4**
        # Construct cur_total that is exactly 1% above budget_actual_spend
        cur_total = budget_actual_spend * Decimal("1.01")

        engine = AggregationEngine([])
        service = DataIntegrityService(engine, _FakeNotifier())

        result = service.reconcile_cur_vs_budgets(cur_total, budget_actual_spend, tr)

        # Exactly 1% discrepancy should be within threshold
        assert result.is_within_threshold is True
        assert result.discrepancy_pct == Decimal("1") * Decimal("100") / Decimal("100")

    @settings(max_examples=100)
    @given(
        budget_actual_spend=_positive_decimals,
        tr=time_range(),
    )
    def test_zero_discrepancy_is_within_threshold(
        self,
        budget_actual_spend: Decimal,
        tr: TimeRange,
    ):
        """When CUR total equals budget actual spend, discrepancy is 0% (within threshold).

        Feature: axonllm-ledger, Property 18: CUR vs Budget Reconciliation Threshold
        """
        # **Validates: Requirements 11.4**
        engine = AggregationEngine([])
        service = DataIntegrityService(engine, _FakeNotifier())

        result = service.reconcile_cur_vs_budgets(
            budget_actual_spend, budget_actual_spend, tr
        )

        assert result.discrepancy_pct == Decimal("0")
        assert result.is_within_threshold is True
