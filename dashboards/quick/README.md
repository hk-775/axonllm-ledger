# Amazon Quick dashboard

AxonLLM Ledger uses Amazon Quick's Quick Sight APIs to deploy dashboards as
versioned asset bundles. Asset bundles are preferable to creating every visual
through individual API calls because they preserve the reviewed analysis,
datasets, calculated fields, themes, and dashboard dependencies as one release
artifact.

## Proposed sheets

### 1. Executive overview

- Total billed AI spend
- Forecast versus budget
- Accounts over budget
- Estimated optimization opportunity
- Spend trend by day or month

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

- Last ingestion time
- Missing-period alerts
- Deduplicated record count
- CUR-to-budget reconciliation discrepancy

## Dataset contract

The Python API in `axonllm_ledger.quick_dataset` produces:

| Table | Purpose |
|---|---|
| `cost_aggregations` | User, account, organizational-unit, and model totals |
| `model_access` | User-to-model access relationships |
| `budgets` | Budget limits, forecasts, actual spend, and exceeded state |
| `optimization_recommendations` | Cost Optimization Hub recommendations |

Publish these tables to a queryable Athena layer before refreshing the Quick
datasets. Parquet is preferred for production because it reduces scan cost and
preserves explicit column types.

## Asset-bundle workflow

1. Create the Athena data source and Ledger datasets in an isolated authoring account.
2. Build and review the analysis using the sheets above.
3. Publish the analysis as the AxonLLM Ledger dashboard.
4. Export the dashboard with all dependencies as a Quick asset bundle.
5. Store the immutable `.qs` artifact in a release bucket.
6. Import it with `QuickDashboardProvisioner`, overriding resource IDs, names,
   data-source parameters, permissions, and tags for the target account.
7. Require a successful strict-validation import before promoting the dashboard.

The example deployment parameters are in
[`import.example.json`](import.example.json).

## Security

- Do not package credentials in the asset bundle.
- Use a dedicated Athena workgroup with enforced output encryption and scan limits.
- Restrict the Quick service role to the exact Athena workgroup, Glue catalog,
  and S3 prefixes required by Ledger.
- Apply row-level security when multiple tenants or business units share one dataset.
- Keep dashboard sharing and embedding permissions outside the public bundle.
