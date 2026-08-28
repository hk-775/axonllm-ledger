"""Unit tests for the Cost Optimization Hub Ingestion Service.

Covers:
- Valid recommendation processing
- GenAI filtering (non-GenAI recommendations filtered out)
- Missing required fields
- Nullable model_id
- Account/model association
- Ingestion logging
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from axonllm_ledger.coh_ingestion import (
    associate_with_account,
    associate_with_model,
    ingest_coh,
    process_recommendations,
    process_single_recommendation,
)
from axonllm_ledger.models import (
    IngestionStatus,
    OptimizationRecommendation,
)


def _make_raw_rec(**overrides) -> dict:
    """Build a valid raw COH recommendation dict with sensible defaults."""
    defaults = {
        "recommendation_id": "rec-001",
        "account_id": "123456789012",
        "model_id": "anthropic.claude-v2",
        "recommendation_type": "rightsizing",
        "estimated_savings": "150.00",
        "description": "Consider reserved capacity for this model",
        "service": "AmazonBedrock",
    }
    defaults.update(overrides)
    return defaults


class TestProcessSingleRecommendation:
    def test_valid_bedrock_recommendation(self):
        raw = _make_raw_rec()
        result = process_single_recommendation(raw)

        assert isinstance(result, OptimizationRecommendation)
        assert result.recommendationId == "rec-001"
        assert result.accountId == "123456789012"
        assert result.modelId == "anthropic.claude-v2"
        assert result.recommendationType == "rightsizing"
        assert result.estimatedSavings == Decimal("150.00")
        assert result.description == "Consider reserved capacity for this model"
        assert result.ingestedAt is not None

    def test_valid_sagemaker_recommendation(self):
        raw = _make_raw_rec(service="AmazonSageMaker", model_id="my-endpoint")
        result = process_single_recommendation(raw)

        assert result is not None
        assert result.modelId == "my-endpoint"

    def test_nullable_model_id(self):
        raw = _make_raw_rec(model_id=None)
        result = process_single_recommendation(raw)

        assert result is not None
        assert result.modelId is None

    def test_empty_string_model_id_treated_as_none(self):
        raw = _make_raw_rec(model_id="")
        result = process_single_recommendation(raw)

        assert result is not None
        assert result.modelId is None


class TestGenAIFiltering:
    def test_non_genai_service_filtered_out(self):
        raw = _make_raw_rec(service="AmazonEC2")
        result = process_single_recommendation(raw)
        assert result is None

    def test_empty_service_filtered_out(self):
        raw = _make_raw_rec(service="")
        result = process_single_recommendation(raw)
        assert result is None

    def test_missing_service_key_filtered_out(self):
        raw = _make_raw_rec()
        del raw["service"]
        result = process_single_recommendation(raw)
        assert result is None

    def test_bedrock_passes_filter(self):
        raw = _make_raw_rec(service="AmazonBedrock")
        assert process_single_recommendation(raw) is not None

    def test_sagemaker_passes_filter(self):
        raw = _make_raw_rec(service="AmazonSageMaker")
        assert process_single_recommendation(raw) is not None

    def test_case_sensitive_service_filter(self):
        raw = _make_raw_rec(service="amazonbedrock")
        assert process_single_recommendation(raw) is None


class TestMissingRequiredFields:
    def test_missing_recommendation_id(self, caplog):
        raw = _make_raw_rec(recommendation_id="")
        with caplog.at_level(logging.WARNING):
            result = process_single_recommendation(raw)
        assert result is None
        assert "recommendation_id" in caplog.text

    def test_missing_account_id(self, caplog):
        raw = _make_raw_rec(account_id="")
        with caplog.at_level(logging.WARNING):
            result = process_single_recommendation(raw)
        assert result is None
        assert "account_id" in caplog.text

    def test_missing_recommendation_type(self, caplog):
        raw = _make_raw_rec(recommendation_type="")
        with caplog.at_level(logging.WARNING):
            result = process_single_recommendation(raw)
        assert result is None
        assert "recommendation_type" in caplog.text

    def test_missing_estimated_savings(self, caplog):
        raw = _make_raw_rec()
        del raw["estimated_savings"]
        with caplog.at_level(logging.WARNING):
            result = process_single_recommendation(raw)
        assert result is None
        assert "estimated_savings" in caplog.text

    def test_empty_estimated_savings(self, caplog):
        raw = _make_raw_rec(estimated_savings="")
        with caplog.at_level(logging.WARNING):
            result = process_single_recommendation(raw)
        assert result is None
        assert "estimated_savings" in caplog.text

    def test_invalid_estimated_savings(self, caplog):
        raw = _make_raw_rec(estimated_savings="not-a-number")
        with caplog.at_level(logging.WARNING):
            result = process_single_recommendation(raw)
        assert result is None
        assert "invalid estimated_savings" in caplog.text

    def test_missing_description(self, caplog):
        raw = _make_raw_rec(description="")
        with caplog.at_level(logging.WARNING):
            result = process_single_recommendation(raw)
        assert result is None
        assert "description" in caplog.text


class TestAssociations:
    def test_associate_with_account(self):
        raw = _make_raw_rec()
        rec = process_single_recommendation(raw)
        assert rec is not None
        associate_with_account(rec, "new-account-999")
        assert rec.accountId == "new-account-999"

    def test_associate_with_model(self):
        raw = _make_raw_rec(model_id=None)
        rec = process_single_recommendation(raw)
        assert rec is not None
        assert rec.modelId is None
        associate_with_model(rec, "new-model-id")
        assert rec.modelId == "new-model-id"


class TestProcessRecommendations:
    def test_processes_multiple_valid_recommendations(self):
        raw_data = [
            _make_raw_rec(recommendation_id="r1"),
            _make_raw_rec(recommendation_id="r2", service="AmazonSageMaker"),
            _make_raw_rec(recommendation_id="r3"),
        ]
        results = process_recommendations(raw_data)
        assert len(results) == 3
        assert results[0].recommendationId == "r1"
        assert results[1].recommendationId == "r2"
        assert results[2].recommendationId == "r3"

    def test_filters_non_genai_recommendations(self):
        raw_data = [
            _make_raw_rec(recommendation_id="r1", service="AmazonBedrock"),
            _make_raw_rec(recommendation_id="r2", service="AmazonEC2"),
            _make_raw_rec(recommendation_id="r3", service="AmazonRDS"),
            _make_raw_rec(recommendation_id="r4", service="AmazonSageMaker"),
        ]
        results = process_recommendations(raw_data)
        assert len(results) == 2
        assert {r.recommendationId for r in results} == {"r1", "r4"}

    def test_skips_invalid_records(self):
        raw_data = [
            _make_raw_rec(recommendation_id="r1"),
            _make_raw_rec(recommendation_id=""),  # invalid
            _make_raw_rec(recommendation_id="r3"),
        ]
        results = process_recommendations(raw_data)
        assert len(results) == 2

    def test_empty_list(self):
        assert process_recommendations([]) == []


class TestIngestCoh:
    def test_successful_ingestion(self):
        raw_data = [
            _make_raw_rec(recommendation_id="r1"),
            _make_raw_rec(recommendation_id="r2"),
        ]
        recs, log = ingest_coh(raw_data, s3_prefix="s3://bucket/cid/coh/")

        assert len(recs) == 2
        assert log.source == "COH"
        assert log.s3Key == "s3://bucket/cid/coh/"
        assert log.recordCount == 2
        assert log.skippedCount == 0
        assert log.status == IngestionStatus.SUCCESS

    def test_partial_ingestion_with_non_genai(self):
        raw_data = [
            _make_raw_rec(recommendation_id="r1", service="AmazonBedrock"),
            _make_raw_rec(recommendation_id="r2", service="AmazonEC2"),
        ]
        recs, log = ingest_coh(raw_data)

        assert len(recs) == 1
        assert log.recordCount == 1
        assert log.skippedCount == 1
        assert log.status == IngestionStatus.PARTIAL

    def test_all_non_genai_produces_failed_status(self):
        raw_data = [
            _make_raw_rec(service="AmazonEC2"),
            _make_raw_rec(service="AmazonRDS"),
        ]
        recs, log = ingest_coh(raw_data)

        assert len(recs) == 0
        assert log.recordCount == 0
        assert log.skippedCount == 2
        assert log.status == IngestionStatus.FAILED

    def test_empty_input_produces_success(self):
        recs, log = ingest_coh([])

        assert len(recs) == 0
        assert log.status == IngestionStatus.SUCCESS
        assert log.recordCount == 0
        assert log.skippedCount == 0

    def test_log_has_valid_id_and_timestamps(self):
        recs, log = ingest_coh([_make_raw_rec()])

        uuid.UUID(log.logId)  # validates UUID format
        assert log.startedAt is not None
        assert log.completedAt is not None
        assert log.completedAt >= log.startedAt
