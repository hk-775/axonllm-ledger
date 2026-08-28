"""Unit tests for AccessRecord retention policy.

Validates Requirement 9.4: THE Ledger SHALL retain Access_Records
for a minimum of 12 months.
"""

from datetime import datetime, timedelta, timezone

from axonllm_ledger.models import AccessRecord
from axonllm_ledger.retention import RETENTION_PERIOD, RetentionPolicy


def _make_record(timestamp: datetime) -> AccessRecord:
    """Helper to create an AccessRecord with a given timestamp."""
    return AccessRecord(
        accessId="acc-1",
        userId="user-1",
        modelId="model-1",
        accountId="111111111111",
        timestamp=timestamp,
        sourceRecordId="rec-1",
    )


class TestRetentionPeriodConstant:
    def test_retention_period_is_365_days(self):
        assert RETENTION_PERIOD == timedelta(days=365)


class TestIsExpired:
    def test_default_reference_time_supports_aware_timestamp(self):
        record = _make_record(datetime.now(timezone.utc))
        policy = RetentionPolicy()
        assert policy.is_expired(record) is False

    def test_default_reference_time_supports_legacy_naive_timestamp(self):
        record = _make_record(datetime.now())
        policy = RetentionPolicy()
        assert policy.is_expired(record) is False

    def test_recent_record_not_expired(self):
        now = datetime(2024, 6, 15, 12, 0, 0)
        record = _make_record(now - timedelta(days=30))
        policy = RetentionPolicy()
        assert policy.is_expired(record, as_of=now) is False

    def test_old_record_expired(self):
        now = datetime(2024, 6, 15, 12, 0, 0)
        record = _make_record(now - timedelta(days=400))
        policy = RetentionPolicy()
        assert policy.is_expired(record, as_of=now) is True

    def test_exactly_at_boundary_not_expired(self):
        """A record exactly 365 days old is NOT expired (boundary is >)."""
        now = datetime(2024, 6, 15, 12, 0, 0)
        record = _make_record(now - timedelta(days=365))
        policy = RetentionPolicy()
        assert policy.is_expired(record, as_of=now) is False

    def test_one_second_past_boundary_expired(self):
        now = datetime(2024, 6, 15, 12, 0, 0)
        record = _make_record(now - timedelta(days=365, seconds=1))
        policy = RetentionPolicy()
        assert policy.is_expired(record, as_of=now) is True


class TestApply:
    def test_empty_list(self):
        policy = RetentionPolicy()
        now = datetime(2024, 6, 15)
        retained, expired = policy.apply([], as_of=now)
        assert retained == []
        assert expired == []

    def test_all_retained(self):
        now = datetime(2024, 6, 15)
        records = [
            _make_record(now - timedelta(days=10)),
            _make_record(now - timedelta(days=100)),
            _make_record(now - timedelta(days=364)),
        ]
        policy = RetentionPolicy()
        retained, expired = policy.apply(records, as_of=now)
        assert len(retained) == 3
        assert len(expired) == 0

    def test_all_expired(self):
        now = datetime(2024, 6, 15)
        records = [
            _make_record(now - timedelta(days=400)),
            _make_record(now - timedelta(days=500)),
        ]
        policy = RetentionPolicy()
        retained, expired = policy.apply(records, as_of=now)
        assert len(retained) == 0
        assert len(expired) == 2

    def test_mixed_retained_and_expired(self):
        now = datetime(2024, 6, 15)
        recent = _make_record(now - timedelta(days=30))
        old = _make_record(now - timedelta(days=400))
        policy = RetentionPolicy()
        retained, expired = policy.apply([recent, old], as_of=now)
        assert retained == [recent]
        assert expired == [old]

    def test_boundary_record_retained(self):
        """Exactly 365 days old should be retained (not expired)."""
        now = datetime(2024, 6, 15)
        boundary = _make_record(now - timedelta(days=365))
        policy = RetentionPolicy()
        retained, expired = policy.apply([boundary], as_of=now)
        assert len(retained) == 1
        assert len(expired) == 0
