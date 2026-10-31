"""Webhook delivery: a signed JSON POST to a configured URL.

* :class:`WebhookNotificationSink` implements the strict
  :class:`~niuu.ports.notifications.NotificationSink` used by the Forge outbox. It
  POSTs the full notification and raises
  :class:`~niuu.ports.notifications.NotificationDeliveryError` (retryable on 408,
  425, 429, 5xx and network errors; permanent on other 4xx and redirects).
* :class:`WebhookNotificationAdapter` is the per-user
  :class:`~niuu.ports.notification_channel.NotificationChannel` Ting uses; it
  raises the same errors. It moved here from ``ting.adapters.webhook_notification``,
  which re-exports it.

Both sign the exact request body with HMAC-SHA256 when a ``secret`` is set and
send it as ``X-Niuu-Signature: sha256=<hex>``. Receivers should reject requests
whose signature does not match.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from niuu.adapters.notifications.delivery_http import (
    DEFAULT_TIMEOUT_SECONDS,
    SIGNATURE_HEADER,
    redact_url,
    sign_body,
    status_failure,
    transport_failure,
)
from niuu.ports.notification_channel import (
    Notification,
    NotificationChannel,
    NotificationUrgency,
)
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationDeliveryError,
    NotificationSink,
    OutboundNotification,
)

logger = logging.getLogger(__name__)

EVENT_HEADER = "X-Niuu-Event"
DELIVERY_HEADER = "X-Niuu-Delivery"
FORGE_NOTIFICATION_EVENT = "forge.notification"
PAYLOAD_VERSION = 1

_URGENCY_RANK = {
    NotificationUrgency.LOW: 0,
    NotificationUrgency.MEDIUM: 1,
    NotificationUrgency.HIGH: 2,
}


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _headers(body: bytes, *, secret: str, extra: dict[str, str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json", **extra}
    if secret:
        headers[SIGNATURE_HEADER] = sign_body(secret, body)
    return headers


class WebhookNotificationSink(NotificationSink):
    """POSTs each notification as signed JSON to ``url``.

    Body::

        {"type": "forge.notification", "version": 1, "delivery_id": "…",
         "attempt": 1, "notification": {…OutboundNotification.to_payload()…}}

    Delivery is at-least-once: ``X-Niuu-Delivery`` (also ``delivery_id``) is
    stable across retries of the same delivery, so receivers can de-duplicate.
    """

    def __init__(
        self,
        url: str = "",
        *,
        name: str = "webhook",
        secret: str = "",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        **_unused: object,
    ) -> None:
        self._name = name
        self._url = url
        self._secret = secret
        self._extra_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._target = f"webhook {redact_url(url)}" if url else "webhook"

    @property
    def name(self) -> str:
        return self._name

    async def send(self, message: OutboundNotification) -> DeliveryResult:
        if not self._url:
            raise NotificationDeliveryError("Webhook sink has no url configured", retryable=False)
        body = _encode(
            {
                "type": FORGE_NOTIFICATION_EVENT,
                "version": PAYLOAD_VERSION,
                "delivery_id": message.delivery_id,
                "attempt": message.attempt,
                "notification": message.to_payload(),
            }
        )
        extra = {**self._extra_headers, EVENT_HEADER: FORGE_NOTIFICATION_EVENT}
        if message.delivery_id:
            extra[DELIVERY_HEADER] = message.delivery_id
        try:
            response = await self._client.post(
                self._url, content=body, headers=_headers(body, secret=self._secret, extra=extra)
            )
        except httpx.HTTPError as exc:
            raise transport_failure(exc, self._target) from None
        if not response.is_success:
            raise status_failure(self._target, response)
        request_id = response.headers.get("x-request-id")
        return DeliveryResult(provider_message_id=request_id, detail=f"HTTP {response.status_code}")

    async def close(self) -> None:
        await self._client.aclose()


class WebhookNotificationAdapter(NotificationChannel):
    """Webhook channel for Ting's event notifications.

    POSTs Ting's notification JSON to a user-configured URL: a Slack/Discord
    webhook, a custom backend, an n8n / Zapier hook. ``send`` raises
    :class:`~niuu.ports.notifications.NotificationDeliveryError` when the POST is
    not accepted.
    """

    def __init__(
        self,
        url: str,
        *,
        secret: str = "",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        min_urgency: str = "low",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._secret = secret
        self._client = httpx.AsyncClient(timeout=timeout)
        self._min_urgency = NotificationUrgency(min_urgency)
        self._extra_headers = dict(headers or {})

    def should_notify(self, notification: Notification) -> bool:
        """Only notify if the urgency meets the minimum threshold."""
        return _URGENCY_RANK.get(notification.urgency, 0) >= _URGENCY_RANK[self._min_urgency]

    async def send(self, notification: Notification) -> None:
        """POST a JSON payload describing the notification; raise if it is not accepted."""
        if not self._url:
            raise NotificationDeliveryError(
                "Webhook channel has no url configured", retryable=False
            )

        body = _encode(
            {
                "event_type": notification.event_type,
                "title": notification.title,
                "body": notification.body,
                "urgency": str(notification.urgency),
                "owner_id": notification.owner_id,
                "metadata": dict(notification.metadata),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        headers = _headers(body, secret=self._secret, extra=self._extra_headers)
        target = redact_url(self._url)
        try:
            resp = await self._client.post(self._url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            # Only the type: httpx messages can carry the full (secret-bearing) URL.
            raise transport_failure(exc, target) from None
        if not resp.is_success:
            raise status_failure(target, resp)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
