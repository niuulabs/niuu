"""In-memory event broadcaster adapter for SSE real-time updates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from volundr.domain.models import EventType, RealtimeEvent, Stats, TimelineResponse
from volundr.domain.ports import EventBroadcaster

if TYPE_CHECKING:
    from sleipnir.ports.events import SleipnirPublisher
    from volundr.domain.models import Session, TimelineEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping from Volundr EventType → Sleipnir event type string
# ---------------------------------------------------------------------------
# Import lazily to avoid a hard dependency on sleipnir at module load time.
def _build_realtime_sleipnir_map() -> dict[str, str]:
    from sleipnir.domain import registry  # noqa: PLC0415

    return {
        EventType.SESSION_CREATED.value: registry.VOLUNDR_SESSION_CREATED,
        EventType.SESSION_UPDATED.value: registry.VOLUNDR_SESSION_UPDATED,
        EventType.SESSION_DELETED.value: registry.VOLUNDR_SESSION_DELETED,
        EventType.STATS_UPDATED.value: registry.VOLUNDR_STATS_UPDATED,
        EventType.CHRONICLE_CREATED.value: registry.VOLUNDR_CHRONICLE_CREATED,
        EventType.CHRONICLE_UPDATED.value: registry.VOLUNDR_CHRONICLE_UPDATED,
        EventType.CHRONICLE_DELETED.value: registry.VOLUNDR_CHRONICLE_DELETED,
        EventType.CHRONICLE_EVENT.value: registry.VOLUNDR_CHRONICLE_UPDATED,
    }


class InMemoryEventBroadcaster(EventBroadcaster):
    """In-memory implementation of EventBroadcaster using asyncio queues.

    Each subscriber gets their own queue to receive events. Events are
    published to all active subscriber queues. If a subscriber's queue
    is full, old events are dropped to prevent memory issues with slow
    consumers.

    When a :class:`~sleipnir.ports.events.SleipnirPublisher` is provided,
    high-level session lifecycle and chronicle events are also forwarded to
    the platform-wide event bus so that Skuld brokers and other services can
    react in real time.
    """

    def __init__(
        self,
        max_queue_size: int = 100,
        sleipnir_publisher: SleipnirPublisher | None = None,
        sleipnir_source: str = "volundr",
    ):
        """Initialize the broadcaster.

        Args:
            max_queue_size: Maximum number of events to buffer per subscriber.
                When exceeded, oldest events are dropped.
            sleipnir_publisher: Optional Sleipnir publisher.  When provided,
                session lifecycle and chronicle events are forwarded to the
                platform event bus in addition to local SSE clients.
            sleipnir_source: Source string used in Sleipnir events (e.g.
                ``"volundr"`` or ``"volundr:prod"``).
        """
        self._subscribers: set[asyncio.Queue[RealtimeEvent]] = set()
        self._max_queue_size = max_queue_size
        self._lock = asyncio.Lock()
        self._sleipnir_publisher = sleipnir_publisher
        self._sleipnir_source = sleipnir_source
        self._sleipnir_type_map: dict[str, str] | None = None

    async def publish(self, event: RealtimeEvent) -> None:
        """Publish an event to all connected subscribers.

        Also forwards to Sleipnir if a publisher was provided at construction.

        Args:
            event: The event to broadcast.
        """
        subscriber_count = len(self._subscribers)
        logger.info(
            "SSE publish: event_type=%s, subscribers=%d",
            event.type.value,
            subscriber_count,
        )

        async with self._lock:
            dead_queues: list[asyncio.Queue[RealtimeEvent]] = []

            for queue in self._subscribers:
                try:
                    # Non-blocking put - if queue is full, drop oldest and retry
                    if queue.full():
                        logger.warning(
                            "SSE publish: subscriber queue full (size=%d), dropping oldest event",
                            self._max_queue_size,
                        )
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass  # Expected: race between full() check and get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    # Queue is broken, mark for removal
                    logger.warning("SSE publish: dead subscriber queue detected, removing")
                    dead_queues.append(queue)

            # Remove dead queues
            for queue in dead_queues:
                self._subscribers.discard(queue)

        logger.debug(
            "SSE publish complete: event_type=%s, delivered_to=%d subscribers",
            event.type.value,
            subscriber_count - len(dead_queues),
        )

        # Forward to Sleipnir when a publisher is configured
        if self._sleipnir_publisher is not None:
            await self._forward_to_sleipnir(event)

    async def _forward_to_sleipnir(self, event: RealtimeEvent) -> None:
        """Convert a RealtimeEvent to a SleipnirEvent and publish it."""
        if self._sleipnir_type_map is None:
            self._sleipnir_type_map = _build_realtime_sleipnir_map()

        sleipnir_type = self._sleipnir_type_map.get(event.type.value)
        if sleipnir_type is None:
            return  # Heartbeats and other non-forwarded events

        try:
            from datetime import UTC  # noqa: PLC0415

            from sleipnir.domain.events import SleipnirEvent  # noqa: PLC0415

            sleipnir_event = SleipnirEvent(
                event_type=sleipnir_type,
                source=self._sleipnir_source,
                payload=dict(event.data),
                summary=f"{event.type.value} broadcast",
                urgency=0.5,
                domain="code",
                timestamp=(
                    event.timestamp.replace(tzinfo=UTC)
                    if event.timestamp.tzinfo is None
                    else event.timestamp
                ),
            )
            await self._sleipnir_publisher.publish(sleipnir_event)
        except Exception:
            logger.warning(
                "InMemoryEventBroadcaster: failed to forward %s to Sleipnir",
                event.type.value,
                exc_info=True,
            )

    async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
        """Subscribe to receive events.

        Returns:
            An async generator that yields events as they are published.
        """
        queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue(maxsize=self._max_queue_size)

        async with self._lock:
            self._subscribers.add(queue)

        subscriber_count = len(self._subscribers)
        logger.info(
            "SSE subscribe: new subscriber connected, total_subscribers=%d",
            subscriber_count,
        )

        try:
            while True:
                event = await queue.get()
                logger.debug(
                    "SSE subscribe: yielding event_type=%s to subscriber",
                    event.type.value,
                )
                yield event
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
            logger.info(
                "SSE subscribe: subscriber disconnected, total_subscribers=%d",
                len(self._subscribers),
            )

    @property
    def subscriber_count(self) -> int:
        """Return the number of active subscribers."""
        return len(self._subscribers)

    def create_session_event(self, event_type: EventType, session: Session) -> RealtimeEvent:
        """Create a session-related event.

        Args:
            event_type: The type of session event.
            session: The session data.

        Returns:
            A RealtimeEvent with the session data.
        """
        return RealtimeEvent(
            type=event_type,
            data={
                "id": str(session.id),
                "name": session.name,
                "model": session.model,
                "repo": session.repo,
                "branch": session.branch,
                "status": session.status.value,
                "chat_endpoint": session.chat_endpoint,
                "code_endpoint": session.code_endpoint,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "last_active": (
                    session.last_active.isoformat()
                    if session.last_active
                    else session.created_at.isoformat()
                ),
                "message_count": session.message_count,
                "tokens_used": session.tokens_used,
                "pod_name": session.pod_name,
                "error": session.error,
                "source": session.source.model_dump() if session.source else None,
                "tracker_issue_id": session.tracker_issue_id,
                "issue_tracker_url": session.issue_tracker_url,
                "task_type": getattr(session, "task_type", None),
                "workload_type": getattr(session, "workload_type", "session"),
                "owner_id": str(session.owner_id) if session.owner_id else None,
                "tenant_id": str(session.tenant_id) if session.tenant_id else None,
            },
            timestamp=datetime.now(UTC),
        )

    def create_session_deleted_event(self, session_id: UUID) -> RealtimeEvent:
        """Create a session deleted event.

        Args:
            session_id: The ID of the deleted session.

        Returns:
            A RealtimeEvent for the deleted session.
        """
        return RealtimeEvent(
            type=EventType.SESSION_DELETED,
            data={"id": str(session_id)},
            timestamp=datetime.now(UTC),
        )

    def create_stats_event(self, stats: Stats) -> RealtimeEvent:
        """Create a stats update event.

        Args:
            stats: The current statistics.

        Returns:
            A RealtimeEvent with the stats data.
        """
        return RealtimeEvent(
            type=EventType.STATS_UPDATED,
            data={
                "active_sessions": stats.active_sessions,
                "total_sessions": stats.total_sessions,
                "tokens_today": stats.tokens_today,
                "local_tokens": stats.local_tokens,
                "cloud_tokens": stats.cloud_tokens,
                "cost_today": float(stats.cost_today),
            },
            timestamp=datetime.now(UTC),
        )

    def create_heartbeat_event(self) -> RealtimeEvent:
        """Create a heartbeat event to keep connections alive.

        Returns:
            A RealtimeEvent heartbeat.
        """
        return RealtimeEvent(
            type=EventType.HEARTBEAT,
            data={},
            timestamp=datetime.now(UTC),
        )

    async def publish_session_created(self, session: Session) -> None:
        """Publish a session created event.

        Args:
            session: The created session.
        """
        event = self.create_session_event(EventType.SESSION_CREATED, session)
        await self.publish(event)

    async def publish_session_updated(self, session: Session) -> None:
        """Publish a session updated event.

        Args:
            session: The updated session.
        """
        logger.info(
            "SSE broadcast session_updated: session=%s, tokens_used=%d, status=%s",
            session.id,
            session.tokens_used,
            session.status.value,
        )
        event = self.create_session_event(EventType.SESSION_UPDATED, session)
        await self.publish(event)

    async def publish_session_deleted(self, session_id: UUID) -> None:
        """Publish a session deleted event.

        Args:
            session_id: The ID of the deleted session.
        """
        event = self.create_session_deleted_event(session_id)
        await self.publish(event)

    async def publish_stats(self, stats: Stats) -> None:
        """Publish a stats update event.

        Args:
            stats: The current statistics.
        """
        logger.info(
            "SSE broadcast stats_updated: tokens_today=%d, cloud=%d, local=%d, cost=%.4f",
            stats.tokens_today,
            stats.cloud_tokens,
            stats.local_tokens,
            float(stats.cost_today),
        )
        event = self.create_stats_event(stats)
        await self.publish(event)

    async def publish_heartbeat(self) -> None:
        """Publish a heartbeat event."""
        event = self.create_heartbeat_event()
        await self.publish(event)

    async def publish_chronicle_event(
        self,
        session_id: UUID,
        event: TimelineEvent,
        timeline: TimelineResponse,
    ) -> None:
        """Publish a chronicle timeline event.

        Args:
            session_id: The session this event belongs to.
            event: The new timeline event to append.
            timeline: The full aggregated timeline (files, commits, token_burn).
        """
        event_data: dict = {
            "t": event.t,
            "type": event.type.value,
            "label": event.label,
        }
        if event.tokens is not None:
            event_data["tokens"] = event.tokens
        if event.action is not None:
            event_data["action"] = event.action
        if event.ins is not None:
            event_data["ins"] = event.ins
        if event.del_ is not None:
            event_data["del"] = event.del_
        if event.hash is not None:
            event_data["hash"] = event.hash
        if event.exit_code is not None:
            event_data["exit"] = event.exit_code

        realtime_event = RealtimeEvent(
            type=EventType.CHRONICLE_EVENT,
            data={
                "session_id": str(session_id),
                "event": event_data,
                "files": [
                    {"path": f.path, "status": f.status, "ins": f.ins, "del": f.del_}
                    for f in timeline.files
                ],
                "commits": [
                    {"hash": c.hash, "msg": c.msg, "time": c.time} for c in timeline.commits
                ],
                "token_burn": timeline.token_burn,
            },
            timestamp=datetime.now(UTC),
        )
        logger.info(
            "SSE broadcast chronicle_event: session=%s, type=%s, t=%d",
            session_id,
            event.type.value,
            event.t,
        )
        await self.publish(realtime_event)
