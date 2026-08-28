# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Once this repository
is public, use GitHub private vulnerability reporting from the repository's
Security tab.

Include the affected component, reproduction steps, potential impact, and any
suggested mitigation.

## Security boundaries

- AxonLLM Ledger must not log or export AWS credentials.
- Dashboard deployment uses the standard AWS SDK credential chain.
- Sample data must contain synthetic account IDs, users, and costs.
- Billing exports and organization inventories may contain sensitive business
  information and must not be committed.
- Quick asset-bundle imports default to rollback and strict validation.
- Data-source permissions and dashboard sharing remain adopter-controlled.

## Supported versions

Until the first public release, only the latest commit on `main` is supported.
