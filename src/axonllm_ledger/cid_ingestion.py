"""CID Data Ingestion Service for the AxonLLM Ledger system.

Orchestrates collection runs across Budgets, Organizations, and Cost
Optimization Hub sources. Logs collection results, sends alert
notifications on failure, and enforces a maximum 24-hour collection
interval.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

from axonllm_ledger.models import IngestionStatus

logger = logging.getLogger(__name__)


@dataclass
class CIDCollectionResult:
    """Result of a single CID source collection run."""

    source: str
    record_count: int
    status: IngestionStatus
    error_message: Optional[str] = None
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AlertNotifier(Protocol):
    """Protocol for sending alert notifications (e.g. via SNS)."""

    def send_alert(self, subject: str, message: str) -> None:
        """Send an alert notification with the given subject and message."""
        ...


def log_collection_run(
    source: str,
    record_count: int,
    status: IngestionStatus,
    error_message: str | None = None,
) -> CIDCollectionResult:
    """Create a CIDCollectionResult log entry for a collection run.

    Logs the collection timestamp, source name, record count, and status.

    Requirements: 2.3
    """
    result = CIDCollectionResult(
        source=source,
        record_count=record_count,
        status=status,
        error_message=error_message,
    )
    logger.info(
        "CID collection run: source=%s, records=%d, status=%s, time=%s",
        result.source,
        result.record_count,
        result.status.value,
        result.collected_at.isoformat(),
    )
    if error_message:
        logger.error(
            "CID collection failure: source=%s, error=%s",
            source,
            error_message,
        )
    return result


def check_and_alert_on_failure(
    result: CIDCollectionResult,
    notifier: AlertNotifier,
) -> bool:
    """Send an alert notification if the collection result indicates failure.

    Returns True if an alert was sent, False otherwise.

    Requirements: 2.4
    """
    if result.status is IngestionStatus.FAILED:
        subject = f"CID Collection Failure: {result.source}"
        message = (
            f"CID data collection failed for source '{result.source}'.\n"
            f"Error: {result.error_message or 'Unknown error'}\n"
            f"Timestamp: {result.collected_at.isoformat()}"
        )
        notifier.send_alert(subject, message)
        logger.warning(
            "Alert sent for CID collection failure: source=%s",
            result.source,
        )
        return True
    return False


class CIDIngestionService:
    """Orchestrates CID data collection across all sources.

    Runs Budgets, Organizations, and COH ingestion pipelines, logs each
    collection run, and alerts on failures.

    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
    """

    COLLECTION_INTERVAL_SECONDS: int = 86400  # 24 hours

    def __init__(self, notifier: AlertNotifier) -> None:
        self._notifier = notifier
        self._results_history: list[CIDCollectionResult] = []

    @property
    def results_history(self) -> list[CIDCollectionResult]:
        """Return the history of collection results."""
        return list(self._results_history)

    def _run_single_source(
        self,
        source_name: str,
        ingest_fn,
        raw_data,
        *,
        s3_prefix: str = "",
    ) -> CIDCollectionResult:
        """Run ingestion for a single source and log the result."""
        try:
            processed, ingestion_log = ingest_fn(raw_data, s3_prefix=s3_prefix)
            result = log_collection_run(
                source=source_name,
                record_count=ingestion_log.recordCount,
                status=ingestion_log.status,
                error_message=ingestion_log.errorMessage,
            )
        except Exception as exc:
            result = log_collection_run(
                source=source_name,
                record_count=0,
                status=IngestionStatus.FAILED,
                error_message=str(exc),
            )

        check_and_alert_on_failure(result, self._notifier)
        self._results_history.append(result)
        return result

    def run_collection(
        self,
        budgets_data: list[dict],
        orgs_data: list[dict],
        coh_data: list[dict],
        *,
        budgets_prefix: str = "",
        orgs_prefix: str = "",
        coh_prefix: str = "",
    ) -> list[CIDCollectionResult]:
        """Run all three CID ingestion pipelines.

        Processes Budgets, Organizations, and COH data, logs each run,
        and sends alerts on any failures.

        Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
        """
        from axonllm_ledger.budget_ingestion import ingest_budgets
        from axonllm_ledger.coh_ingestion import ingest_coh
        from axonllm_ledger.organizations_ingestion import ingest_organizations

        results: list[CIDCollectionResult] = []

        # Budgets
        result = self._run_single_source(
            "Budgets", ingest_budgets, budgets_data, s3_prefix=budgets_prefix,
        )
        results.append(result)

        # Organizations — returns a 3-tuple; wrap to match 2-tuple interface
        def _ingest_orgs(data, *, s3_prefix=""):
            processed, _hierarchy_map, log = ingest_organizations(data, s3_prefix=s3_prefix)
            return processed, log

        result = self._run_single_source(
            "Organizations", _ingest_orgs, orgs_data, s3_prefix=orgs_prefix,
        )
        results.append(result)

        # COH
        result = self._run_single_source(
            "COH", ingest_coh, coh_data, s3_prefix=coh_prefix,
        )
        results.append(result)

        return results
