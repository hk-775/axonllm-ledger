"""Unit tests for AxonLLM Ledger data models."""

from datetime import datetime, timezone
from decimal import Decimal

from axonllm_ledger.models import (
    AccessRecord,
    AccountHierarchy,
    CostAggregation,
    DimensionType,
    ExportStatus,
    IngestionLog,
    IngestionStatus,
    ExportRecord,
    OptimizationRecommendation,
    ProcessedBudget,
    UsageRecord,
)


class TestEnums:
    def test_dimension_type_values(self):
        assert DimensionType.USER.value == "USER"
        assert DimensionType.ACCOUNT.value == "ACCOUNT"
        assert DimensionType.OU.value == "OU"
        assert DimensionType.MODEL.value == "MODEL"
        assert len(DimensionType) == 4

    def test_ingestion_status_values(self):
        assert IngestionStatus.SUCCESS.value == "SUCCESS"
        assert IngestionStatus.PARTIAL.value == "PARTIAL"
        assert IngestionStatus.FAILED.value == "FAILED"
        assert len(IngestionStatus) == 3

    def test_export_status_values(self):
        assert ExportStatus.SUCCESS.value == "SUCCESS"
        assert ExportStatus.FAILED.value == "FAILED"
        assert ExportStatus.RETRYING.value == "RETRYING"
        assert len(ExportStatus) == 3


class TestUsageRecord:
    def _make_record(self, **overrides) -> UsageRecord:
        defaults = dict(
            recordId="rec-1",
            lineItemId="li-100",
            userId="user-a",
            accountId="111111111111",
            modelId="anthropic.claude-v2",
            serviceName="AmazonBedrock",
            usageStartDate=datetime(2024, 1, 15, 10, 0, 0),
            usageEndDate=datetime(2024, 1, 15, 11, 0, 0),
            inputTokens=500,
            outputTokens=200,
            invocationCount=3,
            cost=Decimal("0.0125"),
            ingestedAt=datetime(2024, 1, 15, 12, 0, 0),
            sourceExportId="export-001",
        )
        defaults.update(overrides)
        return UsageRecord(**defaults)

    def test_create_usage_record(self):
        rec = self._make_record()
        assert rec.recordId == "rec-1"
        assert rec.serviceName == "AmazonBedrock"
        assert rec.cost == Decimal("0.0125")
        assert rec.inputTokens == 500

    def test_deduplication_key(self):
        rec = self._make_record()
        key = rec.deduplication_key
        assert key == ("li-100", datetime(2024, 1, 15, 10, 0, 0), "111111111111")

    def test_deduplication_key_same_for_equal_fields(self):
        rec1 = self._make_record(recordId="a", cost=Decimal("1"))
        rec2 = self._make_record(recordId="b", cost=Decimal("2"))
        assert rec1.deduplication_key == rec2.deduplication_key

    def test_deduplication_key_differs_when_line_item_differs(self):
        rec1 = self._make_record(lineItemId="li-1")
        rec2 = self._make_record(lineItemId="li-2")
        assert rec1.deduplication_key != rec2.deduplication_key

    def test_deduplication_key_differs_when_account_differs(self):
        rec1 = self._make_record(accountId="111")
        rec2 = self._make_record(accountId="222")
        assert rec1.deduplication_key != rec2.deduplication_key

    def test_deduplication_key_differs_when_start_date_differs(self):
        rec1 = self._make_record(usageStartDate=datetime(2024, 1, 1))
        rec2 = self._make_record(usageStartDate=datetime(2024, 1, 2))
        assert rec1.deduplication_key != rec2.deduplication_key

    def test_generate_id_returns_uuid_string(self):
        id1 = UsageRecord.generate_id()
        id2 = UsageRecord.generate_id()
        assert isinstance(id1, str)
        assert len(id1) == 36  # UUID format
        assert id1 != id2


class TestAccessRecord:
    def test_create_access_record(self):
        rec = AccessRecord(
            accessId="acc-1",
            userId="user-a",
            modelId="anthropic.claude-v2",
            accountId="111111111111",
            timestamp=datetime(2024, 1, 15, 10, 0, 0),
            sourceRecordId="rec-1",
        )
        assert rec.accessId == "acc-1"
        assert rec.sourceRecordId == "rec-1"

    def test_generate_id(self):
        assert len(AccessRecord.generate_id()) == 36


class TestProcessedBudget:
    def test_create_budget_exceeded(self):
        budget = ProcessedBudget(
            budgetId="bud-1",
            budgetName="GenAI Monthly",
            accountId="111111111111",
            budgetLimit=Decimal("1000.00"),
            forecastedSpend=Decimal("1200.00"),
            actualSpend=Decimal("1100.00"),
            periodStart=datetime(2024, 1, 1),
            periodEnd=datetime(2024, 2, 1),
            isExceeded=True,
            ingestedAt=datetime(2024, 1, 20),
        )
        assert budget.isExceeded is True
        assert budget.actualSpend > budget.budgetLimit

    def test_create_budget_not_exceeded(self):
        budget = ProcessedBudget(
            budgetId="bud-2",
            budgetName="GenAI Monthly",
            accountId="111111111111",
            budgetLimit=Decimal("1000.00"),
            forecastedSpend=Decimal("800.00"),
            actualSpend=Decimal("500.00"),
            periodStart=datetime(2024, 1, 1),
            periodEnd=datetime(2024, 2, 1),
            isExceeded=False,
            ingestedAt=datetime(2024, 1, 20),
        )
        assert budget.isExceeded is False


class TestAccountHierarchy:
    def test_create_with_tags(self):
        ah = AccountHierarchy(
            accountId="111111111111",
            accountName="Dev Account",
            organizationalUnitId="ou-abc",
            organizationalUnitName="Engineering",
            parentOUId="ou-root",
            tags={"team": "ml", "env": "dev"},
        )
        assert ah.tags["team"] == "ml"
        assert ah.organizationalUnitName == "Engineering"

    def test_default_empty_tags(self):
        ah = AccountHierarchy(
            accountId="111111111111",
            accountName="Dev Account",
            organizationalUnitId="ou-abc",
            organizationalUnitName="Engineering",
            parentOUId="ou-root",
        )
        assert ah.tags == {}
        assert ah.ingestedAt.tzinfo is timezone.utc


class TestOptimizationRecommendation:
    def test_create_with_model(self):
        rec = OptimizationRecommendation(
            recommendationId="opt-1",
            accountId="111111111111",
            modelId="anthropic.claude-v2",
            recommendationType="rightsizing",
            estimatedSavings=Decimal("150.00"),
            description="Consider using a smaller model",
            ingestedAt=datetime(2024, 1, 20),
        )
        assert rec.modelId == "anthropic.claude-v2"

    def test_create_without_model(self):
        rec = OptimizationRecommendation(
            recommendationId="opt-2",
            accountId="111111111111",
            modelId=None,
            recommendationType="reserved_capacity",
            estimatedSavings=Decimal("500.00"),
            description="Consider reserved capacity",
            ingestedAt=datetime(2024, 1, 20),
        )
        assert rec.modelId is None


class TestCostAggregation:
    def test_create_aggregation(self):
        agg = CostAggregation(
            aggregationId="agg-1",
            dimension=DimensionType.USER,
            dimensionValue="user-a",
            timeRangeStart=datetime(2024, 1, 1),
            timeRangeEnd=datetime(2024, 2, 1),
            totalCost=Decimal("250.50"),
            totalInvocations=1000,
            totalInputTokens=500000,
            totalOutputTokens=200000,
            computedAt=datetime(2024, 2, 1, 1, 0, 0),
        )
        assert agg.dimension == DimensionType.USER
        assert agg.totalCost == Decimal("250.50")

    def test_generate_id(self):
        assert len(CostAggregation.generate_id()) == 36


class TestExportRecord:
    def test_create_success(self):
        rec = ExportRecord(
            exportId="exp-1",
            exportPeriodStart=datetime(2024, 1, 1),
            exportPeriodEnd=datetime(2024, 2, 1),
            recordCount=5000,
            status=ExportStatus.SUCCESS,
            attemptCount=1,
            exportedAt=datetime(2024, 2, 1, 2, 0, 0),
        )
        assert rec.status == ExportStatus.SUCCESS
        assert rec.errorMessage is None

    def test_create_failed_with_error(self):
        rec = ExportRecord(
            exportId="exp-2",
            exportPeriodStart=datetime(2024, 1, 1),
            exportPeriodEnd=datetime(2024, 2, 1),
            recordCount=0,
            status=ExportStatus.FAILED,
            attemptCount=3,
            exportedAt=datetime(2024, 2, 1, 2, 0, 0),
            errorMessage="Connection timeout",
        )
        assert rec.status == ExportStatus.FAILED
        assert rec.errorMessage == "Connection timeout"


class TestIngestionLog:
    def test_create_success_log(self):
        log = IngestionLog(
            logId="log-1",
            source="CUR",
            s3Key="s3://bucket/cur/2024-01/data.parquet",
            recordCount=1500,
            skippedCount=3,
            duplicateCount=12,
            status=IngestionStatus.SUCCESS,
            startedAt=datetime(2024, 1, 15, 10, 0, 0),
            completedAt=datetime(2024, 1, 15, 10, 5, 0),
        )
        assert log.status == IngestionStatus.SUCCESS
        assert log.errorMessage is None
        assert log.recordCount == 1500

    def test_create_failed_log(self):
        log = IngestionLog(
            logId="log-2",
            source="Budgets",
            s3Key="s3://bucket/cid/budgets/data.json",
            recordCount=0,
            skippedCount=0,
            duplicateCount=0,
            status=IngestionStatus.FAILED,
            startedAt=datetime(2024, 1, 15, 10, 0, 0),
            completedAt=datetime(2024, 1, 15, 10, 0, 5),
            errorMessage="S3 access denied",
        )
        assert log.status == IngestionStatus.FAILED
        assert log.errorMessage == "S3 access denied"

    def test_generate_id(self):
        assert len(IngestionLog.generate_id()) == 36
