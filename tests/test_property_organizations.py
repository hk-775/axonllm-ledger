"""Property-based tests for Organizations hierarchy ingestion.

Feature: axonllm-ledger, Property 6: Organizations Ingestion Produces Valid Account Hierarchy

Validates: Requirements 4.1, 4.2
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.models import AccountHierarchy
from axonllm_ledger.organizations_ingestion import (
    build_hierarchy_map,
    get_account_tags,
    map_account_to_ou,
    process_hierarchy,
)


# --- Strategies ---

_account_ids = st.from_regex(r"[0-9]{12}", fullmatch=True)

_account_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=" -_"),
    min_size=1,
    max_size=40,
)

_ou_ids = st.from_regex(r"ou-[a-z0-9]{4}-[a-z0-9]{4}", fullmatch=True)

_ou_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=" -_"),
    min_size=1,
    max_size=40,
)

_parent_ou_ids = st.from_regex(r"r-[a-z0-9]{4}", fullmatch=True)

_tag_keys = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=20,
)

_tag_values = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_ "),
    min_size=1,
    max_size=30,
)

_tags = st.dictionaries(keys=_tag_keys, values=_tag_values, min_size=0, max_size=5)


@st.composite
def valid_raw_account(draw):
    """Generate a valid raw account dict with all required fields."""
    return {
        "account_id": draw(_account_ids),
        "account_name": draw(_account_names),
        "ou_id": draw(_ou_ids),
        "ou_name": draw(_ou_names),
        "parent_ou_id": draw(_parent_ou_ids),
        "tags": draw(_tags),
    }


@st.composite
def valid_raw_account_list(draw):
    """Generate a list of valid raw accounts with unique account IDs."""
    num_accounts = draw(st.integers(min_value=1, max_value=10))
    ids = draw(
        st.lists(
            _account_ids,
            min_size=num_accounts,
            max_size=num_accounts,
            unique=True,
        )
    )
    accounts = []
    for acct_id in ids:
        raw = draw(valid_raw_account())
        raw["account_id"] = acct_id
        accounts.append(raw)
    return accounts


# --- Property Tests ---


class TestOrganizationsHierarchyIngestion:
    """Property 6: Organizations Ingestion Produces Valid Account Hierarchy.

    For any valid Organizations_Source data, ingestion should produce
    AccountHierarchy records where every account is mapped to its
    organizational unit and associated tags. For any UsageRecord with an
    accountId present in the hierarchy, the system should resolve the
    correct organizational unit and tags.

    **Validates: Requirements 4.1, 4.2**
    """

    @settings(max_examples=100)
    @given(raw_accounts=valid_raw_account_list())
    def test_process_hierarchy_maps_every_account_to_ou_and_tags(
        self, raw_accounts: list[dict]
    ):
        """For any valid Organizations data, process_hierarchy produces
        AccountHierarchy records where every account is mapped to its OU
        and tags.

        Feature: axonllm-ledger, Property 6: Organizations Ingestion Produces Valid Account Hierarchy
        """
        # **Validates: Requirements 4.1, 4.2**
        results = process_hierarchy(raw_accounts)

        # Every valid raw account should produce an AccountHierarchy
        assert len(results) == len(raw_accounts)

        # Build a lookup from raw data for verification
        raw_by_id = {r["account_id"]: r for r in raw_accounts}

        for record in results:
            assert isinstance(record, AccountHierarchy)

            raw = raw_by_id[record.accountId]

            # Account is mapped to its OU (Requirement 4.1)
            assert record.organizationalUnitId == str(raw["ou_id"])
            assert record.organizationalUnitName == str(raw["ou_name"])
            assert record.parentOUId == str(raw["parent_ou_id"])

            # Account has correct tags (Requirement 4.2)
            expected_tags = {str(k): str(v) for k, v in raw["tags"].items()}
            assert record.tags == expected_tags

            # Metadata fields are populated
            assert record.accountName == str(raw["account_name"])
            assert record.ingestedAt is not None

    @settings(max_examples=100)
    @given(raw_accounts=valid_raw_account_list())
    def test_hierarchy_map_resolves_correct_ou_and_tags(
        self, raw_accounts: list[dict]
    ):
        """For any account in the hierarchy map, map_account_to_ou returns
        the correct OU and get_account_tags returns the correct tags.

        Feature: axonllm-ledger, Property 6: Organizations Ingestion Produces Valid Account Hierarchy
        """
        # **Validates: Requirements 4.1, 4.2**
        processed = process_hierarchy(raw_accounts)
        hierarchy = build_hierarchy_map(processed)

        raw_by_id = {r["account_id"]: r for r in raw_accounts}

        for account_id, raw in raw_by_id.items():
            # map_account_to_ou should resolve the correct OU
            ou_result = map_account_to_ou(account_id, hierarchy)
            assert ou_result is not None, (
                f"Account {account_id} should be found in hierarchy"
            )
            ou_id, ou_name = ou_result
            assert ou_id == str(raw["ou_id"])
            assert ou_name == str(raw["ou_name"])

            # get_account_tags should return the correct tags
            tags = get_account_tags(account_id, hierarchy)
            expected_tags = {str(k): str(v) for k, v in raw["tags"].items()}
            assert tags == expected_tags

        # Accounts NOT in the hierarchy should return None / empty
        not_in_hierarchy = "NOTFOUND00000"
        assert not_in_hierarchy not in raw_by_id  # guaranteed by format
        missing_result = map_account_to_ou(not_in_hierarchy, hierarchy)
        assert missing_result is None

        missing_tags = get_account_tags(not_in_hierarchy, hierarchy)
        assert missing_tags == {}
