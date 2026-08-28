"""AccessRecord retention policy for the AxonLLM Ledger system.

Implements a 12-month minimum retention policy for AccessRecords
as required by Requirement 9.4.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from axonllm_ledger.models import AccessRecord

# Minimum retention period: 12 months (365 days)
RETENTION_PERIOD = timedelta(days=365)


class RetentionPolicy:
    """Applies retention rules to AccessRecords.

    Records older than RETENTION_PERIOD from the reference time
    are considered expired and eligible for cleanup.
    """

    def __init__(self, retention_period: timedelta = RETENTION_PERIOD) -> None:
        self.retention_period = retention_period

    def is_expired(self, record: AccessRecord, as_of: datetime | None = None) -> bool:
        """Check if a single AccessRecord has exceeded the retention period.

        Args:
            record: The AccessRecord to check.
            as_of: Reference datetime. Defaults to the current UTC time.

        Returns:
            True if the record's timestamp is older than the retention period.
        """
        if as_of is None:
            as_of = _current_time_matching(record.timestamp)
        return (as_of - record.timestamp) > self.retention_period

    def apply(
        self,
        records: List[AccessRecord],
        as_of: datetime | None = None,
    ) -> Tuple[List[AccessRecord], List[AccessRecord]]:
        """Partition records into retained and expired lists.

        Args:
            records: List of AccessRecords to evaluate.
            as_of: Reference datetime. Defaults to the current UTC time.

        Returns:
            A tuple of (retained, expired) record lists.
        """
        if as_of is None:
            as_of = datetime.now(timezone.utc)
            if records and records[0].timestamp.tzinfo is None:
                as_of = as_of.replace(tzinfo=None)

        retained: List[AccessRecord] = []
        expired: List[AccessRecord] = []

        for record in records:
            if self.is_expired(record, as_of):
                expired.append(record)
            else:
                retained.append(record)

        return retained, expired


def _current_time_matching(reference: datetime) -> datetime:
    """Return current UTC time with awareness matching ``reference``."""
    now = datetime.now(timezone.utc)
    if reference.tzinfo is None:
        return now.replace(tzinfo=None)
    return now
