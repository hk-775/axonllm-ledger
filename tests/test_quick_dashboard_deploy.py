"""Tests for AxonLLM Ledger dashboard deployment."""

from __future__ import annotations

import json
from typing import Any

from botocore.session import Session
from botocore.validate import ParamValidator
import pytest

from axonllm_ledger.quick_dashboard_definition import DATASET_IDENTIFIERS
from axonllm_ledger.quick_dashboard_deploy import (
    QuickDashboardDeployer,
    QuickDashboardDeploymentConfig,
    build_dashboard_request,
    build_data_set_request,
    build_data_source_request,
    build_quick_s3_bucket_policy,
    build_s3_manifest,
    render_quick_table_csv,
    validate_quick_tables,
)


def _config() -> QuickDashboardDeploymentConfig:
    return QuickDashboardDeploymentConfig(
        aws_account_id="123456789012",
        region_name="us-east-1",
        principal_arn=(
            "arn:aws:quicksight:us-east-1:123456789012:"
            "user/default/admin"
        ),
        bucket_name="axonllm-ledger-dashboard-123456789012-us-east-1",
        quick_role_arn=(
            "arn:aws:iam::123456789012:"
            "role/service-role/aws-quicksight-service-role-v0"
        ),
        poll_delay_seconds=0,
    )


def _tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "cost_aggregations": [
            {
                "period_start": "2026-03-01T00:00:00",
                "period_end": "2026-03-02T07:00:00",
                "dimension_type": "MODEL",
                "dimension_value": "anthropic.claude-3-sonnet",
                "total_cost_usd": "1.25",
                "total_invocations": 10,
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
            }
        ],
        "model_access": [
            {
                "period_start": "2026-03-01T00:00:00",
                "period_end": "2026-03-02T07:00:00",
                "user_id": "user-alice",
                "model_id": "anthropic.claude-3-sonnet",
            }
        ],
        "budgets": [
            {
                "budget_id": "budget-1",
                "budget_name": "AI budget",
                "account_id": "111111111111",
                "budget_limit_usd": "100.00",
                "forecasted_spend_usd": "90.00",
                "actual_spend_usd": "80.00",
                "period_start": "2026-03-01T00:00:00",
                "period_end": "2026-03-31T23:59:59",
                "is_exceeded": False,
                "ingested_at": "2026-03-02T08:00:00+00:00",
            }
        ],
        "optimization_recommendations": [
            {
                "recommendation_id": "rec-1",
                "account_id": "111111111111",
                "model_id": "anthropic.claude-3-sonnet",
                "recommendation_type": "RightsizeModel",
                "estimated_savings_usd": "12.50",
                "description": "Use a smaller model for routine workloads",
                "ingested_at": "2026-03-02T08:00:00+00:00",
            }
        ],
    }


def test_renders_csv_with_normalized_datetime_and_boolean() -> None:
    rendered = render_quick_table_csv("budgets", _tables()["budgets"])

    assert "2026-03-01 00:00:00" in rendered
    assert "2026-03-02 08:00:00" in rendered
    assert ",false," in rendered
    assert rendered.endswith("\n")


def test_builds_manifest_for_exact_data_object() -> None:
    manifest = build_s3_manifest("ledger-bucket", "prefix/data.csv")

    assert manifest["fileLocations"] == [
        {"URIs": ["s3://ledger-bucket/prefix/data.csv"]}
    ]
    assert manifest["globalUploadSettings"]["containsHeader"] == "true"


def test_builds_scoped_bucket_policy_for_account_quick_role() -> None:
    config = _config()
    policy = build_quick_s3_bucket_policy(config)

    assert policy["Statement"][0]["Principal"]["AWS"] == config.quick_role_arn
    assert policy["Statement"][1]["Resource"] == (
        "arn:aws:s3:::axonllm-ledger-dashboard-123456789012-us-east-1/"
        "axonllm-ledger/dashboard-v1/*"
    )


def test_create_requests_validate_against_current_botocore_model() -> None:
    config = _config()
    data_set_arns = {
        name: (
            "arn:aws:quicksight:us-east-1:123456789012:"
            f"dataset/axonllm-ledger-{name.replace('_', '-')}"
        )
        for name in DATASET_IDENTIFIERS
    }
    requests = {
        "CreateDataSource": build_data_source_request(
            "cost_aggregations",
            "axonllm-ledger/dashboard-v1/manifests/cost.json",
            config.quick_role_arn,
            config,
        ),
        "CreateDataSet": build_data_set_request(
            "cost_aggregations",
            (
                "arn:aws:quicksight:us-east-1:123456789012:"
                "datasource/axonllm-ledger-cost-aggregations"
            ),
            config,
        ),
        "CreateDashboard": build_dashboard_request(data_set_arns, config),
    }
    service = Session().get_service_model("quicksight")
    validator = ParamValidator()

    for operation_name, request in requests.items():
        operation = service.operation_model(operation_name)
        errors = validator.validate(request, operation.input_shape)
        assert not errors.has_errors(), errors.generate_report()


def test_s3_data_set_casts_physical_string_columns_in_logical_table() -> None:
    request = build_data_set_request(
        "cost_aggregations",
        (
            "arn:aws:quicksight:us-east-1:123456789012:"
            "datasource/axonllm-ledger-cost-aggregations"
        ),
        _config(),
    )

    columns = request["PhysicalTableMap"]["physical"]["S3Source"][
        "InputColumns"
    ]
    transforms = request["LogicalTableMap"]["logical"]["DataTransforms"]
    assert {column["Type"] for column in columns} == {"STRING"}
    assert {
        transform["CastColumnTypeOperation"]["ColumnName"]
        for transform in transforms
    } == {
        "period_start",
        "period_end",
        "total_cost_usd",
        "total_invocations",
        "total_input_tokens",
        "total_output_tokens",
    }


def test_rejects_incomplete_table_rows() -> None:
    tables = _tables()
    del tables["budgets"][0]["budget_id"]

    with pytest.raises(ValueError, match="budget_id"):
        validate_quick_tables(tables)


class _BucketAlreadyOwnedByYou(Exception):
    pass


class _ResourceNotFoundException(Exception):
    pass


class _FakeS3:
    class exceptions:
        BucketAlreadyOwnedByYou = _BucketAlreadyOwnedByYou

    def __init__(self) -> None:
        self.objects: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.bucket_policy: dict[str, Any] | None = None

    def create_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("create_bucket")
        return {}

    def put_public_access_block(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("put_public_access_block")
        return {}

    def put_bucket_encryption(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("put_bucket_encryption")
        return {}

    def put_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("put_bucket_versioning")
        return {}

    def put_bucket_tagging(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("put_bucket_tagging")
        return {}

    def put_bucket_policy(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("put_bucket_policy")
        self.bucket_policy = json.loads(kwargs["Policy"])
        return {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.objects.append(kwargs)
        return {}


class _FakeQuick:
    class exceptions:
        ResourceNotFoundException = _ResourceNotFoundException

    def __init__(self) -> None:
        self.created_data_sources: list[str] = []
        self.created_data_sets: list[str] = []
        self.created_ingestions: list[dict[str, Any]] = []
        self.dashboard_describe_calls = 0

    def describe_data_source(self, **kwargs: Any) -> dict[str, Any]:
        raise _ResourceNotFoundException

    def create_data_source(self, **kwargs: Any) -> dict[str, Any]:
        data_source_id = kwargs["DataSourceId"]
        self.created_data_sources.append(data_source_id)
        return {
            "Arn": (
                "arn:aws:quicksight:us-east-1:123456789012:"
                f"datasource/{data_source_id}"
            )
        }

    def update_data_source(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def update_data_source_permissions(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def describe_data_set(self, **kwargs: Any) -> dict[str, Any]:
        raise _ResourceNotFoundException

    def create_data_set(self, **kwargs: Any) -> dict[str, Any]:
        data_set_id = kwargs["DataSetId"]
        self.created_data_sets.append(data_set_id)
        return {
            "Arn": (
                "arn:aws:quicksight:us-east-1:123456789012:"
                f"dataset/{data_set_id}"
            ),
            "IngestionId": f"ingestion-{data_set_id}",
        }

    def update_data_set(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def update_data_set_permissions(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def describe_ingestion(self, **kwargs: Any) -> dict[str, Any]:
        return {"Ingestion": {"IngestionStatus": "COMPLETED"}}

    def create_ingestion(self, **kwargs: Any) -> dict[str, Any]:
        self.created_ingestions.append(kwargs)
        return {"IngestionId": kwargs["IngestionId"]}

    def describe_dashboard(self, **kwargs: Any) -> dict[str, Any]:
        self.dashboard_describe_calls += 1
        if self.dashboard_describe_calls == 1:
            raise _ResourceNotFoundException
        return {
            "Dashboard": {
                "Arn": (
                    "arn:aws:quicksight:us-east-1:123456789012:"
                    "dashboard/axonllm-ledger"
                ),
                "Version": {
                    "VersionNumber": 1,
                    "Status": "CREATION_SUCCESSFUL",
                },
            }
        }

    def create_dashboard(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "VersionArn": (
                "arn:aws:quicksight:us-east-1:123456789012:"
                "dashboard/axonllm-ledger/version/1"
            )
        }

    def update_dashboard(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def update_dashboard_permissions(self, **kwargs: Any) -> dict[str, Any]:
        return {}

    def update_dashboard_published_version(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {}


def test_deploys_all_resources_with_fake_clients() -> None:
    s3 = _FakeS3()
    quick = _FakeQuick()
    deployer = QuickDashboardDeployer(s3, quick, sleep=lambda _: None)

    result = deployer.deploy(_tables(), _config())

    assert len(s3.objects) == 8
    assert len(quick.created_data_sources) == 4
    assert len(quick.created_data_sets) == 4
    assert result.dashboard_id == "axonllm-ledger"
    assert result.dashboard_url.endswith("/sn/dashboards/axonllm-ledger")
    assert result.role_arn == _config().quick_role_arn
    assert s3.bucket_policy is not None


def test_refreshes_spice_without_recreating_dashboard_resources() -> None:
    s3 = _FakeS3()
    quick = _FakeQuick()
    deployer = QuickDashboardDeployer(s3, quick, sleep=lambda _: None)

    result = deployer.refresh_data(_tables(), _config())

    assert len(s3.objects) == 8
    assert len(quick.created_ingestions) == 4
    assert quick.created_data_sources == []
    assert quick.created_data_sets == []
    assert quick.dashboard_describe_calls == 0
    assert set(result.ingestion_ids) == set(DATASET_IDENTIFIERS)
