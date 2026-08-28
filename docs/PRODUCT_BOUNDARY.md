# Product boundary

## AxonLLM

AxonLLM is the request-path product. It routes model traffic, translates
provider APIs, records request-level usage, estimates cost, and enforces
budgets before or immediately after model invocation.

## AxonLLM Ledger

AxonLLM Ledger is the financial system of record for AI consumption. It works
from AWS billing and cost-management sources rather than relying only on
runtime estimates.

Ledger owns:

- CUR ingestion and billed-cost reconciliation
- AWS account and organizational-unit allocation
- AWS Budgets comparison
- Cost Optimization Hub recommendation ingestion
- user and model access reporting
- analytics export contracts
- Amazon Quick dashboard lifecycle

Ledger does not route prompts, invoke models, manage provider credentials, or
replace AxonLLM's real-time quota enforcement.

## Integration contract

The long-term integration uses two complementary feeds:

1. AxonLLM emits request-level usage identity and routing metadata.
2. Ledger ingests AWS billing records and reconciles them against that runtime data.

The shared identity should include tenant, project, user, AWS account, logical
model, provider model, region, and a stable request or correlation identifier.
This allows the control plane to show both estimated runtime cost and billed
AWS cost without treating either source as interchangeable.
