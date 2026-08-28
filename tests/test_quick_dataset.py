"""Tests for the Amazon Quick dashboard data contract."""

from datetime import datetime
from decimal import Decimal

from axonllm_ledger.aggregation import AggregationResult, TimeRange
from axonllm_ledger.export import LedgerExportPackage
from axonllm_ledger.models import OptimizationRecommendation, ProcessedBudget
from axonllm_ledger.quick_dataset import build_quick_dataset_tables


def _aggregation(value: str, cost: str) -> AggregationResult:
    return AggregationResult(
        dimension_value=value,
        total_cost=Decimal(cost),
        total_invocations=4,
        total_input_tokens=1200,
        total_output_tokens=600,
    )


def _package() -> LedgerExportPackage:
    return LedgerExportPackage(
        cost_by_user=[_aggregation("alice", "1.25")],
        cost_by_account=[_aggregation("111111111111", "1.25")],
        cost_by_ou=[_aggregation("Engineering", "1.25")],
        cost_by_model=[_aggregation("anthropic.claude-3-sonnet", "1.25")],
        model_access_per_user={
            "bob": ["amazon.titan-text-express"],
            "alice": ["meta.llama3-70b-instruct", "anthropic.claude-3-sonnet"],
        },
        budget_comparisons=[
            ProcessedBudget(
                budgetId="budget-1",
                budgetName="Bedrock Monthly",
                accountId="111111111111",
                budgetLimit=Decimal("100"),
                forecastedSpend=Decimal("90"),
                actualSpend=Decimal("80"),
                periodStart=datetime(2026, 8, 1),
                periodEnd=datetime(2026, 9, 1),
                isExceeded=False,
                ingestedAt=datetime(2026, 8, 28),
            )
        ],
        optimization_recommendations=[
            OptimizationRecommendation(
                recommendationId="recommendation-1",
                accountId="111111111111",
                modelId="anthropic.claude-3-sonnet",
                recommendationType="rightsizing",
                estimatedSavings=Decimal("12.50"),
                description="Use a lower-cost model for simple requests",
                ingestedAt=datetime(2026, 8, 28),
            )
        ],
        export_period=TimeRange(
            start=datetime(2026, 8, 1),
            end=datetime(2026, 9, 1),
        ),
    )


def test_builds_all_dashboard_tables():
    tables = build_quick_dataset_tables(_package())

    assert len(tables.cost_aggregations) == 4
    assert len(tables.model_access) == 3
    assert len(tables.budgets) == 1
    assert len(tables.optimization_recommendations) == 1


def test_cost_rows_include_dimension_and_exact_decimal_text():
    tables = build_quick_dataset_tables(_package())

    model_row = next(
        row
        for row in tables.cost_aggregations
        if row["dimension_type"] == "MODEL"
    )
    assert model_row["dimension_value"] == "anthropic.claude-3-sonnet"
    assert model_row["total_cost_usd"] == "1.25"
    assert model_row["period_start"] == "2026-08-01T00:00:00"


def test_model_access_rows_are_deterministic():
    tables = build_quick_dataset_tables(_package())

    assert tables.model_access == (
        {
            "period_start": "2026-08-01T00:00:00",
            "period_end": "2026-09-01T00:00:00",
            "user_id": "alice",
            "model_id": "anthropic.claude-3-sonnet",
        },
        {
            "period_start": "2026-08-01T00:00:00",
            "period_end": "2026-09-01T00:00:00",
            "user_id": "alice",
            "model_id": "meta.llama3-70b-instruct",
        },
        {
            "period_start": "2026-08-01T00:00:00",
            "period_end": "2026-09-01T00:00:00",
            "user_id": "bob",
            "model_id": "amazon.titan-text-express",
        },
    )


def test_json_output_preserves_financial_values_as_decimal_text():
    payload = build_quick_dataset_tables(_package()).to_json()

    assert '"budget_limit_usd": "100"' in payload
    assert '"estimated_savings_usd": "12.50"' in payload
