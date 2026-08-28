"""Ledger pipeline that wires ingestion, aggregation, and export together.

Orchestrates the end-to-end flow:
  CUR/CID ingestion → in-memory data store → batch aggregation → analytics export

Requirements: 1.1, 2.1, 10.1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from axonllm_ledger.aggregation import (
    AggregationEngine,
    BatchAggregationScheduler,
    TimeRange,
)
from axonllm_ledger.cid_ingestion import CIDCollectionResult, CIDIngestionService
from axonllm_ledger.cur_ingestion import DeduplicationStore, IngestionResult
from axonllm_ledger.data_integrity import DataIntegrityService
from axonllm_ledger.export import (
    AlertNotifier,
    ExportService,
    LedgerExportPackage,
    package_export_data,
)
from axonllm_ledger.models import (
    AccessRecord,
    AccountHierarchy,
    CostAggregation,
    ExportRecord,
    OptimizationRecommendation,
    ProcessedBudget,
    UsageRecord,
)
from axonllm_ledger.s3_trigger import CURIngestionTrigger
from axonllm_ledger.quick_dataset import (
    QuickDatasetTables,
    build_quick_dataset_tables,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the CostTrackingPipeline."""

    s3_bucket: str = ""
    s3_prefix: str = ""
    sns_topic_arn: str = ""
    budgets_prefix: str = "cid/budgets/"
    orgs_prefix: str = "cid/organizations/"
    coh_prefix: str = "cid/coh/"


class CostTrackingPipeline:
    """Wires ingestion services to the data store and aggregation engine.

    Uses an in-memory data store (lists of records) to connect:
    - CUR Ingestion → Data Store → Batch Aggregation
    - CID Ingestion → Data Store
    - Aggregation → Analytics Export
    - Data Integrity checks across all stored data
    """

    def __init__(
        self,
        config: PipelineConfig,
        notifier: AlertNotifier,
        delivery_target=None,
        *,
        cur_trigger: CURIngestionTrigger | None = None,
    ) -> None:
        self._config = config
        self._notifier = notifier

        # In-memory data store
        self._usage_records: List[UsageRecord] = []
        self._access_records: List[AccessRecord] = []
        self._budgets: List[ProcessedBudget] = []
        self._hierarchy: dict[str, AccountHierarchy] = {}
        self._recommendations: List[OptimizationRecommendation] = []
        self._aggregations: List[CostAggregation] = []

        # Sub-components
        self._dedup_store = DeduplicationStore()
        self._cur_trigger = cur_trigger or CURIngestionTrigger(
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            dedup_store=self._dedup_store,
        )
        self._cid_service = CIDIngestionService(notifier=notifier)
        self._export_service: Optional[ExportService] = None
        if delivery_target is not None:
            self._export_service = ExportService(
                target=delivery_target, notifier=notifier,
            )

    @classmethod
    def from_boto3(
        cls,
        config: PipelineConfig,
        notifier: AlertNotifier,
        delivery_target=None,
        *,
        region_name: str | None = None,
        profile_name: str | None = None,
    ) -> CostTrackingPipeline:
        """Create a pipeline with a production S3 CUR ingestion adapter."""
        if not config.s3_bucket:
            raise ValueError("config.s3_bucket is required for AWS S3 ingestion")
        dedup_store = DeduplicationStore()
        trigger = CURIngestionTrigger.from_boto3(
            bucket=config.s3_bucket,
            prefix=config.s3_prefix,
            dedup_store=dedup_store,
            region_name=region_name,
            profile_name=profile_name,
        )
        pipeline = cls(
            config=config,
            notifier=notifier,
            delivery_target=delivery_target,
            cur_trigger=trigger,
        )
        pipeline._dedup_store = dedup_store
        return pipeline

    # -- accessors for stored data -----------------------------------------

    @property
    def usage_records(self) -> List[UsageRecord]:
        return list(self._usage_records)

    @property
    def access_records(self) -> List[AccessRecord]:
        return list(self._access_records)

    @property
    def budgets(self) -> List[ProcessedBudget]:
        return list(self._budgets)

    @property
    def hierarchy(self) -> dict[str, AccountHierarchy]:
        return dict(self._hierarchy)

    @property
    def recommendations(self) -> List[OptimizationRecommendation]:
        return list(self._recommendations)

    @property
    def aggregations(self) -> List[CostAggregation]:
        return list(self._aggregations)

    # -- CUR ingestion -----------------------------------------------------

    def run_cur_ingestion_event(self, event: dict) -> IngestionResult:
        """Run CUR ingestion from an S3 event, store records, and trigger aggregation.

        Requirements: 1.1
        """
        result = self._cur_trigger.handle_s3_event(event)
        self._store_cur_result(result)
        self._run_batch_aggregation()
        return result

    def run_cur_ingestion_poll(self) -> List[IngestionResult]:
        """Run CUR ingestion via polling, store records, and trigger aggregation.

        Requirements: 1.1
        """
        results = self._cur_trigger.poll_for_new_exports()
        for result in results:
            self._store_cur_result(result)
        if results:
            self._run_batch_aggregation()
        return results

    # -- CID ingestion -----------------------------------------------------

    def run_cid_ingestion(
        self,
        budgets_data: list[dict],
        orgs_data: list[dict],
        coh_data: list[dict],
    ) -> List[CIDCollectionResult]:
        """Run CID ingestion for Budgets, Organizations, and COH data.

        Stores processed budgets, hierarchy, and recommendations in the
        in-memory data store.

        Requirements: 2.1
        """
        from axonllm_ledger.budget_ingestion import ingest_budgets
        from axonllm_ledger.coh_ingestion import ingest_coh
        from axonllm_ledger.organizations_ingestion import ingest_organizations

        results = self._cid_service.run_collection(
            budgets_data=budgets_data,
            orgs_data=orgs_data,
            coh_data=coh_data,
            budgets_prefix=self._config.budgets_prefix,
            orgs_prefix=self._config.orgs_prefix,
            coh_prefix=self._config.coh_prefix,
        )

        # Store processed data from successful ingestions
        try:
            processed_budgets, _ = ingest_budgets(
                budgets_data, s3_prefix=self._config.budgets_prefix,
            )
            self._budgets.extend(processed_budgets)
        except Exception:
            logger.exception("Failed to store budgets")

        try:
            processed_orgs, hierarchy_map, _ = ingest_organizations(
                orgs_data, s3_prefix=self._config.orgs_prefix,
            )
            self._hierarchy.update(hierarchy_map)
        except Exception:
            logger.exception("Failed to store organizations")

        try:
            processed_recs, _ = ingest_coh(
                coh_data, s3_prefix=self._config.coh_prefix,
            )
            self._recommendations.extend(processed_recs)
        except Exception:
            logger.exception("Failed to store COH recommendations")

        return results

    # -- Export ------------------------------------------------------------

    def build_export_package(self, time_range: TimeRange) -> LedgerExportPackage:
        """Build the provider-neutral analytics package for a time range."""
        engine = self._build_engine()
        user_ids = sorted({record.userId for record in self._usage_records})
        return package_export_data(
            engine=engine,
            time_range=time_range,
            budgets=self._budgets,
            recommendations=self._recommendations,
            user_ids=user_ids,
        )

    def build_quick_tables(self, time_range: TimeRange) -> QuickDatasetTables:
        """Build the stable Amazon Quick table contract for a time range."""
        return build_quick_dataset_tables(self.build_export_package(time_range))

    def run_export(self, time_range: TimeRange) -> Optional[ExportRecord]:
        """Package aggregated data and deliver it to the configured target.

        Requirements: 10.1
        """
        if self._export_service is None:
            logger.warning("No delivery target configured; skipping export")
            return None

        package = self.build_export_package(time_range)
        return self._export_service.execute_export(package)

    # -- Integrity checks --------------------------------------------------

    def run_integrity_checks(self, time_range: TimeRange) -> dict:
        """Run all data integrity validations for the given time range.

        Returns a dict with keys: consistency, gaps, reconciliation.
        """
        engine = self._build_engine()
        integrity = DataIntegrityService(engine=engine, notifier=self._notifier)

        consistency = integrity.validate_cross_dimension_consistency(time_range)

        # Collect ingestion timestamps for gap detection
        ingestion_timestamps = sorted({r.ingestedAt for r in self._usage_records})
        from datetime import timedelta

        gaps = integrity.detect_data_gaps(
            source="CUR",
            ingestion_timestamps=ingestion_timestamps,
            expected_interval=timedelta(hours=1),
        )

        # Reconcile CUR vs Budgets
        from decimal import Decimal

        cur_total = sum((r.cost for r in self._usage_records), Decimal("0"))
        budget_spend = sum((b.actualSpend for b in self._budgets), Decimal("0"))
        reconciliation = integrity.reconcile_cur_vs_budgets(
            cur_total=cur_total,
            budget_actual_spend=budget_spend,
            time_range=time_range,
        )

        return {
            "consistency": consistency,
            "gaps": gaps,
            "reconciliation": reconciliation,
        }

    # -- Internal helpers --------------------------------------------------

    def _store_cur_result(self, result: IngestionResult) -> None:
        """Store new UsageRecords and AccessRecords from a CUR ingestion."""
        self._usage_records.extend(result.new_records)
        self._access_records.extend(result.access_records)

    def _build_engine(self) -> AggregationEngine:
        """Build an AggregationEngine from current in-memory data."""
        return AggregationEngine(
            records=self._usage_records,
            hierarchy=self._hierarchy or None,
            access_records=self._access_records,
        )

    def _run_batch_aggregation(self) -> List[CostAggregation]:
        """Trigger batch aggregation over all stored records."""
        if not self._usage_records:
            return []

        engine = self._build_engine()
        scheduler = BatchAggregationScheduler(engine)

        # Determine time range from stored records
        starts = [r.usageStartDate for r in self._usage_records]
        ends = [r.usageEndDate for r in self._usage_records]
        time_range = TimeRange(start=min(starts), end=max(ends))

        new_aggs = scheduler.run_batch([time_range])
        self._aggregations = new_aggs  # replace with latest
        return new_aggs
