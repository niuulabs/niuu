"""Push delivery for Forge notifications through Volundr's push channels.

``PushNotificationSink`` wraps the configured push ``NotificationChannel`` (APNs,
webhook relay or logging, from ``push.adapter``) and the owner's registered
devices (``DeviceTokenRepository``). It is opt-in: an operator lists it under
``notifications.sinks`` and an owner rule selects it.

It is also how the needs-input push is delivered: with ``push.enabled`` every owner
who registers a device gets a "Needs-input pushes" rule that sends the attention
items Forge raises (``source: system``) to the sink named by ``push.attention_sink``.
"""

from __future__ import annotations

from collections.abc import Mapping

from niuu.domain.notifications import NotificationSeverity
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationDeliveryError,
    NotificationSink,
    OutboundNotification,
)
from volundr.domain.models import PushMessage
from volundr.domain.notifications import PUSH_CATEGORY_KEY, PUSH_DATA_KEY
from volundr.domain.ports import DeviceTokenRepository, NotificationChannel
from volundr.domain.push import PushTokenKind

PUSH_NOTIFICATION_EVENT = "session.notification"

#: Push urgency per severity (the channel's 0..1 scale).
DEFAULT_SEVERITY_URGENCY: dict[str, float] = {
    NotificationSeverity.INFO.value: 0.3,
    NotificationSeverity.SUCCESS.value: 0.5,
    NotificationSeverity.WARNING.value: 0.8,
    NotificationSeverity.CRITICAL.value: 1.0,
}

#: Push bodies stay short: APNs caps the whole payload at 4 KB.
DEFAULT_PUSH_BODY_CHARS = 280


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _push_shaping(message: OutboundNotification) -> tuple[str, dict[str, str]]:
    """The APNs category and custom keys a notification asks its push to carry.

    Set by the producer in the notification's metadata (``PUSH_CATEGORY_KEY``,
    ``PUSH_DATA_KEY``); absent for every other push. A malformed value is a producer
    bug that no retry can fix, so it fails the delivery instead of being dropped.
    """
    category = message.metadata.get(PUSH_CATEGORY_KEY, "")
    data = message.metadata.get(PUSH_DATA_KEY, {})
    if not isinstance(category, str):
        raise NotificationDeliveryError(
            f"Notification metadata {PUSH_CATEGORY_KEY!r} must be a string", retryable=False
        )
    valid = isinstance(data, dict) and all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    )
    if not valid:
        raise NotificationDeliveryError(
            f"Notification metadata {PUSH_DATA_KEY!r} must map strings to strings", retryable=False
        )
    return category, dict(data)


class PushNotificationSink(NotificationSink):
    """Pushes a notification to every device the owner registered.

    ``device_repository`` and ``push_channel`` are supplied by Volundr when it
    builds the configured sinks; the remaining keyword arguments come from the
    sink's config entry.
    """

    def __init__(
        self,
        *,
        device_repository: DeviceTokenRepository,
        push_channel: NotificationChannel,
        name: str = "push",
        severity_urgency: Mapping[str, float] | None = None,
        max_body_chars: int = DEFAULT_PUSH_BODY_CHARS,
        **_unused: object,
    ) -> None:
        if max_body_chars < 1:
            raise ValueError("max_body_chars must be at least 1")
        self._name = name
        self._devices = device_repository
        self._channel = push_channel
        self._urgency = {**DEFAULT_SEVERITY_URGENCY, **(severity_urgency or {})}
        self._max_body_chars = max_body_chars

    @property
    def name(self) -> str:
        return self._name

    async def send(self, message: OutboundNotification) -> DeliveryResult:
        if not message.owner_id:
            raise NotificationDeliveryError(
                "Push needs an owner to find devices for", retryable=False
            )
        category, data = _push_shaping(message)
        # Alert tokens only: Live Activity and widget tokens take other push types.
        devices = await self._devices.list_for_owner(message.owner_id, kinds=(PushTokenKind.ALERT,))
        try:
            push = PushMessage(
                owner_id=message.owner_id,
                title=message.title,
                body=_shorten(message.body, self._max_body_chars),
                session_id=message.session_id or "",
                kind=message.kind.value,
                urgency=self._urgency.get(message.severity.value, 0.0),
                request_id=str(message.metadata.get("request_id") or ""),
                event_type=PUSH_NOTIFICATION_EVENT,
                notification_id=message.notification_id,
                url=message.url or "",
                resident_id=message.resident_id or "",
                resident_session_id=message.resident_session_id or "",
                category=category,
                data=data,
            )
        except ValueError as exc:
            raise NotificationDeliveryError(str(exc), retryable=False) from None
        detail = await self._channel.deliver(push, devices)
        return DeliveryResult(detail=detail)
