"""Data models for the AxonLLM Ledger system.

Defines all core data model classes, enums, and composite keys used
throughout AxonLLM Ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class DimensionType(Enum):
    """Aggregation dimension types for cost reports."""

    USER = "USER"
    ACCOUNT = "ACCOUNT"
    OU = "OU"
    MODEL = "MODEL"


class IngestionStatus(Enum):
    """Status of an ingestion run."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExportStatus(Enum):
    """Status of an analytics export run."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"



@dataclass
class UsageRecord:
    """A single GenAI usage event derived from a CUR line item.

    Deduplication Key: (lineItemId, usageStartDate, accountId)
    """

    recordId: str
    lineItemId: str
    userId: str
    accountId: str
    modelId: str
    serviceName: str
    usageStartDate: datetime
    usageEndDate: datetime
    inputTokens: int
    outputTokens: int
    invocationCount: int
    cost: Decimal
    ingestedAt: datetime
    sourceExportId: str

    @property
    def deduplication_key(self) -> tuple[str, datetime, str]:
        """Composite key used for deduplication: (lineItemId, usageStartDate, accountId)."""
        return (self.lineItemId, self.usageStartDate, self.accountId)

    @staticmethod
    def generate_id() -> str:
        """Generate a new UUID for recordId."""
        return str(uuid.uuid4())


@dataclass
class AccessRecord:
    """A user's access to a specific GenAI model.

    Retention: Minimum 12 months.
    """

    accessId: str
    userId: str
    modelId: str
    accountId: str
    timestamp: datetime
    sourceRecordId: str

    @staticmethod
    def generate_id() -> str:
        """Generate a new UUID for accessId."""
        return str(uuid.uuid4())


@dataclass
class ProcessedBudget:
    """An ingested budget from AWS Budgets."""

    budgetId: str
    budgetName: str
    accountId: str
    budgetLimit: Decimal
    forecastedSpend: Decimal
    actualSpend: Decimal
    periodStart: datetime
    periodEnd: datetime
    isExceeded: bool
    ingestedAt: datetime


@dataclass
class AccountHierarchy:
    """AWS Organizations account structure."""

    accountId: str
    accountName: str
    organizationalUnitId: str
    organizationalUnitName: str
    parentOUId: str
    tags: dict[str, str] = field(default_factory=dict)
    ingestedAt: datetime = field(default_factory=datetime.utcnow)


@dataclass
class OptimizationRecommendation:
    """A Cost Optimization Hub recommendation."""

    recommendationId: str
    accountId: str
    modelId: Optional[str]
    recommendationType: str
    estimatedSavings: Decimal
    description: str
    ingestedAt: datetime


@dataclass
class CostAggregation:
    """Pre-computed cost aggregation for a dimension and time range."""

    aggregationId: str
    dimension: DimensionType
    dimensionValue: str
    timeRangeStart: datetime
    timeRangeEnd: datetime
    totalCost: Decimal
    totalInvocations: int
    totalInputTokens: int
    totalOutputTokens: int
    computedAt: datetime

    @staticmethod
    def generate_id() -> str:
        """Generate a new UUID for aggregationId."""
        return str(uuid.uuid4())


@dataclass
class ExportRecord:
    """Metadata for an analytics export run."""

    exportId: str
    exportPeriodStart: datetime
    exportPeriodEnd: datetime
    recordCount: int
    status: ExportStatus
    attemptCount: int
    exportedAt: datetime
    errorMessage: Optional[str] = None

    @staticmethod
    def generate_id() -> str:
        """Generate a new UUID for exportId."""
        return str(uuid.uuid4())


@dataclass
class IngestionLog:
    """Tracks ingestion runs for auditing and gap detection."""

    logId: str
    source: str
    s3Key: str
    recordCount: int
    skippedCount: int
    duplicateCount: int
    status: IngestionStatus
    startedAt: datetime
    completedAt: datetime
    errorMessage: Optional[str] = None

    @staticmethod
    def generate_id() -> str:
        """Generate a new UUID for logId."""
        return str(uuid.uuid4())
