"""Webhook notification adapter — POSTs notifications to a configured URL."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from tyr.ports.notification_channel import (
    Notification,
    NotificationChannel,
    NotificationUrgency,
)

logger = logging.getLogger(__name__)

_SIGNATURE_HEADER = "X-Niuu-Signature"
_DEFAULT_TIMEOUT_SECONDS = 10.0


class WebhookNotificationAdapter(NotificationChannel):
    """POSTs a JSON notification payload to a user-configured URL.

    Used for outbound integrations into anything that can accept an HTTP
    POST: a Slack/Discord webhook URL, a custom backend, an n8n / Zapier
    hook, etc.

    If a ``secret`` is provided, the request body is HMAC-SHA256 signed
    and the hex digest is sent in the ``X-Niuu-Signature`` header as
    ``sha256=<hex>``. The receiver should reject requests whose signature
    doesn't match — this prevents an attacker who knows the URL from
    forging notifications.
    """

    def __init__(
        self,
        url: str,
        *,
        secret: str = "",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        min_urgency: str = "low",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._secret = secret
        self._client = httpx.AsyncClient(timeout=timeout)
        self._min_urgency = NotificationUrgency(min_urgency)
        self._urgency_rank = {
            NotificationUrgency.LOW: 0,
            NotificationUrgency.MEDIUM: 1,
            NotificationUrgency.HIGH: 2,
        }
        self._extra_headers = dict(headers or {})

    def should_notify(self, notification: Notification) -> bool:
        """Only notify if the urgency meets the minimum threshold."""
        return (
            self._urgency_rank.get(notification.urgency, 0)
            >= self._urgency_rank[self._min_urgency]
        )

    async def send(self, notification: Notification) -> None:
        """POST a JSON payload describing the notification."""
        if not self._url:
            logger.warning("Cannot send webhook — url not configured")
            return

        payload: dict[str, Any] = {
            "event_type": notification.event_type,
            "title": notification.title,
            "body": notification.body,
            "urgency": str(notification.urgency),
            "owner_id": notification.owner_id,
            "metadata": dict(notification.metadata),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers.update(self._extra_headers)
        if self._secret:
            digest = hmac.new(self._secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers[_SIGNATURE_HEADER] = f"sha256={digest}"

        try:
            resp = await self._client.post(self._url, content=body, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "Webhook %s returned %d (body: %s)",
                    self._url,
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception:
            logger.warning(
                "Failed to deliver webhook notification to %s",
                self._url,
                exc_info=True,
            )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
