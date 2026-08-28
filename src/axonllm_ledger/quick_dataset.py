"""Stable row-oriented data contract for Amazon Quick dashboards."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from axonllm_ledger.export import LedgerExportPackage


@dataclass(frozen=True)
class QuickDatasetTables:
    """Dashboard-ready tables derived from a Ledger export package."""

    cost_aggregations: tuple[dict[str, Any], ...]
    model_access: tuple[dict[str, Any], ...]
    budgets: tuple[dict[str, Any], ...]
    optimization_recommendations: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        """Return mutable, JSON-serializable table collections."""
        return {
            "cost_aggregations": [dict(row) for row in self.cost_aggregations],
            "model_access": [dict(row) for row in self.model_access],
            "budgets": [dict(row) for row in self.budgets],
            "optimization_recommendations": [
                dict(row) for row in self.optimization_recommendations
            ],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize all dashboard tables to deterministic JSON."""
        return json.dumps(self.as_dict(), indent=indent, sort_keys=True)


def build_quick_dataset_tables(package: LedgerExportPackage) -> QuickDatasetTables:
    """Convert a Ledger export package into the Amazon Quick table contract."""
    period_start = (
        package.export_period.start.isoformat() if package.export_period else None
    )
    period_end = package.export_period.end.isoformat() if package.export_period else None

    cost_rows: list[dict[str, Any]] = []
    dimensions = (
        ("USER", package.cost_by_user),
        ("ACCOUNT", package.cost_by_account),
        ("ORGANIZATIONAL_UNIT", package.cost_by_ou),
        ("MODEL", package.cost_by_model),
    )
    for dimension_type, aggregations in dimensions:
        for aggregation in aggregations:
            cost_rows.append(
                {
                    "period_start": period_start,
                    "period_end": period_end,
                    "dimension_type": dimension_type,
                    "dimension_value": aggregation.dimension_value,
                    "total_cost_usd": str(aggregation.total_cost),
                    "total_invocations": aggregation.total_invocations,
                    "total_input_tokens": aggregation.total_input_tokens,
                    "total_output_tokens": aggregation.total_output_tokens,
                }
            )

    access_rows = [
        {
            "period_start": period_start,
            "period_end": period_end,
            "user_id": user_id,
            "model_id": model_id,
        }
        for user_id in sorted(package.model_access_per_user)
        for model_id in sorted(package.model_access_per_user[user_id])
    ]

    budget_rows = [
        {
            "budget_id": budget.budgetId,
            "budget_name": budget.budgetName,
            "account_id": budget.accountId,
            "budget_limit_usd": str(budget.budgetLimit),
            "forecasted_spend_usd": str(budget.forecastedSpend),
            "actual_spend_usd": str(budget.actualSpend),
            "period_start": budget.periodStart.isoformat(),
            "period_end": budget.periodEnd.isoformat(),
            "is_exceeded": budget.isExceeded,
            "ingested_at": budget.ingestedAt.isoformat(),
        }
        for budget in package.budget_comparisons
    ]

    recommendation_rows = [
        {
            "recommendation_id": recommendation.recommendationId,
            "account_id": recommendation.accountId,
            "model_id": recommendation.modelId,
            "recommendation_type": recommendation.recommendationType,
            "estimated_savings_usd": str(recommendation.estimatedSavings),
            "description": recommendation.description,
            "ingested_at": recommendation.ingestedAt.isoformat(),
        }
        for recommendation in package.optimization_recommendations
    ]

    return QuickDatasetTables(
        cost_aggregations=tuple(cost_rows),
        model_access=tuple(access_rows),
        budgets=tuple(budget_rows),
        optimization_recommendations=tuple(recommendation_rows),
    )
