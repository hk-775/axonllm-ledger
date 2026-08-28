"""Unit tests for analytics export packaging and delivery."""

from datetime import datetime
from decimal import Decimal

from axonllm_ledger.aggregation import (
    AggregationEngine,
    AggregationResult,
    TimeRange,
)
from axonllm_ledger.export import (
    LedgerExportPackage,
    package_export_data,
)
from axonllm_ledger.models import (
    AccessRecord,
    OptimizationRecommendation,
    ProcessedBudget,
    UsageRecord,
)

TIME_RANGE = TimeRange(start=datetime(2024, 3, 1), end=datetime(2024, 4, 1))


def _make_usage(
    user_id: str = "user-a",
    account_id: str = "111111111111",
    model_id: str = "anthropic.claude-v2",
    cost: Decimal = Decimal("1.00"),
    start: datetime = datetime(2024, 3, 15),
) -> UsageRecord:
    return UsageRecord(
        recordId=UsageRecord.generate_id(),
        lineItemId="li-1",
        userId=user_id,
        accountId=account_id,
        modelId=model_id,
        serviceName="AmazonBedrock",
        usageStartDate=start,
        usageEndDate=start,
        inputTokens=100,
        outputTokens=50,
        invocationCount=1,
        cost=cost,
        ingestedAt=datetime(2024, 3, 16),
        sourceExportId="export-001",
    )


def _make_access(
    user_id: str = "user-a",
    model_id: str = "anthropic.claude-v2",
    account_id: str = "111111111111",
    ts: datetime = datetime(2024, 3, 15),
) -> AccessRecord:
    return AccessRecord(
        accessId=AccessRecord.generate_id(),
        userId=user_id,
        modelId=model_id,
        accountId=account_id,
        timestamp=ts,
        sourceRecordId="rec-1",
    )


def _make_budget(
    account_id: str = "111111111111",
    limit: Decimal = Decimal("100.00"),
    actual: Decimal = Decimal("80.00"),
) -> ProcessedBudget:
    return ProcessedBudget(
        budgetId="budget-1",
        budgetName="GenAI Budget",
        accountId=account_id,
        budgetLimit=limit,
        forecastedSpend=Decimal("90.00"),
        actualSpend=actual,
        periodStart=datetime(2024, 3, 1),
        periodEnd=datetime(2024, 4, 1),
        isExceeded=actual > limit,
        ingestedAt=datetime(2024, 3, 16),
    )


def _make_recommendation(
    account_id: str = "111111111111",
    model_id: str = "anthropic.claude-v2",
    savings: Decimal = Decimal("15.00"),
) -> OptimizationRecommendation:
    return OptimizationRecommendation(
        recommendationId="rec-1",
        accountId=account_id,
        modelId=model_id,
        recommendationType="rightsizing",
        estimatedSavings=savings,
        description="Consider reserved capacity",
        ingestedAt=datetime(2024, 3, 16),
    )


class TestEmptyDataProducesEmptyPackage:
    def test_empty_engine_returns_empty_lists(self):
        engine = AggregationEngine([])
        pkg = package_export_data(engine, TIME_RANGE, [], [], [])

        assert pkg.cost_by_user == []
        assert pkg.cost_by_account == []
        assert pkg.cost_by_ou == []
        assert pkg.cost_by_model == []
        assert pkg.model_access_per_user == {}
        assert pkg.budget_comparisons == []
        assert pkg.optimization_recommendations == []


class TestCostAggregationCategories:
    def test_package_contains_all_four_cost_categories(self):
        r1 = _make_usage(user_id="user-a", account_id="acct-1", model_id="model-a")
        r2 = _make_usage(user_id="user-b", account_id="acct-2", model_id="model-b")
        engine = AggregationEngine([r1, r2])

        pkg = package_export_data(engine, TIME_RANGE, [], [], [])

        assert len(pkg.cost_by_user) == 2
        assert len(pkg.cost_by_account) == 2
        assert len(pkg.cost_by_model) == 2
        # OU aggregation groups under "Unknown OU" when no hierarchy
        assert len(pkg.cost_by_ou) >= 1


class TestModelAccessPerUser:
    def test_model_access_included_per_user(self):
        access = [
            _make_access(user_id="user-a", model_id="model-x"),
            _make_access(user_id="user-a", model_id="model-y"),
            _make_access(user_id="user-b", model_id="model-x"),
        ]
        engine = AggregationEngine([], access_records=access)

        pkg = package_export_data(
            engine, TIME_RANGE, [], [], ["user-a", "user-b"]
        )

        assert sorted(pkg.model_access_per_user["user-a"]) == [
            "model-x",
            "model-y",
        ]
        assert pkg.model_access_per_user["user-b"] == ["model-x"]

    def test_user_with_no_access_returns_empty_list(self):
        engine = AggregationEngine([])
        pkg = package_export_data(engine, TIME_RANGE, [], [], ["user-z"])

        assert pkg.model_access_per_user["user-z"] == []


class TestBudgetComparisons:
    def test_budgets_included_in_package(self):
        budgets = [_make_budget(), _make_budget(account_id="222222222222")]
        engine = AggregationEngine([])

        pkg = package_export_data(engine, TIME_RANGE, budgets, [], [])

        assert len(pkg.budget_comparisons) == 2
        assert pkg.budget_comparisons[0].budgetLimit == Decimal("100.00")
        assert pkg.budget_comparisons[0].actualSpend == Decimal("80.00")


class TestOptimizationRecommendations:
    def test_recommendations_included_in_package(self):
        recs = [_make_recommendation(), _make_recommendation(savings=Decimal("25.00"))]
        engine = AggregationEngine([])

        pkg = package_export_data(engine, TIME_RANGE, [], recs, [])

        assert len(pkg.optimization_recommendations) == 2
        assert pkg.optimization_recommendations[0].estimatedSavings == Decimal("15.00")
        assert pkg.optimization_recommendations[1].estimatedSavings == Decimal("25.00")


class TestTimeRangePreserved:
    def test_export_period_matches_input_time_range(self):
        engine = AggregationEngine([])
        pkg = package_export_data(engine, TIME_RANGE, [], [], [])

        assert pkg.export_period is not None
        assert pkg.export_period.start == datetime(2024, 3, 1)
        assert pkg.export_period.end == datetime(2024, 4, 1)


def test_record_count_includes_every_export_category():
    package = LedgerExportPackage(
        cost_by_user=[AggregationResult("user", Decimal("1"), 1, 1, 1)],
        cost_by_account=[AggregationResult("account", Decimal("1"), 1, 1, 1)],
        model_access_per_user={"user": ["model-a", "model-b"]},
        budget_comparisons=[_make_budget()],
        optimization_recommendations=[_make_recommendation()],
    )

    assert package.record_count == 6


# ---------------------------------------------------------------------------
# Unit tests for ExportService (task 8.3)
# ---------------------------------------------------------------------------

from axonllm_ledger.export import (
    DeliveryTarget,
    AlertNotifier,
    ExportService,
)
from axonllm_ledger.models import ExportStatus


def _make_package() -> LedgerExportPackage:
    """Create a minimal LedgerExportPackage for delivery tests."""
    return LedgerExportPackage(export_period=TIME_RANGE)


class _FakeTarget:
    """DeliveryTarget that can be configured to fail N times then succeed."""

    def __init__(self, fail_count: int = 0) -> None:
        self._fail_count = fail_count
        self._calls = 0

    def deliver(self, package: LedgerExportPackage) -> bool:
        self._calls += 1
        if self._calls <= self._fail_count:
            raise RuntimeError(f"delivery failure #{self._calls}")
        return True

    @property
    def call_count(self) -> int:
        return self._calls


class _FakeNotifier:
    """AlertNotifier that records sent alerts."""

    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    def send_alert(self, subject: str, message: str) -> None:
        self.alerts.append((subject, message))


class TestSuccessOnFirstAttempt:
    def test_returns_success_with_attempt_count_1(self):
        target = _FakeTarget(fail_count=0)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        assert result.status == ExportStatus.SUCCESS
        assert result.attemptCount == 1
        assert result.errorMessage is None
        assert target.call_count == 1
        assert notifier.alerts == []


class TestSuccessOnRetry:
    def test_fail_once_succeed_on_second(self):
        target = _FakeTarget(fail_count=1)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        assert result.status == ExportStatus.SUCCESS
        assert result.attemptCount == 2
        assert result.errorMessage is None
        assert target.call_count == 2
        assert notifier.alerts == []

    def test_fail_twice_succeed_on_third(self):
        target = _FakeTarget(fail_count=2)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        assert result.status == ExportStatus.SUCCESS
        assert result.attemptCount == 3
        assert target.call_count == 3
        assert notifier.alerts == []


class TestAllRetriesFail:
    def test_three_failures_sends_alert_and_returns_failed(self):
        target = _FakeTarget(fail_count=10)  # always fails
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        assert result.status == ExportStatus.FAILED
        assert result.attemptCount == 3
        assert result.errorMessage is not None
        assert target.call_count == 3
        # Exactly one alert sent
        assert len(notifier.alerts) == 1

    def test_alert_contains_period_info(self):
        target = _FakeTarget(fail_count=10)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        service.execute_export(_make_package())

        subject, message = notifier.alerts[0]
        assert "failed" in subject.lower()
        # Period info from TIME_RANGE should appear in the message
        assert "2024-03-01" in message
        assert "2024-04-01" in message


class TestAttemptCountCorrectness:
    def test_attempt_count_matches_actual_calls(self):
        for fail_count in range(4):
            target = _FakeTarget(fail_count=fail_count)
            notifier = _FakeNotifier()
            service = ExportService(target, notifier)

            result = service.execute_export(_make_package())

            expected_attempts = min(fail_count + 1, 3)
            assert result.attemptCount == expected_attempts


# ---------------------------------------------------------------------------
# Unit tests for export logging (task 8.5)
# ---------------------------------------------------------------------------


class TestExportLoggingOnSuccess:
    """Verify that a successful export logs a ExportRecord."""

    def test_successful_export_is_logged(self):
        target = _FakeTarget(fail_count=0)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        assert len(service.export_log) == 1
        logged = service.export_log[0]
        assert logged.exportId == result.exportId
        assert logged.status == ExportStatus.SUCCESS
        assert logged.recordCount == result.recordCount
        assert logged.exportedAt == result.exportedAt

    def test_logged_record_contains_timestamp(self):
        target = _FakeTarget(fail_count=0)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        logged = service.export_log[0]
        assert isinstance(logged.exportedAt, datetime)

    def test_logged_record_contains_record_count(self):
        target = _FakeTarget(fail_count=0)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        logged = service.export_log[0]
        assert isinstance(logged.recordCount, int)
        assert logged.recordCount >= 0


class TestExportLoggingOnFailure:
    """Verify that a failed export (all retries exhausted) logs a ExportRecord."""

    def test_failed_export_is_logged(self):
        target = _FakeTarget(fail_count=10)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        assert len(service.export_log) == 1
        logged = service.export_log[0]
        assert logged.exportId == result.exportId
        assert logged.status == ExportStatus.FAILED
        assert logged.errorMessage is not None

    def test_failed_record_has_correct_attempt_count(self):
        target = _FakeTarget(fail_count=10)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        service.execute_export(_make_package())

        logged = service.export_log[0]
        assert logged.attemptCount == 3


class TestExportLoggingOnRetrySuccess:
    """Verify logging when export succeeds after retries."""

    def test_retry_success_is_logged_once(self):
        target = _FakeTarget(fail_count=2)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        result = service.execute_export(_make_package())

        assert len(service.export_log) == 1
        logged = service.export_log[0]
        assert logged.status == ExportStatus.SUCCESS
        assert logged.attemptCount == 3


class TestExportLogAccumulation:
    """Verify that multiple exports accumulate in the log."""

    def test_multiple_exports_accumulate(self):
        target = _FakeTarget(fail_count=0)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        service.execute_export(_make_package())
        service.execute_export(_make_package())
        service.execute_export(_make_package())

        assert len(service.export_log) == 3
        # Each entry should have a unique exportId
        ids = [r.exportId for r in service.export_log]
        assert len(set(ids)) == 3


class TestLogExportResultDirect:
    """Verify the log_export_result method can be called directly."""

    def test_direct_log_persists_record(self):
        from axonllm_ledger.models import ExportRecord

        target = _FakeTarget(fail_count=0)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        record = ExportRecord(
            exportId="manual-001",
            exportPeriodStart=datetime(2024, 1, 1),
            exportPeriodEnd=datetime(2024, 2, 1),
            recordCount=42,
            status=ExportStatus.SUCCESS,
            attemptCount=1,
            exportedAt=datetime(2024, 1, 15),
            errorMessage=None,
        )
        service.log_export_result(record)

        assert len(service.export_log) == 1
        assert service.export_log[0].exportId == "manual-001"
        assert service.export_log[0].recordCount == 42


class TestExportLogPeriodFields:
    """Verify that logged records contain correct export period fields."""

    def test_logged_record_has_export_period(self):
        target = _FakeTarget(fail_count=0)
        notifier = _FakeNotifier()
        service = ExportService(target, notifier)

        service.execute_export(_make_package())

        logged = service.export_log[0]
        assert logged.exportPeriodStart == TIME_RANGE.start
        assert logged.exportPeriodEnd == TIME_RANGE.end
