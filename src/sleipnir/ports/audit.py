"""Port interface for the Sleipnir audit log repository."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from sleipnir.domain.events import SleipnirEvent

#: Default maximum events returned by a single query.
DEFAULT_QUERY_LIMIT = 100


@dataclass
class AuditQuery:
    """Parameters for querying the audit log.

    :param event_type_pattern: Shell-style glob pattern for event type filtering
        (e.g. ``"ravn.*"``, ``"*"``).  ``None`` matches all.
    :param from_ts: Return events at or after this timestamp (inclusive).
    :param to_ts: Return events at or before this timestamp (inclusive).
    :param correlation_id: Filter to events with this exact correlation ID.
    :param source: Filter to events from this exact source identifier.
    :param service: Exact source component before the first colon.
    :param limit: Maximum number of events to return.
    """

    event_type_pattern: str | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None
    correlation_id: str | None = None
    source: str | None = None
    limit: int = field(default=DEFAULT_QUERY_LIMIT)
    service: str | None = None


class AuditRepository(ABC):
    """Port for the audit log persistent store.

    Implementations must be safe for concurrent async callers.
    """

    @abstractmethod
    async def append(self, event: SleipnirEvent) -> None:
        """Persist *event* to the audit log.

        Duplicate event_ids are silently ignored (idempotent).

        :param event: The event to store.
        """

    @abstractmethod
    async def query(self, q: AuditQuery) -> list[SleipnirEvent]:
        """Return events matching the criteria in *q*.

        Results are ordered newest-first.  At most ``q.limit`` events are
        returned.

        :param q: Query parameters.
        :returns: Matching events, newest first.
        """

    @abstractmethod
    async def purge_expired(self) -> int:
        """Delete events whose TTL has elapsed.

        An event is expired when ``NOW() > timestamp + ttl``.  Events with
        ``ttl=None`` are never purged.

        :returns: Number of rows deleted.
        """
