"""Analytics export services for AxonLLM Ledger.

Packages aggregated cost data, model access data, budget comparisons,
and optimization recommendations for delivery to dashboard and BI targets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Protocol, runtime_checkable

from axonllm_ledger.aggregation import (
    AggregationEngine,
    AggregationResult,
    TimeRange,
)
from axonllm_ledger.models import (
    ExportStatus,
    ExportRecord,
    OptimizationRecommendation,
    ProcessedBudget,
)


@dataclass
class LedgerExportPackage:
    """All data categories produced for a single analytics export period."""

    cost_by_user: List[AggregationResult] = field(default_factory=list)
    cost_by_account: List[AggregationResult] = field(default_factory=list)
    cost_by_ou: List[AggregationResult] = field(default_factory=list)
    cost_by_model: List[AggregationResult] = field(default_factory=list)
    model_access_per_user: Dict[str, List[str]] = field(default_factory=dict)
    budget_comparisons: List[ProcessedBudget] = field(default_factory=list)
    optimization_recommendations: List[OptimizationRecommendation] = field(
        default_factory=list
    )
    export_period: TimeRange | None = None

    @property
    def record_count(self) -> int:
        """Return the total number of rows represented by this package."""
        return (
            len(self.cost_by_user)
            + len(self.cost_by_account)
            + len(self.cost_by_ou)
            + len(self.cost_by_model)
            + sum(len(models) for models in self.model_access_per_user.values())
            + len(self.budget_comparisons)
            + len(self.optimization_recommendations)
        )


def package_export_data(
    engine: AggregationEngine,
    time_range: TimeRange,
    budgets: List[ProcessedBudget],
    recommendations: List[OptimizationRecommendation],
    user_ids: List[str],
) -> LedgerExportPackage:
    """Package all data categories for an analytics export period.

    Aggregates costs by user, account, OU, and model; builds a
    model-access-per-user mapping; and bundles budget comparisons
    and optimization recommendations into a single export package.
    """
    return LedgerExportPackage(
        cost_by_user=engine.aggregate_by_user(time_range),
        cost_by_account=engine.aggregate_by_account(time_range),
        cost_by_ou=engine.aggregate_by_ou(time_range),
        cost_by_model=engine.aggregate_by_model(time_range),
        model_access_per_user={
            uid: engine.get_access_report_for_user(uid, time_range)
            for uid in user_ids
        },
        budget_comparisons=budgets,
        optimization_recommendations=recommendations,
        export_period=time_range,
    )


@runtime_checkable
class DeliveryTarget(Protocol):
    """Protocol for delivering export packages to an analytics target."""

    def deliver(self, package: LedgerExportPackage) -> bool:
        """Deliver the export package. Returns True on success, raises on failure."""
        ...


@runtime_checkable
class AlertNotifier(Protocol):
    """Protocol for sending alert notifications (e.g. via SNS)."""

    def send_alert(self, subject: str, message: str) -> None:
        """Send an alert notification with the given subject and message."""
        ...


class ExportService:
    """Deliver analytics packages with bounded retries and alerting.

    Attempts delivery up to ``MAX_ATTEMPTS`` times. Sends an alert if all
    attempts are exhausted. Each completed export
    (success or failure) is persisted via :meth:`log_export_result`.
    """

    MAX_ATTEMPTS: int = 3

    def __init__(self, target: DeliveryTarget, notifier: AlertNotifier) -> None:
        self._target = target
        self._notifier = notifier
        self._export_log: List[ExportRecord] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def export_log(self) -> List[ExportRecord]:
        """Return the list of all logged export records."""
        return list(self._export_log)

    def execute_export(self, package: LedgerExportPackage) -> ExportRecord:
        """Attempt to deliver *package*, retrying on failure.

        Returns an :class:`ExportRecord` describing the outcome.
        The record is also persisted via :meth:`log_export_result`.
        """
        export_id = ExportRecord.generate_id()
        last_error: str | None = None

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                self._target.deliver(package)
                record = self._build_record(
                    export_id, package, ExportStatus.SUCCESS, attempt, None
                )
                self.log_export_result(record)
                return record
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)

        # All retries exhausted — send alert
        period_info = self._period_description(package)
        self._notifier.send_alert(
            subject="Ledger export failed after all attempts",
            message=(
                f"Export {export_id} failed after {self.MAX_ATTEMPTS} attempts. "
                f"Period: {period_info}. Last error: {last_error}"
            ),
        )

        record = self._build_record(
            export_id, package, ExportStatus.FAILED, self.MAX_ATTEMPTS, last_error
        )
        self.log_export_result(record)
        return record

    def log_export_result(self, record: ExportRecord) -> None:
        """Persist a :class:`ExportRecord` to the export log.

        Called automatically by :meth:`execute_export` for every completed
        export run (success or failure).  Can also be called directly to
        log externally-produced records.
        """
        self._export_log.append(record)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_record(
        export_id: str,
        package: LedgerExportPackage,
        status: ExportStatus,
        attempt_count: int,
        error_message: str | None,
    ) -> ExportRecord:
        period = package.export_period
        return ExportRecord(
            exportId=export_id,
            exportPeriodStart=period.start if period else datetime.min,
            exportPeriodEnd=period.end if period else datetime.min,
            recordCount=package.record_count,
            status=status,
            attemptCount=attempt_count,
            exportedAt=datetime.now(timezone.utc),
            errorMessage=error_message,
        )

    @staticmethod
    def _period_description(package: LedgerExportPackage) -> str:
        if package.export_period:
            return (
                f"{package.export_period.start.isoformat()} – "
                f"{package.export_period.end.isoformat()}"
            )
        return "unknown"
