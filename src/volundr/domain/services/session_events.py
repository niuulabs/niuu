"""Principal-scoped view of the realtime session event stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from enum import Enum

from volundr.domain.models import EventType, Principal, RealtimeEvent, Stats
from volundr.domain.ports import EventBroadcaster

from .session import SessionService
from .stats import StatsService

# ``SessionService.visibility_scope``: (tenant bound, owner bound), None unbounded.
_Scope = tuple[str | None, str | None]


class EventAudience(Enum):
    """Who may receive an event of a given type."""

    # Deployment-wide signals with no session or tenant data of their own.
    ANY_SUBSCRIBER = "any_subscriber"
    # A figure-less tick; each subscriber receives figures computed over the
    # sessions it may list, exactly what GET /stats returns to it.
    SCOPED_AGGREGATE = "scoped_aggregate"
    # Carries the session id plus its ``owner_id``/``tenant_id``.
    SESSION = "session"
    # A per-reader hint whose ``owner_id`` is the reader, not the session owner.
    READER = "reader"
    # Names no session, owner or tenant, so no bounded subscriber can be shown it.
    UNATTRIBUTED = "unattributed"


EVENT_AUDIENCE: dict[EventType, EventAudience] = {
    EventType.HEARTBEAT: EventAudience.ANY_SUBSCRIBER,
    EventType.STATS_UPDATED: EventAudience.SCOPED_AGGREGATE,
    EventType.SESSION_CREATED: EventAudience.SESSION,
    EventType.SESSION_UPDATED: EventAudience.SESSION,
    EventType.SESSION_DELETED: EventAudience.SESSION,
    EventType.SESSION_ACTIVITY: EventAudience.SESSION,
    EventType.SESSION_NEEDS_INPUT: EventAudience.SESSION,
    EventType.CHRONICLE_CREATED: EventAudience.SESSION,
    EventType.CHRONICLE_UPDATED: EventAudience.SESSION,
    EventType.CHRONICLE_DELETED: EventAudience.SESSION,
    EventType.CHRONICLE_EVENT: EventAudience.SESSION,
    EventType.PR_CREATED: EventAudience.SESSION,
    EventType.SESSION_READ_STATE: EventAudience.READER,
    EventType.PR_MERGED: EventAudience.UNATTRIBUTED,
}


def stats_event(stats: Stats, *, timestamp: datetime) -> RealtimeEvent:
    """Build the ``stats_updated`` event a subscriber receives for *stats*."""
    return RealtimeEvent(
        type=EventType.STATS_UPDATED,
        data={
            "active_sessions": stats.active_sessions,
            "total_sessions": stats.total_sessions,
            "sessions_today": stats.sessions_today,
            "tokens_today": stats.tokens_today,
            "local_tokens": stats.local_tokens,
            "cloud_tokens": stats.cloud_tokens,
            "cost_today": float(stats.cost_today),
            "sparklines": stats.sparklines or {},
        },
        timestamp=timestamp,
    )


class SessionEventStream:
    """Deliver each subscriber only the events for sessions it may list.

    Visibility is ``SessionService.may_observe``, the same bounds as
    ``SessionService.list_sessions``: a caller's own sessions, or its tenant's
    sessions for a tenant admin. With no identity configured (``principal`` is
    ``None`` and no authorization adapter), the stream is unscoped, exactly like
    the list endpoint.

    A ``stats_updated`` tick is replaced by figures over the same bounds. They
    are computed once per tick for each distinct scope, however many
    subscribers share it.
    """

    def __init__(
        self,
        broadcaster: EventBroadcaster,
        sessions: SessionService,
        stats: StatsService,
    ) -> None:
        self._broadcaster = broadcaster
        self._sessions = sessions
        self._stats = stats
        self._stats_locks: dict[_Scope, asyncio.Lock] = {}
        self._latest_stats: dict[_Scope, tuple[RealtimeEvent, RealtimeEvent]] = {}

    def authorize(self, principal: Principal | None) -> None:
        """Refuse a subscription before any response bytes are sent.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
        """
        self._sessions.visibility_scope(principal)

    async def subscribe(self, principal: Principal | None) -> AsyncGenerator[RealtimeEvent, None]:
        """Yield the broadcast events *principal* is allowed to observe."""
        scope = self._sessions.visibility_scope(principal)
        # Owner and tenant never change for a session, so one decision per
        # session holds for the life of this subscription.
        decisions: dict[tuple[str, str | None, str | None], bool] = {}
        async for event in self._broadcaster.subscribe():
            if EVENT_AUDIENCE[event.type] is EventAudience.SCOPED_AGGREGATE:
                yield await self._scoped_stats(event, principal, scope)
                continue
            if await self._visible(event, principal, decisions):
                yield event

    async def _scoped_stats(
        self, tick: RealtimeEvent, principal: Principal | None, scope: _Scope
    ) -> RealtimeEvent:
        # Every subscriber's queue holds the same tick object, so identity
        # marks the tick a cached result was computed for.
        async with self._stats_locks.setdefault(scope, asyncio.Lock()):
            latest = self._latest_stats.get(scope)
            if latest is not None and latest[0] is tick:
                return latest[1]
            stats = await self._stats.get_stats(principal)
            event = stats_event(stats, timestamp=tick.timestamp)
            self._latest_stats[scope] = (tick, event)
            return event

    async def _visible(
        self,
        event: RealtimeEvent,
        principal: Principal | None,
        decisions: dict[tuple[str, str | None, str | None], bool],
    ) -> bool:
        audience = EVENT_AUDIENCE[event.type]
        if audience is EventAudience.ANY_SUBSCRIBER:
            return True
        if audience is EventAudience.UNATTRIBUTED:
            return principal is None
        if audience is EventAudience.READER:
            return principal is None or event.data["owner_id"] == principal.user_id
        if "owner_id" not in event.data or "tenant_id" not in event.data:
            raise ValueError(
                f"{event.type.value} event carries no owner_id/tenant_id, so it cannot be "
                "scoped to a subscriber; the publisher must include both"
            )
        session_id = str(event.data.get("session_id") or event.data["id"])
        owner_id = event.data["owner_id"] or None
        tenant_id = event.data["tenant_id"] or None
        key = (session_id, owner_id, tenant_id)
        if key not in decisions:
            decisions[key] = await self._sessions.may_observe(
                principal,
                session_id=session_id,
                owner_id=owner_id,
                tenant_id=tenant_id,
            )
        return decisions[key]
