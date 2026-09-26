"""In-memory live-activity window for the Mímir 3D memory UI.

Tracks who is reading/writing which page *right now*, in a small bounded
ring buffer scoped to one process. This is presence/activity, not an audit
log: it is never persisted, and it is deliberately distinct from the
existing durable ``GET /mimir/activity`` and ``GET /mimir/mounts/recent-writes``
routes (which stay attributed to "mimir" and are out of scope here — see
``mimir.router``).

``LiveActivityRecorder`` is a plain, synchronous, dependency-free domain
class so it is trivially unit-testable with an injected clock — no asyncio,
no I/O. ``record()`` never blocks and never raises for a normal read/write:
recording activity must not be able to turn a successful page read into a
failed request.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

ActivityKind = Literal["read", "write"]


@dataclass(frozen=True)
class LiveActivityEvent:
    """A single recorded read or write, attributed where a caller could be verified."""

    id: str
    timestamp: datetime
    kind: ActivityKind
    mount: str
    path: str
    actor: str | None


class LiveActivityRecorder:
    """A bounded, thread-safe ring buffer of recent Mímir page activity.

    Args:
        buffer_size: Maximum number of events retained (oldest evicted
            first). Comes from ``mimir.config.LiveActivityConfig`` — never
            hardcoded by a caller (``.claude/rules/no-magic-numbers.md``).
        window_seconds: Only events within this many seconds of "now" (per
            *clock*) are ever returned by ``list_since``.
        clock: Returns the current UTC time. Injectable so tests control
            time deterministically instead of racing the wall clock;
            defaults to ``datetime.now(UTC)``.
    """

    def __init__(
        self,
        buffer_size: int,
        window_seconds: int,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._window_seconds = window_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._events: deque[LiveActivityEvent] = deque(maxlen=buffer_size)
        # Guards the deque against concurrent access from different asyncio
        # tasks/threads. record()/list_since() do no I/O and never await
        # while holding it, so this is never a contention point.
        self._lock = Lock()

    def record(self, *, kind: ActivityKind, mount: str, path: str, actor: str | None) -> None:
        """Append one event, timestamped now. Synchronous — no I/O, never raises
        for a full buffer (the oldest event is simply evicted)."""
        event = LiveActivityEvent(
            id=uuid.uuid4().hex,
            timestamp=self._clock(),
            kind=kind,
            mount=mount,
            path=path,
            actor=actor,
        )
        with self._lock:
            self._events.append(event)

    def list_since(self, since: datetime | None) -> list[LiveActivityEvent]:
        """Return events newer than *since*, newest first, within the configured window.

        *since* is exclusive (strictly newer). A naive *since* is treated as
        UTC. Events older than ``window_seconds`` relative to "now" are
        never returned even when *since* is older still or ``None``.
        """
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        cutoff = self._clock() - timedelta(seconds=self._window_seconds)
        with self._lock:
            events = list(self._events)
        matching = [
            event
            for event in events
            if event.timestamp > cutoff and (since is None or event.timestamp > since)
        ]
        matching.sort(key=lambda event: event.timestamp, reverse=True)
        return matching
