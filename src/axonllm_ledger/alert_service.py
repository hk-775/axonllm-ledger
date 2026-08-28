"""Alert notification service for the AxonLLM Ledger system.

Provides concrete implementations of the AlertNotifier protocol:
- SNSAlertNotifier: publishes alerts to an AWS SNS topic
- LoggingAlertNotifier: logs alerts via Python logging (for local dev/testing)

Alert-worthy events (Requirements 2.4, 10.7, 11.1, 11.3, 11.4):
- CID pipeline collection failure
- Data gap detection
- Analytics export failure after retries
- Cross-dimension consistency violation
- CUR vs Budget reconciliation discrepancy > 1%
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List

logger = logging.getLogger(__name__)


@dataclass
class AlertRecord:
    """A recorded alert for inspection in tests."""

    subject: str
    message: str


class SNSAlertNotifier:
    """Publishes alert notifications to an AWS SNS topic.

    Parameters
    ----------
    topic_arn:
        The ARN of the SNS topic to publish to.
    sns_client:
        An optional boto3 SNS client. If not provided, one is created
        lazily on first use via ``boto3.client('sns')``.
    """

    def __init__(self, topic_arn: str, sns_client: Any = None) -> None:
        self._topic_arn = topic_arn
        self._sns_client = sns_client

    def _get_client(self) -> Any:
        if self._sns_client is None:
            import boto3

            self._sns_client = boto3.client("sns")
        return self._sns_client

    def send_alert(self, subject: str, message: str) -> None:
        """Publish an alert to the configured SNS topic.

        Logs the alert and publishes via SNS. If the publish call
        fails, the error is logged but not re-raised so that callers
        are never interrupted by notification failures.
        """
        logger.info("Sending SNS alert: subject=%s", subject)
        try:
            self._get_client().publish(
                TopicArn=self._topic_arn,
                Subject=subject,
                Message=message,
            )
        except Exception:
            logger.exception("Failed to publish SNS alert: subject=%s", subject)


class LoggingAlertNotifier:
    """Logs alerts via Python logging and stores them for test assertions.

    Useful for local development and unit testing where SNS is not
    available.
    """

    def __init__(self) -> None:
        self._alerts: List[AlertRecord] = []

    @property
    def alerts(self) -> List[AlertRecord]:
        """Return the list of recorded alerts."""
        return list(self._alerts)

    def send_alert(self, subject: str, message: str) -> None:
        """Log the alert and store it for later inspection."""
        logger.warning("ALERT — %s: %s", subject, message)
        self._alerts.append(AlertRecord(subject=subject, message=message))
