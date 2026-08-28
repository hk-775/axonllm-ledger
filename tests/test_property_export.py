"""Property-based tests for analytics export completeness.

Feature: axonllm-ledger, Property 13: Analytics Export Contains All Required Data Categories

Validates: Requirements 10.2, 10.3, 10.4, 10.5
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.aggregation import AggregationEngine, TimeRange
from axonllm_ledger.export import package_export_data
from axonllm_ledger.models import (
    AccessRecord,
    OptimizationRecommendation,
    ProcessedBudget,
    UsageRecord,
)


# --- Strategies (reuse patterns from test_property_aggregation.py) ---

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


@st.composite
def processed_budget(draw):
    """Generate a random ProcessedBudget."""
    limit = draw(_costs)
    actual = draw(_costs)
    start = draw(_datetimes)
    end = start + timedelta(days=draw(st.integers(min_value=1, max_value=30)))
    return ProcessedBudget(
        budgetId=draw(st.uuids()).hex,
        budgetName=draw(st.text(min_size=1, max_size=20)),
        accountId=draw(_account_ids),
        budgetLimit=limit,
        forecastedSpend=draw(_costs),
        actualSpend=actual,
        periodStart=start,
        periodEnd=end,
        isExceeded=actual > limit,
        ingestedAt=datetime.now(tz=None),
    )


@st.composite
def optimization_recommendation(draw):
    """Generate a random OptimizationRecommendation."""
    return OptimizationRecommendation(
        recommendationId=draw(st.uuids()).hex,
        accountId=draw(_account_ids),
        modelId=draw(st.one_of(st.none(), _model_ids)),
        recommendationType=draw(
            st.sampled_from(["rightsizing", "reserved_capacity", "spot_usage"])
        ),
        estimatedSavings=draw(_costs),
        description=draw(st.text(min_size=1, max_size=50)),
        ingestedAt=datetime.now(tz=None),
    )


@st.composite
def time_range(draw):
    """Generate a random TimeRange ensuring start < end."""
    a = draw(_datetimes)
    b = draw(_datetimes)
    if a >= b:
        a, b = _BASE_DT, _BASE_DT + timedelta(days=_RANGE_DAYS)
    return TimeRange(start=a, end=b)


# --- Property Test ---


class TestExportCompleteness:
    """Property 13: Analytics Export Contains All Required Data Categories.

    For any export period, the analytics export output should contain: cost
    aggregations by user, by account, by organizational unit, and by model;
    model access data per user; budget threshold and actual spend comparisons;
    and Cost Optimization Hub recommendations with estimated savings.

    **Validates: Requirements 10.2, 10.3, 10.4, 10.5**
    """

    # Feature: axonllm-ledger, Property 13: Analytics Export Contains All Required Data Categories

    @settings(max_examples=100)
    @given(
        records=st.lists(usage_record(), min_size=0, max_size=20),
        access_recs=st.lists(access_record(), min_size=0, max_size=20),
        budgets=st.lists(processed_budget(), min_size=0, max_size=5),
        recommendations=st.lists(optimization_recommendation(), min_size=0, max_size=5),
        tr=time_range(),
        user_ids_input=st.lists(_user_ids, min_size=0, max_size=4, unique=True),
    )
    def test_export_contains_all_required_categories(
        self,
        records: list[UsageRecord],
        access_recs: list[AccessRecord],
        budgets: list[ProcessedBudget],
        recommendations: list[OptimizationRecommendation],
        tr: TimeRange,
        user_ids_input: list[str],
    ):
        """Package export data contains all required data categories.

        Feature: axonllm-ledger, Property 13: Analytics Export Contains All Required Data Categories
        **Validates: Requirements 10.2, 10.3, 10.4, 10.5**
        """
        engine = AggregationEngine(records, access_records=access_recs)
        pkg = package_export_data(engine, tr, budgets, recommendations, user_ids_input)

        # cost_by_user matches engine.aggregate_by_user
        expected_by_user = engine.aggregate_by_user(tr)
        assert pkg.cost_by_user == expected_by_user

        # cost_by_account matches engine.aggregate_by_account
        expected_by_account = engine.aggregate_by_account(tr)
        assert pkg.cost_by_account == expected_by_account

        # cost_by_ou matches engine.aggregate_by_ou
        expected_by_ou = engine.aggregate_by_ou(tr)
        assert pkg.cost_by_ou == expected_by_ou

        # cost_by_model matches engine.aggregate_by_model
        expected_by_model = engine.aggregate_by_model(tr)
        assert pkg.cost_by_model == expected_by_model

        # model_access_per_user has entries for each user_id provided
        assert set(pkg.model_access_per_user.keys()) == set(user_ids_input)
        for uid in user_ids_input:
            expected_models = engine.get_access_report_for_user(uid, tr)
            assert pkg.model_access_per_user[uid] == expected_models

        # budget_comparisons == the input budgets
        assert pkg.budget_comparisons == budgets

        # optimization_recommendations == the input recommendations
        assert pkg.optimization_recommendations == recommendations

        # export_period == the input time_range
        assert pkg.export_period == tr


# ---------------------------------------------------------------------------
# Property 15: Analytics Export Retry Logic
# Feature: axonllm-ledger, Property 15: Analytics Export Retry Logic
# Validates: Requirements 10.7
# ---------------------------------------------------------------------------

from axonllm_ledger.export import (
    ExportService,
    LedgerExportPackage,
)
from axonllm_ledger.models import ExportStatus


class _FakeDeliveryTarget:
    """DeliveryTarget that fails ``fail_count`` times then succeeds."""

    def __init__(self, fail_count: int) -> None:
        self._fail_count = fail_count
        self._calls = 0

    def deliver(self, package: LedgerExportPackage) -> bool:
        self._calls += 1
        if self._calls <= self._fail_count:
            raise RuntimeError(f"delivery failure #{self._calls}")
        return True


class _FakeAlertNotifier:
    """AlertNotifier that records sent alerts."""

    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    def send_alert(self, subject: str, message: str) -> None:
        self.alerts.append((subject, message))


class TestExportRetryLogic:
    """Property 15: Analytics Export Retry Logic.

    For any failed analytics export delivery, the system should retry up to 3
    times. If all 3 retries fail, an alert notification should be sent. The
    total attempt count should never exceed 3.

    **Validates: Requirements 10.7**
    """

    # Feature: axonllm-ledger, Property 15: Analytics Export Retry Logic

    @settings(max_examples=100)
    @given(fail_count=st.integers(min_value=0, max_value=10))
    def test_retry_logic_respects_max_retries(self, fail_count: int):
        """Export retries up to 3 times, sends alert only when all fail.

        Feature: axonllm-ledger, Property 15: Analytics Export Retry Logic
        **Validates: Requirements 10.7**
        """
        target = _FakeDeliveryTarget(fail_count)
        notifier = _FakeAlertNotifier()
        service = ExportService(target, notifier)
        package = LedgerExportPackage(
            export_period=TimeRange(
                start=datetime(2024, 1, 1), end=datetime(2024, 2, 1)
            )
        )

        result = service.execute_export(package)

        # attemptCount must never exceed MAX_ATTEMPTS (3)
        assert result.attemptCount <= 3

        if fail_count < 3:
            # Delivery succeeds within the retry budget
            assert result.status == ExportStatus.SUCCESS
            assert result.attemptCount == fail_count + 1
            assert len(notifier.alerts) == 0
        else:
            # All 3 attempts failed — alert must be sent
            assert result.status == ExportStatus.FAILED
            assert result.attemptCount == 3
            assert len(notifier.alerts) == 1


# ---------------------------------------------------------------------------
# Property 14: Analytics Export Logging Contains Required Fields
# Feature: axonllm-ledger, Property 14: Analytics Export Logging Contains Required Fields
# Validates: Requirements 10.6
# ---------------------------------------------------------------------------


class TestExportLogging:
    """Property 14: Analytics Export Logging Contains Required Fields.

    For any completed analytics export (success or failure), the resulting log
    entry should contain the export timestamp, record count, and export status.

    **Validates: Requirements 10.6**
    """

    # Feature: axonllm-ledger, Property 14: Analytics Export Logging Contains Required Fields

    @settings(max_examples=100)
    @given(fail_count=st.integers(min_value=0, max_value=10))
    def test_export_log_contains_required_fields(self, fail_count: int):
        """Every export run produces a log entry with exportedAt, recordCount, and status.

        Feature: axonllm-ledger, Property 14: Analytics Export Logging Contains Required Fields
        **Validates: Requirements 10.6**
        """
        target = _FakeDeliveryTarget(fail_count)
        notifier = _FakeAlertNotifier()
        service = ExportService(target, notifier)
        package = LedgerExportPackage(
            export_period=TimeRange(
                start=datetime(2024, 1, 1), end=datetime(2024, 2, 1)
            )
        )

        result = service.execute_export(package)

        # A log entry must always be created
        assert len(service.export_log) == 1
        logged = service.export_log[0]

        # exportedAt must be a datetime and not None
        assert logged.exportedAt is not None
        assert isinstance(logged.exportedAt, datetime)

        # recordCount must be an int >= 0
        assert isinstance(logged.recordCount, int)
        assert logged.recordCount >= 0

        # status must be a valid ExportStatus value
        assert isinstance(logged.status, ExportStatus)
        assert logged.status in (ExportStatus.SUCCESS, ExportStatus.FAILED)

        # The logged record must match the returned result
        assert logged is result
