"""Unit tests for the Budget Ingestion Service.

Tests cover:
- Processing valid budget records into ProcessedBudget models
- Threshold flagging (isExceeded) when actualSpend > budgetLimit
- Account association
- Handling missing/invalid required fields
- Edge cases: zero spend, exactly at threshold, negative values
- Ingestion logging
"""

import logging
from datetime import datetime
from decimal import Decimal

import pytest

from axonllm_ledger.budget_ingestion import (
    associate_with_account,
    check_threshold_exceeded,
    ingest_budgets,
    process_budgets,
    process_single_budget,
)
from axonllm_ledger.models import IngestionStatus, ProcessedBudget


def _make_raw_budget(**overrides) -> dict:
    """Build a valid raw budget dict with sensible defaults."""
    defaults = {
        "budget_id": "budget-001",
        "budget_name": "GenAI Monthly Budget",
        "account_id": "123456789012",
        "budget_limit": "1000.00",
        "forecasted_spend": "800.00",
        "actual_spend": "750.00",
        "period_start": "2024-06-01T00:00:00Z",
        "period_end": "2024-06-30T23:59:59Z",
    }
    defaults.update(overrides)
    return defaults


class TestProcessSingleBudget:
    def test_valid_budget_produces_processed_budget(self):
        raw = _make_raw_budget()
        result = process_single_budget(raw)

        assert isinstance(result, ProcessedBudget)
        assert result.budgetId == "budget-001"
        assert result.budgetName == "GenAI Monthly Budget"
        assert result.accountId == "123456789012"
        assert result.budgetLimit == Decimal("1000.00")
        assert result.forecastedSpend == Decimal("800.00")
        assert result.actualSpend == Decimal("750.00")
        assert result.periodStart == datetime(2024, 6, 1, 0, 0, 0)
        assert result.periodEnd == datetime(2024, 6, 30, 23, 59, 59)
        assert result.isExceeded is False
        assert result.ingestedAt is not None

    def test_exceeded_budget_flagged(self):
        raw = _make_raw_budget(actual_spend="1500.00", budget_limit="1000.00")
        result = process_single_budget(raw)

        assert result is not None
        assert result.isExceeded is True

    def test_exactly_at_threshold_not_exceeded(self):
        raw = _make_raw_budget(actual_spend="1000.00", budget_limit="1000.00")
        result = process_single_budget(raw)

        assert result is not None
        assert result.isExceeded is False

    def test_one_cent_over_threshold_exceeded(self):
        raw = _make_raw_budget(actual_spend="1000.01", budget_limit="1000.00")
        result = process_single_budget(raw)

        assert result is not None
        assert result.isExceeded is True

    def test_zero_spend_not_exceeded(self):
        raw = _make_raw_budget(actual_spend="0", budget_limit="1000.00")
        result = process_single_budget(raw)

        assert result is not None
        assert result.isExceeded is False

    def test_missing_forecasted_spend_defaults_to_zero(self):
        raw = _make_raw_budget()
        del raw["forecasted_spend"]
        result = process_single_budget(raw)

        assert result is not None
        assert result.forecastedSpend == Decimal("0")

    def test_missing_actual_spend_defaults_to_zero(self):
        raw = _make_raw_budget()
        del raw["actual_spend"]
        result = process_single_budget(raw)

        assert result is not None
        assert result.actualSpend == Decimal("0")
        assert result.isExceeded is False


class TestProcessSingleBudgetMissingFields:
    def test_missing_budget_id(self, caplog):
        raw = _make_raw_budget(budget_id="")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "budget_id" in caplog.text

    def test_missing_budget_name(self, caplog):
        raw = _make_raw_budget(budget_name="")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "budget_name" in caplog.text

    def test_missing_account_id(self, caplog):
        raw = _make_raw_budget(account_id="")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "account_id" in caplog.text

    def test_missing_budget_limit(self, caplog):
        raw = _make_raw_budget()
        del raw["budget_limit"]
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "budget_limit" in caplog.text

    def test_empty_budget_limit(self, caplog):
        raw = _make_raw_budget(budget_limit="")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "budget_limit" in caplog.text

    def test_missing_period_start(self, caplog):
        raw = _make_raw_budget(period_start="")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "period_start" in caplog.text

    def test_missing_period_end(self, caplog):
        raw = _make_raw_budget(period_end="")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "period_end" in caplog.text

    def test_invalid_budget_limit(self, caplog):
        raw = _make_raw_budget(budget_limit="not-a-number")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "invalid budget_limit" in caplog.text

    def test_invalid_period_start(self, caplog):
        raw = _make_raw_budget(period_start="not-a-date")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "invalid period_start" in caplog.text

    def test_invalid_period_end(self, caplog):
        raw = _make_raw_budget(period_end="not-a-date")
        with caplog.at_level(logging.WARNING):
            result = process_single_budget(raw)
        assert result is None
        assert "invalid period_end" in caplog.text


class TestCheckThresholdExceeded:
    def test_exceeded(self):
        budget = ProcessedBudget(
            budgetId="b1", budgetName="Test", accountId="acct1",
            budgetLimit=Decimal("100"), forecastedSpend=Decimal("0"),
            actualSpend=Decimal("150"), periodStart=datetime(2024, 1, 1),
            periodEnd=datetime(2024, 1, 31), isExceeded=True,
            ingestedAt=datetime(2024, 1, 15),
        )
        assert check_threshold_exceeded(budget) is True

    def test_not_exceeded(self):
        budget = ProcessedBudget(
            budgetId="b1", budgetName="Test", accountId="acct1",
            budgetLimit=Decimal("100"), forecastedSpend=Decimal("0"),
            actualSpend=Decimal("50"), periodStart=datetime(2024, 1, 1),
            periodEnd=datetime(2024, 1, 31), isExceeded=False,
            ingestedAt=datetime(2024, 1, 15),
        )
        assert check_threshold_exceeded(budget) is False

    def test_exactly_at_limit(self):
        budget = ProcessedBudget(
            budgetId="b1", budgetName="Test", accountId="acct1",
            budgetLimit=Decimal("100"), forecastedSpend=Decimal("0"),
            actualSpend=Decimal("100"), periodStart=datetime(2024, 1, 1),
            periodEnd=datetime(2024, 1, 31), isExceeded=False,
            ingestedAt=datetime(2024, 1, 15),
        )
        assert check_threshold_exceeded(budget) is False


class TestAssociateWithAccount:
    def test_updates_account_id(self):
        budget = ProcessedBudget(
            budgetId="b1", budgetName="Test", accountId="old-acct",
            budgetLimit=Decimal("100"), forecastedSpend=Decimal("0"),
            actualSpend=Decimal("50"), periodStart=datetime(2024, 1, 1),
            periodEnd=datetime(2024, 1, 31), isExceeded=False,
            ingestedAt=datetime(2024, 1, 15),
        )
        associate_with_account(budget, "new-acct-123")
        assert budget.accountId == "new-acct-123"


class TestProcessBudgets:
    def test_processes_multiple_valid_budgets(self):
        raw_budgets = [
            _make_raw_budget(budget_id="b1"),
            _make_raw_budget(budget_id="b2", actual_spend="1500.00"),
            _make_raw_budget(budget_id="b3"),
        ]
        results = process_budgets(raw_budgets)

        assert len(results) == 3
        assert results[0].budgetId == "b1"
        assert results[0].isExceeded is False
        assert results[1].budgetId == "b2"
        assert results[1].isExceeded is True
        assert results[2].budgetId == "b3"

    def test_skips_invalid_budgets(self):
        raw_budgets = [
            _make_raw_budget(budget_id="b1"),
            _make_raw_budget(budget_id=""),  # invalid
            _make_raw_budget(budget_id="b3"),
        ]
        results = process_budgets(raw_budgets)
        assert len(results) == 2

    def test_empty_list(self):
        results = process_budgets([])
        assert results == []


class TestIngestBudgets:
    def test_successful_ingestion(self):
        raw_budgets = [
            _make_raw_budget(budget_id="b1"),
            _make_raw_budget(budget_id="b2"),
        ]
        budgets, log = ingest_budgets(raw_budgets, s3_prefix="s3://bucket/cid/budgets/")

        assert len(budgets) == 2
        assert log.source == "Budgets"
        assert log.s3Key == "s3://bucket/cid/budgets/"
        assert log.recordCount == 2
        assert log.skippedCount == 0
        assert log.status == IngestionStatus.SUCCESS

    def test_partial_ingestion(self):
        raw_budgets = [
            _make_raw_budget(budget_id="b1"),
            _make_raw_budget(budget_id=""),  # invalid
        ]
        budgets, log = ingest_budgets(raw_budgets)

        assert len(budgets) == 1
        assert log.recordCount == 1
        assert log.skippedCount == 1
        assert log.status == IngestionStatus.PARTIAL

    def test_all_invalid_produces_failed_status(self):
        raw_budgets = [
            _make_raw_budget(budget_id=""),
            _make_raw_budget(budget_name=""),
        ]
        budgets, log = ingest_budgets(raw_budgets)

        assert len(budgets) == 0
        assert log.recordCount == 0
        assert log.skippedCount == 2
        assert log.status == IngestionStatus.FAILED

    def test_empty_input_produces_success(self):
        budgets, log = ingest_budgets([])

        assert len(budgets) == 0
        assert log.status == IngestionStatus.SUCCESS
        assert log.recordCount == 0
        assert log.skippedCount == 0

    def test_log_has_valid_id_and_timestamps(self):
        budgets, log = ingest_budgets([_make_raw_budget()])

        assert len(log.logId) == 36  # UUID
        assert log.startedAt is not None
        assert log.completedAt is not None
        assert log.completedAt >= log.startedAt
