# AxonLLM Ledger

**Open-source cost intelligence for Amazon Bedrock and AWS AI workloads.**

AxonLLM Ledger turns AWS billing and organizational data into model-, user-,
account-, and organizational-unit-level cost views. It complements AxonLLM's
real-time routing and budget controls with billed-cost reconciliation,
optimization data, and dashboard-ready analytics.

The project is currently an alpha. Its existing calculation and ingestion
behavior is covered by unit and property-based tests, while production storage,
CUR 2.0 normalization, and a distributable Amazon Quick asset bundle remain
release milestones.

## What Ledger does

- Parses Legacy CUR-shaped line items for Amazon Bedrock and Amazon SageMaker.
- Deduplicates redelivered or overlapping billing records.
- Attributes cost and usage by user, AWS account, organizational unit, and model.
- Tracks which users accessed which models.
- Compares billed cost with AWS Budgets data.
- Ingests AWS Organizations hierarchy and Cost Optimization Hub recommendations.
- Validates cross-dimension consistency and detects ingestion gaps.
- Produces a stable analytics package for Amazon Quick, Looker, or another BI target.
- Imports and monitors Amazon Quick dashboards through the Quick Sight asset-bundle API.

## AxonLLM family boundary

| Component | Responsibility |
|---|---|
| **AxonLLM** | Routes live model requests, records request-level usage, estimates cost, and enforces runtime budgets. |
| **AxonLLM Ledger** | Reconciles AWS billing data, allocates spend across the organization, and provides financial and optimization dashboards. |

AxonLLM answers, "What is this request expected to cost right now?" Ledger
answers, "What did AWS bill, who consumed it, how does it compare with budget,
and where can we optimize?"

## Data flow

```text
Legacy CUR / normalized CID exports
                |
                v
       Parse and deduplicate
                |
                v
   Aggregate and reconcile spend
                |
                v
      Ledger analytics package
          |              |
          v              v
 Amazon Quick tables   Optional BI target
          |
          v
 Versioned Quick asset bundle
```

## Install for development

Python 3.11 or later is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,quick]"
python -m pytest -q
```

Run the checked-in sample pipeline:

```bash
PYTHONPATH=src python examples/run_sample_pipeline.py
```

The sample writes generated Ledger and Amazon Quick table payloads under
`build/`.

## Core API

```python
from axonllm_ledger.aggregation import AggregationEngine, TimeRange
from axonllm_ledger.export import package_export_data
from axonllm_ledger.quick_dataset import build_quick_dataset_tables

engine = AggregationEngine(records=usage_records)
package = package_export_data(
    engine=engine,
    time_range=TimeRange(start=period_start, end=period_end),
    budgets=budgets,
    recommendations=recommendations,
    user_ids=user_ids,
)

quick_tables = build_quick_dataset_tables(package)
```

The Quick contract contains four tables:

- `cost_aggregations`
- `model_access`
- `budgets`
- `optimization_recommendations`

Financial values are represented as exact decimal text in the JSON contract so
they can be cast to fixed-precision decimal columns by Athena without
floating-point loss.

## Amazon Quick dashboard deployment

Install the optional AWS SDK dependency:

```bash
python -m pip install "axonllm-ledger[quick]"
```

Then import a versioned asset bundle:

```python
from axonllm_ledger.quick_dashboard import (
    QuickAssetBundleImportConfig,
    QuickDashboardProvisioner,
)

provisioner = QuickDashboardProvisioner.from_boto3(region_name="us-east-1")
config = QuickAssetBundleImportConfig(
    aws_account_id="123456789012",
    job_id="axonllm-ledger-dashboard-v1",
    override_parameters={
        "ResourceIdOverrideConfiguration": {
            "PrefixForAllResources": "axonllm-ledger-"
        }
    },
)

provisioner.start_import_from_s3(
    "s3://my-dashboard-artifacts/axonllm-ledger.qs",
    config,
)
status = provisioner.wait_for_import(config)
if not status.succeeded:
    raise RuntimeError(status.errors)
```

The SDK uses the standard AWS credential chain and does not accept or persist
plaintext credentials. See [the Quick dashboard guide](dashboards/quick/README.md)
for the planned sheets and asset-bundle workflow.

## Current data-format boundary

The parser currently expects Legacy CUR-style field names such as
`product/servicecode`, `lineItem/UsageAccountId`, and
`lineItem/UnblendedCost`. CUR 2.0 and FOCUS use different schemas and require a
normalization layer before ingestion. Supporting those formats directly is a
planned milestone.

## Current implementation limits

- The core pipeline uses in-memory collections; production persistence is not implemented.
- S3 listing and object-loading hooks are stubs intended for an AWS adapter.
- The repository defines and provisions Quick assets, but the production `.qs`
  bundle must be authored and exported from a reviewed Quick environment.
- Looker remains possible through the generic `DeliveryTarget` protocol, but
  Amazon Quick is the primary dashboard target.

## Development

```bash
python -m pytest -q
python -m build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) before
submitting changes or vulnerability reports.

## License

MIT-0. See [LICENSE](LICENSE).
