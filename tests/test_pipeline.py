"""Unit tests for the CostTrackingPipeline orchestration layer.

Verifies:
- CUR ingestion stores records and triggers aggregation
- CID ingestion stores budgets/orgs/recommendations
- Export packages data correctly
- Integrity checks run all validations
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from axonllm_ledger.aggregation import TimeRange
from axonllm_ledger.alert_service import LoggingAlertNotifier
from axonllm_ledger.cur_ingestion import DeduplicationStore, IngestionResult
from axonllm_ledger.export import LedgerExportPackage
from axonllm_ledger.models import (
    AccessRecord,
    ExportStatus,
    IngestionLog,
    IngestionStatus,
    UsageRecord,
)
from axonllm_ledger.pipeline import CostTrackingPipeline, PipelineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_usage_record(
    user_id: str = "user-1",
    account_id: str = "111111111111",
    model_id: str = "model-a",
    cost: str = "1.50",
    line_item_id: str = "li-001",
    start: datetime | None = None,
) -> UsageRecord:
    ts = start or datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
    return UsageRecord(
        recordId=UsageRecord.generate_id(),
        lineItemId=line_item_id,
        userId=user_id,
        accountId=account_id,
        modelId=model_id,
        serviceName="AmazonBedrock",
        usageStartDate=ts,
        usageEndDate=ts + timedelta(hours=1),
        inputTokens=100,
        outputTokens=50,
        invocationCount=1,
        cost=Decimal(cost),
        ingestedAt=datetime.now(timezone.utc),
        sourceExportId="export-001",
    )


def _make_access_record(usage: UsageRecord) -> AccessRecord:
    return AccessRecord(
        accessId=AccessRecord.generate_id(),
        userId=usage.userId,
        modelId=usage.modelId,
        accountId=usage.accountId,
        timestamp=usage.usageStartDate,
        sourceRecordId=usage.recordId,
    )


def _make_raw_line_item(
    user_id: str = "user-1",
    account_id: str = "111111111111",
    model_id: str = "bedrock-model-a",
    cost: str = "2.00",
    line_item_id: str = "li-100",
) -> dict:
    """Return a raw CUR line item dict that the parser will accept."""
    return {
        "identity/LineItemId": line_item_id,
        "product/servicecode": "AmazonBedrock",
        "lineItem/UsageAccountId": account_id,
        "lineItem/UsageStartDate": "2024-01-15T12:00:00Z",
        "lineItem/UsageEndDate": "2024-01-15T13:00:00Z",
        "lineItem/UnblendedCost": cost,
        "lineItem/UsageAmount": "100",
        "resourceTags/user:UserId": user_id,
        "lineItem/ResourceId": f"arn:aws:bedrock:us-east-1:{account_id}:model/{model_id}",
    }


def _s3_event(key: str = "cur/export-001.parquet") -> dict:
    return {
        "Records": [
            {"s3": {"bucket": {"name": "my-bucket"}, "object": {"key": key}}}
        ]
    }


class FakeDeliveryTarget:
    """Delivery target that records calls and can be set to fail."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[LedgerExportPackage] = []
        self._fail = fail

    def deliver(self, package: LedgerExportPackage) -> bool:
        if self._fail:
            raise RuntimeError("delivery failed")
        self.calls.append(package)
        return True


# ---------------------------------------------------------------------------
# Tests: CUR ingestion stores records and triggers aggregation
# ---------------------------------------------------------------------------

class TestCURIngestion:
    def _pipeline_with_raw_items(self, raw_items: list[dict]):
        """Create a pipeline whose CUR trigger returns *raw_items*."""
        notifier = LoggingAlertNotifier()
        config = PipelineConfig(s3_bucket="bucket", s3_prefix="cur/")
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)
        # Monkey-patch the trigger's _read_export to return our items
        pipeline._cur_trigger._read_export = lambda _key: raw_items
        return pipeline

    def test_event_ingestion_stores_records(self):
        raw = [_make_raw_line_item()]
        pipeline = self._pipeline_with_raw_items(raw)

        result = pipeline.run_cur_ingestion_event(_s3_event())

        assert len(result.new_records) == 1
        assert len(pipeline.usage_records) == 1
        assert len(pipeline.access_records) == 1

    def test_event_ingestion_triggers_aggregation(self):
        raw = [_make_raw_line_item()]
        pipeline = self._pipeline_with_raw_items(raw)

        pipeline.run_cur_ingestion_event(_s3_event())

        assert len(pipeline.aggregations) > 0

    def test_poll_ingestion_stores_records(self):
        raw = [_make_raw_line_item()]
        notifier = LoggingAlertNotifier()
        config = PipelineConfig(s3_bucket="bucket", s3_prefix="cur/")
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)
        pipeline._cur_trigger._read_export = lambda _key: raw
        pipeline._cur_trigger._list_new_exports = lambda: ["cur/export-poll.parquet"]

        results = pipeline.run_cur_ingestion_poll()

        assert len(results) == 1
        assert len(pipeline.usage_records) == 1
        assert len(pipeline.aggregations) > 0

    def test_deduplication_across_events(self):
        raw = [_make_raw_line_item()]
        pipeline = self._pipeline_with_raw_items(raw)

        pipeline.run_cur_ingestion_event(_s3_event("cur/a.parquet"))
        pipeline.run_cur_ingestion_event(_s3_event("cur/b.parquet"))

        # Same line item should only be stored once
        assert len(pipeline.usage_records) == 1

    def test_accepts_injected_cur_trigger(self):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig(s3_bucket="bucket", s3_prefix="cur/")
        trigger = type(
            "FakeTrigger",
            (),
            {
                "handle_s3_event": lambda self, event: IngestionResult(
                    new_records=[],
                    access_records=[],
                    skipped_count=0,
                    duplicate_count=0,
                    log=IngestionLog(
                        logId="log-1",
                        source="CUR",
                        s3Key="cur/export.csv",
                        recordCount=0,
                        skippedCount=0,
                        duplicateCount=0,
                        status=IngestionStatus.SUCCESS,
                        startedAt=datetime.now(timezone.utc),
                        completedAt=datetime.now(timezone.utc),
                    ),
                ),
                "poll_for_new_exports": lambda self: [],
            },
        )()

        pipeline = CostTrackingPipeline(
            config=config,
            notifier=notifier,
            cur_trigger=trigger,
        )

        assert pipeline.run_cur_ingestion_event(_s3_event()).log.s3Key == (
            "cur/export.csv"
        )


# ---------------------------------------------------------------------------
# Tests: CID ingestion stores budgets/orgs/recommendations
# ---------------------------------------------------------------------------

class TestCIDIngestion:
    def test_stores_budgets(self):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig()
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)

        budgets_data = [
            {
                "budget_id": "b-001",
                "budget_name": "GenAI-Budget",
                "account_id": "111111111111",
                "budget_limit": "1000.00",
                "forecasted_spend": "800.00",
                "actual_spend": "750.00",
                "period_start": "2024-01-01T00:00:00Z",
                "period_end": "2024-02-01T00:00:00Z",
            }
        ]

        pipeline.run_cid_ingestion(
            budgets_data=budgets_data, orgs_data=[], coh_data=[],
        )

        assert len(pipeline.budgets) == 1
        assert pipeline.budgets[0].budgetName == "GenAI-Budget"

    def test_stores_organizations_hierarchy(self):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig()
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)

        orgs_data = [
            {
                "account_id": "111111111111",
                "account_name": "Dev Account",
                "ou_id": "ou-001",
                "ou_name": "Engineering",
                "parent_ou_id": "r-root",
                "tags": {},
            }
        ]

        pipeline.run_cid_ingestion(
            budgets_data=[], orgs_data=orgs_data, coh_data=[],
        )

        assert "111111111111" in pipeline.hierarchy
        assert pipeline.hierarchy["111111111111"].organizationalUnitName == "Engineering"

    def test_stores_recommendations(self):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig()
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)

        coh_data = [
            {
                "recommendation_id": "rec-001",
                "account_id": "111111111111",
                "model_id": "model-a",
                "recommendation_type": "rightsizing",
                "estimated_savings": "50.00",
                "description": "Downsize model",
                "service": "AmazonBedrock",
            }
        ]

        pipeline.run_cid_ingestion(
            budgets_data=[], orgs_data=[], coh_data=coh_data,
        )

        assert len(pipeline.recommendations) == 1
        assert pipeline.recommendations[0].recommendationType == "rightsizing"


# ---------------------------------------------------------------------------
# Tests: Export packages data correctly
# ---------------------------------------------------------------------------

class TestExport:
    def test_export_packages_and_delivers(self):
        notifier = LoggingAlertNotifier()
        target = FakeDeliveryTarget()
        config = PipelineConfig(s3_bucket="bucket", s3_prefix="cur/")
        pipeline = CostTrackingPipeline(
            config=config, notifier=notifier, delivery_target=target,
        )

        # Inject a usage record directly for export
        rec = _make_usage_record()
        pipeline._usage_records.append(rec)
        pipeline._access_records.append(_make_access_record(rec))

        time_range = TimeRange(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
        export_record = pipeline.run_export(time_range)

        assert export_record is not None
        assert export_record.status == ExportStatus.SUCCESS
        assert len(target.calls) == 1
        pkg = target.calls[0]
        assert len(pkg.cost_by_user) > 0

    def test_builds_amazon_quick_tables_without_delivery_target(self):
        notifier = LoggingAlertNotifier()
        pipeline = CostTrackingPipeline(config=PipelineConfig(), notifier=notifier)
        record = _make_usage_record()
        pipeline._usage_records.append(record)
        pipeline._access_records.append(_make_access_record(record))
        time_range = TimeRange(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )

        tables = pipeline.build_quick_tables(time_range)

        assert len(tables.cost_aggregations) == 4
        assert tables.model_access[0]["user_id"] == "user-1"
        assert tables.model_access[0]["model_id"] == "model-a"

    def test_export_without_target_returns_none(self):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig()
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)

        time_range = TimeRange(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
        assert pipeline.run_export(time_range) is None


# ---------------------------------------------------------------------------
# Tests: Integrity checks run all validations
# ---------------------------------------------------------------------------

class TestIntegrityChecks:
    def test_runs_all_checks(self):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig()
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)

        # Add some records
        rec = _make_usage_record()
        pipeline._usage_records.append(rec)

        time_range = TimeRange(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
        result = pipeline.run_integrity_checks(time_range)

        assert "consistency" in result
        assert "gaps" in result
        assert "reconciliation" in result

    def test_consistency_passes_for_valid_data(self):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig()
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)

        rec = _make_usage_record()
        pipeline._usage_records.append(rec)

        time_range = TimeRange(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
        result = pipeline.run_integrity_checks(time_range)

        assert result["consistency"].is_consistent is True

    def test_reconciliation_within_threshold(self):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig()
        pipeline = CostTrackingPipeline(config=config, notifier=notifier)

        # No budgets and no records → both zero → within threshold
        time_range = TimeRange(
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        )
        result = pipeline.run_integrity_checks(time_range)

        assert result["reconciliation"].is_within_threshold is True


# ---------------------------------------------------------------------------
# Integration-test helpers (use field names the parsers actually expect)
# ---------------------------------------------------------------------------

def _valid_cur_item(
    user_id: str = "user-1",
    account_id: str = "111111111111",
    model_id: str = "bedrock-model-a",
    cost: str = "2.00",
    line_item_id: str = "li-100",
) -> dict:
    """Raw CUR line item with correct field names for the parser."""
    return {
        "identity/LineItemId": line_item_id,
        "product/servicecode": "AmazonBedrock",
        "lineItem/UsageAccountId": account_id,
        "lineItem/UsageStartDate": "2024-01-15T12:00:00Z",
        "lineItem/UsageEndDate": "2024-01-15T13:00:00Z",
        "lineItem/UnblendedCost": cost,
        "lineItem/UsageAmount": "100",
        "resourceTags/user:UserId": user_id,
        "lineItem/ResourceId": f"arn:aws:bedrock:us-east-1:{account_id}:model/{model_id}",
    }


def _valid_budget_item(
    budget_id: str = "b-001",
    budget_name: str = "GenAI-Budget",
    account_id: str = "111111111111",
    budget_limit: str = "1000.00",
    actual_spend: str = "750.00",
) -> dict:
    return {
        "budget_id": budget_id,
        "budget_name": budget_name,
        "account_id": account_id,
        "budget_limit": budget_limit,
        "forecasted_spend": "800.00",
        "actual_spend": actual_spend,
        "period_start": "2024-01-01T00:00:00Z",
        "period_end": "2024-02-01T00:00:00Z",
    }


def _valid_org_item(
    account_id: str = "111111111111",
    account_name: str = "Dev Account",
    ou_id: str = "ou-001",
    ou_name: str = "Engineering",
) -> dict:
    return {
        "account_id": account_id,
        "account_name": account_name,
        "ou_id": ou_id,
        "ou_name": ou_name,
        "parent_ou_id": "r-root",
        "tags": {},
    }


def _valid_coh_item(
    recommendation_id: str = "rec-001",
    account_id: str = "111111111111",
) -> dict:
    return {
        "recommendation_id": recommendation_id,
        "account_id": account_id,
        "model_id": "model-a",
        "recommendation_type": "rightsizing",
        "estimated_savings": "50.00",
        "description": "Downsize model",
        "service": "AmazonBedrock",
    }


# ---------------------------------------------------------------------------
# Integration Tests: End-to-end ingestion → aggregation → export
# ---------------------------------------------------------------------------

class TestIntegrationCURToExport:
    """CUR ingestion → aggregation → analytics delivery pipeline."""

    def _pipeline_with_raw_items(self, raw_items, target):
        notifier = LoggingAlertNotifier()
        config = PipelineConfig(s3_bucket="bucket", s3_prefix="cur/")
        pipeline = CostTrackingPipeline(
            config=config, notifier=notifier, delivery_target=target,
        )
        pipeline._cur_trigger._read_export = lambda _key: raw_items
        return pipeline

    def test_cur_ingest_aggregate_export(self):
        """Ingest CUR data, verify aggregations, export, verify package."""
        target = FakeDeliveryTarget()
        raw = [
            _valid_cur_item(user_id="alice", cost="3.00", line_item_id="li-1"),
            _valid_cur_item(user_id="bob", cost="2.00", line_item_id="li-2"),
        ]
        pipeline = self._pipeline_with_raw_items(raw, target)

        pipeline.run_cur_ingestion_event(_s3_event())

        # Records stored
        assert len(pipeline.usage_records) == 2
        assert len(pipeline.access_records) == 2
        # Aggregations computed
        assert len(pipeline.aggregations) > 0

        # Export — use naive datetimes to match CUR parser output
        time_range = TimeRange(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 2, 1),
        )
        export_record = pipeline.run_export(time_range)

        assert export_record is not None
        assert export_record.status == ExportStatus.SUCCESS
        assert len(target.calls) == 1
        pkg = target.calls[0]
        assert len(pkg.cost_by_user) == 2
        assert len(pkg.cost_by_account) >= 1


class TestIntegrationCIDToAggregation:
    """CID ingestion → budget/org/COH processing → aggregation pipeline."""

    def test_cid_plus_cur_export_includes_all_data(self):
        """Ingest CID + CUR data, export, verify budgets/hierarchy/recs in package."""
        target = FakeDeliveryTarget()
        notifier = LoggingAlertNotifier()
        config = PipelineConfig(s3_bucket="bucket", s3_prefix="cur/")
        pipeline = CostTrackingPipeline(
            config=config, notifier=notifier, delivery_target=target,
        )

        # CID ingestion (snake_case keys matching parser expectations)
        pipeline.run_cid_ingestion(
            budgets_data=[_valid_budget_item()],
            orgs_data=[_valid_org_item()],
            coh_data=[_valid_coh_item()],
        )

        assert len(pipeline.budgets) == 1
        assert "111111111111" in pipeline.hierarchy
        assert len(pipeline.recommendations) == 1

        # CUR ingestion (so aggregation has data)
        raw = [_valid_cur_item(cost="5.00", line_item_id="li-cid-1")]
        pipeline._cur_trigger._read_export = lambda _key: raw
        pipeline.run_cur_ingestion_event(_s3_event())

        # Export — use naive datetimes to match CUR parser output
        time_range = TimeRange(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 2, 1),
        )
        export_record = pipeline.run_export(time_range)

        assert export_record is not None
        assert export_record.status == ExportStatus.SUCCESS
        pkg = target.calls[0]

        # Budgets appear in export
        assert len(pkg.budget_comparisons) == 1
        assert pkg.budget_comparisons[0].budgetName == "GenAI-Budget"

        # Recommendations appear in export
        assert len(pkg.optimization_recommendations) == 1
        assert pkg.optimization_recommendations[0].recommendationType == "rightsizing"

        # Hierarchy used for OU aggregation
        assert len(pkg.cost_by_ou) >= 1

        # Cost data present
        assert len(pkg.cost_by_user) >= 1


class TestIntegrationFullFlowIntegrity:
    """Full flow: CUR + CID ingestion → export → integrity checks."""

    def test_full_flow_integrity_passes(self):
        """Ingest CUR + CID, export, run integrity checks — all should pass."""
        target = FakeDeliveryTarget()
        notifier = LoggingAlertNotifier()
        config = PipelineConfig(s3_bucket="bucket", s3_prefix="cur/")
        pipeline = CostTrackingPipeline(
            config=config, notifier=notifier, delivery_target=target,
        )

        # CID ingestion
        pipeline.run_cid_ingestion(
            budgets_data=[_valid_budget_item(actual_spend="10.00")],
            orgs_data=[],
            coh_data=[],
        )

        # CUR ingestion
        raw = [
            _valid_cur_item(cost="5.00", line_item_id="li-int-1"),
            _valid_cur_item(
                user_id="user-2", cost="5.00", line_item_id="li-int-2",
            ),
        ]
        pipeline._cur_trigger._read_export = lambda _key: raw
        pipeline.run_cur_ingestion_event(_s3_event())

        time_range = TimeRange(
            start=datetime(2024, 1, 1),
            end=datetime(2024, 2, 1),
        )

        # Export succeeds
        export_record = pipeline.run_export(time_range)
        assert export_record.status == ExportStatus.SUCCESS

        # Integrity checks
        result = pipeline.run_integrity_checks(time_range)
        assert result["consistency"].is_consistent is True
        assert len(result["gaps"]) == 0
