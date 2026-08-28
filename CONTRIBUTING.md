# Contributing to AxonLLM Ledger

Thank you for helping improve AxonLLM Ledger.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,quick]"
```

## Validate a change

```bash
python -m pytest -q
python -m build
```

Changes to cost calculations, reconciliation, or dashboard data contracts must
include tests. AWS integrations should accept an injected client so unit tests
do not require credentials or make network calls.

Do not commit credentials, CUR exports containing customer data, account
inventories, Quick asset bundles with account-specific identifiers, or generated
build artifacts.

## Pull requests

1. Create a focused branch from `main`.
2. Add or update tests and documentation.
3. Run the full validation commands.
4. Explain any data-contract or compatibility impact in the pull request.

By contributing, you agree that your contributions will be licensed under the
MIT-0 License.
