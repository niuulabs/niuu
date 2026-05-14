"""In-memory event bus adapter — fan-out pub/sub using asyncio queues.

This is the default EventBusPort implementation. Each subscriber gets a
dedicated ``asyncio.Queue``; ``emit`` fans out to all queues.  State-type
events are cached and replayed to new subscribers as an initial snapshot.
"""

from __future__ import annotations

import asyncio
from collections import deque

from ting.ports.event_bus import EventBusPort, TingEvent

# Event types that represent current persistent state.
# These are cached and replayed to new subscribers as an initial snapshot,
# so the UI doesn't need a separate hydration call.
_SNAPSHOT_TYPES: frozenset[str] = frozenset({"dispatcher.state"})


class InMemoryEventBus(EventBusPort):
    """In-process broadcast bus for Ting SSE events.

    One queue per connected SSE client.  ``emit`` fans out to all queues.
    State-type events are cached and replayed to new clients on subscribe.
    All emitted events are stored in a fixed-size ring buffer accessible
    via ``get_log``.
    """

    def __init__(self, max_clients: int = 10, log_size: int = 100) -> None:
        self._max_clients = max_clients
        self._queues: list[asyncio.Queue[TingEvent]] = []
        self._snapshots: dict[str, TingEvent] = {}
        self._log: deque[TingEvent] = deque(maxlen=log_size)

    @property
    def client_count(self) -> int:
        """Number of currently connected SSE clients."""
        return len(self._queues)

    @property
    def at_capacity(self) -> bool:
        """True when the maximum number of SSE clients is already connected."""
        return len(self._queues) >= self._max_clients

    def subscribe(self) -> asyncio.Queue[TingEvent]:
        """Register a new SSE client and return its dedicated queue."""
        q: asyncio.Queue[TingEvent] = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[TingEvent]) -> None:
        """Remove a client queue — safe to call even if already removed."""
        try:
            self._queues.remove(q)
        except ValueError:
            pass  # Queue already removed; safe to ignore

    async def emit(self, event: TingEvent) -> None:
        """Broadcast an event to every connected client queue.

        State-type events (``_SNAPSHOT_TYPES``) are also saved so they can be
        replayed to new subscribers via ``get_snapshot``.  All events are
        appended to the activity ring buffer.
        """
        if event.event in _SNAPSHOT_TYPES:
            self._snapshots[event.event] = event
        self._log.append(event)
        for q in list(self._queues):
            await q.put(event)

    def get_snapshot(self) -> list[TingEvent]:
        """Return the current state snapshot for delivery to a new subscriber."""
        return list(self._snapshots.values())

    def get_log(self, n: int) -> list[TingEvent]:
        """Return the last *n* events from the ring buffer, oldest-first."""
        events = list(self._log)
        return events[-n:] if n < len(events) else events
