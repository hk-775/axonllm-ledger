"""Property-based tests for budget ingestion and threshold flagging.

Feature: axonllm-ledger, Property 5: Budget Ingestion Produces Complete Records with Threshold Flagging

Validates: Requirements 3.1, 3.2, 3.3
"""

from datetime import datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.budget_ingestion import (
    check_threshold_exceeded,
    process_single_budget,
)
from axonllm_ledger.models import ProcessedBudget


# --- Strategies ---

_budget_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)

_budget_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=" -_"),
    min_size=1,
    max_size=50,
)

_account_ids = st.from_regex(r"[0-9]{12}", fullmatch=True)

_positive_decimals = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("9999999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

_non_negative_decimals = st.decimals(
    min_value=Decimal("0.00"),
    max_value=Decimal("9999999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

_iso_timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
).map(lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ"))


@st.composite
def valid_raw_budget(draw):
    """Generate a valid raw budget dict with all required fields."""
    return {
        "budget_id": draw(_budget_ids),
        "budget_name": draw(_budget_names),
        "account_id": draw(_account_ids),
        "budget_limit": str(draw(_positive_decimals)),
        "forecasted_spend": str(draw(_non_negative_decimals)),
        "actual_spend": str(draw(_non_negative_decimals)),
        "period_start": draw(_iso_timestamps),
        "period_end": draw(_iso_timestamps),
    }


# --- Property Tests ---


class TestBudgetIngestionCompleteness:
    """Property 5: Budget Ingestion Produces Complete Records with Threshold Flagging.

    For any valid Budgets_Source data, ingestion should produce a ProcessedBudget
    record containing the budget definition, threshold, forecasted spend, actual
    spend, and the correct associated account identifier. For any ProcessedBudget
    where actualSpend exceeds budgetLimit, the isExceeded flag should be true;
    otherwise it should be false.

    **Validates: Requirements 3.1, 3.2, 3.3**
    """

    @settings(max_examples=100)
    @given(raw_budget=valid_raw_budget())
    def test_valid_budget_produces_complete_record(self, raw_budget: dict):
        """For any valid budget data, process_single_budget produces a ProcessedBudget
        with correct field values matching the input.

        Feature: axonllm-ledger, Property 5: Budget Ingestion Produces Complete Records with Threshold Flagging
        """
        # **Validates: Requirements 3.1, 3.2**
        result = process_single_budget(raw_budget)

        assert result is not None, "Valid budget data should produce a ProcessedBudget"
        assert isinstance(result, ProcessedBudget)

        # Verify budget definition fields match input
        assert result.budgetId == raw_budget["budget_id"]
        assert result.budgetName == raw_budget["budget_name"]

        # Verify account association (Requirement 3.2)
        assert result.accountId == raw_budget["account_id"]

        # Verify budget limit / threshold
        assert result.budgetLimit == Decimal(raw_budget["budget_limit"])

        # Verify forecasted spend
        assert result.forecastedSpend == Decimal(raw_budget["forecasted_spend"])

        # Verify actual spend
        assert result.actualSpend == Decimal(raw_budget["actual_spend"])

        # Verify period timestamps are parsed
        assert result.periodStart is not None
        assert result.periodEnd is not None

        # Verify ingestedAt is set
        assert result.ingestedAt is not None

    @settings(max_examples=100)
    @given(raw_budget=valid_raw_budget())
    def test_threshold_flagging_correctness(self, raw_budget: dict):
        """For any ProcessedBudget where actualSpend > budgetLimit, isExceeded is True;
        otherwise False.

        Feature: axonllm-ledger, Property 5: Budget Ingestion Produces Complete Records with Threshold Flagging
        """
        # **Validates: Requirements 3.3**
        result = process_single_budget(raw_budget)
        assert result is not None

        actual_spend = Decimal(raw_budget["actual_spend"])
        budget_limit = Decimal(raw_budget["budget_limit"])

        # Verify isExceeded flag on the ProcessedBudget
        if actual_spend > budget_limit:
            assert result.isExceeded is True, (
                f"isExceeded should be True when actualSpend ({actual_spend}) "
                f"> budgetLimit ({budget_limit})"
            )
        else:
            assert result.isExceeded is False, (
                f"isExceeded should be False when actualSpend ({actual_spend}) "
                f"<= budgetLimit ({budget_limit})"
            )

        # Also verify check_threshold_exceeded agrees with the flag
        assert check_threshold_exceeded(result) == result.isExceeded
