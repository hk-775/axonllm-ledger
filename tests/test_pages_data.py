from __future__ import annotations

import json
from pathlib import Path

from scripts.build_pages_data import build_dashboard_data

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_pages_data_matches_canonical_sample_export() -> None:
    payload = build_dashboard_data(REPOSITORY_ROOT)

    assert payload["project"] == {
        "name": "AxonLLM Ledger",
        "dashboard_name": "Amazon Quick dashboard preview",
        "release": "0.1.0 Beta",
        "sheets": 6,
        "visuals": 28,
        "sample_data": True,
    }
    assert payload["period"] == {
        "start": "2026-03-01T00:00:00Z",
        "end": "2026-03-02T07:00:00Z",
    }
    assert payload["ingestion"] == {
        "raw_rows": 38,
        "non_genai_filtered": 2,
        "incomplete_skipped": 1,
        "duplicates_removed": 3,
        "accepted_rows": 32,
    }
    assert payload["quick_table_counts"] == {
        "cost_aggregations": 26,
        "model_access": 26,
        "budgets": 5,
        "optimization_recommendations": 5,
    }

    records = payload["records"]
    assert round(sum(record["cost_usd"] for record in records), 4) == 0.696
    assert sum(record["invocations"] for record in records) == 175
    assert len({record["account_id"] for record in records}) == 5
    assert len({record["user_id"] for record in records}) == 8
    assert len({record["model_id"] for record in records}) == 9

    recommendations = payload["recommendations"]
    assert round(
        sum(item["estimated_savings_usd"] for item in recommendations), 2
    ) == 113.50
    assert {item["service"] for item in recommendations} == {
        "AmazonBedrock",
        "AmazonSageMaker",
    }


def test_committed_pages_payload_is_current() -> None:
    expected = build_dashboard_data(REPOSITORY_ROOT)
    committed = json.loads(
        (REPOSITORY_ROOT / "site/data/dashboard.json").read_text(
            encoding="utf-8"
        )
    )
    assert committed == expected
