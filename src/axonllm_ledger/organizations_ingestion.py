"""Organizations Ingestion Service for the AxonLLM Ledger system.

Reads AWS Organizations data from CID-specific S3 prefixes, builds
account-to-OU hierarchy mappings, maps accounts to organizational units
and associated tags, and handles edge cases like circular hierarchy
detection and accounts with no OU.

Requirements: 4.1, 4.2
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from axonllm_ledger.models import AccountHierarchy, IngestionLog, IngestionStatus

logger = logging.getLogger(__name__)

# Required fields in raw account/org data
_REQUIRED_ACCOUNT_FIELDS = (
    "account_id",
    "account_name",
)


def process_single_account(raw_account: dict) -> AccountHierarchy | None:
    """Parse a single raw account dict into an AccountHierarchy record.

    Returns an AccountHierarchy if required fields are present and valid.
    Returns None and logs a warning for invalid/incomplete records.

    Expected raw_account keys:
      - account_id: AWS account ID (required)
      - account_name: human-readable account name (required)
      - ou_id: organizational unit ID (optional, defaults to "")
      - ou_name: organizational unit name (optional, defaults to "")
      - parent_ou_id: parent OU ID for nested hierarchy (optional, defaults to "")
      - tags: dict of account tags (optional, defaults to {})
    """
    account_id = raw_account.get("account_id", "")
    account_name = raw_account.get("account_name", "")

    # Validate required fields
    missing = []
    if not account_id:
        missing.append("account_id")
    if not account_name:
        missing.append("account_name")

    if missing:
        logger.warning(
            "Skipping account record %s: missing required fields: %s",
            account_id or "<unknown>",
            ", ".join(missing),
        )
        return None

    ou_id = raw_account.get("ou_id", "") or ""
    ou_name = raw_account.get("ou_name", "") or ""
    parent_ou_id = raw_account.get("parent_ou_id", "") or ""
    tags = raw_account.get("tags") or {}

    if not isinstance(tags, dict):
        logger.warning(
            "Account %s has invalid tags (not a dict), defaulting to empty.",
            account_id,
        )
        tags = {}

    return AccountHierarchy(
        accountId=str(account_id),
        accountName=str(account_name),
        organizationalUnitId=str(ou_id),
        organizationalUnitName=str(ou_name),
        parentOUId=str(parent_ou_id),
        tags={str(k): str(v) for k, v in tags.items()},
        ingestedAt=datetime.now(timezone.utc),
    )


def process_hierarchy(raw_accounts: list[dict]) -> list[AccountHierarchy]:
    """Batch process raw account data into AccountHierarchy records.

    Parses each raw account dict, validates required fields, and returns
    a list of valid AccountHierarchy records.

    Requirements: 4.1
    """
    results: list[AccountHierarchy] = []
    for raw in raw_accounts:
        account = process_single_account(raw)
        if account is not None:
            results.append(account)
    return results


def build_hierarchy_map(
    accounts: list[AccountHierarchy],
) -> dict[str, AccountHierarchy]:
    """Build an accountId -> AccountHierarchy lookup map.

    If duplicate account IDs exist, the last entry wins.

    Requirements: 4.1, 4.2
    """
    return {account.accountId: account for account in accounts}


def map_account_to_ou(
    account_id: str,
    hierarchy: dict[str, AccountHierarchy],
) -> tuple[str, str] | None:
    """Return (ouId, ouName) for an account, or None if not found.

    Requirements: 4.2
    """
    account = hierarchy.get(account_id)
    if account is None:
        return None
    return (account.organizationalUnitId, account.organizationalUnitName)


def get_account_tags(
    account_id: str,
    hierarchy: dict[str, AccountHierarchy],
) -> dict[str, str]:
    """Return tags for an account. Returns empty dict if account not found.

    Requirements: 4.2
    """
    account = hierarchy.get(account_id)
    if account is None:
        return {}
    return dict(account.tags)


def detect_circular_hierarchy(accounts: list[AccountHierarchy]) -> list[str]:
    """Detect circular OU references in the hierarchy.

    Walks the parentOUId chain for each account's OU. If a cycle is
    detected (an OU is visited twice while walking up), the account is
    flagged.

    Returns a list of account IDs that belong to OUs involved in cycles.
    """
    # Build OU -> parent OU mapping
    ou_to_parent: dict[str, str] = {}
    for account in accounts:
        ou_id = account.organizationalUnitId
        parent_id = account.parentOUId
        if ou_id and parent_id:
            ou_to_parent[ou_id] = parent_id

    # For each OU, walk up the chain and detect cycles
    cyclic_ous: set[str] = set()
    for ou_id in ou_to_parent:
        visited: set[str] = set()
        current = ou_id
        while current and current in ou_to_parent:
            if current in visited:
                # Cycle detected — mark all OUs in the cycle
                cyclic_ous.add(current)
                cycle_node = ou_to_parent[current]
                while cycle_node != current:
                    cyclic_ous.add(cycle_node)
                    cycle_node = ou_to_parent[cycle_node]
                break
            visited.add(current)
            current = ou_to_parent[current]

    if cyclic_ous:
        logger.error(
            "Circular hierarchy detected in OUs: %s",
            ", ".join(sorted(cyclic_ous)),
        )

    # Return account IDs whose OU is in a cycle
    affected_accounts = [
        account.accountId
        for account in accounts
        if account.organizationalUnitId in cyclic_ous
    ]
    return sorted(affected_accounts)


def ingest_organizations(
    raw_accounts: list[dict],
    *,
    s3_prefix: str = "",
) -> tuple[list[AccountHierarchy], dict[str, AccountHierarchy], IngestionLog]:
    """Full Organizations ingestion pipeline.

    Processes raw account data, builds the hierarchy map, detects
    circular references, and creates an IngestionLog entry.

    Parameters
    ----------
    raw_accounts:
        Raw account/org data dicts as read from S3.
    s3_prefix:
        The S3 prefix from which the data was read.

    Returns
    -------
    Tuple of (processed accounts, hierarchy map, ingestion log).
    """
    started_at = datetime.now(timezone.utc)

    processed = process_hierarchy(raw_accounts)
    skipped = len(raw_accounts) - len(processed)

    hierarchy_map = build_hierarchy_map(processed)

    # Detect circular hierarchy and log warnings
    circular_accounts = detect_circular_hierarchy(processed)
    error_message = None
    if circular_accounts:
        error_message = (
            f"Circular hierarchy detected for accounts: "
            f"{', '.join(circular_accounts)}"
        )

    completed_at = datetime.now(timezone.utc)

    status = IngestionStatus.SUCCESS
    if skipped > 0 and processed:
        status = IngestionStatus.PARTIAL
    elif not processed and raw_accounts:
        status = IngestionStatus.FAILED
    if circular_accounts and status == IngestionStatus.SUCCESS:
        status = IngestionStatus.PARTIAL

    log = IngestionLog(
        logId=IngestionLog.generate_id(),
        source="Organizations",
        s3Key=s3_prefix,
        recordCount=len(processed),
        skippedCount=skipped,
        duplicateCount=0,
        status=status,
        startedAt=started_at,
        completedAt=completed_at,
        errorMessage=error_message,
    )

    return processed, hierarchy_map, log
