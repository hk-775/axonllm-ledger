"""Export the deployed AxonLLM Ledger dashboard as a portable .qs bundle."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from axonllm_ledger import __version__
from axonllm_ledger.quick_dashboard import (
    QuickAssetBundleExportConfig,
    QuickDashboardProvisioner,
)


def main() -> int:
    """Export the dashboard and return a process exit code."""
    parser = argparse.ArgumentParser(
        description=(
            "Export the AxonLLM Ledger Amazon Quick dashboard and all of its "
            "dependencies as a portable .qs asset bundle."
        )
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile")
    parser.add_argument("--aws-account-id")
    parser.add_argument("--dashboard-id", default="axonllm-ledger")
    parser.add_argument("--job-id")
    parser.add_argument(
        "--output",
        default="build/axonllm-ledger-dashboard.qs",
    )
    parser.add_argument(
        "--include-permissions",
        action="store_true",
        help="Include resource permissions in the exported bundle",
    )
    parser.add_argument(
        "--exclude-tags",
        action="store_true",
        help="Do not include resource tags in the exported bundle",
    )
    args = parser.parse_args()

    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        print(
            'Install the Quick integration with: pip install "axonllm-ledger[quick]"',
            file=sys.stderr,
        )
        return 2

    try:
        session_kwargs: dict[str, str] = {"region_name": args.region}
        if args.profile:
            session_kwargs["profile_name"] = args.profile
        session = boto3.Session(**session_kwargs)
        client_config = Config(
            retries={"mode": "standard", "total_max_attempts": 4},
            connect_timeout=5,
            read_timeout=90,
        )
        identity = session.client("sts", config=client_config).get_caller_identity()
        account_id = args.aws_account_id or identity["Account"]
        partition = identity["Arn"].split(":", 2)[1]
        dashboard_arn = (
            f"arn:{partition}:quicksight:{args.region}:{account_id}:"
            f"dashboard/{args.dashboard_id}"
        )
        job_id = args.job_id or (
            "axonllm-ledger-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        )
        config = QuickAssetBundleExportConfig(
            aws_account_id=account_id,
            job_id=job_id,
            resource_arns=(dashboard_arn,),
            include_permissions=args.include_permissions,
            include_tags=not args.exclude_tags,
        )
        provisioner = QuickDashboardProvisioner.from_boto3(
            region_name=args.region,
            profile_name=args.profile,
        )
        provisioner.start_export(config)
        status = provisioner.wait_for_export(config)
        if not status.succeeded:
            raise RuntimeError(f"asset-bundle export failed: {list(status.errors)}")
        if not status.download_url:
            raise RuntimeError("asset-bundle export succeeded without a download URL")

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        request = Request(
            status.download_url,
            headers={"User-Agent": f"axonllm-ledger/{__version__}"},
        )
        with urlopen(request, timeout=90) as response:
            output_path.write_bytes(response.read())
        print(output_path)
        return 0
    except (BotoCoreError, ClientError, OSError, ValueError, RuntimeError) as exc:
        print(f"Dashboard export failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
