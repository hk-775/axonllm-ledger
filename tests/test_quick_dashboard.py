"""Tests for Amazon Quick dashboard asset-bundle provisioning."""

from __future__ import annotations

from typing import Any

import pytest

from axonllm_ledger.quick_dashboard import (
    QuickAssetBundleImportConfig,
    QuickDashboardProvisioner,
)


class _FakeQuickSightClient:
    def __init__(self, statuses: list[dict[str, Any]] | None = None) -> None:
        self.start_requests: list[dict[str, Any]] = []
        self.describe_requests: list[dict[str, Any]] = []
        self._statuses = list(statuses or [])

    def start_asset_bundle_import_job(self, **kwargs: Any) -> dict[str, Any]:
        self.start_requests.append(kwargs)
        return {
            "Arn": "arn:aws:quicksight:us-east-1:123456789012:asset-bundle-import-job/job-1",
            "AssetBundleImportJobId": kwargs["AssetBundleImportJobId"],
            "RequestId": "request-1",
            "Status": 202,
        }

    def describe_asset_bundle_import_job(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_requests.append(kwargs)
        if not self._statuses:
            raise AssertionError("No fake status response configured")
        return self._statuses.pop(0)


def _config(**overrides: Any) -> QuickAssetBundleImportConfig:
    values: dict[str, Any] = {
        "aws_account_id": "123456789012",
        "job_id": "axonllm-ledger-dashboard-v1",
    }
    values.update(overrides)
    return QuickAssetBundleImportConfig(**values)


class TestQuickAssetBundleImportConfig:
    def test_rejects_invalid_account_id(self):
        with pytest.raises(ValueError, match="12-digit"):
            _config(aws_account_id="1234")

    def test_rejects_invalid_job_id(self):
        with pytest.raises(ValueError, match="job_id"):
            _config(job_id="invalid job id")

    def test_rejects_invalid_failure_action(self):
        with pytest.raises(ValueError, match="failure_action"):
            _config(failure_action="DELETE")


class TestStartImport:
    def test_starts_import_from_bytes_with_safe_defaults(self):
        client = _FakeQuickSightClient()
        provisioner = QuickDashboardProvisioner(client)

        result = provisioner.start_import_from_bytes(b"bundle", _config())

        assert result.job_id == "axonllm-ledger-dashboard-v1"
        assert result.http_status == 202
        request = client.start_requests[0]
        assert request["AwsAccountId"] == "123456789012"
        assert request["AssetBundleImportSource"] == {"Body": b"bundle"}
        assert request["FailureAction"] == "ROLLBACK"
        assert request["OverrideValidationStrategy"] == {
            "StrictModeForAllResources": True
        }

    def test_passes_asset_override_configuration(self):
        client = _FakeQuickSightClient()
        provisioner = QuickDashboardProvisioner(client)
        config = _config(
            override_parameters={
                "ResourceIdOverrideConfiguration": {
                    "PrefixForAllResources": "axonllm-ledger-"
                },
                "Dashboards": [
                    {
                        "DashboardId": "ledger-overview",
                        "Name": "AxonLLM Ledger",
                    }
                ],
            },
            override_permissions={"DataSources": []},
            override_tags={"Dashboards": []},
        )

        provisioner.start_import_from_bytes(b"bundle", config)

        request = client.start_requests[0]
        assert request["OverrideParameters"] == config.override_parameters
        assert request["OverridePermissions"] == config.override_permissions
        assert request["OverrideTags"] == config.override_tags

    def test_starts_import_from_s3(self):
        client = _FakeQuickSightClient()
        provisioner = QuickDashboardProvisioner(client)

        provisioner.start_import_from_s3(
            "s3://dashboard-artifacts/axonllm-ledger.qs",
            _config(),
        )

        assert client.start_requests[0]["AssetBundleImportSource"] == {
            "S3Uri": "s3://dashboard-artifacts/axonllm-ledger.qs"
        }

    def test_rejects_empty_bundle(self):
        provisioner = QuickDashboardProvisioner(_FakeQuickSightClient())
        with pytest.raises(ValueError, match="must not be empty"):
            provisioner.start_import_from_bytes(b"", _config())

    def test_rejects_unsupported_uri(self):
        provisioner = QuickDashboardProvisioner(_FakeQuickSightClient())
        with pytest.raises(ValueError, match="s3://"):
            provisioner.start_import_from_s3("file:///tmp/dashboard.qs", _config())


class TestDescribeAndWait:
    def test_describe_normalizes_errors_and_warnings(self):
        client = _FakeQuickSightClient(
            [
                {
                    "AssetBundleImportJobId": "axonllm-ledger-dashboard-v1",
                    "JobStatus": "FAILED_ROLLBACK_COMPLETED",
                    "Arn": "arn:job",
                    "Errors": [{"Message": "invalid data source"}],
                    "RollbackErrors": [],
                    "Warnings": [{"Message": "unused override"}],
                }
            ]
        )
        status = QuickDashboardProvisioner(client).describe_import(_config())

        assert status.is_terminal
        assert not status.succeeded
        assert status.errors == ({"Message": "invalid data source"},)
        assert status.warnings == ({"Message": "unused override"},)

    def test_waits_until_import_succeeds(self):
        client = _FakeQuickSightClient(
            [
                {"JobStatus": "QUEUED_FOR_IMMEDIATE_EXECUTION"},
                {"JobStatus": "IN_PROGRESS"},
                {
                    "JobStatus": "SUCCESSFUL",
                    "AssetBundleImportJobId": "axonllm-ledger-dashboard-v1",
                },
            ]
        )
        delays: list[float] = []
        provisioner = QuickDashboardProvisioner(client, sleep=delays.append)

        status = provisioner.wait_for_import(
            _config(),
            delay_seconds=2,
            max_attempts=3,
        )

        assert status.succeeded
        assert delays == [2, 2]
        assert len(client.describe_requests) == 3

    def test_times_out_after_max_attempts(self):
        client = _FakeQuickSightClient(
            [{"JobStatus": "IN_PROGRESS"}, {"JobStatus": "IN_PROGRESS"}]
        )
        provisioner = QuickDashboardProvisioner(client, sleep=lambda _: None)

        with pytest.raises(TimeoutError, match="did not finish"):
            provisioner.wait_for_import(_config(), delay_seconds=0, max_attempts=2)

    def test_validates_wait_configuration(self):
        provisioner = QuickDashboardProvisioner(_FakeQuickSightClient())
        with pytest.raises(ValueError, match="non-negative"):
            provisioner.wait_for_import(_config(), delay_seconds=-1)
        with pytest.raises(ValueError, match="at least 1"):
            provisioner.wait_for_import(_config(), max_attempts=0)
