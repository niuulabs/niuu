"""Notification delivery port: sink-neutral messages and the sinks that deliver them.

A Forge notification is recorded once, in a durable feed. Delivering it to an
external channel (Telegram, a webhook, push) is a separate, retried step owned by
an outbox dispatcher. The dispatcher renders each notification into an
:class:`OutboundNotification` (a plain, sink-neutral message with its deep link
already resolved) and hands it to a :class:`NotificationSink`.

A sink either returns a :class:`DeliveryResult` or raises
:class:`NotificationDeliveryError`. It never swallows a failure: the dispatcher
needs to know whether to retry, give up, or record success.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from niuu.domain.notifications import (
    NotificationKind,
    NotificationSeverity,
    NotificationSource,
)


@dataclass(frozen=True)
class OutboundLink:
    """A link carried by a notification (a PR, an artifact, a file)."""

    label: str
    url: str | None = None
    kind: str = "url"


@dataclass(frozen=True)
class OutboundNotification:
    """A rendered, sink-neutral notification ready for external delivery.

    ``url`` is the absolute deep link back to Forge (the session at the
    notification, or the feed). ``delivery_id`` and ``attempt`` identify this
    delivery attempt: delivery is at-least-once, so receivers that must not act
    twice should de-duplicate on ``delivery_id``.
    """

    notification_id: str
    title: str
    kind: NotificationKind
    severity: NotificationSeverity
    source: NotificationSource
    created_at: datetime
    body: str = ""
    owner_id: str = ""
    session_id: str | None = None
    session_label: str | None = None
    host_label: str | None = None
    project_id: str | None = None
    url: str | None = None
    links: tuple[OutboundLink, ...] = ()
    engine: str | None = None
    model: str | None = None
    correlation_id: str | None = None
    delivery_id: str | None = None
    attempt: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Set for a resident's notification (it has no session): the resident, and the
    #: channel it came from (``None`` for the resident's main room).
    resident_id: str | None = None
    resident_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """The full notification as JSON-safe data (used by webhook-style sinks)."""
        return {
            "id": self.notification_id,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "source": self.source.value,
            "title": self.title,
            "body": self.body,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "session_name": self.session_label,
            "resident_id": self.resident_id,
            "resident_session_id": self.resident_session_id,
            "host": self.host_label,
            "project_id": self.project_id,
            "url": self.url,
            "links": [
                {"label": link.label, "url": link.url, "kind": link.kind} for link in self.links
            ],
            "engine": self.engine,
            "model": self.model,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class DeliveryResult:
    """A successful delivery. ``provider_message_id`` is the remote id, if any."""

    provider_message_id: str | None = None
    detail: str = ""


class NotificationDeliveryError(Exception):
    """A delivery failed.

    ``retryable`` tells the dispatcher whether another attempt can succeed (a
    rate limit, a 5xx, a network error) or not (a bad token, an unknown chat, a
    4xx). ``retry_after`` is the provider's requested wait in seconds, when it
    sent one. Messages must never contain credentials.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        retry_after: float | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.status_code = status_code


class NotificationSink(ABC):
    """Delivers rendered notifications to one external channel."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The sink's configured name (shown in delivery rows and logs)."""

    @abstractmethod
    async def send(self, message: OutboundNotification) -> DeliveryResult:
        """Deliver ``message`` or raise :class:`NotificationDeliveryError`."""

    def skip_reason(self, message: OutboundNotification) -> str | None:
        """Why this sink deliberately does not deliver ``message``, else ``None``.

        A skipped delivery is recorded as ``suppressed`` with this reason. The
        push sink uses it to avoid repeating a push another path already sent.
        """
        return None

    async def close(self) -> None:
        """Release network clients. Safe to call more than once."""
        return None


class NotificationSinkUnavailableError(NotificationDeliveryError):
    """A rule's sink cannot be resolved (missing, disabled, not permitted)."""


class NotificationSinkResolver(ABC):
    """Resolves the sink behind an owner's integration connection."""

    @abstractmethod
    async def for_connection(self, owner_id: str, connection_id: str) -> NotificationSink:
        """Build the sink for ``connection_id``, which must belong to ``owner_id``.

        Raises :class:`NotificationSinkUnavailableError` when the connection is
        missing, disabled, owned by someone else, not a messaging integration, or
        has no usable credential or adapter. The caller owns the returned sink and
        must ``close()`` it.
        """
