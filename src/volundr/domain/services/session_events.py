"""Principal-scoped view of the realtime session event stream."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from enum import Enum

from volundr.domain.models import EventType, Principal, RealtimeEvent
from volundr.domain.ports import EventBroadcaster

from .session import SessionService


class EventAudience(Enum):
    """Who may receive an event of a given type."""

    # Deployment-wide signals with no session or tenant data of their own.
    ANY_SUBSCRIBER = "any_subscriber"
    # Carries the session id plus its ``owner_id``/``tenant_id``.
    SESSION = "session"
    # A per-reader hint whose ``owner_id`` is the reader, not the session owner.
    READER = "reader"
    # Names no session, owner or tenant, so no bounded subscriber can be shown it.
    UNATTRIBUTED = "unattributed"


EVENT_AUDIENCE: dict[EventType, EventAudience] = {
    EventType.HEARTBEAT: EventAudience.ANY_SUBSCRIBER,
    # Same deployment-wide figures GET /stats returns to any caller.
    EventType.STATS_UPDATED: EventAudience.ANY_SUBSCRIBER,
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


class SessionEventStream:
    """Deliver each subscriber only the events for sessions it may list.

    Visibility is ``SessionService.may_observe``, the same bounds as
    ``SessionService.list_sessions``: a caller's own sessions, or its tenant's
    sessions for a tenant admin. With no identity configured (``principal`` is
    ``None`` and no authorization adapter), the stream is unscoped, exactly like
    the list endpoint.
    """

    def __init__(self, broadcaster: EventBroadcaster, sessions: SessionService) -> None:
        self._broadcaster = broadcaster
        self._sessions = sessions

    def authorize(self, principal: Principal | None) -> None:
        """Refuse a subscription before any response bytes are sent.

        Raises:
            PermissionError: Authorization is configured and no principal was given.
        """
        self._sessions.visibility_scope(principal)

    async def subscribe(self, principal: Principal | None) -> AsyncGenerator[RealtimeEvent, None]:
        """Yield the broadcast events *principal* is allowed to observe."""
        self.authorize(principal)
        # Owner and tenant never change for a session, so one decision per
        # session holds for the life of this subscription.
        decisions: dict[tuple[str, str | None, str | None], bool] = {}
        async for event in self._broadcaster.subscribe():
            if await self._visible(event, principal, decisions):
                yield event

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
