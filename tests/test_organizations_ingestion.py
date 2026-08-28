"""Unit tests for the Organizations Ingestion Service.

Covers:
- Valid account processing
- Missing required fields
- Hierarchy map building and lookups
- Circular hierarchy detection
- Accounts with no OU
- Ingestion logging
"""

from __future__ import annotations

import uuid

import pytest

from axonllm_ledger.models import AccountHierarchy, IngestionStatus
from axonllm_ledger.organizations_ingestion import (
    build_hierarchy_map,
    detect_circular_hierarchy,
    get_account_tags,
    ingest_organizations,
    map_account_to_ou,
    process_hierarchy,
    process_single_account,
)


def _make_raw_account(**overrides) -> dict:
    """Create a valid raw account dict with optional overrides."""
    base = {
        "account_id": "111111111111",
        "account_name": "Dev Account",
        "ou_id": "ou-abc-123",
        "ou_name": "Engineering",
        "parent_ou_id": "r-root",
        "tags": {"env": "dev", "team": "platform"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# process_single_account
# ---------------------------------------------------------------------------

class TestProcessSingleAccount:
    def test_valid_account_produces_hierarchy(self):
        raw = _make_raw_account()
        result = process_single_account(raw)
        assert result is not None
        assert result.accountId == "111111111111"
        assert result.accountName == "Dev Account"
        assert result.organizationalUnitId == "ou-abc-123"
        assert result.organizationalUnitName == "Engineering"
        assert result.parentOUId == "r-root"
        assert result.tags == {"env": "dev", "team": "platform"}

    def test_account_with_no_ou(self):
        raw = _make_raw_account(ou_id="", ou_name="", parent_ou_id="")
        result = process_single_account(raw)
        assert result is not None
        assert result.organizationalUnitId == ""
        assert result.organizationalUnitName == ""
        assert result.parentOUId == ""

    def test_account_with_none_ou_fields(self):
        raw = _make_raw_account(ou_id=None, ou_name=None, parent_ou_id=None)
        result = process_single_account(raw)
        assert result is not None
        assert result.organizationalUnitId == ""
        assert result.organizationalUnitName == ""
        assert result.parentOUId == ""

    def test_account_with_no_tags(self):
        raw = _make_raw_account(tags={})
        result = process_single_account(raw)
        assert result is not None
        assert result.tags == {}

    def test_account_with_none_tags_defaults_to_empty(self):
        raw = _make_raw_account(tags=None)
        result = process_single_account(raw)
        assert result is not None
        assert result.tags == {}

    def test_account_with_invalid_tags_defaults_to_empty(self, caplog):
        raw = _make_raw_account(tags="not-a-dict")
        result = process_single_account(raw)
        assert result is not None
        assert result.tags == {}
        assert "invalid tags" in caplog.text

    def test_ingested_at_is_set(self):
        raw = _make_raw_account()
        result = process_single_account(raw)
        assert result is not None
        assert result.ingestedAt is not None


class TestProcessSingleAccountMissingFields:
    def test_missing_account_id(self, caplog):
        raw = _make_raw_account()
        del raw["account_id"]
        assert process_single_account(raw) is None
        assert "account_id" in caplog.text

    def test_missing_account_name(self, caplog):
        raw = _make_raw_account()
        del raw["account_name"]
        assert process_single_account(raw) is None
        assert "account_name" in caplog.text

    def test_empty_account_id(self, caplog):
        raw = _make_raw_account(account_id="")
        assert process_single_account(raw) is None
        assert "account_id" in caplog.text

    def test_empty_account_name(self, caplog):
        raw = _make_raw_account(account_name="")
        assert process_single_account(raw) is None
        assert "account_name" in caplog.text

    def test_missing_both_required_fields(self, caplog):
        raw = _make_raw_account()
        del raw["account_id"]
        del raw["account_name"]
        assert process_single_account(raw) is None
        assert "account_id" in caplog.text
        assert "account_name" in caplog.text


# ---------------------------------------------------------------------------
# process_hierarchy
# ---------------------------------------------------------------------------

class TestProcessHierarchy:
    def test_processes_multiple_valid_accounts(self):
        raw_list = [
            _make_raw_account(account_id="111", account_name="Acct 1"),
            _make_raw_account(account_id="222", account_name="Acct 2"),
            _make_raw_account(account_id="333", account_name="Acct 3"),
        ]
        results = process_hierarchy(raw_list)
        assert len(results) == 3
        assert {r.accountId for r in results} == {"111", "222", "333"}

    def test_skips_invalid_accounts(self):
        raw_list = [
            _make_raw_account(account_id="111", account_name="Valid"),
            _make_raw_account(account_id="", account_name="Invalid"),
        ]
        results = process_hierarchy(raw_list)
        assert len(results) == 1
        assert results[0].accountId == "111"

    def test_empty_list(self):
        assert process_hierarchy([]) == []


# ---------------------------------------------------------------------------
# build_hierarchy_map
# ---------------------------------------------------------------------------

class TestBuildHierarchyMap:
    def test_builds_lookup_map(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", account_name="A1"),
            _make_raw_account(account_id="222", account_name="A2"),
        ])
        hmap = build_hierarchy_map(accounts)
        assert "111" in hmap
        assert "222" in hmap
        assert hmap["111"].accountName == "A1"

    def test_duplicate_account_id_last_wins(self):
        a1 = process_single_account(_make_raw_account(account_id="111", account_name="First"))
        a2 = process_single_account(_make_raw_account(account_id="111", account_name="Second"))
        hmap = build_hierarchy_map([a1, a2])
        assert hmap["111"].accountName == "Second"

    def test_empty_list_produces_empty_map(self):
        assert build_hierarchy_map([]) == {}


# ---------------------------------------------------------------------------
# map_account_to_ou
# ---------------------------------------------------------------------------

class TestMapAccountToOU:
    def test_returns_ou_for_known_account(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", ou_id="ou-1", ou_name="Eng"),
        ])
        hmap = build_hierarchy_map(accounts)
        result = map_account_to_ou("111", hmap)
        assert result == ("ou-1", "Eng")

    def test_returns_none_for_unknown_account(self):
        hmap = build_hierarchy_map([])
        assert map_account_to_ou("999", hmap) is None

    def test_account_with_empty_ou(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", ou_id="", ou_name=""),
        ])
        hmap = build_hierarchy_map(accounts)
        result = map_account_to_ou("111", hmap)
        assert result == ("", "")


# ---------------------------------------------------------------------------
# get_account_tags
# ---------------------------------------------------------------------------

class TestGetAccountTags:
    def test_returns_tags_for_known_account(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", tags={"env": "prod", "cost-center": "42"}),
        ])
        hmap = build_hierarchy_map(accounts)
        tags = get_account_tags("111", hmap)
        assert tags == {"env": "prod", "cost-center": "42"}

    def test_returns_empty_dict_for_unknown_account(self):
        hmap = build_hierarchy_map([])
        assert get_account_tags("999", hmap) == {}

    def test_returns_copy_not_reference(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", tags={"k": "v"}),
        ])
        hmap = build_hierarchy_map(accounts)
        tags = get_account_tags("111", hmap)
        tags["new_key"] = "new_val"
        # Original should be unmodified
        assert "new_key" not in hmap["111"].tags


# ---------------------------------------------------------------------------
# detect_circular_hierarchy
# ---------------------------------------------------------------------------

class TestDetectCircularHierarchy:
    def test_no_cycles_returns_empty(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", ou_id="ou-child", parent_ou_id="ou-parent"),
            _make_raw_account(account_id="222", ou_id="ou-parent", parent_ou_id="r-root"),
        ])
        assert detect_circular_hierarchy(accounts) == []

    def test_self_referencing_ou(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", ou_id="ou-loop", parent_ou_id="ou-loop"),
        ])
        result = detect_circular_hierarchy(accounts)
        assert "111" in result

    def test_two_node_cycle(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", ou_id="ou-a", parent_ou_id="ou-b"),
            _make_raw_account(account_id="222", ou_id="ou-b", parent_ou_id="ou-a"),
        ])
        result = detect_circular_hierarchy(accounts)
        assert "111" in result
        assert "222" in result

    def test_accounts_with_no_ou_not_flagged(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", ou_id="", parent_ou_id=""),
        ])
        assert detect_circular_hierarchy(accounts) == []

    def test_mixed_cyclic_and_non_cyclic(self):
        accounts = process_hierarchy([
            _make_raw_account(account_id="111", ou_id="ou-good", parent_ou_id="r-root"),
            _make_raw_account(account_id="222", ou_id="ou-bad", parent_ou_id="ou-bad"),
        ])
        result = detect_circular_hierarchy(accounts)
        assert "111" not in result
        assert "222" in result


# ---------------------------------------------------------------------------
# ingest_organizations
# ---------------------------------------------------------------------------

class TestIngestOrganizations:
    def test_successful_ingestion(self):
        raw_list = [
            _make_raw_account(account_id="111", account_name="A1"),
            _make_raw_account(account_id="222", account_name="A2"),
        ]
        accounts, hmap, log = ingest_organizations(raw_list, s3_prefix="s3://bucket/orgs/")
        assert len(accounts) == 2
        assert "111" in hmap
        assert "222" in hmap
        assert log.source == "Organizations"
        assert log.s3Key == "s3://bucket/orgs/"
        assert log.recordCount == 2
        assert log.skippedCount == 0
        assert log.status == IngestionStatus.SUCCESS

    def test_partial_ingestion(self):
        raw_list = [
            _make_raw_account(account_id="111", account_name="Valid"),
            _make_raw_account(account_id="", account_name="Invalid"),
        ]
        accounts, hmap, log = ingest_organizations(raw_list)
        assert len(accounts) == 1
        assert log.recordCount == 1
        assert log.skippedCount == 1
        assert log.status == IngestionStatus.PARTIAL

    def test_all_invalid_produces_failed_status(self):
        raw_list = [
            _make_raw_account(account_id="", account_name=""),
        ]
        accounts, hmap, log = ingest_organizations(raw_list)
        assert len(accounts) == 0
        assert log.status == IngestionStatus.FAILED

    def test_empty_input_produces_success(self):
        accounts, hmap, log = ingest_organizations([])
        assert len(accounts) == 0
        assert hmap == {}
        assert log.status == IngestionStatus.SUCCESS
        assert log.recordCount == 0

    def test_circular_hierarchy_sets_partial_and_error(self):
        raw_list = [
            _make_raw_account(account_id="111", ou_id="ou-a", parent_ou_id="ou-b"),
            _make_raw_account(account_id="222", ou_id="ou-b", parent_ou_id="ou-a"),
        ]
        accounts, hmap, log = ingest_organizations(raw_list)
        assert len(accounts) == 2
        assert log.status == IngestionStatus.PARTIAL
        assert log.errorMessage is not None
        assert "Circular hierarchy" in log.errorMessage

    def test_log_has_valid_id_and_timestamps(self):
        raw_list = [_make_raw_account()]
        _, _, log = ingest_organizations(raw_list)
        # logId should be a valid UUID
        uuid.UUID(log.logId)
        assert log.startedAt is not None
        assert log.completedAt is not None
        assert log.completedAt >= log.startedAt
