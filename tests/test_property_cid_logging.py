"""Property-based tests for CID collection logging.

Feature: axonllm-ledger, Property 4: CID Collection Logging Contains Required Fields

Validates: Requirements 2.3, 2.4
"""

from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st

from axonllm_ledger.cid_ingestion import (
    CIDCollectionResult,
    check_and_alert_on_failure,
    log_collection_run,
)
from axonllm_ledger.models import IngestionStatus


# --- Strategies ---

_source_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=40,
)

_record_counts = st.integers(min_value=0, max_value=1_000_000)

_statuses = st.sampled_from(list(IngestionStatus))

_error_messages = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Z"), whitelist_characters="-_.,:;"),
        min_size=1,
        max_size=200,
    ),
)


class _FakeNotifier:
    """Captures alert calls for assertion."""

    def __init__(self):
        self.alerts: list[tuple[str, str]] = []

    def send_alert(self, subject: str, message: str) -> None:
        self.alerts.append((subject, message))


# --- Property Tests ---


class TestCIDCollectionLogging:
    """Property 4: CID Collection Logging Contains Required Fields.

    For any completed CID collection run (success or failure), the resulting
    log entry should contain the collection timestamp, source name, record
    count, and collection status. For any failed collection run, an alert
    notification should also be produced containing the source name and error
    details.

    **Validates: Requirements 2.3, 2.4**
    """

    @settings(max_examples=100)
    @given(
        source=_source_names,
        record_count=_record_counts,
        status=_statuses,
        error_message=_error_messages,
    )
    def test_log_entry_contains_required_fields(
        self, source: str, record_count: int, status: IngestionStatus, error_message
    ):
        """For any collection run, log_collection_run produces a CIDCollectionResult
        with collection timestamp, source name, record count, and status.

        Feature: axonllm-ledger, Property 4: CID Collection Logging Contains Required Fields
        """
        result = log_collection_run(
            source=source,
            record_count=record_count,
            status=status,
            error_message=error_message,
        )

        assert isinstance(result, CIDCollectionResult)
        # Required field: source name
        assert result.source == source
        # Required field: record count
        assert result.record_count == record_count
        # Required field: status
        assert result.status is status
        # Required field: collection timestamp
        assert isinstance(result.collected_at, datetime)
        assert result.collected_at.tzinfo is not None
        # Error message preserved
        assert result.error_message == error_message

    @settings(max_examples=100)
    @given(
        source=_source_names,
        error_message=st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "Z"),
                whitelist_characters="-_.,:;",
            ),
            min_size=1,
            max_size=200,
        ),
    )
    def test_failed_run_produces_alert_with_source_and_error(
        self, source: str, error_message: str
    ):
        """For any failed collection run, check_and_alert_on_failure sends an
        alert containing the source name and error details.

        Feature: axonllm-ledger, Property 4: CID Collection Logging Contains Required Fields
        """
        result = log_collection_run(
            source=source,
            record_count=0,
            status=IngestionStatus.FAILED,
            error_message=error_message,
        )

        notifier = _FakeNotifier()
        alert_sent = check_and_alert_on_failure(result, notifier)

        assert alert_sent is True
        assert len(notifier.alerts) == 1

        subject, message = notifier.alerts[0]
        # Alert must contain source name
        assert source in subject or source in message
        # Alert must contain error details
        assert error_message in message or error_message in subject

    @settings(max_examples=100)
    @given(
        source=_source_names,
        record_count=_record_counts,
        status=st.sampled_from(
            [s for s in IngestionStatus if s is not IngestionStatus.FAILED]
        ),
    )
    def test_non_failed_run_does_not_alert(
        self, source: str, record_count: int, status: IngestionStatus
    ):
        """For any non-failed collection run, no alert is sent.

        Feature: axonllm-ledger, Property 4: CID Collection Logging Contains Required Fields
        """
        result = log_collection_run(
            source=source,
            record_count=record_count,
            status=status,
        )

        notifier = _FakeNotifier()
        alert_sent = check_and_alert_on_failure(result, notifier)

        assert alert_sent is False
        assert len(notifier.alerts) == 0
