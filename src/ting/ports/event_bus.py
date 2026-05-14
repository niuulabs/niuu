"""EventBus port — abstract interface for Ting's event pub/sub system.

Consumers subscribe to receive TingEvent objects; producers emit events.
The port is infrastructure-agnostic — the in-memory implementation lives
in ``ting.adapters.memory_event_bus``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class TingEvent:
    """A single Ting SSE event.

    Attributes:
        event: Dot-separated event type (e.g. ``run.state_changed``).
        data: Arbitrary event payload.
        owner_id: Tenant/user who owns the resource that triggered this event.
            Empty string when the owner is unknown or the event is global.
        id: Unique event identifier (auto-generated UUID by default).
        timestamp: UTC timestamp when the event was created.
    """

    event: str
    data: dict[str, Any]
    owner_id: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_sse(self) -> str:
        """Serialise as SSE wire format (id / event / data / blank line)."""
        return f"id: {self.id}\nevent: {self.event}\ndata: {json.dumps(self.data)}\n\n"


class EventBusPort(ABC):
    """Abstract event bus for Ting domain events.

    Implementations must support fan-out to multiple subscribers,
    snapshot caching for state-type events, and capacity management.
    """

    @abstractmethod
    def subscribe(self) -> asyncio.Queue[TingEvent]:
        """Register a new subscriber and return its dedicated queue."""

    @abstractmethod
    def unsubscribe(self, q: asyncio.Queue[TingEvent]) -> None:
        """Remove a subscriber queue — safe to call even if already removed."""

    @abstractmethod
    async def emit(self, event: TingEvent) -> None:
        """Broadcast an event to every connected subscriber."""

    @abstractmethod
    def get_snapshot(self) -> list[TingEvent]:
        """Return the current state snapshot for delivery to a new subscriber."""

    @abstractmethod
    def get_log(self, n: int) -> list[TingEvent]:
        """Return the last *n* events from the activity ring buffer.

        The returned list is ordered oldest-first.  If fewer than *n* events
        have been emitted the full buffer is returned.
        """

    @property
    @abstractmethod
    def client_count(self) -> int:
        """Number of currently connected subscribers."""

    @property
    @abstractmethod
    def at_capacity(self) -> bool:
        """True when the maximum number of subscribers is already connected."""
