"""Data Integrity Service for the AxonLLM Ledger system.

Validates cross-dimension consistency, detects data gaps,
and reconciles CUR vs Budget data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List

from axonllm_ledger.aggregation import AggregationEngine, TimeRange
from axonllm_ledger.export import AlertNotifier

logger = logging.getLogger(__name__)


@dataclass
class ConsistencyResult:
    """Result of a cross-dimension consistency validation."""

    is_consistent: bool
    user_total: Decimal
    account_total: Decimal
    discrepancy: Decimal
    time_range: TimeRange


@dataclass
class DataGap:
    """A detected gap in ingestion data for a source."""

    source: str
    gap_start: datetime
    gap_end: datetime


@dataclass
class ReconciliationResult:
    """Result of CUR vs Budget reconciliation."""

    cur_total: Decimal
    budget_actual_spend: Decimal
    discrepancy_pct: Decimal
    is_within_threshold: bool
    time_range: TimeRange




class DataIntegrityService:
    """Validates data integrity across aggregation dimensions."""

    def __init__(
        self,
        engine: AggregationEngine,
        notifier: AlertNotifier,
    ) -> None:
        self._engine = engine
        self._notifier = notifier

    def validate_cross_dimension_consistency(
        self, time_range: TimeRange
    ) -> ConsistencyResult:
        """Validate that per-user cost totals equal per-account cost totals.

        Sums all per-user costs and all per-account costs for the given
        time range.  If they differ, logs the discrepancy and sends an
        alert via the notifier.
        """
        user_results = self._engine.aggregate_by_user(time_range)
        account_results = self._engine.aggregate_by_account(time_range)

        user_total = sum(
            (r.total_cost for r in user_results), Decimal("0")
        )
        account_total = sum(
            (r.total_cost for r in account_results), Decimal("0")
        )

        discrepancy = abs(user_total - account_total)
        is_consistent = discrepancy == Decimal("0")

        if not is_consistent:
            logger.warning(
                "Cross-dimension consistency violation: "
                "user_total=%s, account_total=%s, discrepancy=%s, "
                "time_range=%s–%s",
                user_total,
                account_total,
                discrepancy,
                time_range.start.isoformat(),
                time_range.end.isoformat(),
            )
            self._notifier.send_alert(
                subject="Cross-dimension consistency violation",
                message=(
                    f"Per-user cost total ({user_total}) does not match "
                    f"per-account cost total ({account_total}). "
                    f"Discrepancy: {discrepancy}. "
                    f"Time range: {time_range.start.isoformat()} – "
                    f"{time_range.end.isoformat()}"
                ),
            )

        return ConsistencyResult(
            is_consistent=is_consistent,
            user_total=user_total,
            account_total=account_total,
            discrepancy=discrepancy,
            time_range=time_range,
        )


    def detect_data_gaps(
        self,
        source: str,
        ingestion_timestamps: List[datetime],
        expected_interval: timedelta,
    ) -> List[DataGap]:
        """Detect gaps in a sequence of ingestion timestamps.

        Sorts the provided timestamps, then checks each consecutive pair.
        If the gap between two consecutive timestamps exceeds
        *expected_interval*, a :class:`DataGap` is recorded, logged,
        and an alert is sent via the notifier.

        Returns the list of detected gaps (empty if none).
        """
        if len(ingestion_timestamps) < 2:
            return []

        sorted_ts = sorted(ingestion_timestamps)
        gaps: List[DataGap] = []

        for prev, curr in zip(sorted_ts, sorted_ts[1:]):
            if curr - prev > expected_interval:
                gap = DataGap(source=source, gap_start=prev, gap_end=curr)
                gaps.append(gap)
                logger.warning(
                    "Data gap detected: source=%s, gap_start=%s, gap_end=%s",
                    source,
                    prev.isoformat(),
                    curr.isoformat(),
                )
                self._notifier.send_alert(
                    subject="Data gap detected",
                    message=(
                        f"Gap in ingestion data for source '{source}'. "
                        f"Missing period: {prev.isoformat()} – "
                        f"{curr.isoformat()}"
                    ),
                )

        return gaps

    def reconcile_cur_vs_budgets(
        self,
        cur_total: Decimal,
        budget_actual_spend: Decimal,
        time_range: TimeRange,
    ) -> ReconciliationResult:
        """Reconcile CUR-derived cost total against Budgets actual spend.

        Computes the absolute percentage discrepancy between the two
        values.  If the discrepancy exceeds 1%, logs it and sends an
        alert.  Returns a :class:`ReconciliationResult`.
        """
        if budget_actual_spend == Decimal("0"):
            if cur_total == Decimal("0"):
                discrepancy_pct = Decimal("0")
            else:
                discrepancy_pct = Decimal("Infinity")
        else:
            discrepancy_pct = (
                abs(cur_total - budget_actual_spend)
                / budget_actual_spend
                * Decimal("100")
            )

        is_within = discrepancy_pct <= Decimal("1")

        if not is_within:
            logger.warning(
                "CUR vs Budget discrepancy: cur_total=%s, "
                "budget_actual_spend=%s, discrepancy_pct=%s%%, "
                "time_range=%s–%s",
                cur_total,
                budget_actual_spend,
                discrepancy_pct,
                time_range.start.isoformat(),
                time_range.end.isoformat(),
            )
            self._notifier.send_alert(
                subject="CUR vs Budget reconciliation discrepancy",
                message=(
                    f"CUR total ({cur_total}) differs from Budget actual "
                    f"spend ({budget_actual_spend}) by {discrepancy_pct}%. "
                    f"Time range: {time_range.start.isoformat()} – "
                    f"{time_range.end.isoformat()}"
                ),
            )

        return ReconciliationResult(
            cur_total=cur_total,
            budget_actual_spend=budget_actual_spend,
            discrepancy_pct=discrepancy_pct,
            is_within_threshold=is_within,
            time_range=time_range,
        )
