"""Property-based tests for dimension aggregation correctness.

Feature: axonllm-ledger, Property 8: Dimension Aggregation Returns Correct Totals

Validates: Requirements 6.1, 6.2, 7.1, 7.2, 8.1, 8.2
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.aggregation import AggregationEngine, TimeRange
from axonllm_ledger.models import AccountHierarchy, UsageRecord


# --- Strategies ---

# Anchor datetimes within a small window so time filtering is meaningful
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


# --- Helpers ---


def _manual_aggregate(records, time_range, key_fn):
    """Manually compute expected aggregation totals.

    Filters records by [start, end) and groups by key_fn.
    Returns dict mapping dimension_value -> (cost, invocations, input_tokens, output_tokens).
    """
    totals = defaultdict(lambda: [Decimal("0"), 0, 0, 0])
    for r in records:
        if time_range.start <= r.usageStartDate < time_range.end:
            k = key_fn(r)
            totals[k][0] += r.cost
            totals[k][1] += r.invocationCount
            totals[k][2] += r.inputTokens
            totals[k][3] += r.outputTokens
    return totals


def _assert_results_match(results, expected):
    """Assert aggregation results match manually computed expected totals."""
    result_map = {r.dimension_value: r for r in results}

    assert set(result_map.keys()) == set(expected.keys()), (
        f"Dimension values mismatch: got {set(result_map.keys())}, "
        f"expected {set(expected.keys())}"
    )

    for dim_val, (exp_cost, exp_inv, exp_in, exp_out) in expected.items():
        r = result_map[dim_val]
        assert r.total_cost == exp_cost, (
            f"[{dim_val}] cost: {r.total_cost} != {exp_cost}"
        )
        assert r.total_invocations == exp_inv, (
            f"[{dim_val}] invocations: {r.total_invocations} != {exp_inv}"
        )
        assert r.total_input_tokens == exp_in, (
            f"[{dim_val}] input_tokens: {r.total_input_tokens} != {exp_in}"
        )
        assert r.total_output_tokens == exp_out, (
            f"[{dim_val}] output_tokens: {r.total_output_tokens} != {exp_out}"
        )


# --- Property Tests ---


class TestDimensionAggregationCorrectness:
    """Property 8: Dimension Aggregation Returns Correct Totals.

    For any set of UsageRecords, any aggregation dimension (user, account, or
    model), and any time range, the aggregated total cost, total invocation
    count, total input tokens, and total output tokens for each dimension value
    should equal the sum of those fields across all UsageRecords matching that
    dimension value within the time range.

    **Validates: Requirements 6.1, 6.2, 7.1, 7.2, 8.1, 8.2**
    """

    @settings(max_examples=100)
    @given(records=_usage_records, tr=time_range())
    def test_aggregate_by_user_returns_correct_totals(
        self, records: list[UsageRecord], tr: TimeRange
    ):
        """Aggregation by user returns correct totals for each user.

        Feature: axonllm-ledger, Property 8: Dimension Aggregation Returns Correct Totals
        """
        # **Validates: Requirements 6.1, 6.2**
        engine = AggregationEngine(records)
        results = engine.aggregate_by_user(tr)
        expected = _manual_aggregate(records, tr, lambda r: r.userId)
        _assert_results_match(results, expected)

    @settings(max_examples=100)
    @given(records=_usage_records, tr=time_range())
    def test_aggregate_by_account_returns_correct_totals(
        self, records: list[UsageRecord], tr: TimeRange
    ):
        """Aggregation by account returns correct totals for each account.

        Feature: axonllm-ledger, Property 8: Dimension Aggregation Returns Correct Totals
        """
        # **Validates: Requirements 7.1, 7.2**
        engine = AggregationEngine(records)
        results = engine.aggregate_by_account(tr)
        expected = _manual_aggregate(records, tr, lambda r: r.accountId)
        _assert_results_match(results, expected)

    @settings(max_examples=100)
    @given(records=_usage_records, tr=time_range())
    def test_aggregate_by_model_returns_correct_totals(
        self, records: list[UsageRecord], tr: TimeRange
    ):
        """Aggregation by model returns correct totals for each model.

        Feature: axonllm-ledger, Property 8: Dimension Aggregation Returns Correct Totals
        """
        # **Validates: Requirements 8.1, 8.2**
        engine = AggregationEngine(records)
        results = engine.aggregate_by_model(tr)
        expected = _manual_aggregate(records, tr, lambda r: r.modelId)
        _assert_results_match(results, expected)


class TestSubDimensionBreakdownConsistency:
    """Property 9: Sub-Dimension Breakdowns Sum to Dimension Total.

    For any specific dimension value (a user, an account, or a model) and any
    time range, the sum of costs in the sub-dimension breakdown (e.g., per-model
    breakdown for a user) should equal the total cost for that dimension value
    in the same time range.

    **Validates: Requirements 6.3, 7.3, 8.3**
    """

    @settings(max_examples=100)
    @given(records=_usage_records, tr=time_range(), uid=_user_ids)
    def test_user_model_breakdown_sums_to_total(
        self, records: list[UsageRecord], tr: TimeRange, uid: str
    ):
        """Sum of model_breakdown fields equals the user report totals.

        Feature: axonllm-ledger, Property 9: Sub-Dimension Breakdowns Sum to Dimension Total
        """
        # **Validates: Requirements 6.3**
        engine = AggregationEngine(records)
        report = engine.get_cost_report_for_user(uid, tr)

        breakdown_cost = sum(
            (mb.total_cost for mb in report.model_breakdown), Decimal("0")
        )
        breakdown_invocations = sum(mb.total_invocations for mb in report.model_breakdown)
        breakdown_input_tokens = sum(mb.total_input_tokens for mb in report.model_breakdown)
        breakdown_output_tokens = sum(mb.total_output_tokens for mb in report.model_breakdown)

        assert breakdown_cost == report.total_cost, (
            f"User {uid}: model_breakdown cost sum {breakdown_cost} != total {report.total_cost}"
        )
        assert breakdown_invocations == report.total_invocations, (
            f"User {uid}: model_breakdown invocations sum {breakdown_invocations} != total {report.total_invocations}"
        )
        assert breakdown_input_tokens == report.total_input_tokens, (
            f"User {uid}: model_breakdown input_tokens sum {breakdown_input_tokens} != total {report.total_input_tokens}"
        )
        assert breakdown_output_tokens == report.total_output_tokens, (
            f"User {uid}: model_breakdown output_tokens sum {breakdown_output_tokens} != total {report.total_output_tokens}"
        )

    @settings(max_examples=100)
    @given(records=_usage_records, tr=time_range(), aid=_account_ids)
    def test_account_breakdowns_sum_to_total(
        self, records: list[UsageRecord], tr: TimeRange, aid: str
    ):
        """Sum of model_breakdown and user_breakdown fields each equal the account report totals.

        Feature: axonllm-ledger, Property 9: Sub-Dimension Breakdowns Sum to Dimension Total
        """
        # **Validates: Requirements 7.3**
        engine = AggregationEngine(records)
        report = engine.get_cost_report_for_account(aid, tr)

        # model breakdown
        mb_cost = sum((mb.total_cost for mb in report.model_breakdown), Decimal("0"))
        mb_inv = sum(mb.total_invocations for mb in report.model_breakdown)
        mb_in = sum(mb.total_input_tokens for mb in report.model_breakdown)
        mb_out = sum(mb.total_output_tokens for mb in report.model_breakdown)

        assert mb_cost == report.total_cost, (
            f"Account {aid}: model_breakdown cost sum {mb_cost} != total {report.total_cost}"
        )
        assert mb_inv == report.total_invocations
        assert mb_in == report.total_input_tokens
        assert mb_out == report.total_output_tokens

        # user breakdown
        ub_cost = sum((ub.total_cost for ub in report.user_breakdown), Decimal("0"))
        ub_inv = sum(ub.total_invocations for ub in report.user_breakdown)
        ub_in = sum(ub.total_input_tokens for ub in report.user_breakdown)
        ub_out = sum(ub.total_output_tokens for ub in report.user_breakdown)

        assert ub_cost == report.total_cost, (
            f"Account {aid}: user_breakdown cost sum {ub_cost} != total {report.total_cost}"
        )
        assert ub_inv == report.total_invocations
        assert ub_in == report.total_input_tokens
        assert ub_out == report.total_output_tokens

    @settings(max_examples=100)
    @given(records=_usage_records, tr=time_range(), mid=_model_ids)
    def test_model_breakdowns_sum_to_total(
        self, records: list[UsageRecord], tr: TimeRange, mid: str
    ):
        """Sum of user_breakdown and account_breakdown fields each equal the model report totals.

        Feature: axonllm-ledger, Property 9: Sub-Dimension Breakdowns Sum to Dimension Total
        """
        # **Validates: Requirements 8.3**
        engine = AggregationEngine(records)
        report = engine.get_cost_report_for_model(mid, tr)

        # user breakdown
        ub_cost = sum((ub.total_cost for ub in report.user_breakdown), Decimal("0"))
        ub_inv = sum(ub.total_invocations for ub in report.user_breakdown)
        ub_in = sum(ub.total_input_tokens for ub in report.user_breakdown)
        ub_out = sum(ub.total_output_tokens for ub in report.user_breakdown)

        assert ub_cost == report.total_cost, (
            f"Model {mid}: user_breakdown cost sum {ub_cost} != total {report.total_cost}"
        )
        assert ub_inv == report.total_invocations
        assert ub_in == report.total_input_tokens
        assert ub_out == report.total_output_tokens

        # account breakdown
        ab_cost = sum((ab.total_cost for ab in report.account_breakdown), Decimal("0"))
        ab_inv = sum(ab.total_invocations for ab in report.account_breakdown)
        ab_in = sum(ab.total_input_tokens for ab in report.account_breakdown)
        ab_out = sum(ab.total_output_tokens for ab in report.account_breakdown)

        assert ab_cost == report.total_cost, (
            f"Model {mid}: account_breakdown cost sum {ab_cost} != total {report.total_cost}"
        )
        assert ab_inv == report.total_invocations
        assert ab_in == report.total_input_tokens
        assert ab_out == report.total_output_tokens


# --- OU Aggregation Strategies ---

_ou_names = st.sampled_from(["Engineering", "Finance", "Marketing"])


@st.composite
def account_hierarchy_mapping(draw):
    """Generate a random hierarchy mapping for the 3 account IDs.

    Each account is randomly assigned to an OU from a small set, or
    omitted from the hierarchy entirely (to test "Unknown OU" behavior).
    """
    hierarchy: dict[str, AccountHierarchy] = {}
    for account_id in ["111111111111", "222222222222", "333333333333"]:
        include = draw(st.booleans())
        if include:
            ou_name = draw(_ou_names)
            hierarchy[account_id] = AccountHierarchy(
                accountId=account_id,
                accountName=f"Account-{account_id[:4]}",
                organizationalUnitId=f"ou-{ou_name.lower()}",
                organizationalUnitName=ou_name,
                parentOUId="r-root",
                tags={},
            )
    return hierarchy


class TestOUAggregationConsistency:
    """Property 10: OU Aggregation Equals Sum of Constituent Account Costs.

    For any set of UsageRecords, an AccountHierarchy, and any time range,
    the total cost aggregated for an organizational unit should equal the
    sum of costs for all accounts belonging to that OU within the time range.

    **Validates: Requirements 4.3, 7.4**
    """

    # Feature: axonllm-ledger, Property 10: OU Aggregation Equals Sum of Constituent Account Costs

    @settings(max_examples=100)
    @given(
        records=_usage_records,
        tr=time_range(),
        hierarchy=account_hierarchy_mapping(),
    )
    def test_ou_totals_equal_sum_of_constituent_account_costs(
        self,
        records: list[UsageRecord],
        tr: TimeRange,
        hierarchy: dict[str, AccountHierarchy],
    ):
        """OU aggregation totals match the sum of per-account totals for accounts in that OU.

        Feature: axonllm-ledger, Property 10: OU Aggregation Equals Sum of Constituent Account Costs
        """
        # **Validates: Requirements 4.3, 7.4**

        engine = AggregationEngine(records, hierarchy=hierarchy)
        ou_results = engine.aggregate_by_ou(tr)
        account_results = engine.aggregate_by_account(tr)

        # Build a lookup: account_id -> AggregationResult
        account_map = {r.dimension_value: r for r in account_results}

        # Build expected OU totals from account results + hierarchy
        expected_ou: dict[str, list] = defaultdict(
            lambda: [Decimal("0"), 0, 0, 0]
        )
        for acct_result in account_results:
            acct_id = acct_result.dimension_value
            if acct_id in hierarchy:
                ou_name = hierarchy[acct_id].organizationalUnitName
            else:
                ou_name = "Unknown OU"
            expected_ou[ou_name][0] += acct_result.total_cost
            expected_ou[ou_name][1] += acct_result.total_invocations
            expected_ou[ou_name][2] += acct_result.total_input_tokens
            expected_ou[ou_name][3] += acct_result.total_output_tokens

        # Verify OU results match expected
        ou_map = {r.dimension_value: r for r in ou_results}

        assert set(ou_map.keys()) == set(expected_ou.keys()), (
            f"OU keys mismatch: got {set(ou_map.keys())}, "
            f"expected {set(expected_ou.keys())}"
        )

        for ou_name, (exp_cost, exp_inv, exp_in, exp_out) in expected_ou.items():
            r = ou_map[ou_name]
            assert r.total_cost == exp_cost, (
                f"[{ou_name}] cost: {r.total_cost} != {exp_cost}"
            )
            assert r.total_invocations == exp_inv, (
                f"[{ou_name}] invocations: {r.total_invocations} != {exp_inv}"
            )
            assert r.total_input_tokens == exp_in, (
                f"[{ou_name}] input_tokens: {r.total_input_tokens} != {exp_in}"
            )
            assert r.total_output_tokens == exp_out, (
                f"[{ou_name}] output_tokens: {r.total_output_tokens} != {exp_out}"
            )

    @settings(max_examples=100)
    @given(
        records=_usage_records,
        tr=time_range(),
    )
    def test_accounts_without_hierarchy_go_to_unknown_ou(
        self,
        records: list[UsageRecord],
        tr: TimeRange,
    ):
        """When no hierarchy is provided, all accounts aggregate under 'Unknown OU'.

        Feature: axonllm-ledger, Property 10: OU Aggregation Equals Sum of Constituent Account Costs
        """
        # **Validates: Requirements 4.3, 7.4**

        engine = AggregationEngine(records, hierarchy=None)
        ou_results = engine.aggregate_by_ou(tr)

        # With no hierarchy, there should be at most one OU: "Unknown OU"
        if ou_results:
            assert len(ou_results) == 1, (
                f"Expected at most 1 OU ('Unknown OU'), got {len(ou_results)}"
            )
            assert ou_results[0].dimension_value == "Unknown OU"

            # The Unknown OU total should equal the sum of all account totals
            account_results = engine.aggregate_by_account(tr)
            expected_cost = sum(
                (r.total_cost for r in account_results), Decimal("0")
            )
            expected_inv = sum(r.total_invocations for r in account_results)
            expected_in = sum(r.total_input_tokens for r in account_results)
            expected_out = sum(r.total_output_tokens for r in account_results)

            assert ou_results[0].total_cost == expected_cost
            assert ou_results[0].total_invocations == expected_inv
            assert ou_results[0].total_input_tokens == expected_in
            assert ou_results[0].total_output_tokens == expected_out
