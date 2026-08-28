"""Budget Ingestion Service for the AxonLLM Ledger system.

Reads budget data from CID-specific S3 prefixes, processes raw budget
records into ProcessedBudget models, associates them with account
identifiers, and flags budgets as exceeded when actualSpend > budgetLimit.

Requirements: 3.1, 3.2, 3.3
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from axonllm_ledger.models import IngestionLog, IngestionStatus, ProcessedBudget

logger = logging.getLogger(__name__)

# Required fields in raw budget data
_REQUIRED_BUDGET_FIELDS = (
    "budget_id",
    "budget_name",
    "account_id",
    "budget_limit",
    "period_start",
    "period_end",
)


def _safe_decimal(value: Any) -> Decimal | None:
    """Convert a value to Decimal, returning None on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO-format timestamp string, returning None on failure."""
    if not value or not isinstance(value, str):
        return None
    try:
        cleaned = value.strip().rstrip("Z")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def check_threshold_exceeded(budget: ProcessedBudget) -> bool:
    """Check whether a budget's actual spend exceeds its budget limit.

    Returns True if actualSpend > budgetLimit, False otherwise.
    Requirement 3.3
    """
    return budget.actualSpend > budget.budgetLimit


def associate_with_account(budget: ProcessedBudget, account_id: str) -> None:
    """Associate a ProcessedBudget with an account identifier.

    Requirement 3.2
    """
    budget.accountId = account_id


def process_single_budget(raw_budget: dict) -> ProcessedBudget | None:
    """Parse a single raw budget dict into a ProcessedBudget.

    Returns a ProcessedBudget if all required fields are present and valid.
    Returns None and logs a warning for invalid/incomplete records.

    Expected raw_budget keys:
      - budget_id: unique budget identifier
      - budget_name: human-readable name
      - account_id: associated AWS account ID
      - budget_limit: threshold amount in USD
      - forecasted_spend: forecasted spend (optional, defaults to 0)
      - actual_spend: actual spend (optional, defaults to 0)
      - period_start: ISO-format start of budget period
      - period_end: ISO-format end of budget period
    """
    budget_id = raw_budget.get("budget_id", "")
    budget_name = raw_budget.get("budget_name", "")
    account_id = raw_budget.get("account_id", "")
    budget_limit_raw = raw_budget.get("budget_limit")
    period_start_str = raw_budget.get("period_start", "")
    period_end_str = raw_budget.get("period_end", "")

    # Validate required fields
    missing = []
    if not budget_id:
        missing.append("budget_id")
    if not budget_name:
        missing.append("budget_name")
    if not account_id:
        missing.append("account_id")
    if budget_limit_raw is None or str(budget_limit_raw).strip() == "":
        missing.append("budget_limit")
    if not period_start_str:
        missing.append("period_start")
    if not period_end_str:
        missing.append("period_end")

    if missing:
        logger.warning(
            "Skipping budget record %s: missing required fields: %s",
            budget_id or "<unknown>",
            ", ".join(missing),
        )
        return None

    # Parse typed values
    budget_limit = _safe_decimal(budget_limit_raw)
    if budget_limit is None:
        logger.warning(
            "Skipping budget record %s: invalid budget_limit: %s",
            budget_id,
            budget_limit_raw,
        )
        return None

    period_start = _parse_timestamp(period_start_str)
    if period_start is None:
        logger.warning(
            "Skipping budget record %s: invalid period_start: %s",
            budget_id,
            period_start_str,
        )
        return None

    period_end = _parse_timestamp(period_end_str)
    if period_end is None:
        logger.warning(
            "Skipping budget record %s: invalid period_end: %s",
            budget_id,
            period_end_str,
        )
        return None

    forecasted_spend = _safe_decimal(raw_budget.get("forecasted_spend", 0)) or Decimal("0")
    actual_spend = _safe_decimal(raw_budget.get("actual_spend", 0)) or Decimal("0")

    # Build ProcessedBudget with isExceeded flag
    is_exceeded = actual_spend > budget_limit

    budget = ProcessedBudget(
        budgetId=budget_id,
        budgetName=budget_name,
        accountId=account_id,
        budgetLimit=budget_limit,
        forecastedSpend=forecasted_spend,
        actualSpend=actual_spend,
        periodStart=period_start,
        periodEnd=period_end,
        isExceeded=is_exceeded,
        ingestedAt=datetime.now(timezone.utc),
    )

    return budget


def process_budgets(raw_budgets: list[dict]) -> list[ProcessedBudget]:
    """Process a list of raw budget dicts into ProcessedBudget records.

    Parses each raw budget, validates required fields, computes the
    isExceeded flag, and associates each budget with its account.

    Requirements: 3.1, 3.2, 3.3
    """
    results: list[ProcessedBudget] = []
    for raw in raw_budgets:
        budget = process_single_budget(raw)
        if budget is not None:
            results.append(budget)
    return results


def ingest_budgets(
    raw_budgets: list[dict],
    *,
    s3_prefix: str = "",
) -> tuple[list[ProcessedBudget], IngestionLog]:
    """Ingest budget data from a CID-specific S3 prefix.

    Processes raw budget records and creates an IngestionLog entry.

    Parameters
    ----------
    raw_budgets:
        Raw budget data dicts as read from S3.
    s3_prefix:
        The S3 prefix from which the data was read.

    Returns
    -------
    Tuple of (processed budgets, ingestion log).
    """
    started_at = datetime.now(timezone.utc)

    processed = process_budgets(raw_budgets)
    skipped = len(raw_budgets) - len(processed)

    completed_at = datetime.now(timezone.utc)

    status = IngestionStatus.SUCCESS
    if skipped > 0 and processed:
        status = IngestionStatus.PARTIAL
    elif not processed and raw_budgets:
        status = IngestionStatus.FAILED

    log = IngestionLog(
        logId=IngestionLog.generate_id(),
        source="Budgets",
        s3Key=s3_prefix,
        recordCount=len(processed),
        skippedCount=skipped,
        duplicateCount=0,
        status=status,
        startedAt=started_at,
        completedAt=completed_at,
    )

    return processed, log
