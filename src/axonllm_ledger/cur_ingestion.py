"""CUR Ingestion Service for the AxonLLM Ledger system.

Parses CUR (Cost and Usage Report) line items, filters for GenAI services
(Amazon Bedrock, SageMaker), extracts required fields, and produces
UsageRecords. Logs and skips incomplete records.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from axonllm_ledger.cost_normalization import normalize_cost_row
from axonllm_ledger.models import AccessRecord, IngestionLog, IngestionStatus, UsageRecord

logger = logging.getLogger(__name__)

# GenAI service codes that we ingest from CUR
GENAI_SERVICE_CODES = frozenset({"AmazonBedrock", "AmazonSageMaker"})

# Required fields for a valid GenAI CUR line item
_REQUIRED_FIELDS = ("user_id", "account_id", "model_id", "timestamp", "cost")


def extract_model_id_from_arn(arn: str) -> Optional[str]:
    """Extract the model identifier from an AWS resource ARN.

    Supports ARN formats such as:
      arn:aws:bedrock:us-east-1:123456789012:foundation-model/anthropic.claude-v2
      arn:aws:bedrock:us-east-1:123456789012:inference-profile/anthropic.claude-v2
      arn:aws:sagemaker:us-east-1:123456789012:endpoint/my-llm-endpoint

    Returns the model/endpoint name portion, or None if the ARN cannot be parsed.
    """
    if not arn or not isinstance(arn, str):
        return None

    # Match standard ARN pattern and grab the resource portion
    match = re.match(
        r"^arn:aws[a-zA-Z-]*:[a-zA-Z0-9-]+:[a-zA-Z0-9-]*:\d{12}:(.+)$", arn
    )
    if not match:
        return None

    resource = match.group(1)
    # Resource is typically "resource-type/resource-id"
    parts = resource.split("/", 1)
    if len(parts) == 2:
        return parts[1]
    return resource


def parse_line_item(raw_item: dict) -> Optional[UsageRecord]:
    """Parse a raw CUR line item dict into a UsageRecord.

    Returns a UsageRecord for GenAI service line items with all required fields.
    Returns None for non-GenAI line items.
    Logs and returns None for GenAI line items missing required fields.

    Expected raw_item keys (CUR column naming conventions):
      - product/servicecode: e.g. "AmazonBedrock"
      - identity/LineItemId: CUR line item identifier
      - lineItem/UsageAccountId: AWS account ID
      - resourceTags/user:UserId (or identity/lineItemId fallback): user identifier
      - lineItem/ResourceId: resource ARN (used to extract model ID)
      - lineItem/UsageStartDate: ISO-format timestamp
      - lineItem/UsageEndDate: ISO-format timestamp
      - lineItem/UsageAmount: token count / usage amount
      - lineItem/UnblendedCost: cost in USD
    """
    raw_item = normalize_cost_row(raw_item)

    # --- Step 1: Filter by service code ---
    service_code = raw_item.get("product/servicecode", "")
    if service_code not in GENAI_SERVICE_CODES:
        return None

    line_item_id = raw_item.get("identity/LineItemId", "")

    # --- Step 2: Extract fields ---
    user_id = (
        raw_item.get("resourceTags/user:UserId")
        or raw_item.get("identity/lineItemId")
        or ""
    )
    account_id = raw_item.get("lineItem/UsageAccountId", "")
    resource_arn = raw_item.get("lineItem/ResourceId", "")
    model_id = raw_item.get("axonllm/modelId", "") or (
        extract_model_id_from_arn(resource_arn) if resource_arn else ""
    )
    timestamp_str = raw_item.get("lineItem/UsageStartDate", "")
    cost_str = raw_item.get("lineItem/UnblendedCost", "")

    # --- Step 3: Validate required fields ---
    extracted = {
        "user_id": user_id,
        "account_id": account_id,
        "model_id": model_id or "",
        "timestamp": timestamp_str,
        "cost": cost_str,
    }

    missing = [name for name in _REQUIRED_FIELDS if not extracted[name]]
    if missing:
        logger.warning(
            "Skipping CUR line item %s: missing required fields: %s",
            line_item_id or "<unknown>",
            ", ".join(missing),
        )
        return None

    # --- Step 4: Parse typed values ---
    try:
        usage_start = _parse_timestamp(timestamp_str)
    except (ValueError, TypeError):
        logger.warning(
            "Skipping CUR line item %s: invalid timestamp: %s",
            line_item_id,
            timestamp_str,
        )
        return None

    end_date_str = raw_item.get("lineItem/UsageEndDate", "")
    try:
        usage_end = _parse_timestamp(end_date_str) if end_date_str else usage_start
    except (ValueError, TypeError):
        usage_end = usage_start

    try:
        cost = Decimal(str(cost_str))
    except (InvalidOperation, ValueError):
        logger.warning(
            "Skipping CUR line item %s: invalid cost value: %s",
            line_item_id,
            cost_str,
        )
        return None

    input_tokens = _safe_int(raw_item.get("lineItem/UsageAmount", 0))
    output_tokens = _safe_int(raw_item.get("product/outputTokens", 0))
    invocation_count = _safe_int(raw_item.get("product/invocationCount", 0)) or 1

    # --- Step 5: Build UsageRecord ---
    return UsageRecord(
        recordId=UsageRecord.generate_id(),
        lineItemId=line_item_id,
        userId=user_id,
        accountId=account_id,
        modelId=model_id,
        serviceName=service_code,
        usageStartDate=usage_start,
        usageEndDate=usage_end,
        inputTokens=input_tokens,
        outputTokens=output_tokens,
        invocationCount=invocation_count,
        cost=cost,
        ingestedAt=datetime.now(timezone.utc),
        sourceExportId=raw_item.get("bill/BillType", ""),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO-format timestamp string into a datetime."""
    cleaned = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _safe_int(value) -> int:
    """Convert a value to int, returning 0 on failure."""
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


@dataclass
class IngestionResult:
    """Result of ingesting a batch of CUR line items."""

    new_records: list[UsageRecord]
    access_records: list[AccessRecord]
    skipped_count: int
    duplicate_count: int
    log: IngestionLog


class DeduplicationStore:
    """In-memory store that tracks seen deduplication keys.

    Uses a set of composite keys ``(lineItemId, usageStartDate, accountId)``
    to detect duplicates.  Can be backed by DynamoDB or RDS later.
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, datetime, str]] = set()

    def contains(self, key: tuple[str, datetime, str]) -> bool:
        return key in self._seen

    def add(self, key: tuple[str, datetime, str]) -> None:
        self._seen.add(key)

    def __len__(self) -> int:
        return len(self._seen)


def deduplicate_record(record: UsageRecord, store: DeduplicationStore) -> bool:
    """Check whether *record* is new (not yet seen).

    Returns ``True`` if the record is new and registers it in *store*.
    Returns ``False`` if the record is a duplicate.
    """
    key = record.deduplication_key
    if store.contains(key):
        return False
    store.add(key)
    return True

def create_access_record(record: UsageRecord) -> AccessRecord:
    """Create an AccessRecord from a UsageRecord.

    For every valid GenAI CUR line item that produces a UsageRecord,
    an AccessRecord is created containing the user ID, model ID,
    account ID, and timestamp.

    Requirements: 9.1
    """
    return AccessRecord(
        accessId=AccessRecord.generate_id(),
        userId=record.userId,
        modelId=record.modelId,
        accountId=record.accountId,
        timestamp=record.usageStartDate,
        sourceRecordId=record.recordId,
    )


def ingest_line_items(
    raw_items: list[dict],
    store: DeduplicationStore,
    *,
    s3_key: str = "",
) -> IngestionResult:
    """Parse, deduplicate, and ingest a batch of raw CUR line items.

    Parameters
    ----------
    raw_items:
        Raw CUR line item dicts as they come from the export file.
    store:
        Deduplication store used to track already-seen records.
    s3_key:
        Optional S3 object key for the IngestionLog entry.

    Returns
    -------
    IngestionResult with new UsageRecords, counts, and an IngestionLog entry.
    """
    started_at = datetime.now(timezone.utc)
    new_records: list[UsageRecord] = []
    access_records: list[AccessRecord] = []
    skipped = 0
    duplicates = 0

    for raw in raw_items:
        record = parse_line_item(raw)
        if record is None:
            skipped += 1
            continue
        if deduplicate_record(record, store):
            new_records.append(record)
            access_records.append(create_access_record(record))
        else:
            duplicates += 1

    completed_at = datetime.now(timezone.utc)

    status = IngestionStatus.SUCCESS
    if skipped > 0 and new_records:
        status = IngestionStatus.PARTIAL
    elif not new_records and not raw_items:
        status = IngestionStatus.SUCCESS
    elif not new_records and skipped == len(raw_items):
        status = IngestionStatus.PARTIAL

    log = IngestionLog(
        logId=IngestionLog.generate_id(),
        source="CUR",
        s3Key=s3_key,
        recordCount=len(new_records),
        skippedCount=skipped,
        duplicateCount=duplicates,
        status=status,
        startedAt=started_at,
        completedAt=completed_at,
    )

    return IngestionResult(
        new_records=new_records,
        access_records=access_records,
        skipped_count=skipped,
        duplicate_count=duplicates,
        log=log,
    )
