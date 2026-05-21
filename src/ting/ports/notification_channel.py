"""Notification channel port — abstract interface for delivering notifications."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class NotificationUrgency(StrEnum):
    """Urgency level for a notification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Notification:
    """A user-facing notification built from a domain event."""

    title: str
    body: str
    urgency: NotificationUrgency
    owner_id: str
    event_type: str
    metadata: dict[str, str] = field(default_factory=dict)


class NotificationChannel(ABC):
    """Abstract notification delivery channel (Telegram, Slack, etc.)."""

    @abstractmethod
    async def send(self, notification: Notification) -> None:
        """Deliver a notification through this channel."""

    @abstractmethod
    def should_notify(self, notification: Notification) -> bool:
        """Return True if this channel should deliver the given notification."""
