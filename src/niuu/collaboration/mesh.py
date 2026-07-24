"""Project collaboration events carried by a Niuu mesh into a room sink."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from niuu.mesh import mesh_event_prefix
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

FrameSink = Callable[[str, dict[str, Any]], Awaitable[None]]
PeerRegistrar = Callable[..., Awaitable[Any]]
ParticipantCheck = Callable[[str], bool]


def mesh_peer_id(event: SleipnirEvent) -> str:
    """Return the outer mesh publisher identity, not an inner runtime source."""
    source = event.source or ""
    if ":" in source:
        return source.split(":", 1)[1]
    if source:
        return source
    return str(event.payload.get("ravn_source") or "")


class MeshCollaborationBridge:
    """Consume already-projected collaboration events from a mesh transport."""

    def __init__(
        self,
        subscriber: SleipnirSubscriber,
        *,
        handle_frame: FrameSink,
        register_peer: PeerRegistrar,
        has_participant: ParticipantCheck,
        session_id: str | None = None,
        environment_id: str = "",
    ) -> None:
        self._subscriber = subscriber
        self._handle_frame = handle_frame
        self._register_peer = register_peer
        self._has_participant = has_participant
        self._session_id = session_id
        self._event_prefix = mesh_event_prefix(environment_id)
        self._subscription: Subscription | None = None

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            [f"{self._event_prefix}.*"], self._handle_event
        )

    async def stop(self) -> None:
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None

    async def _handle_event(self, event: SleipnirEvent) -> None:
        if not self._matches_session(event):
            return
        ravn_event = event.payload.get("ravn_event")
        if isinstance(ravn_event, dict) and ravn_event.get("collaboration_routing_only"):
            return
        peer_id = mesh_peer_id(event)
        if not peer_id:
            return
        events = event.payload.get("collaboration_events")
        if not isinstance(events, list):
            logger.debug("mesh event has no collaboration projection type=%s", event.event_type)
            return

        if not self._has_participant(peer_id):
            persona = self._projected_persona(events) or peer_id
            await self._register_peer(
                peer_id=peer_id,
                persona=persona,
                display_name=persona,
            )

        projected_events = [event for event in events if isinstance(event, dict)]
        if projected_events:
            await self._handle_frame(
                peer_id,
                {"type": "collaboration.events", "events": projected_events},
            )

    def _matches_session(self, event: SleipnirEvent) -> bool:
        if self._session_id is None:
            return True
        return self._session_id in {
            event.correlation_id,
            event.payload.get("ravn_session_id"),
            event.payload.get("ravn_root_correlation_id"),
        }

    @staticmethod
    def _projected_persona(events: list[Any]) -> str:
        for event in events:
            if isinstance(event, dict) and event.get("persona"):
                return str(event["persona"])
        return ""
