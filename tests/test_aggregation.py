"""Unit tests for the Aggregation Engine (per-user cost aggregation)."""

from datetime import datetime, timezone
from decimal import Decimal

from axonllm_ledger.aggregation import (
    AggregationEngine,
    AggregationResult,
    DetailedUserCostReport,
    ModelBreakdown,
    TimeRange,
)
from axonllm_ledger.models import UsageRecord


def _make_record(
    user_id: str = "user-a",
    model_id: str = "anthropic.claude-v2",
    account_id: str = "111111111111",
    cost: Decimal = Decimal("1.00"),
    invocations: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 50,
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
        inputTokens=input_tokens,
        outputTokens=output_tokens,
        invocationCount=invocations,
        cost=cost,
        ingestedAt=datetime(2024, 3, 16),
        sourceExportId="export-001",
    )


TIME_RANGE = TimeRange(
    start=datetime(2024, 3, 1),
    end=datetime(2024, 4, 1),
)


class TestAggregateByUserEmpty:
    def test_empty_records(self):
        engine = AggregationEngine([])
        result = engine.aggregate_by_user(TIME_RANGE)
        assert result == []


class TestAggregateByUserSingle:
    def test_single_user_single_model(self):
        rec = _make_record(cost=Decimal("2.50"), invocations=3, input_tokens=500, output_tokens=200)
        engine = AggregationEngine([rec])
        results = engine.aggregate_by_user(TIME_RANGE)

        assert len(results) == 1
        r = results[0]
        assert r.dimension_value == "user-a"
        assert r.total_cost == Decimal("2.50")
        assert r.total_invocations == 3
        assert r.total_input_tokens == 500
        assert r.total_output_tokens == 200

    def test_single_user_multiple_models(self):
        r1 = _make_record(model_id="model-a", cost=Decimal("1.00"), invocations=2, input_tokens=100, output_tokens=50)
        r2 = _make_record(model_id="model-b", cost=Decimal("3.00"), invocations=5, input_tokens=300, output_tokens=150)
        engine = AggregationEngine([r1, r2])
        results = engine.aggregate_by_user(TIME_RANGE)

        assert len(results) == 1
        r = results[0]
        assert r.total_cost == Decimal("4.00")
        assert r.total_invocations == 7
        assert r.total_input_tokens == 400
        assert r.total_output_tokens == 200


class TestAggregateByUserMultiple:
    def test_multiple_users(self):
        r1 = _make_record(user_id="user-a", cost=Decimal("1.00"))
        r2 = _make_record(user_id="user-b", cost=Decimal("2.00"))
        r3 = _make_record(user_id="user-a", cost=Decimal("0.50"))
        engine = AggregationEngine([r1, r2, r3])
        results = engine.aggregate_by_user(TIME_RANGE)

        by_user = {r.dimension_value: r for r in results}
        assert len(by_user) == 2
        assert by_user["user-a"].total_cost == Decimal("1.50")
        assert by_user["user-b"].total_cost == Decimal("2.00")


class TestAggregateByUserTimeFiltering:
    def test_records_outside_range_excluded(self):
        inside = _make_record(cost=Decimal("5.00"), start=datetime(2024, 3, 15))
        before = _make_record(cost=Decimal("10.00"), start=datetime(2024, 2, 28))
        after = _make_record(cost=Decimal("10.00"), start=datetime(2024, 4, 1))
        engine = AggregationEngine([inside, before, after])
        results = engine.aggregate_by_user(TIME_RANGE)

        assert len(results) == 1
        assert results[0].total_cost == Decimal("5.00")

    def test_record_at_start_boundary_included(self):
        rec = _make_record(cost=Decimal("1.00"), start=datetime(2024, 3, 1, 0, 0, 0))
        engine = AggregationEngine([rec])
        results = engine.aggregate_by_user(TIME_RANGE)
        assert len(results) == 1

    def test_record_at_end_boundary_excluded(self):
        rec = _make_record(cost=Decimal("1.00"), start=datetime(2024, 4, 1, 0, 0, 0))
        engine = AggregationEngine([rec])
        results = engine.aggregate_by_user(TIME_RANGE)
        assert results == []


class TestGetCostReportForUserEmpty:
    def test_no_records_for_user(self):
        engine = AggregationEngine([])
        report = engine.get_cost_report_for_user("user-a", TIME_RANGE)

        assert report.user_id == "user-a"
        assert report.total_cost == Decimal("0")
        assert report.total_invocations == 0
        assert report.total_input_tokens == 0
        assert report.total_output_tokens == 0
        assert report.model_breakdown == []


class TestGetCostReportForUserSingle:
    def test_single_model(self):
        rec = _make_record(model_id="model-x", cost=Decimal("3.00"), invocations=4, input_tokens=800, output_tokens=400)
        engine = AggregationEngine([rec])
        report = engine.get_cost_report_for_user("user-a", TIME_RANGE)

        assert report.total_cost == Decimal("3.00")
        assert report.total_invocations == 4
        assert len(report.model_breakdown) == 1
        assert report.model_breakdown[0].model_id == "model-x"
        assert report.model_breakdown[0].total_cost == Decimal("3.00")


class TestGetCostReportForUserBreakdown:
    def test_multiple_models_breakdown(self):
        r1 = _make_record(model_id="model-a", cost=Decimal("1.00"), invocations=2, input_tokens=100, output_tokens=50)
        r2 = _make_record(model_id="model-b", cost=Decimal("4.00"), invocations=8, input_tokens=900, output_tokens=450)
        r3 = _make_record(model_id="model-a", cost=Decimal("2.00"), invocations=3, input_tokens=200, output_tokens=100)
        engine = AggregationEngine([r1, r2, r3])
        report = engine.get_cost_report_for_user("user-a", TIME_RANGE)

        assert report.total_cost == Decimal("7.00")
        assert report.total_invocations == 13
        assert report.total_input_tokens == 1200
        assert report.total_output_tokens == 600

        by_model = {m.model_id: m for m in report.model_breakdown}
        assert len(by_model) == 2
        assert by_model["model-a"].total_cost == Decimal("3.00")
        assert by_model["model-a"].total_invocations == 5
        assert by_model["model-a"].total_input_tokens == 300
        assert by_model["model-a"].total_output_tokens == 150
        assert by_model["model-b"].total_cost == Decimal("4.00")
        assert by_model["model-b"].total_invocations == 8

    def test_only_returns_data_for_requested_user(self):
        r1 = _make_record(user_id="user-a", cost=Decimal("1.00"))
        r2 = _make_record(user_id="user-b", cost=Decimal("99.00"))
        engine = AggregationEngine([r1, r2])
        report = engine.get_cost_report_for_user("user-a", TIME_RANGE)

        assert report.total_cost == Decimal("1.00")

    def test_time_range_filtering(self):
        inside = _make_record(cost=Decimal("5.00"), start=datetime(2024, 3, 10))
        outside = _make_record(cost=Decimal("99.00"), start=datetime(2024, 5, 1))
        engine = AggregationEngine([inside, outside])
        report = engine.get_cost_report_for_user("user-a", TIME_RANGE)

        assert report.total_cost == Decimal("5.00")


# ---------------------------------------------------------------------------
# Per-account aggregation tests (task 6.2)
# ---------------------------------------------------------------------------

from axonllm_ledger.aggregation import (
    DetailedAccountCostReport,
    UserBreakdown,
)


class TestAggregateByAccountEmpty:
    def test_empty_records(self):
        engine = AggregationEngine([])
        result = engine.aggregate_by_account(TIME_RANGE)
        assert result == []


class TestAggregateByAccountSingle:
    def test_single_account_single_model(self):
        rec = _make_record(
            account_id="111111111111",
            cost=Decimal("2.50"),
            invocations=3,
            input_tokens=500,
            output_tokens=200,
        )
        engine = AggregationEngine([rec])
        results = engine.aggregate_by_account(TIME_RANGE)

        assert len(results) == 1
        r = results[0]
        assert r.dimension_value == "111111111111"
        assert r.total_cost == Decimal("2.50")
        assert r.total_invocations == 3
        assert r.total_input_tokens == 500
        assert r.total_output_tokens == 200


class TestAggregateByAccountMultiple:
    def test_multiple_accounts(self):
        r1 = _make_record(account_id="111111111111", cost=Decimal("1.00"))
        r2 = _make_record(account_id="222222222222", cost=Decimal("2.00"))
        r3 = _make_record(account_id="111111111111", cost=Decimal("0.50"))
        engine = AggregationEngine([r1, r2, r3])
        results = engine.aggregate_by_account(TIME_RANGE)

        by_acct = {r.dimension_value: r for r in results}
        assert len(by_acct) == 2
        assert by_acct["111111111111"].total_cost == Decimal("1.50")
        assert by_acct["222222222222"].total_cost == Decimal("2.00")


class TestAggregateByAccountTimeFiltering:
    def test_records_outside_range_excluded(self):
        inside = _make_record(cost=Decimal("5.00"), start=datetime(2024, 3, 15))
        before = _make_record(cost=Decimal("10.00"), start=datetime(2024, 2, 28))
        after = _make_record(cost=Decimal("10.00"), start=datetime(2024, 4, 1))
        engine = AggregationEngine([inside, before, after])
        results = engine.aggregate_by_account(TIME_RANGE)

        assert len(results) == 1
        assert results[0].total_cost == Decimal("5.00")


class TestGetCostReportForAccountEmpty:
    def test_no_records_for_account(self):
        engine = AggregationEngine([])
        report = engine.get_cost_report_for_account("111111111111", TIME_RANGE)

        assert report.account_id == "111111111111"
        assert report.total_cost == Decimal("0")
        assert report.total_invocations == 0
        assert report.total_input_tokens == 0
        assert report.total_output_tokens == 0
        assert report.model_breakdown == []
        assert report.user_breakdown == []


class TestGetCostReportForAccountBreakdown:
    def test_per_model_and_per_user_breakdown(self):
        r1 = _make_record(
            user_id="user-a", model_id="model-a",
            cost=Decimal("1.00"), invocations=2, input_tokens=100, output_tokens=50,
        )
        r2 = _make_record(
            user_id="user-b", model_id="model-b",
            cost=Decimal("4.00"), invocations=8, input_tokens=900, output_tokens=450,
        )
        r3 = _make_record(
            user_id="user-a", model_id="model-a",
            cost=Decimal("2.00"), invocations=3, input_tokens=200, output_tokens=100,
        )
        engine = AggregationEngine([r1, r2, r3])
        report = engine.get_cost_report_for_account("111111111111", TIME_RANGE)

        assert report.total_cost == Decimal("7.00")
        assert report.total_invocations == 13
        assert report.total_input_tokens == 1200
        assert report.total_output_tokens == 600

        by_model = {m.model_id: m for m in report.model_breakdown}
        assert len(by_model) == 2
        assert by_model["model-a"].total_cost == Decimal("3.00")
        assert by_model["model-a"].total_invocations == 5
        assert by_model["model-b"].total_cost == Decimal("4.00")

        by_user = {u.user_id: u for u in report.user_breakdown}
        assert len(by_user) == 2
        assert by_user["user-a"].total_cost == Decimal("3.00")
        assert by_user["user-a"].total_invocations == 5
        assert by_user["user-a"].total_input_tokens == 300
        assert by_user["user-a"].total_output_tokens == 150
        assert by_user["user-b"].total_cost == Decimal("4.00")
        assert by_user["user-b"].total_invocations == 8

    def test_only_returns_data_for_requested_account(self):
        r1 = _make_record(account_id="111111111111", cost=Decimal("1.00"))
        r2 = _make_record(account_id="222222222222", cost=Decimal("99.00"))
        engine = AggregationEngine([r1, r2])
        report = engine.get_cost_report_for_account("111111111111", TIME_RANGE)

        assert report.total_cost == Decimal("1.00")

    def test_time_range_filtering(self):
        inside = _make_record(cost=Decimal("5.00"), start=datetime(2024, 3, 10))
        outside = _make_record(cost=Decimal("99.00"), start=datetime(2024, 5, 1))
        engine = AggregationEngine([inside, outside])
        report = engine.get_cost_report_for_account("111111111111", TIME_RANGE)

        assert report.total_cost == Decimal("5.00")


# ---------------------------------------------------------------------------
# Per-OU aggregation tests (task 6.3)
# ---------------------------------------------------------------------------

from axonllm_ledger.models import AccountHierarchy


def _make_hierarchy(
    account_id: str,
    ou_name: str,
    ou_id: str = "ou-001",
    parent_ou_id: str = "r-root",
) -> AccountHierarchy:
    return AccountHierarchy(
        accountId=account_id,
        accountName=f"Account {account_id}",
        organizationalUnitId=ou_id,
        organizationalUnitName=ou_name,
        parentOUId=parent_ou_id,
    )


class TestAggregateByOUEmpty:
    def test_empty_records(self):
        engine = AggregationEngine([], hierarchy={})
        result = engine.aggregate_by_ou(TIME_RANGE)
        assert result == []


class TestAggregateByOUAllMapped:
    def test_all_accounts_mapped_to_ous(self):
        hierarchy = {
            "111111111111": _make_hierarchy("111111111111", "Engineering", "ou-eng"),
            "222222222222": _make_hierarchy("222222222222", "Engineering", "ou-eng"),
        }
        r1 = _make_record(account_id="111111111111", cost=Decimal("1.00"), invocations=2, input_tokens=100, output_tokens=50)
        r2 = _make_record(account_id="222222222222", cost=Decimal("3.00"), invocations=5, input_tokens=300, output_tokens=150)
        engine = AggregationEngine([r1, r2], hierarchy=hierarchy)
        results = engine.aggregate_by_ou(TIME_RANGE)

        assert len(results) == 1
        r = results[0]
        assert r.dimension_value == "Engineering"
        assert r.total_cost == Decimal("4.00")
        assert r.total_invocations == 7
        assert r.total_input_tokens == 400
        assert r.total_output_tokens == 200


class TestAggregateByOUUnknown:
    def test_account_not_in_hierarchy(self):
        hierarchy = {
            "111111111111": _make_hierarchy("111111111111", "Engineering", "ou-eng"),
        }
        r1 = _make_record(account_id="111111111111", cost=Decimal("1.00"))
        r2 = _make_record(account_id="999999999999", cost=Decimal("2.00"))
        engine = AggregationEngine([r1, r2], hierarchy=hierarchy)
        results = engine.aggregate_by_ou(TIME_RANGE)

        by_ou = {r.dimension_value: r for r in results}
        assert len(by_ou) == 2
        assert by_ou["Engineering"].total_cost == Decimal("1.00")
        assert by_ou["Unknown OU"].total_cost == Decimal("2.00")


class TestAggregateByOUMultiple:
    def test_multiple_ous(self):
        hierarchy = {
            "111111111111": _make_hierarchy("111111111111", "Engineering", "ou-eng"),
            "222222222222": _make_hierarchy("222222222222", "Finance", "ou-fin"),
            "333333333333": _make_hierarchy("333333333333", "Engineering", "ou-eng"),
        }
        r1 = _make_record(account_id="111111111111", cost=Decimal("1.00"), invocations=1, input_tokens=100, output_tokens=50)
        r2 = _make_record(account_id="222222222222", cost=Decimal("2.00"), invocations=2, input_tokens=200, output_tokens=100)
        r3 = _make_record(account_id="333333333333", cost=Decimal("3.00"), invocations=3, input_tokens=300, output_tokens=150)
        engine = AggregationEngine([r1, r2, r3], hierarchy=hierarchy)
        results = engine.aggregate_by_ou(TIME_RANGE)

        by_ou = {r.dimension_value: r for r in results}
        assert len(by_ou) == 2
        assert by_ou["Engineering"].total_cost == Decimal("4.00")
        assert by_ou["Engineering"].total_invocations == 4
        assert by_ou["Engineering"].total_input_tokens == 400
        assert by_ou["Engineering"].total_output_tokens == 200
        assert by_ou["Finance"].total_cost == Decimal("2.00")
        assert by_ou["Finance"].total_invocations == 2


class TestAggregateByOUNoHierarchy:
    def test_no_hierarchy_all_unknown(self):
        r1 = _make_record(account_id="111111111111", cost=Decimal("1.00"))
        r2 = _make_record(account_id="222222222222", cost=Decimal("2.00"))
        engine = AggregationEngine([r1, r2])
        results = engine.aggregate_by_ou(TIME_RANGE)

        assert len(results) == 1
        assert results[0].dimension_value == "Unknown OU"
        assert results[0].total_cost == Decimal("3.00")


class TestAggregateByOUTimeFiltering:
    def test_records_outside_range_excluded(self):
        hierarchy = {
            "111111111111": _make_hierarchy("111111111111", "Engineering", "ou-eng"),
        }
        inside = _make_record(account_id="111111111111", cost=Decimal("5.00"), start=datetime(2024, 3, 15))
        before = _make_record(account_id="111111111111", cost=Decimal("10.00"), start=datetime(2024, 2, 28))
        after = _make_record(account_id="111111111111", cost=Decimal("10.00"), start=datetime(2024, 4, 1))
        engine = AggregationEngine([inside, before, after], hierarchy=hierarchy)
        results = engine.aggregate_by_ou(TIME_RANGE)

        assert len(results) == 1
        assert results[0].dimension_value == "Engineering"
        assert results[0].total_cost == Decimal("5.00")


# ---------------------------------------------------------------------------
# Per-model aggregation tests (task 6.4)
# ---------------------------------------------------------------------------

from axonllm_ledger.aggregation import (
    AccountBreakdown,
    DetailedModelCostReport,
)


class TestAggregateByModelEmpty:
    def test_empty_records(self):
        engine = AggregationEngine([])
        result = engine.aggregate_by_model(TIME_RANGE)
        assert result == []


class TestAggregateByModelSingle:
    def test_single_model(self):
        rec = _make_record(
            model_id="anthropic.claude-v2",
            cost=Decimal("2.50"),
            invocations=3,
            input_tokens=500,
            output_tokens=200,
        )
        engine = AggregationEngine([rec])
        results = engine.aggregate_by_model(TIME_RANGE)

        assert len(results) == 1
        r = results[0]
        assert r.dimension_value == "anthropic.claude-v2"
        assert r.total_cost == Decimal("2.50")
        assert r.total_invocations == 3
        assert r.total_input_tokens == 500
        assert r.total_output_tokens == 200


class TestAggregateByModelMultiple:
    def test_multiple_models(self):
        r1 = _make_record(model_id="model-a", cost=Decimal("1.00"), invocations=2, input_tokens=100, output_tokens=50)
        r2 = _make_record(model_id="model-b", cost=Decimal("2.00"), invocations=5, input_tokens=300, output_tokens=150)
        r3 = _make_record(model_id="model-a", cost=Decimal("0.50"), invocations=1, input_tokens=50, output_tokens=25)
        engine = AggregationEngine([r1, r2, r3])
        results = engine.aggregate_by_model(TIME_RANGE)

        by_model = {r.dimension_value: r for r in results}
        assert len(by_model) == 2
        assert by_model["model-a"].total_cost == Decimal("1.50")
        assert by_model["model-a"].total_invocations == 3
        assert by_model["model-a"].total_input_tokens == 150
        assert by_model["model-a"].total_output_tokens == 75
        assert by_model["model-b"].total_cost == Decimal("2.00")


class TestAggregateByModelTimeFiltering:
    def test_records_outside_range_excluded(self):
        inside = _make_record(cost=Decimal("5.00"), start=datetime(2024, 3, 15))
        before = _make_record(cost=Decimal("10.00"), start=datetime(2024, 2, 28))
        after = _make_record(cost=Decimal("10.00"), start=datetime(2024, 4, 1))
        engine = AggregationEngine([inside, before, after])
        results = engine.aggregate_by_model(TIME_RANGE)

        assert len(results) == 1
        assert results[0].total_cost == Decimal("5.00")


class TestGetCostReportForModelEmpty:
    def test_no_records_for_model(self):
        engine = AggregationEngine([])
        report = engine.get_cost_report_for_model("model-x", TIME_RANGE)

        assert report.model_id == "model-x"
        assert report.total_cost == Decimal("0")
        assert report.total_invocations == 0
        assert report.total_input_tokens == 0
        assert report.total_output_tokens == 0
        assert report.user_breakdown == []
        assert report.account_breakdown == []


class TestGetCostReportForModelBreakdown:
    def test_per_user_and_per_account_breakdown(self):
        r1 = _make_record(
            user_id="user-a", account_id="111111111111", model_id="model-x",
            cost=Decimal("1.00"), invocations=2, input_tokens=100, output_tokens=50,
        )
        r2 = _make_record(
            user_id="user-b", account_id="222222222222", model_id="model-x",
            cost=Decimal("4.00"), invocations=8, input_tokens=900, output_tokens=450,
        )
        r3 = _make_record(
            user_id="user-a", account_id="111111111111", model_id="model-x",
            cost=Decimal("2.00"), invocations=3, input_tokens=200, output_tokens=100,
        )
        engine = AggregationEngine([r1, r2, r3])
        report = engine.get_cost_report_for_model("model-x", TIME_RANGE)

        assert report.total_cost == Decimal("7.00")
        assert report.total_invocations == 13
        assert report.total_input_tokens == 1200
        assert report.total_output_tokens == 600

        by_user = {u.user_id: u for u in report.user_breakdown}
        assert len(by_user) == 2
        assert by_user["user-a"].total_cost == Decimal("3.00")
        assert by_user["user-a"].total_invocations == 5
        assert by_user["user-a"].total_input_tokens == 300
        assert by_user["user-a"].total_output_tokens == 150
        assert by_user["user-b"].total_cost == Decimal("4.00")
        assert by_user["user-b"].total_invocations == 8

        by_acct = {a.account_id: a for a in report.account_breakdown}
        assert len(by_acct) == 2
        assert by_acct["111111111111"].total_cost == Decimal("3.00")
        assert by_acct["111111111111"].total_invocations == 5
        assert by_acct["111111111111"].total_input_tokens == 300
        assert by_acct["111111111111"].total_output_tokens == 150
        assert by_acct["222222222222"].total_cost == Decimal("4.00")

    def test_only_returns_data_for_requested_model(self):
        r1 = _make_record(model_id="model-x", cost=Decimal("1.00"))
        r2 = _make_record(model_id="model-y", cost=Decimal("99.00"))
        engine = AggregationEngine([r1, r2])
        report = engine.get_cost_report_for_model("model-x", TIME_RANGE)

        assert report.total_cost == Decimal("1.00")

    def test_time_range_filtering(self):
        inside = _make_record(model_id="model-x", cost=Decimal("5.00"), start=datetime(2024, 3, 10))
        outside = _make_record(model_id="model-x", cost=Decimal("99.00"), start=datetime(2024, 5, 1))
        engine = AggregationEngine([inside, outside])
        report = engine.get_cost_report_for_model("model-x", TIME_RANGE)

        assert report.total_cost == Decimal("5.00")

# ---------------------------------------------------------------------------
# Access Report Query Tests (Task 6.8 — Requirements 9.2, 9.3)
# ---------------------------------------------------------------------------

from axonllm_ledger.models import AccessRecord


def _make_access(
    user_id: str = "user-a",
    model_id: str = "anthropic.claude-v2",
    account_id: str = "111111111111",
    timestamp: datetime = datetime(2024, 3, 15, 10, 0, 0),
) -> AccessRecord:
    return AccessRecord(
        accessId=AccessRecord.generate_id(),
        userId=user_id,
        modelId=model_id,
        accountId=account_id,
        timestamp=timestamp,
        sourceRecordId="rec-1",
    )


class TestGetAccessReportForUserEmpty:
    def test_no_access_records(self):
        engine = AggregationEngine([], access_records=[])
        result = engine.get_access_report_for_user("user-a", TIME_RANGE)
        assert result == []

    def test_no_records_for_requested_user(self):
        rec = _make_access(user_id="user-b")
        engine = AggregationEngine([], access_records=[rec])
        result = engine.get_access_report_for_user("user-a", TIME_RANGE)
        assert result == []


class TestGetAccessReportForUserSingleMultipleModels:
    def test_single_user_multiple_models(self):
        recs = [
            _make_access(user_id="user-a", model_id="model-1"),
            _make_access(user_id="user-a", model_id="model-2"),
            _make_access(user_id="user-a", model_id="model-3"),
        ]
        engine = AggregationEngine([], access_records=recs)
        result = engine.get_access_report_for_user("user-a", TIME_RANGE)
        assert result == ["model-1", "model-2", "model-3"]


class TestGetAccessReportForUserDuplicates:
    def test_duplicate_access_returns_distinct(self):
        recs = [
            _make_access(user_id="user-a", model_id="model-1"),
            _make_access(user_id="user-a", model_id="model-1"),
            _make_access(user_id="user-a", model_id="model-2"),
        ]
        engine = AggregationEngine([], access_records=recs)
        result = engine.get_access_report_for_user("user-a", TIME_RANGE)
        assert result == ["model-1", "model-2"]


class TestGetAccessReportForUserTimeFiltering:
    def test_records_outside_range_excluded(self):
        inside = _make_access(user_id="user-a", model_id="model-1", timestamp=datetime(2024, 3, 15))
        outside = _make_access(user_id="user-a", model_id="model-2", timestamp=datetime(2024, 5, 1))
        engine = AggregationEngine([], access_records=[inside, outside])
        result = engine.get_access_report_for_user("user-a", TIME_RANGE)
        assert result == ["model-1"]

    def test_record_at_start_boundary_included(self):
        rec = _make_access(user_id="user-a", model_id="model-1", timestamp=datetime(2024, 3, 1))
        engine = AggregationEngine([], access_records=[rec])
        result = engine.get_access_report_for_user("user-a", TIME_RANGE)
        assert result == ["model-1"]

    def test_record_at_end_boundary_excluded(self):
        rec = _make_access(user_id="user-a", model_id="model-1", timestamp=datetime(2024, 4, 1))
        engine = AggregationEngine([], access_records=[rec])
        result = engine.get_access_report_for_user("user-a", TIME_RANGE)
        assert result == []


class TestGetAccessReportForModelEmpty:
    def test_no_access_records(self):
        engine = AggregationEngine([], access_records=[])
        result = engine.get_access_report_for_model("model-1", TIME_RANGE)
        assert result == []

    def test_no_records_for_requested_model(self):
        rec = _make_access(model_id="model-other")
        engine = AggregationEngine([], access_records=[rec])
        result = engine.get_access_report_for_model("model-1", TIME_RANGE)
        assert result == []


class TestGetAccessReportForModelMultipleUsers:
    def test_multiple_users_same_model(self):
        recs = [
            _make_access(user_id="user-a", model_id="model-1"),
            _make_access(user_id="user-b", model_id="model-1"),
            _make_access(user_id="user-c", model_id="model-1"),
        ]
        engine = AggregationEngine([], access_records=recs)
        result = engine.get_access_report_for_model("model-1", TIME_RANGE)
        assert result == ["user-a", "user-b", "user-c"]


class TestGetAccessReportForModelDuplicates:
    def test_duplicate_access_returns_distinct(self):
        recs = [
            _make_access(user_id="user-a", model_id="model-1"),
            _make_access(user_id="user-a", model_id="model-1"),
            _make_access(user_id="user-b", model_id="model-1"),
        ]
        engine = AggregationEngine([], access_records=recs)
        result = engine.get_access_report_for_model("model-1", TIME_RANGE)
        assert result == ["user-a", "user-b"]


class TestGetAccessReportForModelTimeFiltering:
    def test_records_outside_range_excluded(self):
        inside = _make_access(user_id="user-a", model_id="model-1", timestamp=datetime(2024, 3, 15))
        outside = _make_access(user_id="user-b", model_id="model-1", timestamp=datetime(2024, 5, 1))
        engine = AggregationEngine([], access_records=[inside, outside])
        result = engine.get_access_report_for_model("model-1", TIME_RANGE)
        assert result == ["user-a"]

    def test_record_at_start_boundary_included(self):
        rec = _make_access(user_id="user-a", model_id="model-1", timestamp=datetime(2024, 3, 1))
        engine = AggregationEngine([], access_records=[rec])
        result = engine.get_access_report_for_model("model-1", TIME_RANGE)
        assert result == ["user-a"]

    def test_record_at_end_boundary_excluded(self):
        rec = _make_access(user_id="user-a", model_id="model-1", timestamp=datetime(2024, 4, 1))
        engine = AggregationEngine([], access_records=[rec])
        result = engine.get_access_report_for_model("model-1", TIME_RANGE)
        assert result == []


# ---------------------------------------------------------------------------
# Batch Aggregation Scheduling Tests (Task 6.10 — Requirements 6.1, 7.1, 8.1)
# ---------------------------------------------------------------------------

from axonllm_ledger.aggregation import BatchAggregationScheduler
from axonllm_ledger.models import CostAggregation, DimensionType


class TestBatchAggregationEmpty:
    def test_empty_records_produce_no_aggregations(self):
        engine = AggregationEngine([], hierarchy={})
        scheduler = BatchAggregationScheduler(engine)
        results = scheduler.run_batch([TIME_RANGE])
        assert results == []

    def test_empty_time_ranges_produce_no_aggregations(self):
        rec = _make_record(cost=Decimal("1.00"))
        engine = AggregationEngine([rec], hierarchy={})
        scheduler = BatchAggregationScheduler(engine)
        results = scheduler.run_batch([])
        assert results == []


class TestBatchAggregationSingleTimeRange:
    def test_single_range_produces_all_four_dimensions(self):
        hierarchy = {
            "111111111111": _make_hierarchy("111111111111", "Engineering", "ou-eng"),
        }
        rec = _make_record(
            user_id="user-a",
            model_id="model-x",
            account_id="111111111111",
            cost=Decimal("5.00"),
            invocations=10,
            input_tokens=1000,
            output_tokens=500,
        )
        engine = AggregationEngine([rec], hierarchy=hierarchy)
        scheduler = BatchAggregationScheduler(engine)
        results = scheduler.run_batch([TIME_RANGE])

        dims = {r.dimension for r in results}
        assert dims == {
            DimensionType.USER,
            DimensionType.ACCOUNT,
            DimensionType.OU,
            DimensionType.MODEL,
        }
        assert len(results) == 4

        for r in results:
            assert isinstance(r, CostAggregation)
            assert r.totalCost == Decimal("5.00")
            assert r.totalInvocations == 10
            assert r.totalInputTokens == 1000
            assert r.totalOutputTokens == 500
            assert r.timeRangeStart == TIME_RANGE.start
            assert r.timeRangeEnd == TIME_RANGE.end
            assert r.aggregationId  # non-empty UUID
            assert r.computedAt is not None
            assert r.computedAt.tzinfo is timezone.utc

    def test_dimension_values_are_correct(self):
        hierarchy = {
            "111111111111": _make_hierarchy("111111111111", "Engineering", "ou-eng"),
        }
        rec = _make_record(
            user_id="user-a",
            model_id="model-x",
            account_id="111111111111",
        )
        engine = AggregationEngine([rec], hierarchy=hierarchy)
        scheduler = BatchAggregationScheduler(engine)
        results = scheduler.run_batch([TIME_RANGE])

        by_dim = {r.dimension: r for r in results}
        assert by_dim[DimensionType.USER].dimensionValue == "user-a"
        assert by_dim[DimensionType.ACCOUNT].dimensionValue == "111111111111"
        assert by_dim[DimensionType.OU].dimensionValue == "Engineering"
        assert by_dim[DimensionType.MODEL].dimensionValue == "model-x"


class TestBatchAggregationMultipleTimeRanges:
    def test_multiple_ranges(self):
        range1 = TimeRange(start=datetime(2024, 3, 1), end=datetime(2024, 4, 1))
        range2 = TimeRange(start=datetime(2024, 4, 1), end=datetime(2024, 5, 1))
        rec1 = _make_record(cost=Decimal("1.00"), start=datetime(2024, 3, 15))
        rec2 = _make_record(cost=Decimal("2.00"), start=datetime(2024, 4, 15))
        engine = AggregationEngine([rec1, rec2], hierarchy={})
        scheduler = BatchAggregationScheduler(engine)
        results = scheduler.run_batch([range1, range2])

        # Each range should produce aggregations for USER, ACCOUNT, OU, MODEL
        range1_results = [r for r in results if r.timeRangeStart == range1.start]
        range2_results = [r for r in results if r.timeRangeStart == range2.start]

        assert len(range1_results) == 4  # 1 value per dimension
        assert len(range2_results) == 4

        for r in range1_results:
            assert r.totalCost == Decimal("1.00")
        for r in range2_results:
            assert r.totalCost == Decimal("2.00")


class TestBatchAggregationStoredRetrieval:
    def test_stored_aggregations_are_retrievable(self):
        rec = _make_record(cost=Decimal("3.00"))
        engine = AggregationEngine([rec], hierarchy={})
        scheduler = BatchAggregationScheduler(engine)

        assert scheduler.get_stored_aggregations() == []

        results = scheduler.run_batch([TIME_RANGE])
        stored = scheduler.get_stored_aggregations()

        assert len(stored) == len(results)
        for r in results:
            assert r in stored

    def test_multiple_batches_accumulate(self):
        rec = _make_record(cost=Decimal("1.00"))
        engine = AggregationEngine([rec], hierarchy={})
        scheduler = BatchAggregationScheduler(engine)

        batch1 = scheduler.run_batch([TIME_RANGE])
        batch2 = scheduler.run_batch([TIME_RANGE])
        stored = scheduler.get_stored_aggregations()

        assert len(stored) == len(batch1) + len(batch2)


class TestBatchAggregationRecordFields:
    def test_cost_aggregation_has_correct_dimension_types(self):
        hierarchy = {
            "111111111111": _make_hierarchy("111111111111", "Engineering", "ou-eng"),
        }
        rec = _make_record(account_id="111111111111")
        engine = AggregationEngine([rec], hierarchy=hierarchy)
        scheduler = BatchAggregationScheduler(engine)
        results = scheduler.run_batch([TIME_RANGE])

        for r in results:
            assert isinstance(r.dimension, DimensionType)
            assert r.dimension in (
                DimensionType.USER,
                DimensionType.ACCOUNT,
                DimensionType.OU,
                DimensionType.MODEL,
            )

    def test_aggregation_ids_are_unique(self):
        rec = _make_record()
        engine = AggregationEngine([rec], hierarchy={})
        scheduler = BatchAggregationScheduler(engine)
        results = scheduler.run_batch([TIME_RANGE])

        ids = [r.aggregationId for r in results]
        assert len(ids) == len(set(ids))
