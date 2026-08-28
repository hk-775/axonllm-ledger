"""Amazon Quick dashboard provisioning through the AWS Quick Sight API.

AxonLLM Ledger distributes dashboard definitions as Quick asset bundles. This
module imports those bundles without embedding account-specific identifiers in
the project. Callers can override data sources, data sets, dashboards,
permissions, and tags at deployment time.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{12}$")
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_IMPORT_TERMINAL_STATUSES = frozenset(
    {
        "SUCCESSFUL",
        "FAILED",
        "FAILED_ROLLBACK_COMPLETED",
        "FAILED_ROLLBACK_ERROR",
    }
)
_EXPORT_TERMINAL_STATUSES = frozenset({"SUCCESSFUL", "FAILED"})


@runtime_checkable
class QuickSightClient(Protocol):
    """The subset of the boto3 Quick Sight client used by Ledger."""

    def start_asset_bundle_import_job(self, **kwargs: Any) -> dict[str, Any]:
        """Start an asset-bundle import job."""
        ...

    def describe_asset_bundle_import_job(self, **kwargs: Any) -> dict[str, Any]:
        """Describe an asset-bundle import job."""
        ...

    def start_asset_bundle_export_job(self, **kwargs: Any) -> dict[str, Any]:
        """Start an asset-bundle export job."""
        ...

    def describe_asset_bundle_export_job(self, **kwargs: Any) -> dict[str, Any]:
        """Describe an asset-bundle export job."""
        ...


@dataclass(frozen=True)
class QuickAssetBundleImportConfig:
    """Account-specific settings for one Quick asset-bundle import."""

    aws_account_id: str
    job_id: str
    failure_action: str = "ROLLBACK"
    override_parameters: Mapping[str, Any] = field(default_factory=dict)
    override_permissions: Mapping[str, Any] = field(default_factory=dict)
    override_tags: Mapping[str, Any] = field(default_factory=dict)
    strict_validation: bool = True

    def __post_init__(self) -> None:
        if not _ACCOUNT_ID_PATTERN.fullmatch(self.aws_account_id):
            raise ValueError("aws_account_id must be a 12-digit AWS account ID")
        if not _JOB_ID_PATTERN.fullmatch(self.job_id):
            raise ValueError(
                "job_id must contain only letters, numbers, underscores, or hyphens"
            )
        if self.failure_action not in {"ROLLBACK", "DO_NOTHING"}:
            raise ValueError("failure_action must be ROLLBACK or DO_NOTHING")


@dataclass(frozen=True)
class QuickAssetBundleImportStarted:
    """Response returned when Quick accepts an asset-bundle import."""

    arn: str
    job_id: str
    request_id: str | None
    http_status: int | None


@dataclass(frozen=True)
class QuickAssetBundleImportStatus:
    """Normalized status for a Quick asset-bundle import."""

    job_id: str
    status: str
    arn: str | None = None
    errors: tuple[dict[str, Any], ...] = ()
    rollback_errors: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()

    @property
    def is_terminal(self) -> bool:
        """Return whether the import has reached a terminal state."""
        return self.status in _IMPORT_TERMINAL_STATUSES

    @property
    def succeeded(self) -> bool:
        """Return whether the import completed successfully."""
        return self.status == "SUCCESSFUL"


@dataclass(frozen=True)
class QuickAssetBundleExportConfig:
    """Settings for a portable Quick asset-bundle export."""

    aws_account_id: str
    job_id: str
    resource_arns: tuple[str, ...]
    export_format: str = "QUICKSIGHT_JSON"
    include_all_dependencies: bool = True
    include_permissions: bool = False
    include_tags: bool = True
    strict_validation: bool = True

    def __post_init__(self) -> None:
        if not _ACCOUNT_ID_PATTERN.fullmatch(self.aws_account_id):
            raise ValueError("aws_account_id must be a 12-digit AWS account ID")
        if not _JOB_ID_PATTERN.fullmatch(self.job_id):
            raise ValueError(
                "job_id must contain only letters, numbers, underscores, or hyphens"
            )
        if not 1 <= len(self.resource_arns) <= 100:
            raise ValueError("resource_arns must contain between 1 and 100 ARNs")
        if not all(
            arn.startswith(("arn:aws:quicksight:", "arn:aws-us-gov:quicksight:"))
            for arn in self.resource_arns
        ):
            raise ValueError("resource_arns must contain Amazon Quick Sight ARNs")
        if self.export_format not in {
            "QUICKSIGHT_JSON",
            "CLOUDFORMATION_JSON",
        }:
            raise ValueError(
                "export_format must be QUICKSIGHT_JSON or CLOUDFORMATION_JSON"
            )


@dataclass(frozen=True)
class QuickAssetBundleExportStarted:
    """Response returned when Quick accepts an asset-bundle export."""

    arn: str
    job_id: str
    request_id: str | None
    http_status: int | None


@dataclass(frozen=True)
class QuickAssetBundleExportStatus:
    """Normalized status for a Quick asset-bundle export."""

    job_id: str
    status: str
    arn: str | None = None
    download_url: str | None = None
    resource_arns: tuple[str, ...] = ()
    errors: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()

    @property
    def is_terminal(self) -> bool:
        """Return whether the export has reached a terminal state."""
        return self.status in _EXPORT_TERMINAL_STATUSES

    @property
    def succeeded(self) -> bool:
        """Return whether the export completed successfully."""
        return self.status == "SUCCESSFUL"


class QuickDashboardProvisioner:
    """Import and monitor AxonLLM Ledger dashboards in Amazon Quick."""

    def __init__(
        self,
        client: QuickSightClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._sleep = sleep

    @classmethod
    def from_boto3(
        cls,
        *,
        region_name: str | None = None,
        profile_name: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> QuickDashboardProvisioner:
        """Create a provisioner using boto3's standard credential chain."""
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError(
                'Install the Quick integration with: pip install "axonllm-ledger[quick]"'
            ) from exc

        session_kwargs: dict[str, str] = {}
        if region_name is not None:
            session_kwargs["region_name"] = region_name
        if profile_name is not None:
            session_kwargs["profile_name"] = profile_name

        session = boto3.Session(**session_kwargs)
        client = session.client(
            "quicksight",
            config=Config(
                retries={"mode": "standard", "total_max_attempts": 3},
                connect_timeout=5,
                read_timeout=60,
            ),
        )
        return cls(client, sleep=sleep)

    def start_import_from_bytes(
        self,
        bundle: bytes,
        config: QuickAssetBundleImportConfig,
    ) -> QuickAssetBundleImportStarted:
        """Import a Quick asset bundle supplied as bytes."""
        if not bundle:
            raise ValueError("bundle must not be empty")
        return self._start_import({"Body": bundle}, config)

    def start_import_from_s3(
        self,
        s3_uri: str,
        config: QuickAssetBundleImportConfig,
    ) -> QuickAssetBundleImportStarted:
        """Import a Quick asset bundle from an S3 or HTTPS URI."""
        if not s3_uri.startswith(("s3://", "https://")):
            raise ValueError("s3_uri must start with s3:// or https://")
        return self._start_import({"S3Uri": s3_uri}, config)

    def describe_import(
        self,
        config: QuickAssetBundleImportConfig,
    ) -> QuickAssetBundleImportStatus:
        """Return the current state of an asset-bundle import."""
        response = self._client.describe_asset_bundle_import_job(
            AwsAccountId=config.aws_account_id,
            AssetBundleImportJobId=config.job_id,
        )
        return QuickAssetBundleImportStatus(
            job_id=response.get("AssetBundleImportJobId", config.job_id),
            status=response.get("JobStatus", ""),
            arn=response.get("Arn"),
            errors=tuple(dict(item) for item in response.get("Errors", [])),
            rollback_errors=tuple(
                dict(item) for item in response.get("RollbackErrors", [])
            ),
            warnings=tuple(dict(item) for item in response.get("Warnings", [])),
        )

    def wait_for_import(
        self,
        config: QuickAssetBundleImportConfig,
        *,
        delay_seconds: float = 5,
        max_attempts: int = 60,
    ) -> QuickAssetBundleImportStatus:
        """Poll until the import reaches a terminal state."""
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        for attempt in range(max_attempts):
            status = self.describe_import(config)
            if status.is_terminal:
                return status
            if attempt + 1 < max_attempts:
                self._sleep(delay_seconds)

        raise TimeoutError(
            f"Quick asset-bundle import {config.job_id} did not finish "
            f"after {max_attempts} status checks"
        )

    def start_export(
        self,
        config: QuickAssetBundleExportConfig,
    ) -> QuickAssetBundleExportStarted:
        """Start a portable dashboard asset-bundle export."""
        response = self._client.start_asset_bundle_export_job(
            AwsAccountId=config.aws_account_id,
            AssetBundleExportJobId=config.job_id,
            ResourceArns=list(config.resource_arns),
            IncludeAllDependencies=config.include_all_dependencies,
            ExportFormat=config.export_format,
            IncludePermissions=config.include_permissions,
            IncludeTags=config.include_tags,
            ValidationStrategy={
                "StrictModeForAllResources": config.strict_validation
            },
        )
        return QuickAssetBundleExportStarted(
            arn=response.get("Arn", ""),
            job_id=response.get("AssetBundleExportJobId", config.job_id),
            request_id=response.get("RequestId"),
            http_status=response.get("Status"),
        )

    def describe_export(
        self,
        config: QuickAssetBundleExportConfig,
    ) -> QuickAssetBundleExportStatus:
        """Return the current state of an asset-bundle export."""
        response = self._client.describe_asset_bundle_export_job(
            AwsAccountId=config.aws_account_id,
            AssetBundleExportJobId=config.job_id,
        )
        return QuickAssetBundleExportStatus(
            job_id=response.get("AssetBundleExportJobId", config.job_id),
            status=response.get("JobStatus", ""),
            arn=response.get("Arn"),
            download_url=response.get("DownloadUrl"),
            resource_arns=tuple(response.get("ResourceArns", [])),
            errors=tuple(dict(item) for item in response.get("Errors", [])),
            warnings=tuple(dict(item) for item in response.get("Warnings", [])),
        )

    def wait_for_export(
        self,
        config: QuickAssetBundleExportConfig,
        *,
        delay_seconds: float = 5,
        max_attempts: int = 60,
    ) -> QuickAssetBundleExportStatus:
        """Poll until the export reaches a terminal state."""
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        for attempt in range(max_attempts):
            status = self.describe_export(config)
            if status.is_terminal:
                return status
            if attempt + 1 < max_attempts:
                self._sleep(delay_seconds)

        raise TimeoutError(
            f"Quick asset-bundle export {config.job_id} did not finish "
            f"after {max_attempts} status checks"
        )

    def _start_import(
        self,
        source: dict[str, Any],
        config: QuickAssetBundleImportConfig,
    ) -> QuickAssetBundleImportStarted:
        request: dict[str, Any] = {
            "AwsAccountId": config.aws_account_id,
            "AssetBundleImportJobId": config.job_id,
            "AssetBundleImportSource": source,
            "FailureAction": config.failure_action,
            "OverrideValidationStrategy": {
                "StrictModeForAllResources": config.strict_validation
            },
        }
        if config.override_parameters:
            request["OverrideParameters"] = dict(config.override_parameters)
        if config.override_permissions:
            request["OverridePermissions"] = dict(config.override_permissions)
        if config.override_tags:
            request["OverrideTags"] = dict(config.override_tags)

        response = self._client.start_asset_bundle_import_job(**request)
        return QuickAssetBundleImportStarted(
            arn=response.get("Arn", ""),
            job_id=response.get("AssetBundleImportJobId", config.job_id),
            request_id=response.get("RequestId"),
            http_status=response.get("Status"),
        )
