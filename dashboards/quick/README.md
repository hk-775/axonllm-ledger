# Amazon Quick dashboard

AxonLLM Ledger includes a complete, version-controlled Amazon Quick dashboard.
The Python deployer creates the S3 data layer, SPICE datasets, and dashboard
definition directly through the Quick Sight APIs. After review, the deployed
dashboard can also be exported as a `.qs` asset bundle for immutable
cross-account promotion.

## Interactive sample

The
[GitHub Pages dashboard](https://hk-775.github.io/axonllm-ledger/)
is a static, interactive preview of the same six-sheet information
architecture. It is generated entirely from the synthetic files under
`sample_data/`; it does not call AWS APIs or contain credentials.

To preview it locally:

```bash
python scripts/build_pages_data.py
python -m http.server 8000
```

Then open `http://localhost:8000/site/`. The committed dashboard payload is
regenerated and checked in CI so it cannot silently drift from the canonical
sample inputs.

## Dashboard sheets

### 1. Executive overview

- Total billed AI spend
- Total invocation volume
- Potential optimization savings
- Spend trend and account allocation
- Budget limit, actual spend, and forecast

### 2. Model economics

- Cost by Bedrock model or SageMaker endpoint
- Input and output token volume
- Invocation count
- Cost per invocation
- Model cost trend

### 3. Organizational allocation

- Cost by organizational unit
- Cost by AWS account
- Cost by user
- Drill-down from organizational unit to account to user

### 4. Budgets

- Budget limit, actual spend, and forecast
- Budget utilization percentage
- Exceeded-budget table
- Account and organizational-unit filters

### 5. Optimization

- Cost Optimization Hub recommendations
- Estimated savings by account and recommendation type
- Model-specific recommendations
- Prioritized remediation table

### 6. Data quality

- Cost aggregation row count
- User-to-model access relationship count
- Budget and optimization record counts
- Period and dimension coverage table

## Dataset contract

The Python API in `axonllm_ledger.quick_dataset` produces:

| Table | Purpose |
|---|---|
| `cost_aggregations` | User, account, organizational-unit, and model totals |
| `model_access` | User-to-model access relationships |
| `budgets` | Budget limits, forecasts, actual spend, and exceeded state |
| `optimization_recommendations` | Cost Optimization Hub recommendations |

The included deployer publishes these tables as private CSV objects and imports
them into SPICE. Production deployments can continue to generate the same
contract while replacing the sample export with recurring pipeline output.

## Direct deployment

From the repository root:

```bash
python -m pip install -e ".[quick]"
PYTHONPATH=src python examples/run_sample_pipeline.py
axonllm-ledger-dashboard --region us-east-1
```

By default, the command discovers the current AWS account and the single active
Quick admin or author in the `default` namespace. Use `--aws-account-id`,
`--principal-arn`, `--profile`, or `--bucket` to override discovery.

The deployment creates or updates:

1. `axonllm-ledger-dashboard-ACCOUNT-REGION`, with public access blocked,
   S3-managed encryption, versioning, and project tags.
2. An exact-prefix bucket policy for the account's existing Quick S3 consumer
   or service role. The deployer does not alter that role's trust policy.
3. Four S3 data sources and four SPICE datasets.
4. The `axonllm-ledger` dashboard with strict definition validation.

The direct definition is implemented in
`src/axonllm_ledger/quick_dashboard_definition.py`. Deployment orchestration is
implemented in `src/axonllm_ledger/quick_dashboard_deploy.py`.

## Asset-bundle promotion workflow

1. Create the Athena data source and Ledger datasets in an isolated authoring account.
2. Deploy and review the version-controlled dashboard definition.
3. Publish the reviewed AxonLLM Ledger dashboard.
4. Export the dashboard with all dependencies as a Quick asset bundle.
5. Store the immutable `.qs` artifact in a release bucket.
6. Import it with `QuickDashboardProvisioner`, overriding resource IDs, names,
   data-source parameters, permissions, and tags for the target account.
7. Require a successful strict-validation import before promoting the dashboard.

The example deployment parameters are in
[`import.example.json`](import.example.json).

## Security

- Do not package credentials in the asset bundle.
- Keep the dashboard bucket private and encrypted.
- Restrict the Quick service role to the exact Ledger S3 prefix.
- Apply row-level security when multiple tenants or business units share one dataset.
- Keep dashboard sharing and embedding permissions outside the public bundle.
