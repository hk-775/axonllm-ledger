"""Aggregation Engine for the AxonLLM Ledger system.

Computes cost rollups across dimensions (user, account, OU, model)
from UsageRecord data.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from axonllm_ledger.models import (
    AccessRecord,
    AccountHierarchy,
    CostAggregation,
    DimensionType,
    UsageRecord,
)


@dataclass
class TimeRange:
    """A time range with inclusive start and exclusive end."""

    start: datetime
    end: datetime


@dataclass
class AggregationResult:
    """Aggregated cost totals for a single dimension value."""

    dimension_value: str
    total_cost: Decimal
    total_invocations: int
    total_input_tokens: int
    total_output_tokens: int


@dataclass
class ModelBreakdown:
    """Cost breakdown for a single model within a detailed report."""

    model_id: str
    total_cost: Decimal
    total_invocations: int
    total_input_tokens: int
    total_output_tokens: int


@dataclass
class UserBreakdown:
    """Cost breakdown for a single user within a detailed report."""

    user_id: str
    total_cost: Decimal
    total_invocations: int
    total_input_tokens: int
    total_output_tokens: int


@dataclass
class AccountBreakdown:
    """Cost breakdown for a single account within a detailed report."""

    account_id: str
    total_cost: Decimal
    total_invocations: int
    total_input_tokens: int
    total_output_tokens: int


@dataclass
class DetailedUserCostReport:
    """Detailed cost report for a specific user with per-model breakdown."""

    user_id: str
    total_cost: Decimal
    total_invocations: int
    total_input_tokens: int
    total_output_tokens: int
    model_breakdown: List[ModelBreakdown] = field(default_factory=list)


@dataclass
class DetailedAccountCostReport:
    """Detailed cost report for a specific account with per-model and per-user breakdown."""

    account_id: str
    total_cost: Decimal
    total_invocations: int
    total_input_tokens: int
    total_output_tokens: int
    model_breakdown: List[ModelBreakdown] = field(default_factory=list)
    user_breakdown: List[UserBreakdown] = field(default_factory=list)


@dataclass
class DetailedModelCostReport:
    """Detailed cost report for a specific model with per-user and per-account breakdown."""

    model_id: str
    total_cost: Decimal
    total_invocations: int
    total_input_tokens: int
    total_output_tokens: int
    user_breakdown: List[UserBreakdown] = field(default_factory=list)
    account_breakdown: List[AccountBreakdown] = field(default_factory=list)


class AggregationEngine:
    """Computes cost rollups across dimensions from UsageRecord data."""

    def __init__(
        self,
        records: List[UsageRecord],
        hierarchy: Optional[Dict[str, AccountHierarchy]] = None,
        access_records: Optional[List[AccessRecord]] = None,
    ) -> None:
        self._records = records
        self._hierarchy = hierarchy
        self._access_records = access_records or []

    def _filter_by_time_range(self, time_range: TimeRange) -> List[UsageRecord]:
        """Return records whose usageStartDate falls within [start, end)."""
        return [
            r
            for r in self._records
            if time_range.start <= r.usageStartDate < time_range.end
        ]

    def aggregate_by_user(self, time_range: TimeRange) -> List[AggregationResult]:
        """Aggregate total cost, invocations, and tokens per user."""
        filtered = self._filter_by_time_range(time_range)

        user_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

        for r in filtered:
            d = user_data[r.userId]
            d["cost"] += r.cost
            d["invocations"] += r.invocationCount
            d["input_tokens"] += r.inputTokens
            d["output_tokens"] += r.outputTokens

        return [
            AggregationResult(
                dimension_value=user_id,
                total_cost=d["cost"],
                total_invocations=d["invocations"],
                total_input_tokens=d["input_tokens"],
                total_output_tokens=d["output_tokens"],
            )
            for user_id, d in user_data.items()
        ]

    def get_cost_report_for_user(
        self, user_id: str, time_range: TimeRange
    ) -> DetailedUserCostReport:
        """Return a detailed cost report for a specific user with per-model breakdown."""
        filtered = [
            r
            for r in self._filter_by_time_range(time_range)
            if r.userId == user_id
        ]

        total_cost = Decimal("0")
        total_invocations = 0
        total_input_tokens = 0
        total_output_tokens = 0

        model_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

        for r in filtered:
            total_cost += r.cost
            total_invocations += r.invocationCount
            total_input_tokens += r.inputTokens
            total_output_tokens += r.outputTokens

            md = model_data[r.modelId]
            md["cost"] += r.cost
            md["invocations"] += r.invocationCount
            md["input_tokens"] += r.inputTokens
            md["output_tokens"] += r.outputTokens

        breakdown = [
            ModelBreakdown(
                model_id=model_id,
                total_cost=md["cost"],
                total_invocations=md["invocations"],
                total_input_tokens=md["input_tokens"],
                total_output_tokens=md["output_tokens"],
            )
            for model_id, md in model_data.items()
        ]

        return DetailedUserCostReport(
            user_id=user_id,
            total_cost=total_cost,
            total_invocations=total_invocations,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            model_breakdown=breakdown,
        )
    def aggregate_by_account(self, time_range: TimeRange) -> List[AggregationResult]:
        """Aggregate total cost, invocations, and tokens per account."""
        filtered = self._filter_by_time_range(time_range)

        account_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

        for r in filtered:
            d = account_data[r.accountId]
            d["cost"] += r.cost
            d["invocations"] += r.invocationCount
            d["input_tokens"] += r.inputTokens
            d["output_tokens"] += r.outputTokens

        return [
            AggregationResult(
                dimension_value=account_id,
                total_cost=d["cost"],
                total_invocations=d["invocations"],
                total_input_tokens=d["input_tokens"],
                total_output_tokens=d["output_tokens"],
            )
            for account_id, d in account_data.items()
        ]

    def aggregate_by_ou(self, time_range: TimeRange) -> List[AggregationResult]:
        """Aggregate total cost, invocations, and tokens per organizational unit.

        Uses the hierarchy mapping to resolve each account's OU.
        Accounts not found in the hierarchy are aggregated under "Unknown OU".
        If no hierarchy was provided, all records go to "Unknown OU".
        """
        filtered = self._filter_by_time_range(time_range)

        ou_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

        for r in filtered:
            if self._hierarchy and r.accountId in self._hierarchy:
                ou_name = self._hierarchy[r.accountId].organizationalUnitName
            else:
                ou_name = "Unknown OU"
            d = ou_data[ou_name]
            d["cost"] += r.cost
            d["invocations"] += r.invocationCount
            d["input_tokens"] += r.inputTokens
            d["output_tokens"] += r.outputTokens

        return [
            AggregationResult(
                dimension_value=ou_name,
                total_cost=d["cost"],
                total_invocations=d["invocations"],
                total_input_tokens=d["input_tokens"],
                total_output_tokens=d["output_tokens"],
            )
            for ou_name, d in ou_data.items()
        ]

    def get_cost_report_for_account(
        self, account_id: str, time_range: TimeRange
    ) -> DetailedAccountCostReport:
        """Return a detailed cost report for a specific account with per-model and per-user breakdown."""
        filtered = [
            r
            for r in self._filter_by_time_range(time_range)
            if r.accountId == account_id
        ]

        total_cost = Decimal("0")
        total_invocations = 0
        total_input_tokens = 0
        total_output_tokens = 0

        model_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )
        user_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

        for r in filtered:
            total_cost += r.cost
            total_invocations += r.invocationCount
            total_input_tokens += r.inputTokens
            total_output_tokens += r.outputTokens

            md = model_data[r.modelId]
            md["cost"] += r.cost
            md["invocations"] += r.invocationCount
            md["input_tokens"] += r.inputTokens
            md["output_tokens"] += r.outputTokens

            ud = user_data[r.userId]
            ud["cost"] += r.cost
            ud["invocations"] += r.invocationCount
            ud["input_tokens"] += r.inputTokens
            ud["output_tokens"] += r.outputTokens

        model_breakdown = [
            ModelBreakdown(
                model_id=model_id,
                total_cost=md["cost"],
                total_invocations=md["invocations"],
                total_input_tokens=md["input_tokens"],
                total_output_tokens=md["output_tokens"],
            )
            for model_id, md in model_data.items()
        ]

        user_breakdown = [
            UserBreakdown(
                user_id=uid,
                total_cost=ud["cost"],
                total_invocations=ud["invocations"],
                total_input_tokens=ud["input_tokens"],
                total_output_tokens=ud["output_tokens"],
            )
            for uid, ud in user_data.items()
        ]

        return DetailedAccountCostReport(
            account_id=account_id,
            total_cost=total_cost,
            total_invocations=total_invocations,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            model_breakdown=model_breakdown,
            user_breakdown=user_breakdown,
        )

    def aggregate_by_model(self, time_range: TimeRange) -> List[AggregationResult]:
        """Aggregate total cost, invocations, and tokens per model."""
        filtered = self._filter_by_time_range(time_range)

        model_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

        for r in filtered:
            d = model_data[r.modelId]
            d["cost"] += r.cost
            d["invocations"] += r.invocationCount
            d["input_tokens"] += r.inputTokens
            d["output_tokens"] += r.outputTokens

        return [
            AggregationResult(
                dimension_value=model_id,
                total_cost=d["cost"],
                total_invocations=d["invocations"],
                total_input_tokens=d["input_tokens"],
                total_output_tokens=d["output_tokens"],
            )
            for model_id, d in model_data.items()
        ]

    def get_cost_report_for_model(
        self, model_id: str, time_range: TimeRange
    ) -> DetailedModelCostReport:
        """Return a detailed cost report for a specific model with per-user and per-account breakdown."""
        filtered = [
            r
            for r in self._filter_by_time_range(time_range)
            if r.modelId == model_id
        ]

        total_cost = Decimal("0")
        total_invocations = 0
        total_input_tokens = 0
        total_output_tokens = 0

        user_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )
        account_data: dict[str, dict] = defaultdict(
            lambda: {
                "cost": Decimal("0"),
                "invocations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        )

        for r in filtered:
            total_cost += r.cost
            total_invocations += r.invocationCount
            total_input_tokens += r.inputTokens
            total_output_tokens += r.outputTokens

            ud = user_data[r.userId]
            ud["cost"] += r.cost
            ud["invocations"] += r.invocationCount
            ud["input_tokens"] += r.inputTokens
            ud["output_tokens"] += r.outputTokens

            ad = account_data[r.accountId]
            ad["cost"] += r.cost
            ad["invocations"] += r.invocationCount
            ad["input_tokens"] += r.inputTokens
            ad["output_tokens"] += r.outputTokens

        user_breakdown = [
            UserBreakdown(
                user_id=uid,
                total_cost=ud["cost"],
                total_invocations=ud["invocations"],
                total_input_tokens=ud["input_tokens"],
                total_output_tokens=ud["output_tokens"],
            )
            for uid, ud in user_data.items()
        ]

        account_breakdown = [
            AccountBreakdown(
                account_id=aid,
                total_cost=ad["cost"],
                total_invocations=ad["invocations"],
                total_input_tokens=ad["input_tokens"],
                total_output_tokens=ad["output_tokens"],
            )
            for aid, ad in account_data.items()
        ]

        return DetailedModelCostReport(
            model_id=model_id,
            total_cost=total_cost,
            total_invocations=total_invocations,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            user_breakdown=user_breakdown,
            account_breakdown=account_breakdown,
        )


    def get_access_report_for_user(
        self, user_id: str, time_range: TimeRange
    ) -> list[str]:
        """Return distinct model IDs accessed by a user within [start, end)."""
        models: set[str] = set()
        for rec in self._access_records:
            if (
                rec.userId == user_id
                and time_range.start <= rec.timestamp < time_range.end
            ):
                models.add(rec.modelId)
        return sorted(models)

    def get_access_report_for_model(
        self, model_id: str, time_range: TimeRange
    ) -> list[str]:
        """Return distinct user IDs who accessed a model within [start, end)."""
        users: set[str] = set()
        for rec in self._access_records:
            if (
                rec.modelId == model_id
                and time_range.start <= rec.timestamp < time_range.end
            ):
                users.add(rec.userId)
        return sorted(users)

class BatchAggregationScheduler:
    """Pre-computes aggregations across all dimensions for given time ranges.

    This is an in-memory batch scheduler that runs after ingestion completes,
    producing CostAggregation records for USER, ACCOUNT, OU, and MODEL dimensions.
    """

    def __init__(self, engine: AggregationEngine) -> None:
        self._engine = engine
        self._stored: List[CostAggregation] = []

    def run_batch(self, time_ranges: List[TimeRange]) -> List[CostAggregation]:
        """Pre-compute aggregations for all 4 dimensions across the given time ranges.

        Returns a list of CostAggregation records and stores them internally.
        """
        results: List[CostAggregation] = []
        now = datetime.utcnow()

        dimension_methods = [
            (DimensionType.USER, self._engine.aggregate_by_user),
            (DimensionType.ACCOUNT, self._engine.aggregate_by_account),
            (DimensionType.OU, self._engine.aggregate_by_ou),
            (DimensionType.MODEL, self._engine.aggregate_by_model),
        ]

        for tr in time_ranges:
            for dim_type, agg_method in dimension_methods:
                for agg_result in agg_method(tr):
                    record = CostAggregation(
                        aggregationId=CostAggregation.generate_id(),
                        dimension=dim_type,
                        dimensionValue=agg_result.dimension_value,
                        timeRangeStart=tr.start,
                        timeRangeEnd=tr.end,
                        totalCost=agg_result.total_cost,
                        totalInvocations=agg_result.total_invocations,
                        totalInputTokens=agg_result.total_input_tokens,
                        totalOutputTokens=agg_result.total_output_tokens,
                        computedAt=now,
                    )
                    results.append(record)

        self._stored.extend(results)
        return results

    def get_stored_aggregations(self) -> List[CostAggregation]:
        """Return all pre-computed CostAggregation records."""
        return list(self._stored)
