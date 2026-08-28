"""Unit tests for the CID Data Ingestion Service.

Covers:
- log_collection_run creates correct CIDCollectionResult
- check_and_alert_on_failure sends alert on FAILED status
- check_and_alert_on_failure does NOT send alert on SUCCESS/PARTIAL
- CIDIngestionService.run_collection processes all three sources
- Alert is sent when a source fails
- Collection interval is configurable
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from axonllm_ledger.cid_ingestion import (
    CIDCollectionResult,
    CIDIngestionService,
    check_and_alert_on_failure,
    log_collection_run,
)
from axonllm_ledger.models import IngestionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeNotifier:
    """Test double that records alert calls."""

    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    def send_alert(self, subject: str, message: str) -> None:
        self.alerts.append((subject, message))


def _make_valid_budget(**overrides) -> dict:
    base = {
        "budget_id": "b-1",
        "budget_name": "Test Budget",
        "account_id": "111111111111",
        "budget_limit": "1000.00",
        "forecasted_spend": "800.00",
        "actual_spend": "500.00",
        "period_start": "2024-01-01T00:00:00",
        "period_end": "2024-01-31T23:59:59",
    }
    base.update(overrides)
    return base


def _make_valid_org(**overrides) -> dict:
    base = {
        "account_id": "111111111111",
        "account_name": "Test Account",
        "ou_id": "ou-1",
        "ou_name": "Engineering",
        "parent_ou_id": "r-root",
        "tags": {"env": "prod"},
    }
    base.update(overrides)
    return base


def _make_valid_coh(**overrides) -> dict:
    base = {
        "recommendation_id": "rec-1",
        "account_id": "111111111111",
        "model_id": "model-abc",
        "recommendation_type": "rightsizing",
        "estimated_savings": "50.00",
        "description": "Downsize instance",
        "service": "AmazonBedrock",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# log_collection_run
# ---------------------------------------------------------------------------

class TestLogCollectionRun:
    def test_creates_result_with_correct_fields(self):
        result = log_collection_run("Budgets", 42, IngestionStatus.SUCCESS)

        assert result.source == "Budgets"
        assert result.record_count == 42
        assert result.status is IngestionStatus.SUCCESS
        assert result.error_message is None
        assert isinstance(result.collected_at, datetime)

    def test_creates_result_with_error_message(self):
        result = log_collection_run(
            "COH", 0, IngestionStatus.FAILED, error_message="S3 timeout",
        )

        assert result.source == "COH"
        assert result.record_count == 0
        assert result.status is IngestionStatus.FAILED
        assert result.error_message == "S3 timeout"

    def test_collected_at_is_utc(self):
        result = log_collection_run("Organizations", 10, IngestionStatus.PARTIAL)
        assert result.collected_at.tzinfo is not None

    def test_partial_status(self):
        result = log_collection_run("Budgets", 5, IngestionStatus.PARTIAL)
        assert result.status is IngestionStatus.PARTIAL


# ---------------------------------------------------------------------------
# check_and_alert_on_failure
# ---------------------------------------------------------------------------

class TestCheckAndAlertOnFailure:
    def test_sends_alert_on_failed_status(self):
        notifier = FakeNotifier()
        result = CIDCollectionResult(
            source="Budgets",
            record_count=0,
            status=IngestionStatus.FAILED,
            error_message="Connection refused",
        )

        sent = check_and_alert_on_failure(result, notifier)

        assert sent is True
        assert len(notifier.alerts) == 1
        subject, message = notifier.alerts[0]
        assert "Budgets" in subject
        assert "Connection refused" in message

    def test_does_not_alert_on_success(self):
        notifier = FakeNotifier()
        result = CIDCollectionResult(
            source="Budgets",
            record_count=10,
            status=IngestionStatus.SUCCESS,
        )

        sent = check_and_alert_on_failure(result, notifier)

        assert sent is False
        assert len(notifier.alerts) == 0

    def test_does_not_alert_on_partial(self):
        notifier = FakeNotifier()
        result = CIDCollectionResult(
            source="Organizations",
            record_count=5,
            status=IngestionStatus.PARTIAL,
        )

        sent = check_and_alert_on_failure(result, notifier)

        assert sent is False
        assert len(notifier.alerts) == 0

    def test_alert_message_contains_source_and_error(self):
        notifier = FakeNotifier()
        result = CIDCollectionResult(
            source="COH",
            record_count=0,
            status=IngestionStatus.FAILED,
            error_message="Access denied",
        )

        check_and_alert_on_failure(result, notifier)

        _, message = notifier.alerts[0]
        assert "COH" in message
        assert "Access denied" in message

    def test_alert_with_no_error_message_uses_unknown(self):
        notifier = FakeNotifier()
        result = CIDCollectionResult(
            source="Budgets",
            record_count=0,
            status=IngestionStatus.FAILED,
            error_message=None,
        )

        check_and_alert_on_failure(result, notifier)

        _, message = notifier.alerts[0]
        assert "Unknown error" in message


# ---------------------------------------------------------------------------
# CIDIngestionService
# ---------------------------------------------------------------------------

class TestCIDIngestionServiceRunCollection:
    def test_processes_all_three_sources(self):
        notifier = FakeNotifier()
        service = CIDIngestionService(notifier)

        results = service.run_collection(
            budgets_data=[_make_valid_budget()],
            orgs_data=[_make_valid_org()],
            coh_data=[_make_valid_coh()],
            budgets_prefix="s3://bucket/budgets/",
            orgs_prefix="s3://bucket/orgs/",
            coh_prefix="s3://bucket/coh/",
        )

        assert len(results) == 3
        sources = [r.source for r in results]
        assert "Budgets" in sources
        assert "Organizations" in sources
        assert "COH" in sources

    def test_all_success_with_valid_data(self):
        notifier = FakeNotifier()
        service = CIDIngestionService(notifier)

        results = service.run_collection(
            budgets_data=[_make_valid_budget()],
            orgs_data=[_make_valid_org()],
            coh_data=[_make_valid_coh()],
        )

        for r in results:
            assert r.status is IngestionStatus.SUCCESS
            assert r.record_count >= 1

        # No alerts on success
        assert len(notifier.alerts) == 0

    def test_alert_sent_on_source_failure(self):
        notifier = FakeNotifier()
        service = CIDIngestionService(notifier)

        # All invalid budgets → FAILED status
        results = service.run_collection(
            budgets_data=[{"bad": "data"}],
            orgs_data=[_make_valid_org()],
            coh_data=[_make_valid_coh()],
        )

        budget_result = next(r for r in results if r.source == "Budgets")
        assert budget_result.status is IngestionStatus.FAILED

        # Alert should have been sent for the failed source
        assert len(notifier.alerts) >= 1
        subjects = [s for s, _ in notifier.alerts]
        assert any("Budgets" in s for s in subjects)

    def test_stores_results_in_history(self):
        notifier = FakeNotifier()
        service = CIDIngestionService(notifier)

        service.run_collection(
            budgets_data=[_make_valid_budget()],
            orgs_data=[_make_valid_org()],
            coh_data=[_make_valid_coh()],
        )

        assert len(service.results_history) == 3

    def test_history_accumulates_across_runs(self):
        notifier = FakeNotifier()
        service = CIDIngestionService(notifier)

        service.run_collection(
            budgets_data=[_make_valid_budget()],
            orgs_data=[_make_valid_org()],
            coh_data=[_make_valid_coh()],
        )
        service.run_collection(
            budgets_data=[_make_valid_budget()],
            orgs_data=[_make_valid_org()],
            coh_data=[_make_valid_coh()],
        )

        assert len(service.results_history) == 6

    def test_empty_data_produces_results(self):
        notifier = FakeNotifier()
        service = CIDIngestionService(notifier)

        results = service.run_collection(
            budgets_data=[],
            orgs_data=[],
            coh_data=[],
        )

        assert len(results) == 3
        for r in results:
            assert r.record_count == 0


class TestCIDIngestionServiceCollectionInterval:
    def test_default_interval_is_24_hours(self):
        assert CIDIngestionService.COLLECTION_INTERVAL_SECONDS == 86400

    def test_interval_is_class_attribute(self):
        notifier = FakeNotifier()
        service = CIDIngestionService(notifier)
        assert service.COLLECTION_INTERVAL_SECONDS == 86400

    def test_interval_can_be_overridden(self):
        notifier = FakeNotifier()
        service = CIDIngestionService(notifier)
        service.COLLECTION_INTERVAL_SECONDS = 3600
        assert service.COLLECTION_INTERVAL_SECONDS == 3600
        # Class default unchanged
        assert CIDIngestionService.COLLECTION_INTERVAL_SECONDS == 86400


class TestCIDCollectionResultDataclass:
    def test_default_collected_at(self):
        result = CIDCollectionResult(
            source="Budgets",
            record_count=0,
            status=IngestionStatus.SUCCESS,
        )
        assert isinstance(result.collected_at, datetime)

    def test_custom_collected_at(self):
        ts = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = CIDCollectionResult(
            source="COH",
            record_count=5,
            status=IngestionStatus.SUCCESS,
            collected_at=ts,
        )
        assert result.collected_at == ts
