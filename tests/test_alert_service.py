"""Unit tests for the alert notification service.

Tests cover:
- LoggingAlertNotifier stores alerts correctly
- SNSAlertNotifier calls the SNS client with correct params
- SNSAlertNotifier handles SNS errors gracefully
- Both classes satisfy the AlertNotifier protocol
"""

from unittest.mock import MagicMock

import pytest

from axonllm_ledger.alert_service import (
    AlertRecord,
    LoggingAlertNotifier,
    SNSAlertNotifier,
)
from axonllm_ledger.export import AlertNotifier


# ── Protocol conformance ──────────────────────────────────────────────


class TestProtocolConformance:
    def test_sns_notifier_satisfies_protocol(self):
        notifier = SNSAlertNotifier(topic_arn="arn:aws:sns:us-east-1:123:topic")
        assert isinstance(notifier, AlertNotifier)

    def test_logging_notifier_satisfies_protocol(self):
        notifier = LoggingAlertNotifier()
        assert isinstance(notifier, AlertNotifier)


# ── LoggingAlertNotifier ──────────────────────────────────────────────


class TestLoggingAlertNotifier:
    def test_stores_single_alert(self):
        notifier = LoggingAlertNotifier()
        notifier.send_alert("Test Subject", "Test message body")

        assert len(notifier.alerts) == 1
        assert notifier.alerts[0] == AlertRecord(
            subject="Test Subject", message="Test message body"
        )

    def test_stores_multiple_alerts_in_order(self):
        notifier = LoggingAlertNotifier()
        notifier.send_alert("First", "msg1")
        notifier.send_alert("Second", "msg2")
        notifier.send_alert("Third", "msg3")

        assert len(notifier.alerts) == 3
        assert [a.subject for a in notifier.alerts] == ["First", "Second", "Third"]

    def test_alerts_list_is_a_copy(self):
        notifier = LoggingAlertNotifier()
        notifier.send_alert("A", "B")
        alerts = notifier.alerts
        alerts.clear()
        # Internal list should be unaffected
        assert len(notifier.alerts) == 1

    def test_starts_empty(self):
        notifier = LoggingAlertNotifier()
        assert notifier.alerts == []


# ── SNSAlertNotifier ─────────────────────────────────────────────────


class TestSNSAlertNotifier:
    def test_publishes_to_sns_with_correct_params(self):
        mock_client = MagicMock()
        topic_arn = "arn:aws:sns:us-east-1:123456789012:GenAICostAlerts"
        notifier = SNSAlertNotifier(topic_arn=topic_arn, sns_client=mock_client)

        notifier.send_alert("Pipeline failure", "CID collection failed for Budgets")

        mock_client.publish.assert_called_once_with(
            TopicArn=topic_arn,
            Subject="Pipeline failure",
            Message="CID collection failed for Budgets",
        )

    def test_handles_sns_error_gracefully(self, caplog):
        mock_client = MagicMock()
        mock_client.publish.side_effect = Exception("SNS unavailable")
        notifier = SNSAlertNotifier(
            topic_arn="arn:aws:sns:us-east-1:123:topic", sns_client=mock_client
        )

        # Should not raise
        notifier.send_alert("Error subject", "Error message")

        # Verify the error was logged
        assert any("Failed to publish SNS alert" in r.message for r in caplog.records)

    def test_lazy_client_creation_uses_injected_client(self):
        mock_client = MagicMock()
        notifier = SNSAlertNotifier(
            topic_arn="arn:aws:sns:us-east-1:123:topic", sns_client=mock_client
        )
        notifier.send_alert("sub", "msg")

        # The injected client should be used, not boto3
        assert mock_client.publish.called

    def test_multiple_alerts_each_publish(self):
        mock_client = MagicMock()
        notifier = SNSAlertNotifier(
            topic_arn="arn:aws:sns:us-east-1:123:topic", sns_client=mock_client
        )

        notifier.send_alert("Alert 1", "Message 1")
        notifier.send_alert("Alert 2", "Message 2")

        assert mock_client.publish.call_count == 2
