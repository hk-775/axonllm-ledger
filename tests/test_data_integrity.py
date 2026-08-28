"""Unit tests for the Data Integrity Service (cross-dimension consistency)."""

from datetime import datetime
from decimal import Decimal

from axonllm_ledger.aggregation import AggregationEngine, TimeRange
from axonllm_ledger.data_integrity import (
    ConsistencyResult,
    DataIntegrityService,
    ReconciliationResult,
)
from axonllm_ledger.models import UsageRecord


TIME_RANGE = TimeRange(
    start=datetime(2024, 3, 1),
    end=datetime(2024, 4, 1),
)


def _make_record(
    user_id: str = "user-a",
    account_id: str = "111111111111",
    model_id: str = "anthropic.claude-v2",
    cost: Decimal = Decimal("1.00"),
    start: datetime = datetime(2024, 3, 15, 10, 0, 0),
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


class FakeNotifier:
    """Captures alert calls for assertion."""

    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    def send_alert(self, subject: str, message: str) -> None:
        self.alerts.append((subject, message))


class TestConsistencyEmptyRecords:
    def test_empty_records_are_consistent(self):
        engine = AggregationEngine([])
        notifier = FakeNotifier()
        service = DataIntegrityService(engine, notifier)

        result = service.validate_cross_dimension_consistency(TIME_RANGE)

        assert result.is_consistent is True
        assert result.user_total == Decimal("0")
        assert result.account_total == Decimal("0")
        assert result.discrepancy == Decimal("0")
        assert notifier.alerts == []


class TestConsistencySingleRecord:
    def test_single_record_is_consistent(self):
        rec = _make_record(cost=Decimal("5.00"))
        engine = AggregationEngine([rec])
        notifier = FakeNotifier()
        service = DataIntegrityService(engine, notifier)

        result = service.validate_cross_dimension_consistency(TIME_RANGE)

        assert result.is_consistent is True
        assert result.user_total == Decimal("5.00")
        assert result.account_total == Decimal("5.00")
        assert result.discrepancy == Decimal("0")
        assert notifier.alerts == []


class TestConsistencyMultipleUsersAndAccounts:
    def test_multiple_users_multiple_accounts_consistent(self):
        records = [
            _make_record(user_id="user-a", account_id="111111111111", cost=Decimal("1.00")),
            _make_record(user_id="user-b", account_id="222222222222", cost=Decimal("2.00")),
            _make_record(user_id="user-a", account_id="222222222222", cost=Decimal("3.00")),
        ]
        engine = AggregationEngine(records)
        notifier = FakeNotifier()
        service = DataIntegrityService(engine, notifier)

        result = service.validate_cross_dimension_consistency(TIME_RANGE)

        assert result.is_consistent is True
        assert result.user_total == Decimal("6.00")
        assert result.account_total == Decimal("6.00")
        assert result.discrepancy == Decimal("0")
        assert notifier.alerts == []


class TestConsistencyTimeRangeFiltering:
    def test_records_outside_range_excluded(self):
        inside = _make_record(cost=Decimal("5.00"), start=datetime(2024, 3, 15))
        outside = _make_record(cost=Decimal("99.00"), start=datetime(2024, 5, 1))
        engine = AggregationEngine([inside, outside])
        notifier = FakeNotifier()
        service = DataIntegrityService(engine, notifier)

        result = service.validate_cross_dimension_consistency(TIME_RANGE)

        assert result.is_consistent is True
        assert result.user_total == Decimal("5.00")
        assert result.account_total == Decimal("5.00")


class TestConsistencyResultFields:
    def test_result_contains_time_range(self):
        engine = AggregationEngine([])
        notifier = FakeNotifier()
        service = DataIntegrityService(engine, notifier)

        result = service.validate_cross_dimension_consistency(TIME_RANGE)

        assert result.time_range is TIME_RANGE


# ---------------------------------------------------------------------------
# Data gap detection tests
# ---------------------------------------------------------------------------

from datetime import timedelta

from axonllm_ledger.data_integrity import DataGap


def _make_service(records=None):
    """Build a DataIntegrityService with an optional record list."""
    engine = AggregationEngine(records or [])
    notifier = FakeNotifier()
    return DataIntegrityService(engine, notifier), notifier


class TestDataGapDetectionNoGaps:
    def test_consecutive_timestamps_within_interval(self):
        service, notifier = _make_service()
        timestamps = [
            datetime(2024, 3, 1, 0, 0),
            datetime(2024, 3, 1, 1, 0),
            datetime(2024, 3, 1, 2, 0),
            datetime(2024, 3, 1, 3, 0),
        ]
        gaps = service.detect_data_gaps("CUR", timestamps, timedelta(hours=1))

        assert gaps == []
        assert notifier.alerts == []


class TestDataGapDetectionOneGap:
    def test_single_gap_detected(self):
        service, notifier = _make_service()
        timestamps = [
            datetime(2024, 3, 1, 0, 0),
            datetime(2024, 3, 1, 1, 0),
            # gap: 3 hours instead of 1
            datetime(2024, 3, 1, 4, 0),
            datetime(2024, 3, 1, 5, 0),
        ]
        gaps = service.detect_data_gaps("CUR", timestamps, timedelta(hours=1))

        assert len(gaps) == 1
        assert gaps[0] == DataGap(
            source="CUR",
            gap_start=datetime(2024, 3, 1, 1, 0),
            gap_end=datetime(2024, 3, 1, 4, 0),
        )
        assert len(notifier.alerts) == 1
        assert "CUR" in notifier.alerts[0][1]


class TestDataGapDetectionMultipleGaps:
    def test_multiple_gaps_detected(self):
        service, notifier = _make_service()
        timestamps = [
            datetime(2024, 3, 1, 0, 0),
            datetime(2024, 3, 1, 3, 0),  # gap 1
            datetime(2024, 3, 1, 4, 0),
            datetime(2024, 3, 1, 8, 0),  # gap 2
        ]
        gaps = service.detect_data_gaps("Budgets", timestamps, timedelta(hours=1))

        assert len(gaps) == 2
        assert gaps[0].gap_start == datetime(2024, 3, 1, 0, 0)
        assert gaps[0].gap_end == datetime(2024, 3, 1, 3, 0)
        assert gaps[1].gap_start == datetime(2024, 3, 1, 4, 0)
        assert gaps[1].gap_end == datetime(2024, 3, 1, 8, 0)
        assert all(g.source == "Budgets" for g in gaps)
        assert len(notifier.alerts) == 2


class TestDataGapDetectionEmptyTimestamps:
    def test_empty_list_returns_no_gaps(self):
        service, notifier = _make_service()
        gaps = service.detect_data_gaps("CUR", [], timedelta(hours=1))

        assert gaps == []
        assert notifier.alerts == []


class TestDataGapDetectionSingleTimestamp:
    def test_single_timestamp_returns_no_gaps(self):
        service, notifier = _make_service()
        gaps = service.detect_data_gaps(
            "CUR", [datetime(2024, 3, 1, 0, 0)], timedelta(hours=1)
        )

        assert gaps == []
        assert notifier.alerts == []


# ---------------------------------------------------------------------------
# CUR vs Budget reconciliation tests
# ---------------------------------------------------------------------------


class TestReconciliationExactMatch:
    def test_exact_match_zero_discrepancy(self):
        service, notifier = _make_service()
        result = service.reconcile_cur_vs_budgets(
            cur_total=Decimal("100.00"),
            budget_actual_spend=Decimal("100.00"),
            time_range=TIME_RANGE,
        )

        assert result.discrepancy_pct == Decimal("0")
        assert result.is_within_threshold is True
        assert result.cur_total == Decimal("100.00")
        assert result.budget_actual_spend == Decimal("100.00")
        assert notifier.alerts == []


class TestReconciliationWithinThreshold:
    def test_within_one_percent(self):
        service, notifier = _make_service()
        # 0.5% discrepancy: 100 vs 100.50
        result = service.reconcile_cur_vs_budgets(
            cur_total=Decimal("100.00"),
            budget_actual_spend=Decimal("100.50"),
            time_range=TIME_RANGE,
        )

        assert result.is_within_threshold is True
        assert notifier.alerts == []

    def test_exactly_one_percent(self):
        service, notifier = _make_service()
        # Exactly 1%: 100 vs 101
        result = service.reconcile_cur_vs_budgets(
            cur_total=Decimal("100.00"),
            budget_actual_spend=Decimal("101.00"),
            time_range=TIME_RANGE,
        )

        # 1/101 * 100 ≈ 0.99%, within threshold
        assert result.is_within_threshold is True
        assert notifier.alerts == []


class TestReconciliationExceedsThreshold:
    def test_exceeds_one_percent_sends_alert(self):
        service, notifier = _make_service()
        # ~10% discrepancy
        result = service.reconcile_cur_vs_budgets(
            cur_total=Decimal("110.00"),
            budget_actual_spend=Decimal("100.00"),
            time_range=TIME_RANGE,
        )

        assert result.is_within_threshold is False
        assert result.discrepancy_pct == Decimal("10")
        assert len(notifier.alerts) == 1
        assert "reconciliation" in notifier.alerts[0][0].lower()


class TestReconciliationBothZero:
    def test_both_zero_within_threshold(self):
        service, notifier = _make_service()
        result = service.reconcile_cur_vs_budgets(
            cur_total=Decimal("0"),
            budget_actual_spend=Decimal("0"),
            time_range=TIME_RANGE,
        )

        assert result.discrepancy_pct == Decimal("0")
        assert result.is_within_threshold is True
        assert notifier.alerts == []


class TestReconciliationBudgetZeroCURNonZero:
    def test_budget_zero_cur_nonzero_is_discrepancy(self):
        service, notifier = _make_service()
        result = service.reconcile_cur_vs_budgets(
            cur_total=Decimal("50.00"),
            budget_actual_spend=Decimal("0"),
            time_range=TIME_RANGE,
        )

        assert result.is_within_threshold is False
        assert len(notifier.alerts) == 1
