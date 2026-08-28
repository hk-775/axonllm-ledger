"""Property-based tests for COH recommendation ingestion.

Feature: axonllm-ledger, Property 7: COH Ingestion Produces Complete Recommendation Records

Validates: Requirements 5.1, 5.2, 5.3
"""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.coh_ingestion import (
    GENAI_SERVICES,
    process_single_recommendation,
)
from axonllm_ledger.models import OptimizationRecommendation


# --- Strategies ---

_recommendation_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)

_account_ids = st.from_regex(r"[0-9]{12}", fullmatch=True)

_model_ids = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_./"),
        min_size=1,
        max_size=60,
    ),
)

_recommendation_types = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_ "),
    min_size=1,
    max_size=40,
)

_positive_decimals = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("9999999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

_descriptions = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Z"), whitelist_characters="-_.,:;"),
    min_size=1,
    max_size=100,
)

_genai_services = st.sampled_from(sorted(GENAI_SERVICES))

_non_genai_services = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda s: s not in GENAI_SERVICES)


@st.composite
def valid_genai_raw_recommendation(draw):
    """Generate a valid raw COH recommendation dict for a GenAI service."""
    return {
        "recommendation_id": draw(_recommendation_ids),
        "account_id": draw(_account_ids),
        "model_id": draw(_model_ids),
        "recommendation_type": draw(_recommendation_types),
        "estimated_savings": str(draw(_positive_decimals)),
        "description": draw(_descriptions),
        "service": draw(_genai_services),
    }


@st.composite
def non_genai_raw_recommendation(draw):
    """Generate a raw COH recommendation dict for a non-GenAI service."""
    return {
        "recommendation_id": draw(_recommendation_ids),
        "account_id": draw(_account_ids),
        "model_id": draw(_model_ids),
        "recommendation_type": draw(_recommendation_types),
        "estimated_savings": str(draw(_positive_decimals)),
        "description": draw(_descriptions),
        "service": draw(_non_genai_services),
    }


# --- Property Tests ---


class TestCOHIngestionCompleteness:
    """Property 7: COH Ingestion Produces Complete Recommendation Records.

    For any valid Cost_Optimization_Hub_Source data containing GenAI-relevant
    recommendations, ingestion should produce OptimizationRecommendation records
    with the correct account identifier, model identifier (where applicable),
    estimated savings amount, and recommendation type.

    **Validates: Requirements 5.1, 5.2, 5.3**
    """

    @settings(max_examples=100)
    @given(raw_rec=valid_genai_raw_recommendation())
    def test_genai_recommendation_produces_complete_record(self, raw_rec: dict):
        """For any valid GenAI COH data, process_single_recommendation produces an
        OptimizationRecommendation with correct account_id, model_id,
        estimated_savings, and recommendation_type.

        Feature: axonllm-ledger, Property 7: COH Ingestion Produces Complete Recommendation Records
        """
        # **Validates: Requirements 5.1, 5.2, 5.3**
        result = process_single_recommendation(raw_rec)

        assert result is not None, "Valid GenAI recommendation should produce a record"
        assert isinstance(result, OptimizationRecommendation)

        # Requirement 5.2: correct account identifier
        assert result.accountId == raw_rec["account_id"]

        # Requirement 5.2: correct model identifier (where applicable)
        if raw_rec["model_id"] is not None:
            assert result.modelId == raw_rec["model_id"]
        else:
            assert result.modelId is None

        # Requirement 5.3: correct estimated savings amount
        assert result.estimatedSavings == Decimal(raw_rec["estimated_savings"])

        # Requirement 5.3: correct recommendation type
        assert result.recommendationType == raw_rec["recommendation_type"]

        # Verify remaining fields are populated
        assert result.recommendationId == raw_rec["recommendation_id"]
        assert result.description == raw_rec["description"]
        assert result.ingestedAt is not None

    @settings(max_examples=100)
    @given(raw_rec=non_genai_raw_recommendation())
    def test_non_genai_recommendation_returns_none(self, raw_rec: dict):
        """For any non-GenAI COH data, process_single_recommendation returns None.

        Feature: axonllm-ledger, Property 7: COH Ingestion Produces Complete Recommendation Records
        """
        # **Validates: Requirements 5.1**
        result = process_single_recommendation(raw_rec)

        assert result is None, (
            f"Non-GenAI recommendation (service={raw_rec['service']!r}) "
            f"should be filtered out and return None"
        )
