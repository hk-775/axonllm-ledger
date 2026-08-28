"""Deploy the AxonLLM Ledger dashboard and its Amazon Quick data layer."""

from __future__ import annotations

import csv
import io
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from axonllm_ledger.quick_dashboard_definition import (
    DATASET_IDENTIFIERS,
    build_dashboard_definition,
)

_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{12}$")
_RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")

_DATA_SOURCE_ACTIONS = (
    "quicksight:DescribeDataSource",
    "quicksight:DescribeDataSourcePermissions",
    "quicksight:PassDataSource",
    "quicksight:UpdateDataSource",
    "quicksight:DeleteDataSource",
    "quicksight:UpdateDataSourcePermissions",
)
_DATA_SET_ACTIONS = (
    "quicksight:DescribeDataSet",
    "quicksight:DescribeDataSetPermissions",
    "quicksight:PassDataSet",
    "quicksight:DescribeIngestion",
    "quicksight:ListIngestions",
    "quicksight:UpdateDataSet",
    "quicksight:DeleteDataSet",
    "quicksight:CreateIngestion",
    "quicksight:CancelIngestion",
    "quicksight:UpdateDataSetPermissions",
)
_DASHBOARD_ACTIONS = (
    "quicksight:DescribeDashboard",
    "quicksight:ListDashboardVersions",
    "quicksight:UpdateDashboardPermissions",
    "quicksight:QueryDashboard",
    "quicksight:UpdateDashboard",
    "quicksight:DeleteDashboard",
    "quicksight:DescribeDashboardPermissions",
    "quicksight:UpdateDashboardPublishedVersion",
)

_SUCCESSFUL_DASHBOARD_STATUSES = frozenset(
    {"CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"}
)
_FAILED_DASHBOARD_STATUSES = frozenset({"CREATION_FAILED", "UPDATE_FAILED"})
_TERMINAL_INGESTION_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


@dataclass(frozen=True)
class QuickColumn:
    """One column in an Amazon Quick S3 data set."""

    name: str
    data_type: str


QUICK_TABLE_SCHEMAS: Mapping[str, tuple[QuickColumn, ...]] = {
    "cost_aggregations": (
        QuickColumn("period_start", "DATETIME"),
        QuickColumn("period_end", "DATETIME"),
        QuickColumn("dimension_type", "STRING"),
        QuickColumn("dimension_value", "STRING"),
        QuickColumn("total_cost_usd", "DECIMAL"),
        QuickColumn("total_invocations", "INTEGER"),
        QuickColumn("total_input_tokens", "INTEGER"),
        QuickColumn("total_output_tokens", "INTEGER"),
    ),
    "model_access": (
        QuickColumn("period_start", "DATETIME"),
        QuickColumn("period_end", "DATETIME"),
        QuickColumn("user_id", "STRING"),
        QuickColumn("model_id", "STRING"),
    ),
    "budgets": (
        QuickColumn("budget_id", "STRING"),
        QuickColumn("budget_name", "STRING"),
        QuickColumn("account_id", "STRING"),
        QuickColumn("budget_limit_usd", "DECIMAL"),
        QuickColumn("forecasted_spend_usd", "DECIMAL"),
        QuickColumn("actual_spend_usd", "DECIMAL"),
        QuickColumn("period_start", "DATETIME"),
        QuickColumn("period_end", "DATETIME"),
        QuickColumn("is_exceeded", "BOOLEAN"),
        QuickColumn("ingested_at", "DATETIME"),
    ),
    "optimization_recommendations": (
        QuickColumn("recommendation_id", "STRING"),
        QuickColumn("account_id", "STRING"),
        QuickColumn("model_id", "STRING"),
        QuickColumn("recommendation_type", "STRING"),
        QuickColumn("estimated_savings_usd", "DECIMAL"),
        QuickColumn("description", "STRING"),
        QuickColumn("ingested_at", "DATETIME"),
    ),
}


@runtime_checkable
class S3DeploymentClient(Protocol):
    """S3 operations required by the dashboard deployer."""

    exceptions: Any

    def create_bucket(self, **kwargs: Any) -> dict[str, Any]:
        """Create the dashboard data bucket."""
        ...

    def put_public_access_block(self, **kwargs: Any) -> dict[str, Any]:
        """Block public access to the dashboard data bucket."""
        ...

    def put_bucket_encryption(self, **kwargs: Any) -> dict[str, Any]:
        """Enable default bucket encryption."""
        ...

    def put_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]:
        """Enable object versioning."""
        ...

    def put_bucket_tagging(self, **kwargs: Any) -> dict[str, Any]:
        """Tag the dashboard data bucket."""
        ...

    def put_bucket_policy(self, **kwargs: Any) -> dict[str, Any]:
        """Grant the account Quick role access to the dashboard prefix."""
        ...

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        """Upload a dashboard data or manifest object."""
        ...


@runtime_checkable
class QuickDeploymentClient(Protocol):
    """Quick Sight operations required by the dashboard deployer."""

    exceptions: Any

    def describe_data_source(self, **kwargs: Any) -> dict[str, Any]:
        """Describe a data source."""
        ...

    def create_data_source(self, **kwargs: Any) -> dict[str, Any]:
        """Create a data source."""
        ...

    def update_data_source(self, **kwargs: Any) -> dict[str, Any]:
        """Update a data source."""
        ...

    def update_data_source_permissions(self, **kwargs: Any) -> dict[str, Any]:
        """Grant data source permissions."""
        ...

    def describe_data_set(self, **kwargs: Any) -> dict[str, Any]:
        """Describe a data set."""
        ...

    def create_data_set(self, **kwargs: Any) -> dict[str, Any]:
        """Create a data set."""
        ...

    def update_data_set(self, **kwargs: Any) -> dict[str, Any]:
        """Update a data set."""
        ...

    def update_data_set_permissions(self, **kwargs: Any) -> dict[str, Any]:
        """Grant data set permissions."""
        ...

    def describe_ingestion(self, **kwargs: Any) -> dict[str, Any]:
        """Describe a SPICE ingestion."""
        ...

    def describe_dashboard(self, **kwargs: Any) -> dict[str, Any]:
        """Describe the dashboard."""
        ...

    def create_dashboard(self, **kwargs: Any) -> dict[str, Any]:
        """Create the dashboard."""
        ...

    def update_dashboard(self, **kwargs: Any) -> dict[str, Any]:
        """Update the dashboard."""
        ...

    def update_dashboard_permissions(self, **kwargs: Any) -> dict[str, Any]:
        """Grant dashboard permissions."""
        ...

    def update_dashboard_published_version(self, **kwargs: Any) -> dict[str, Any]:
        """Publish an updated dashboard version."""
        ...


@dataclass(frozen=True)
class QuickDashboardDeploymentConfig:
    """Account-specific settings for a dashboard deployment."""

    aws_account_id: str
    region_name: str
    principal_arn: str
    bucket_name: str
    quick_role_arn: str
    prefix: str = "axonllm-ledger/dashboard-v1"
    dashboard_id: str = "axonllm-ledger"
    dashboard_name: str = "AxonLLM Ledger"
    poll_delay_seconds: float = 5
    max_poll_attempts: int = 120

    def __post_init__(self) -> None:
        if not _ACCOUNT_ID_PATTERN.fullmatch(self.aws_account_id):
            raise ValueError("aws_account_id must be a 12-digit AWS account ID")
        if not self.region_name:
            raise ValueError("region_name must not be empty")
        if not self.principal_arn.startswith("arn:aws:quicksight:"):
            raise ValueError("principal_arn must be an Amazon Quick Sight ARN")
        if not self.quick_role_arn.startswith(
            f"arn:aws:iam::{self.aws_account_id}:role/"
        ):
            raise ValueError(
                "quick_role_arn must be an IAM role ARN in aws_account_id"
            )
        if not _BUCKET_PATTERN.fullmatch(self.bucket_name):
            raise ValueError("bucket_name must be a valid S3 bucket name")
        if not self.prefix or self.prefix.startswith("/") or self.prefix.endswith("/"):
            raise ValueError("prefix must be a non-empty S3 key prefix without edge slashes")
        if not _RESOURCE_ID_PATTERN.fullmatch(self.dashboard_id):
            raise ValueError("dashboard_id contains unsupported characters")
        if self.poll_delay_seconds < 0:
            raise ValueError("poll_delay_seconds must be non-negative")
        if self.max_poll_attempts < 1:
            raise ValueError("max_poll_attempts must be at least 1")


@dataclass(frozen=True)
class QuickDashboardDeploymentResult:
    """Resources created or updated by a dashboard deployment."""

    dashboard_arn: str
    version_arn: str
    dashboard_id: str
    bucket_name: str
    role_arn: str
    data_source_arns: Mapping[str, str]
    data_set_arns: Mapping[str, str]
    dashboard_url: str


class QuickDashboardDeployer:
    """Provision the S3, SPICE, and dashboard resources."""

    def __init__(
        self,
        s3_client: S3DeploymentClient,
        quick_client: QuickDeploymentClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._s3 = s3_client
        self._quick = quick_client
        self._sleep = sleep

    @classmethod
    def from_boto3(
        cls,
        *,
        region_name: str,
        profile_name: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> QuickDashboardDeployer:
        """Create a deployer using boto3's standard credential chain."""
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError(
                'Install the Quick integration with: pip install "axonllm-ledger[quick]"'
            ) from exc

        session_kwargs: dict[str, str] = {"region_name": region_name}
        if profile_name is not None:
            session_kwargs["profile_name"] = profile_name
        session = boto3.Session(**session_kwargs)
        client_config = Config(
            retries={"mode": "standard", "total_max_attempts": 4},
            connect_timeout=5,
            read_timeout=90,
        )
        return cls(
            session.client("s3", config=client_config),
            session.client("quicksight", config=client_config),
            sleep=sleep,
        )

    def deploy(
        self,
        tables: Mapping[str, Sequence[Mapping[str, Any]]],
        config: QuickDashboardDeploymentConfig,
    ) -> QuickDashboardDeploymentResult:
        """Create or update all resources for the AxonLLM Ledger dashboard."""
        validate_quick_tables(tables)
        self._ensure_bucket(config)
        self._ensure_bucket_policy(config)

        manifest_keys: dict[str, str] = {}
        for table_name in DATASET_IDENTIFIERS:
            manifest_keys[table_name] = self._upload_table(
                table_name,
                tables[table_name],
                config,
            )

        data_source_arns: dict[str, str] = {}
        data_set_arns: dict[str, str] = {}
        for table_name in DATASET_IDENTIFIERS:
            data_source_arn = self._ensure_data_source(
                table_name,
                manifest_keys[table_name],
                config.quick_role_arn,
                config,
            )
            data_source_arns[table_name] = data_source_arn
            data_set_arns[table_name] = self._ensure_data_set(
                table_name,
                data_source_arn,
                config,
            )

        dashboard = self._ensure_dashboard(data_set_arns, config)
        return QuickDashboardDeploymentResult(
            dashboard_arn=dashboard["Arn"],
            version_arn=dashboard["VersionArn"],
            dashboard_id=config.dashboard_id,
            bucket_name=config.bucket_name,
            role_arn=config.quick_role_arn,
            data_source_arns=dict(data_source_arns),
            data_set_arns=dict(data_set_arns),
            dashboard_url=(
                f"https://{config.region_name}.quicksight.aws.amazon.com/"
                f"sn/dashboards/{config.dashboard_id}"
            ),
        )

    def _ensure_bucket(self, config: QuickDashboardDeploymentConfig) -> None:
        create_request: dict[str, Any] = {"Bucket": config.bucket_name}
        if config.region_name != "us-east-1":
            create_request["CreateBucketConfiguration"] = {
                "LocationConstraint": config.region_name
            }
        try:
            self._s3.create_bucket(**create_request)
        except self._s3.exceptions.BucketAlreadyOwnedByYou:
            pass

        self._s3.put_public_access_block(
            Bucket=config.bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        self._s3.put_bucket_encryption(
            Bucket=config.bucket_name,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        },
                        "BucketKeyEnabled": False,
                    }
                ]
            },
        )
        self._s3.put_bucket_versioning(
            Bucket=config.bucket_name,
            VersioningConfiguration={"Status": "Enabled"},
        )
        self._s3.put_bucket_tagging(
            Bucket=config.bucket_name,
            Tagging={
                "TagSet": [
                    {"Key": "ManagedBy", "Value": "axonllm-ledger"},
                    {"Key": "Purpose", "Value": "quick-dashboard"},
                ]
            },
        )

    def _ensure_bucket_policy(
        self,
        config: QuickDashboardDeploymentConfig,
    ) -> None:
        self._s3.put_bucket_policy(
            Bucket=config.bucket_name,
            Policy=json.dumps(build_quick_s3_bucket_policy(config)),
        )

    def _upload_table(
        self,
        table_name: str,
        rows: Sequence[Mapping[str, Any]],
        config: QuickDashboardDeploymentConfig,
    ) -> str:
        data_key = f"{config.prefix}/data/{table_name}.csv"
        manifest_key = f"{config.prefix}/manifests/{table_name}.json"
        csv_body = render_quick_table_csv(table_name, rows).encode("utf-8")
        manifest_body = json.dumps(
            build_s3_manifest(config.bucket_name, data_key),
            indent=2,
            sort_keys=True,
        ).encode("utf-8")

        self._s3.put_object(
            Bucket=config.bucket_name,
            Key=data_key,
            Body=csv_body,
            ContentType="text/csv",
            ServerSideEncryption="AES256",
        )
        self._s3.put_object(
            Bucket=config.bucket_name,
            Key=manifest_key,
            Body=manifest_body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        return manifest_key

    def _ensure_data_source(
        self,
        table_name: str,
        manifest_key: str,
        role_arn: str,
        config: QuickDashboardDeploymentConfig,
    ) -> str:
        data_source_id = _data_source_id(table_name)
        request = build_data_source_request(
            table_name,
            manifest_key,
            role_arn,
            config,
        )
        try:
            response = self._quick.describe_data_source(
                AwsAccountId=config.aws_account_id,
                DataSourceId=data_source_id,
            )
            data_source_arn = response["DataSource"]["Arn"]
            self._quick.update_data_source(
                AwsAccountId=config.aws_account_id,
                DataSourceId=data_source_id,
                Name=request["Name"],
                DataSourceParameters=request["DataSourceParameters"],
                SslProperties=request["SslProperties"],
            )
            self._quick.update_data_source_permissions(
                AwsAccountId=config.aws_account_id,
                DataSourceId=data_source_id,
                GrantPermissions=request["Permissions"],
            )
        except self._quick.exceptions.ResourceNotFoundException:
            response = self._quick.create_data_source(**request)
            data_source_arn = response["Arn"]
        return data_source_arn

    def _ensure_data_set(
        self,
        table_name: str,
        data_source_arn: str,
        config: QuickDashboardDeploymentConfig,
    ) -> str:
        data_set_id = _data_set_id(table_name)
        request = build_data_set_request(table_name, data_source_arn, config)
        try:
            response = self._quick.describe_data_set(
                AwsAccountId=config.aws_account_id,
                DataSetId=data_set_id,
            )
            data_set_arn = response["DataSet"]["Arn"]
            update_response = self._quick.update_data_set(
                AwsAccountId=config.aws_account_id,
                DataSetId=data_set_id,
                Name=request["Name"],
                PhysicalTableMap=request["PhysicalTableMap"],
                LogicalTableMap=request["LogicalTableMap"],
                ImportMode=request["ImportMode"],
            )
            self._quick.update_data_set_permissions(
                AwsAccountId=config.aws_account_id,
                DataSetId=data_set_id,
                GrantPermissions=request["Permissions"],
            )
            ingestion_id = update_response.get("IngestionId")
        except self._quick.exceptions.ResourceNotFoundException:
            create_response = self._quick.create_data_set(**request)
            data_set_arn = create_response["Arn"]
            ingestion_id = create_response.get("IngestionId")

        if ingestion_id:
            self._wait_for_ingestion(data_set_id, ingestion_id, config)
        return data_set_arn

    def _wait_for_ingestion(
        self,
        data_set_id: str,
        ingestion_id: str,
        config: QuickDashboardDeploymentConfig,
    ) -> None:
        for attempt in range(config.max_poll_attempts):
            response = self._quick.describe_ingestion(
                AwsAccountId=config.aws_account_id,
                DataSetId=data_set_id,
                IngestionId=ingestion_id,
            )
            ingestion = response["Ingestion"]
            status = ingestion["IngestionStatus"]
            if status == "COMPLETED":
                return
            if status in _TERMINAL_INGESTION_STATUSES:
                message = ingestion.get("ErrorInfo", {}).get(
                    "Message",
                    "unknown ingestion error",
                )
                raise RuntimeError(
                    f"SPICE ingestion {ingestion_id} for {data_set_id} "
                    f"finished with {status}: {message}"
                )
            if attempt + 1 < config.max_poll_attempts:
                self._sleep(config.poll_delay_seconds)
        raise TimeoutError(
            f"SPICE ingestion {ingestion_id} for {data_set_id} did not finish"
        )

    def _ensure_dashboard(
        self,
        data_set_arns: Mapping[str, str],
        config: QuickDashboardDeploymentConfig,
    ) -> dict[str, str]:
        request = build_dashboard_request(data_set_arns, config)
        updated = False
        try:
            self._quick.describe_dashboard(
                AwsAccountId=config.aws_account_id,
                DashboardId=config.dashboard_id,
            )
            response = self._quick.update_dashboard(
                AwsAccountId=config.aws_account_id,
                DashboardId=config.dashboard_id,
                Name=request["Name"],
                Definition=request["Definition"],
                ValidationStrategy=request["ValidationStrategy"],
                DashboardPublishOptions=request["DashboardPublishOptions"],
                VersionDescription=request["VersionDescription"],
            )
            self._quick.update_dashboard_permissions(
                AwsAccountId=config.aws_account_id,
                DashboardId=config.dashboard_id,
                GrantPermissions=request["Permissions"],
            )
            updated = True
        except self._quick.exceptions.ResourceNotFoundException:
            response = self._quick.create_dashboard(**request)

        dashboard = self._wait_for_dashboard(config)
        version_number = dashboard["Version"]["VersionNumber"]
        if updated:
            self._quick.update_dashboard_published_version(
                AwsAccountId=config.aws_account_id,
                DashboardId=config.dashboard_id,
                VersionNumber=version_number,
            )
        return {
            "Arn": dashboard["Arn"],
            "VersionArn": response.get(
                "VersionArn",
                f"{dashboard['Arn']}/version/{version_number}",
            ),
        }

    def _wait_for_dashboard(
        self,
        config: QuickDashboardDeploymentConfig,
    ) -> dict[str, Any]:
        for attempt in range(config.max_poll_attempts):
            response = self._quick.describe_dashboard(
                AwsAccountId=config.aws_account_id,
                DashboardId=config.dashboard_id,
            )
            dashboard = response["Dashboard"]
            version = dashboard["Version"]
            status = version.get("Status") or version.get("CreationStatus", "")
            if status in _SUCCESSFUL_DASHBOARD_STATUSES:
                return dashboard
            if status in _FAILED_DASHBOARD_STATUSES:
                errors = version.get("Errors", [])
                raise RuntimeError(
                    f"dashboard {config.dashboard_id} finished with {status}: "
                    f"{errors}"
                )
            if attempt + 1 < config.max_poll_attempts:
                self._sleep(config.poll_delay_seconds)
        raise TimeoutError(
            f"dashboard {config.dashboard_id} did not finish creating"
        )


def validate_quick_tables(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    """Validate table names and required columns before uploading data."""
    missing_tables = sorted(set(DATASET_IDENTIFIERS) - set(tables))
    if missing_tables:
        raise ValueError(f"missing Quick tables: {', '.join(missing_tables)}")

    for table_name in DATASET_IDENTIFIERS:
        required_columns = {
            column.name for column in QUICK_TABLE_SCHEMAS[table_name]
        }
        for index, row in enumerate(tables[table_name]):
            missing_columns = sorted(required_columns - set(row))
            if missing_columns:
                raise ValueError(
                    f"{table_name} row {index} is missing columns: "
                    + ", ".join(missing_columns)
                )


def render_quick_table_csv(
    table_name: str,
    rows: Sequence[Mapping[str, Any]],
) -> str:
    """Render one Quick table as deterministic UTF-8 CSV text."""
    if table_name not in QUICK_TABLE_SCHEMAS:
        raise ValueError(f"unknown Quick table: {table_name}")

    columns = QUICK_TABLE_SCHEMAS[table_name]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[column.name for column in columns],
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                column.name: _normalize_csv_value(
                    row.get(column.name),
                    column.data_type,
                )
                for column in columns
            }
        )
    return output.getvalue()


def build_s3_manifest(bucket_name: str, data_key: str) -> dict[str, Any]:
    """Build the S3 manifest consumed by one Quick data source."""
    return {
        "fileLocations": [
            {"URIs": [f"s3://{bucket_name}/{data_key}"]}
        ],
        "globalUploadSettings": {
            "format": "CSV",
            "delimiter": ",",
            "textqualifier": '"',
            "containsHeader": "true",
        },
    }


def build_quick_s3_bucket_policy(
    config: QuickDashboardDeploymentConfig,
) -> dict[str, Any]:
    """Grant the existing account Quick role access to the dashboard prefix."""
    bucket_arn = f"arn:aws:s3:::{config.bucket_name}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ListDashboardPrefix",
                "Effect": "Allow",
                "Principal": {"AWS": config.quick_role_arn},
                "Action": "s3:ListBucket",
                "Resource": bucket_arn,
                "Condition": {
                    "StringLike": {
                        "s3:prefix": [
                            config.prefix,
                            f"{config.prefix}/*",
                        ]
                    }
                },
            },
            {
                "Sid": "ReadDashboardObjects",
                "Effect": "Allow",
                "Principal": {"AWS": config.quick_role_arn},
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": f"{bucket_arn}/{config.prefix}/*",
            },
        ],
    }


def build_data_source_request(
    table_name: str,
    manifest_key: str,
    role_arn: str,
    config: QuickDashboardDeploymentConfig,
) -> dict[str, Any]:
    """Build a create-data-source request for one S3 manifest."""
    _schema_for(table_name)
    return {
        "AwsAccountId": config.aws_account_id,
        "DataSourceId": _data_source_id(table_name),
        "Name": f"AxonLLM Ledger - {_display_name(table_name)}",
        "Type": "S3",
        "DataSourceParameters": {
            "S3Parameters": {
                "ManifestFileLocation": {
                    "Bucket": config.bucket_name,
                    "Key": manifest_key,
                },
                "RoleArn": role_arn,
            }
        },
        "Permissions": [
            {
                "Principal": config.principal_arn,
                "Actions": list(_DATA_SOURCE_ACTIONS),
            }
        ],
        "SslProperties": {"DisableSsl": False},
        "Tags": [
            {"Key": "ManagedBy", "Value": "axonllm-ledger"},
            {"Key": "Table", "Value": table_name},
        ],
    }


def build_data_set_request(
    table_name: str,
    data_source_arn: str,
    config: QuickDashboardDeploymentConfig,
) -> dict[str, Any]:
    """Build a SPICE create-data-set request for one dashboard table."""
    schema = _schema_for(table_name)
    physical_table_id = "physical"
    data_transforms = [
        {
            "CastColumnTypeOperation": {
                "ColumnName": column.name,
                "NewColumnType": column.data_type,
                **(
                    {"Format": "yyyy-MM-dd HH:mm:ss"}
                    if column.data_type == "DATETIME"
                    else {}
                ),
            }
        }
        for column in schema
        if column.data_type in {"INTEGER", "DECIMAL", "DATETIME"}
    ]
    return {
        "AwsAccountId": config.aws_account_id,
        "DataSetId": _data_set_id(table_name),
        "Name": f"AxonLLM Ledger - {_display_name(table_name)}",
        "PhysicalTableMap": {
            physical_table_id: {
                "S3Source": {
                    "DataSourceArn": data_source_arn,
                    "UploadSettings": {
                        "Format": "CSV",
                        "StartFromRow": 1,
                        "ContainsHeader": True,
                        "TextQualifier": "DOUBLE_QUOTE",
                        "Delimiter": ",",
                    },
                    "InputColumns": [
                        {
                            "Name": column.name,
                            "Type": "STRING",
                        }
                        for column in schema
                    ],
                }
            }
        },
        "LogicalTableMap": {
            "logical": {
                "Alias": table_name,
                "Source": {"PhysicalTableId": physical_table_id},
                "DataTransforms": data_transforms,
            }
        },
        "ImportMode": "SPICE",
        "Permissions": [
            {
                "Principal": config.principal_arn,
                "Actions": list(_DATA_SET_ACTIONS),
            }
        ],
        "Tags": [
            {"Key": "ManagedBy", "Value": "axonllm-ledger"},
            {"Key": "Table", "Value": table_name},
        ],
    }


def build_dashboard_request(
    data_set_arns: Mapping[str, str],
    config: QuickDashboardDeploymentConfig,
) -> dict[str, Any]:
    """Build the create-dashboard request for the six-sheet dashboard."""
    return {
        "AwsAccountId": config.aws_account_id,
        "DashboardId": config.dashboard_id,
        "Name": config.dashboard_name,
        "Definition": build_dashboard_definition(data_set_arns),
        "Permissions": [
            {
                "Principal": config.principal_arn,
                "Actions": list(_DASHBOARD_ACTIONS),
            }
        ],
        "VersionDescription": "AxonLLM Ledger dashboard v1",
        "DashboardPublishOptions": {
            "AdHocFilteringOption": {"AvailabilityStatus": "ENABLED"},
            "ExportToCSVOption": {"AvailabilityStatus": "ENABLED"},
            "SheetControlsOption": {"VisibilityState": "COLLAPSED"},
            "SheetLayoutElementMaximizationOption": {
                "AvailabilityStatus": "ENABLED"
            },
            "VisualMenuOption": {"AvailabilityStatus": "ENABLED"},
            "VisualAxisSortOption": {"AvailabilityStatus": "ENABLED"},
            "DataPointDrillUpDownOption": {"AvailabilityStatus": "ENABLED"},
            "DataPointTooltipOption": {"AvailabilityStatus": "ENABLED"},
        },
        "ValidationStrategy": {"Mode": "STRICT"},
        "Tags": [
            {"Key": "ManagedBy", "Value": "axonllm-ledger"},
            {"Key": "Workload", "Value": "bedrock-cost-intelligence"},
        ],
    }


def _normalize_csv_value(value: Any, data_type: str) -> str:
    if value is None:
        return ""
    if data_type == "BOOLEAN":
        if isinstance(value, str):
            return value.lower()
        return "true" if bool(value) else "false"
    if data_type == "DATETIME":
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value).strip()
            if not text:
                return ""
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _schema_for(table_name: str) -> tuple[QuickColumn, ...]:
    try:
        return QUICK_TABLE_SCHEMAS[table_name]
    except KeyError as exc:
        raise ValueError(f"unknown Quick table: {table_name}") from exc


def _data_source_id(table_name: str) -> str:
    return f"axonllm-ledger-{table_name.replace('_', '-')}"


def _data_set_id(table_name: str) -> str:
    return f"axonllm-ledger-{table_name.replace('_', '-')}"


def _display_name(table_name: str) -> str:
    return table_name.replace("_", " ").title()
