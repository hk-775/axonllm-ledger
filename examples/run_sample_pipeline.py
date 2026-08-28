#!/usr/bin/env python3
"""Feed the sample CUR and CID data through the Ledger pipeline.

Usage:
    PYTHONPATH=src python examples/run_sample_pipeline.py
    PYTHONPATH=src python examples/run_sample_pipeline.py /path/to/your/cur.csv
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from axonllm_ledger.cur_ingestion import (
    DeduplicationStore,
    ingest_line_items,
    parse_line_item,
)
from axonllm_ledger.budget_ingestion import ingest_budgets
from axonllm_ledger.organizations_ingestion import ingest_organizations
from axonllm_ledger.coh_ingestion import ingest_coh
from axonllm_ledger.aggregation import AggregationEngine, TimeRange
from axonllm_ledger.export import package_export_data, LedgerExportPackage
from axonllm_ledger.quick_dataset import build_quick_dataset_tables


def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_json(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _package_to_json(package: LedgerExportPackage) -> str:
    def _agg(a):
        return {
            "dimension_value": a.dimension_value,
            "total_cost": str(a.total_cost),
            "total_invocations": a.total_invocations,
            "total_input_tokens": a.total_input_tokens,
            "total_output_tokens": a.total_output_tokens,
        }
    data = {
        "export_period": {
            "start": package.export_period.start.isoformat() if package.export_period else None,
            "end": package.export_period.end.isoformat() if package.export_period else None,
        },
        "cost_by_user": [_agg(a) for a in package.cost_by_user],
        "cost_by_account": [_agg(a) for a in package.cost_by_account],
        "cost_by_ou": [_agg(a) for a in package.cost_by_ou],
        "cost_by_model": [_agg(a) for a in package.cost_by_model],
        "model_access_per_user": package.model_access_per_user,
        "budget_comparisons": [
            {"accountId": b.accountId, "budgetName": b.budgetName,
             "budgetLimit": str(b.budgetLimit), "actualSpend": str(b.actualSpend),
             "forecastedSpend": str(b.forecastedSpend), "exceeded": b.isExceeded}
            for b in package.budget_comparisons
        ],
        "optimization_recommendations": [
            {"recommendationId": r.recommendationId, "accountId": r.accountId,
             "modelId": r.modelId, "estimatedSavings": str(r.estimatedSavings),
             "recommendationType": r.recommendationType, "description": r.description}
            for r in package.optimization_recommendations
        ],
    }
    return json.dumps(data, indent=2)


def main() -> None:
    cur_path = sys.argv[1] if len(sys.argv) > 1 else "sample_data/sample_cur.csv"
    budgets_path = "sample_data/sample_budgets.json"
    orgs_path = "sample_data/sample_organizations.json"
    coh_path = "sample_data/sample_coh.json"

    if not Path(cur_path).exists():
        print(f"File not found: {cur_path}")
        sys.exit(1)

    # ===================== CUR INGESTION =====================
    print(f"\n{'='*60}")
    print(f"  CUR INGESTION — {cur_path}")
    print(f"{'='*60}\n")

    raw_items = load_csv(cur_path)
    print(f"Raw line items loaded: {len(raw_items)}")

    # Parse
    parsed, skipped, non_genai = [], 0, 0
    for item in raw_items:
        record = parse_line_item(item)
        if record is None:
            svc = item.get("product/servicecode", "")
            if svc not in ("AmazonBedrock", "AmazonSageMaker"):
                non_genai += 1
            else:
                skipped += 1
        else:
            parsed.append(record)
    print(f"  GenAI records parsed:  {len(parsed)}")
    print(f"  Non-GenAI filtered:    {non_genai}")
    print(f"  GenAI skipped:         {skipped} (missing fields)")

    # Deduplicate
    store = DeduplicationStore()
    result = ingest_line_items(raw_items, store)
    print(f"  After dedup:           {len(result.new_records)} unique")
    print(f"  Duplicates removed:    {result.duplicate_count}")
    print(f"  Access records:        {len(result.access_records)}")

    # ===================== CID INGESTION =====================
    print(f"\n{'='*60}")
    print(f"  CID INGESTION")
    print(f"{'='*60}")

    # Budgets
    budgets_data = load_json(budgets_path) if Path(budgets_path).exists() else []
    budgets, budget_log = ingest_budgets(budgets_data)
    print(f"\n  Budgets ({budget_log.status.value}):")
    print(f"    Processed: {len(budgets)}")
    for b in budgets:
        flag = " EXCEEDED" if b.isExceeded else " within limit"
        print(f"    {b.budgetName} ({b.accountId}): "
              f"${b.actualSpend}/{b.budgetLimit}{flag}")

    # Organizations
    orgs_data = load_json(orgs_path) if Path(orgs_path).exists() else []
    orgs, hierarchy_map, org_log = ingest_organizations(orgs_data)
    print(f"\n  Organizations ({org_log.status.value}):")
    print(f"    Accounts mapped: {len(orgs)}")
    for acct in orgs:
        print(f"    {acct.accountId} ({acct.accountName}) → "
              f"OU: {acct.organizationalUnitName or 'None'} "
              f"tags: {acct.tags}")

    # COH
    coh_data = load_json(coh_path) if Path(coh_path).exists() else []
    recommendations, coh_log = ingest_coh(coh_data)
    print(f"\n  Cost Optimization Hub ({coh_log.status.value}):")
    print(f"    GenAI recommendations: {len(recommendations)}")
    for r in recommendations:
        model = r.modelId or "general"
        print(f"    [{r.recommendationType}] {r.accountId}/{model}: "
              f"save ${r.estimatedSavings} — {r.description[:60]}...")

    # ===================== AGGREGATION =====================
    print(f"\n{'='*60}")
    print(f"  AGGREGATION")
    print(f"{'='*60}\n")

    engine = AggregationEngine(
        records=result.new_records,
        hierarchy=hierarchy_map or None,
        access_records=result.access_records,
    )
    starts = [r.usageStartDate for r in result.new_records]
    ends = [r.usageEndDate for r in result.new_records]
    tr = TimeRange(start=min(starts), end=max(ends))
    print(f"  Time range: {tr.start} → {tr.end}")

    print("\n  Per-User Costs:")
    for a in engine.aggregate_by_user(tr):
        print(f"    {a.dimension_value:15s} ${str(a.total_cost):>10s}  "
              f"({a.total_invocations:>3d} invocations)")

    print("\n  Per-Account Costs:")
    for a in engine.aggregate_by_account(tr):
        print(f"    {a.dimension_value:15s} ${str(a.total_cost):>10s}  "
              f"({a.total_invocations:>3d} invocations)")

    print("\n  Per-OU Costs:")
    for a in engine.aggregate_by_ou(tr):
        print(f"    {a.dimension_value:15s} ${str(a.total_cost):>10s}  "
              f"({a.total_invocations:>3d} invocations)")

    print("\n  Per-Model Costs:")
    for a in engine.aggregate_by_model(tr):
        print(f"    {a.dimension_value:30s} ${str(a.total_cost):>10s}  "
              f"({a.total_invocations:>3d} invocations)")

    print("\n  Access Report:")
    user_ids = sorted({r.userId for r in result.new_records})
    for uid in user_ids:
        models = engine.get_access_report_for_user(uid, tr)
        print(f"    {uid:15s} → {', '.join(models)}")

    # ===================== ANALYTICS EXPORT =====================
    print(f"\n{'='*60}")
    print("  ANALYTICS EXPORT")
    print(f"{'='*60}\n")

    package = package_export_data(
        engine=engine,
        time_range=tr,
        budgets=budgets,
        recommendations=recommendations,
        user_ids=user_ids,
    )
    export_json = _package_to_json(package)
    quick_json = build_quick_dataset_tables(package).to_json()

    output_dir = Path("build")
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "ledger_export.json"
    quick_path = output_dir / "quick_dataset.json"
    out_path.write_text(export_json, encoding="utf-8")
    quick_path.write_text(quick_json, encoding="utf-8")
    print(f"  Ledger export written to: {out_path}")
    print(f"  Amazon Quick tables written to: {quick_path}")

    total = sum(r.cost for r in result.new_records)
    exceeded = sum(1 for b in budgets if b.isExceeded)
    print(f"\n  Summary:")
    print(f"    Total GenAI spend:     ${total:.4f}")
    print(f"    Users:                 {len(user_ids)}")
    print(f"    Accounts:              {len({r.accountId for r in result.new_records})}")
    print(f"    Models:                {len({r.modelId for r in result.new_records})}")
    print(f"    Budgets exceeded:      {exceeded}/{len(budgets)}")
    print(f"    COH recommendations:   {len(recommendations)}")
    potential = sum(r.estimatedSavings for r in recommendations)
    print(f"    Potential savings:      ${potential:.2f}")
    print()


if __name__ == "__main__":
    main()
