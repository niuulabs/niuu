"""Relay external events as neutral observations to a collaboration participant."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from niuu.collaboration.models import Participant
from niuu.collaboration.room import matches_subscription
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

ParticipantLookup = Callable[[], Participant | None]
DirectedSend = Callable[[str, str, dict[str, Any]], Awaitable[str]]
NotificationSend = Callable[[dict[str, Any]], Awaitable[None]]

_ORIGIN_TOKEN_SPLIT = re.compile(r"[\s:/,]+")
_IGNORED_EVENT_PREFIXES = ("ravn.mesh.",)


def render_observation(event: SleipnirEvent, payload_preview_chars: int) -> str:
    """Render evidence without instructions about its interpretation or handling."""
    payload_json = json.dumps(event.payload or {}, sort_keys=True, default=str)
    if len(payload_json) > payload_preview_chars:
        payload_json = payload_json[:payload_preview_chars] + "…(truncated)"
    observation = {
        "event_type": event.event_type,
        "summary": event.summary or "",
        "correlation_id": event.correlation_id or "",
        "payload": payload_json,
    }
    return "External observation received:\n" + json.dumps(observation, sort_keys=True, default=str)


class ObservationRelay:
    """Deliver subscribed external events without embedding agent policy."""

    def __init__(
        self,
        subscriber: SleipnirSubscriber,
        *,
        participant: ParticipantLookup,
        patterns: list[str],
        send_directed: DirectedSend,
        broadcast_notification: NotificationSend,
        payload_preview_chars: int,
    ) -> None:
        self._subscriber = subscriber
        self._participant = participant
        self._patterns = list(patterns)
        self._send_directed = send_directed
        self._broadcast_notification = broadcast_notification
        self._payload_preview_chars = payload_preview_chars
        self._subscription: Subscription | None = None

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(self._patterns, self._handle_event)

    async def stop(self) -> None:
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None

    async def _handle_event(self, event: SleipnirEvent) -> None:
        if event.event_type.startswith(_IGNORED_EVENT_PREFIXES):
            return
        participant = self._participant()
        if participant is None:
            logger.warning("observation dropped because target participant is absent")
            return
        if not matches_subscription(event.event_type, participant.subscribes_to):
            return
        if self._originates_from(event, participant.peer_id):
            return
        provenance = str((event.payload or {}).get("resident_peer_id") or "").strip()
        if provenance and provenance != participant.peer_id:
            return

        await self._broadcast_notification(
            {
                "type": "room_notification",
                "notificationType": "external_observation",
                "participantId": participant.peer_id,
                "eventType": event.event_type,
                "summary": event.summary or "",
                "correlationId": event.correlation_id or "",
            }
        )
        metadata: dict[str, Any] = {
            "external_observation": True,
            "event_type": event.event_type,
            "event_id": event.event_id,
            "correlation_id": event.correlation_id or "",
            "payload": dict(event.payload or {}),
        }
        try:
            await self._send_directed(
                participant.peer_id,
                render_observation(event, self._payload_preview_chars),
                metadata,
            )
        except LookupError:
            logger.warning("observation target disconnected event_type=%s", event.event_type)

    @staticmethod
    def _originates_from(event: SleipnirEvent, peer_id: str) -> bool:
        payload = event.payload or {}
        origin = f"{payload.get('ravn_source') or ''} {event.source or ''}"
        return peer_id in _ORIGIN_TOKEN_SPLIT.split(origin)


__all__ = ["ObservationRelay", "render_observation"]
