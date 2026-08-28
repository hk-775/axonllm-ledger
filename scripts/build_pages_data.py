#!/usr/bin/env python3
"""Build deterministic sample data for the GitHub Pages dashboard."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

GENAI_SERVICE_CODES = frozenset({"AmazonBedrock", "AmazonSageMaker"})
GENAI_RECOMMENDATION_SERVICES = frozenset({"AmazonBedrock", "AmazonSageMaker"})
RESOURCE_ID_PATTERN = re.compile(
    r"^arn:aws[a-zA-Z-]*:[a-zA-Z0-9-]+:[a-zA-Z0-9-]*:\d{12}:(.+)$"
)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _safe_int(value: object) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _model_id(resource_arn: str) -> str:
    match = RESOURCE_ID_PATTERN.match(resource_arn)
    if not match:
        return ""
    resource = match.group(1)
    parts = resource.split("/", 1)
    return parts[1] if len(parts) == 2 else resource


def _iso_timestamp(value: str) -> str:
    return value.strip().replace("+00:00", "Z")


def _build_records(
    raw_rows: Sequence[dict[str, str]],
    accounts_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    metrics = {
        "raw_rows": len(raw_rows),
        "non_genai_filtered": 0,
        "incomplete_skipped": 0,
        "duplicates_removed": 0,
    }

    for row in raw_rows:
        service = row.get("product/servicecode", "")
        if service not in GENAI_SERVICE_CODES:
            metrics["non_genai_filtered"] += 1
            continue

        line_item_id = row.get("identity/LineItemId", "")
        account_id = row.get("lineItem/UsageAccountId", "")
        timestamp = row.get("lineItem/UsageStartDate", "")
        resource_id = row.get("lineItem/ResourceId", "")
        model_id = _model_id(resource_id)
        user_id = row.get("resourceTags/user:UserId", "")
        cost_value = row.get("lineItem/UnblendedCost", "")

        if not all(
            (line_item_id, account_id, timestamp, model_id, user_id, cost_value)
        ):
            metrics["incomplete_skipped"] += 1
            continue

        key = (line_item_id, timestamp, account_id)
        if key in seen:
            metrics["duplicates_removed"] += 1
            continue
        seen.add(key)

        account = accounts_by_id.get(account_id, {})
        records.append(
            {
                "line_item_id": line_item_id,
                "timestamp": _iso_timestamp(timestamp),
                "period_end": _iso_timestamp(
                    row.get("lineItem/UsageEndDate", timestamp)
                ),
                "account_id": account_id,
                "account_name": account.get("account_name", account_id),
                "organizational_unit": account.get("ou_name") or "Unassigned",
                "user_id": user_id,
                "model_id": model_id,
                "service": service,
                "input_tokens": _safe_int(row.get("lineItem/UsageAmount", 0)),
                "output_tokens": _safe_int(row.get("product/outputTokens", 0)),
                "invocations": _safe_int(
                    row.get("product/invocationCount", 0)
                )
                or 1,
                "cost_usd": float(Decimal(cost_value)),
            }
        )

    records.sort(key=lambda record: (record["timestamp"], record["line_item_id"]))
    metrics["accepted_rows"] = len(records)
    return records, metrics


def _build_budgets(raw_budgets: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    budgets = []
    for budget in raw_budgets:
        limit = Decimal(str(budget["budget_limit"]))
        actual = Decimal(str(budget["actual_spend"]))
        forecast = Decimal(str(budget["forecasted_spend"]))
        budgets.append(
            {
                "budget_id": budget["budget_id"],
                "budget_name": budget["budget_name"],
                "account_id": budget["account_id"],
                "budget_limit_usd": float(limit),
                "actual_spend_usd": float(actual),
                "forecasted_spend_usd": float(forecast),
                "period_start": _iso_timestamp(budget["period_start"]),
                "period_end": _iso_timestamp(budget["period_end"]),
                "is_exceeded": actual > limit,
            }
        )
    return budgets


def _build_recommendations(
    raw_recommendations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    recommendations = []
    for recommendation in raw_recommendations:
        if recommendation.get("service") not in GENAI_RECOMMENDATION_SERVICES:
            continue
        recommendations.append(
            {
                "recommendation_id": recommendation["recommendation_id"],
                "account_id": recommendation["account_id"],
                "model_id": recommendation.get("model_id"),
                "recommendation_type": recommendation["recommendation_type"],
                "estimated_savings_usd": float(
                    Decimal(str(recommendation["estimated_savings"]))
                ),
                "description": recommendation["description"],
                "service": recommendation["service"],
            }
        )
    return recommendations


def _quick_table_counts(
    records: Sequence[dict[str, Any]],
    budgets: Sequence[dict[str, Any]],
    recommendations: Sequence[dict[str, Any]],
) -> dict[str, int]:
    dimensions = (
        "user_id",
        "account_id",
        "organizational_unit",
        "model_id",
    )
    cost_aggregation_rows = sum(
        len({record[dimension] for record in records}) for dimension in dimensions
    )
    model_access_rows = len(
        {(record["user_id"], record["model_id"]) for record in records}
    )
    return {
        "cost_aggregations": cost_aggregation_rows,
        "model_access": model_access_rows,
        "budgets": len(budgets),
        "optimization_recommendations": len(recommendations),
    }


def _validate_against_export(
    records: Sequence[dict[str, Any]],
    recommendations: Sequence[dict[str, Any]],
    export: dict[str, Any],
) -> None:
    expected_spend = sum(
        Decimal(str(row["total_cost"])) for row in export["cost_by_account"]
    )
    actual_spend = sum(Decimal(str(row["cost_usd"])) for row in records)
    if actual_spend != expected_spend:
        raise ValueError(
            f"sample spend does not match Ledger export: "
            f"{actual_spend} != {expected_spend}"
        )

    expected_invocations = sum(
        int(row["total_invocations"]) for row in export["cost_by_account"]
    )
    actual_invocations = sum(int(row["invocations"]) for row in records)
    if actual_invocations != expected_invocations:
        raise ValueError(
            f"sample invocations do not match Ledger export: "
            f"{actual_invocations} != {expected_invocations}"
        )

    expected_savings = sum(
        Decimal(str(row["estimatedSavings"]))
        for row in export["optimization_recommendations"]
    )
    actual_savings = sum(
        Decimal(str(row["estimated_savings_usd"])) for row in recommendations
    )
    if actual_savings != expected_savings:
        raise ValueError(
            f"sample savings do not match Ledger export: "
            f"{actual_savings} != {expected_savings}"
        )


def build_dashboard_data(repository_root: Path) -> dict[str, Any]:
    """Build the browser dashboard payload from canonical sample inputs."""
    sample_root = repository_root / "sample_data"
    accounts = _load_json(sample_root / "sample_organizations.json")
    accounts_by_id = {account["account_id"]: account for account in accounts}

    records, ingestion = _build_records(
        _load_csv(sample_root / "sample_cur.csv"),
        accounts_by_id,
    )
    budgets = _build_budgets(_load_json(sample_root / "sample_budgets.json"))
    recommendations = _build_recommendations(
        _load_json(sample_root / "sample_coh.json")
    )
    export = _load_json(sample_root / "ledger_export.json")
    _validate_against_export(records, recommendations, export)

    period_start = min(record["timestamp"] for record in records)
    period_end = max(record["period_end"] for record in records)
    model_access = [
        {
            "account_id": account_id,
            "user_id": user_id,
            "model_id": model_id,
        }
        for account_id, user_id, model_id in sorted(
            {
                (
                    record["account_id"],
                    record["user_id"],
                    record["model_id"],
                )
                for record in records
            }
        )
    ]

    return {
        "project": {
            "name": "AxonLLM Ledger",
            "dashboard_name": "Amazon Quick dashboard preview",
            "release": "0.1.0 Beta",
            "sheets": 6,
            "visuals": 28,
            "sample_data": True,
        },
        "period": {
            "start": period_start,
            "end": period_end,
        },
        "ingestion": ingestion,
        "quick_table_counts": _quick_table_counts(
            records,
            budgets,
            recommendations,
        ),
        "accounts": accounts,
        "records": records,
        "model_access": model_access,
        "budgets": budgets,
        "recommendations": recommendations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("site/data/dashboard.json"),
        help="output path relative to the repository root",
    )
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[1]
    output_path = (
        args.output
        if args.output.is_absolute()
        else repository_root / args.output
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_dashboard_data(repository_root)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote GitHub Pages dashboard data to {output_path}")


if __name__ == "__main__":
    main()
