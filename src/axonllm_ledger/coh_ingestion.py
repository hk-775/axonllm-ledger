"""Cost Optimization Hub Ingestion Service for the AxonLLM Ledger system.

Reads COH data from CID-specific S3 prefixes, filters for GenAI-relevant
recommendations, and creates OptimizationRecommendation records with
account ID, model ID, estimated savings, and recommendation type.

Requirements: 5.1, 5.2, 5.3
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from axonllm_ledger.models import (
    IngestionLog,
    IngestionStatus,
    OptimizationRecommendation,
)

logger = logging.getLogger(__name__)

# GenAI-relevant services (same as CUR filtering)
GENAI_SERVICES = frozenset({"AmazonBedrock", "AmazonSageMaker"})

# Required fields in raw recommendation data
_REQUIRED_FIELDS = (
    "recommendation_id",
    "account_id",
    "recommendation_type",
    "estimated_savings",
    "description",
    "service",
)


def _safe_decimal(value: Any) -> Decimal | None:
    """Convert a value to Decimal, returning None on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def process_single_recommendation(raw_rec: dict) -> OptimizationRecommendation | None:
    """Parse a raw recommendation dict into an OptimizationRecommendation.

    Filters for GenAI-relevant recommendations (service matching
    AmazonBedrock or AmazonSageMaker). Returns None for non-GenAI
    recommendations or records missing required fields.

    Expected raw_rec keys:
      - recommendation_id: unique recommendation identifier (required)
      - account_id: affected AWS account ID (required)
      - model_id: affected model ID (optional/nullable)
      - recommendation_type: type of recommendation (required)
      - estimated_savings: estimated monthly savings in USD (required)
      - description: human-readable description (required)
      - service: AWS service name, used for GenAI filtering (required)

    Requirements: 5.1, 5.2, 5.3
    """
    # Filter by GenAI-relevant service
    service = raw_rec.get("service", "")
    if service not in GENAI_SERVICES:
        return None

    # Validate required fields
    recommendation_id = raw_rec.get("recommendation_id", "")
    account_id = raw_rec.get("account_id", "")
    recommendation_type = raw_rec.get("recommendation_type", "")
    estimated_savings_raw = raw_rec.get("estimated_savings")
    description = raw_rec.get("description", "")

    missing = []
    if not recommendation_id:
        missing.append("recommendation_id")
    if not account_id:
        missing.append("account_id")
    if not recommendation_type:
        missing.append("recommendation_type")
    if estimated_savings_raw is None or str(estimated_savings_raw).strip() == "":
        missing.append("estimated_savings")
    if not description:
        missing.append("description")

    if missing:
        logger.warning(
            "Skipping COH recommendation %s: missing required fields: %s",
            recommendation_id or "<unknown>",
            ", ".join(missing),
        )
        return None

    # Parse estimated savings
    estimated_savings = _safe_decimal(estimated_savings_raw)
    if estimated_savings is None:
        logger.warning(
            "Skipping COH recommendation %s: invalid estimated_savings: %s",
            recommendation_id,
            estimated_savings_raw,
        )
        return None

    # model_id is optional/nullable
    model_id = raw_rec.get("model_id") or None

    return OptimizationRecommendation(
        recommendationId=str(recommendation_id),
        accountId=str(account_id),
        modelId=str(model_id) if model_id else None,
        recommendationType=str(recommendation_type),
        estimatedSavings=estimated_savings,
        description=str(description),
        ingestedAt=datetime.now(timezone.utc),
    )


def process_recommendations(raw_data: list[dict]) -> list[OptimizationRecommendation]:
    """Batch process raw COH data into OptimizationRecommendation records.

    Filters for GenAI-relevant recommendations and validates required fields.

    Requirements: 5.1
    """
    results: list[OptimizationRecommendation] = []
    for raw in raw_data:
        rec = process_single_recommendation(raw)
        if rec is not None:
            results.append(rec)
    return results


def associate_with_account(rec: OptimizationRecommendation, account_id: str) -> None:
    """Associate an OptimizationRecommendation with an account identifier.

    Requirements: 5.2
    """
    rec.accountId = account_id


def associate_with_model(rec: OptimizationRecommendation, model_id: str) -> None:
    """Associate an OptimizationRecommendation with a model identifier.

    Requirements: 5.2
    """
    rec.modelId = model_id


def ingest_coh(
    raw_data: list[dict],
    *,
    s3_prefix: str = "",
) -> tuple[list[OptimizationRecommendation], IngestionLog]:
    """Full COH ingestion pipeline.

    Processes raw COH data, filters for GenAI-relevant recommendations,
    and creates an IngestionLog entry.

    Parameters
    ----------
    raw_data:
        Raw COH recommendation dicts as read from S3.
    s3_prefix:
        The S3 prefix from which the data was read.

    Returns
    -------
    Tuple of (processed recommendations, ingestion log).
    """
    started_at = datetime.now(timezone.utc)

    processed = process_recommendations(raw_data)
    # Count non-GenAI filtered out + invalid records as skipped
    skipped = len(raw_data) - len(processed)

    completed_at = datetime.now(timezone.utc)

    status = IngestionStatus.SUCCESS
    if skipped > 0 and processed:
        status = IngestionStatus.PARTIAL
    elif not processed and raw_data:
        status = IngestionStatus.FAILED

    log = IngestionLog(
        logId=IngestionLog.generate_id(),
        source="COH",
        s3Key=s3_prefix,
        recordCount=len(processed),
        skippedCount=skipped,
        duplicateCount=0,
        status=status,
        startedAt=started_at,
        completedAt=completed_at,
    )

    return processed, log
