"""ResidentRelay — platform Sleipnir events → resident directed turns.

A resident session hosts one long-lived ravn behind the room
(``room.default_target_peer_id``). The resident launches Ting workflows
(research campaigns, spec stacks, planning) that run in OTHER sessions;
their completion and gate events flow over Sleipnir. This relay closes the
loop: matching events wake the resident so the chat resumes itself.

Contract
--------
The resident's register frame declares ``subscribes_to`` — the event types
it wants to be woken for. The relay subscribes to a bounded set of
platform patterns (``resident_relay.event_patterns``) and delivers an event
only when:

1. its type matches the resident participant's ``subscribes_to``, and
2. provenance, when present (``payload["resident_peer_id"]``), names this
   resident.

Delivery is two-fold:

- a **directed message** to the resident (it takes an agent turn: read the
  artifacts, digest into Mimir, report to the room), and
- a **room_notification** broadcast so the operator sees the wake-up in
  the chat history even when detached.

``ravn.mesh.*`` intra-session activity is RoomMeshBridge's job, not ours —
events with that prefix are always ignored here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription

if TYPE_CHECKING:
    from skuld.room_bridge import RoomBridge

logger = logging.getLogger(__name__)

_MESH_PREFIX = "ravn.mesh."
_DEFAULT_PAYLOAD_PREVIEW_CHARS = 2000

DirectedSend = Callable[[str, str, dict[str, Any]], Awaitable[str]]
NotificationSend = Callable[[dict[str, Any]], Awaitable[None]]


def _matches_subscription(event_type: str, subscribes_to: tuple[str, ...] | list[str]) -> bool:
    """True when *event_type* matches a subscribes_to entry (exact or prefix.*)."""
    for pattern in subscribes_to:
        if pattern == event_type:
            return True
        if pattern.endswith(".*") and event_type.startswith(pattern[:-1]):
            return True
        if pattern.endswith("*") and event_type.startswith(pattern[:-1]):
            return True
    return False


def _render_event_message(
    event: SleipnirEvent,
    payload_preview_chars: int = _DEFAULT_PAYLOAD_PREVIEW_CHARS,
) -> str:
    """Render a platform event as the resident's wake-up instruction."""
    payload_json = json.dumps(event.payload or {}, sort_keys=True, default=str)
    if len(payload_json) > payload_preview_chars:
        payload_json = payload_json[:payload_preview_chars] + "…(truncated)"
    parts = [
        f"Platform event received: {event.event_type}",
    ]
    summary = (event.summary or "").strip()
    if summary:
        parts.append(f"Summary: {summary}")
    if event.correlation_id:
        parts.append(f"Correlation: {event.correlation_id}")
    parts.append(f"Payload: {payload_json}")
    parts.append(
        "This event matches your subscriptions. Read the referenced "
        "artifacts with your tools, update the initiative pages in Mimir, "
        "and post a concise summary to the room — the operator may be away, "
        "so your message must stand alone."
    )
    return "\n".join(parts)


class ResidentRelay:
    """Subscribes to platform Sleipnir events and wakes the resident."""

    def __init__(
        self,
        subscriber: SleipnirSubscriber,
        room_bridge: RoomBridge,
        *,
        resident_peer_id: str,
        patterns: list[str],
        send_directed: DirectedSend,
        broadcast_notification: NotificationSend,
        payload_preview_chars: int = _DEFAULT_PAYLOAD_PREVIEW_CHARS,
    ) -> None:
        self._subscriber = subscriber
        self._room_bridge = room_bridge
        self._resident_peer_id = resident_peer_id
        self._patterns = list(patterns)
        self._send_directed = send_directed
        self._broadcast_notification = broadcast_notification
        self._payload_preview_chars = payload_preview_chars
        self._subscription: Subscription | None = None

    async def start(self) -> None:
        if self._subscription is not None:
            logger.warning("ResidentRelay.start() called while already running — ignored")
            return
        self._subscription = await self._subscriber.subscribe(
            self._patterns,
            self._handle_event,
        )
        logger.info(
            "ResidentRelay started: resident=%s patterns=%s",
            self._resident_peer_id,
            self._patterns,
        )

    async def stop(self) -> None:
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None
        logger.info("ResidentRelay stopped: resident=%s", self._resident_peer_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _handle_event(self, event: SleipnirEvent) -> None:
        if event.event_type.startswith(_MESH_PREFIX):
            return

        # Self-origin guard: the resident's own turns publish events (e.g.
        # ravn.task.completed) that can match its own subscriptions. Without
        # this, a resident subscribed to a broad pattern re-wakes itself on
        # every turn — an unbounded feedback loop burning the daily budget.
        # Check the ORIGIN (who published), never resident_peer_id (which is
        # the deliver-to provenance target handled below). Match on exact
        # tokens, not substring: a substring test would make peer id
        # "flock-spec" wrongly suppress a distinct "flock-spec-writer".
        payload = event.payload or {}
        origin = f"{payload.get('ravn_source') or ''} {event.source or ''}"
        origin_tokens = re.split(r"[\s:/,]+", origin)
        if self._resident_peer_id and self._resident_peer_id in origin_tokens:
            return

        participant = self._room_bridge.participants.get(self._resident_peer_id)
        if participant is None:
            logger.warning(
                "ResidentRelay: resident %s not in room — dropping %s",
                self._resident_peer_id,
                event.event_type,
            )
            return

        if not _matches_subscription(event.event_type, participant.subscribes_to):
            return

        provenance_peer = str(payload.get("resident_peer_id") or "").strip()
        if provenance_peer and provenance_peer != self._resident_peer_id:
            return

        logger.info(
            "ResidentRelay: waking resident %s for %s (corr=%s)",
            self._resident_peer_id,
            event.event_type,
            event.correlation_id,
        )

        await self._broadcast_notification(
            {
                "type": "room_notification",
                "notification_type": "platform_event",
                "participantId": self._resident_peer_id,
                "event_type": event.event_type,
                "summary": event.summary or "",
                "correlation_id": event.correlation_id or "",
            }
        )

        metadata: dict[str, Any] = {
            "platform_event": True,
            "event_type": event.event_type,
            "event_id": event.event_id,
            "correlation_id": event.correlation_id or "",
            "payload": event.payload or {},
        }
        try:
            await self._send_directed(
                self._resident_peer_id,
                _render_event_message(event, self._payload_preview_chars),
                metadata,
            )
        except LookupError:
            # Resident registered but its socket dropped between the check
            # and the send. The notification is already in room history; the
            # loss of the turn is visible there rather than silent.
            logger.warning(
                "ResidentRelay: resident %s unreachable for %s — notification "
                "recorded, turn not delivered",
                self._resident_peer_id,
                event.event_type,
            )
