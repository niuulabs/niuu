"""RoomMeshBridge — Sleipnir mesh subscriber that feeds room wire events.

Subscribes to ``ravn.mesh.*`` Sleipnir events so that outcomes from any mesh
peer (Skuld coder, reviewer, security scanner, …) flow into the room UI
without the publisher needing to broadcast them separately.

Architecture
------------
Before NIU-624 the broker had a dual-publish pattern:

    mesh.publish(outcome_event, "code.changed")   # → other ravens
    channels.broadcast(room_outcome)              # → browser (manual copy)

After NIU-624 the broker publishes once to the mesh, and this bridge
translates the resulting ``ravn.mesh.*`` Sleipnir events into room wire
events:

    mesh.publish(outcome_event, "code.changed")
        ↓  (via Sleipnir)
    RoomMeshBridge._handle_event()
        ↓
    RoomBridge._handle_outcome_frame()  /  _handle_activity_frame()
        ↓
    ChannelRegistry.broadcast()  →  browser WebSocket

Session filtering
-----------------
When *session_id* is set, only events whose ``correlation_id`` or
``payload["ravn_session_id"]`` match are forwarded.  Pass ``None`` to
receive all mesh events (useful in shared-bus deployments).

Multiple Skuld support
-----------------------
Each Skuld instance is a distinct mesh peer.  The bridge auto-registers
unknown peers as room participants via
:meth:`~skuld.room_bridge.RoomBridge.register_mesh_peer` when their first
event arrives, so the browser learns about them without any extra wiring.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription

if TYPE_CHECKING:
    from skuld.room_bridge import RoomBridge

logger = logging.getLogger(__name__)

#: Sleipnir event-type patterns this bridge subscribes to.
MESH_PATTERNS: list[str] = ["ravn.mesh.*"]

#: RavnEventType string fragments → room activity type.
_RAVN_TYPE_TO_ACTIVITY: dict[str, str] = {
    "tool_start": "tool_executing",
    "tool_result": "idle",
    "thought": "thinking",
    "response": "response",
}

_INTERNAL_SOURCE_IDS = {"drive_loop"}


def _extract_peer_id(event: SleipnirEvent) -> str:
    """Return the originating peer_id from a mesh Sleipnir event.

    Prefer the actual mesh publisher identity from ``event.source``. The
    nested ``payload["ravn_source"]`` carries the inner executor/agent source
    and may be an ephemeral per-turn identifier such as ``ravn-084b245b``;
    that is useful for provenance, but it is not the room participant identity.

    If no mesh source is available, fall back to ``payload["ravn_source"]``.
    """
    source = event.source or ""
    if ":" in source:
        return source.split(":", 1)[1]
    if source:
        return source

    ravn_source = event.payload.get("ravn_source", "")
    if ":" in ravn_source:
        return ravn_source.split(":", 1)[1]
    return ravn_source


def _extract_persona(event: SleipnirEvent, peer_id: str) -> str:
    """Return the best persona name for a peer from the event payload."""
    ravn_event = event.payload.get("ravn_event", {})
    return ravn_event.get("persona", peer_id)


def _build_activity_frame(ravn_type: str, ravn_event_payload: dict) -> dict | None:
    """Reconstruct a RoomBridge-compatible frame from a mesh activity event.

    Mesh-published activity should look the same as the direct Skuld websocket
    frames so the browser gets identical thought/tool rendering regardless of
    transport.
    """
    ravn_type_lower = ravn_type.lower()

    if "thought" in ravn_type_lower:
        metadata = {"thinking": True} if ravn_event_payload.get("thinking") else {}
        return {
            "type": "thought",
            "data": ravn_event_payload.get("text", ""),
            "metadata": metadata,
        }

    if "response" in ravn_type_lower:
        return {
            "type": "response",
            "data": ravn_event_payload.get("text", ""),
            "metadata": {},
        }

    if "tool_start" in ravn_type_lower:
        metadata = {"input": ravn_event_payload.get("input", {})}
        if "diff" in ravn_event_payload:
            metadata["diff"] = ravn_event_payload["diff"]
        return {
            "type": "tool_start",
            "data": ravn_event_payload.get("tool_name", ""),
            "metadata": metadata,
        }

    if "tool_result" in ravn_type_lower:
        return {
            "type": "tool_result",
            "data": ravn_event_payload.get("result", ""),
            "metadata": {
                "tool_name": ravn_event_payload.get("tool_name", ""),
                "is_error": ravn_event_payload.get("is_error", False),
            },
        }

    return None


class RoomMeshBridge:
    """Subscribes to Sleipnir mesh events and translates them to room wire events.

    This makes the room the single downstream consumer of all mesh activity.
    Any peer that publishes to the mesh (regardless of transport) will have
    its events appear in the browser room UI automatically.

    Lifecycle::

        bridge = RoomMeshBridge(subscriber, room_bridge, session_id="abc")
        await bridge.start()   # subscribes to ravn.mesh.*
        # … events translated and broadcast …
        await bridge.stop()    # unsubscribes

    Also usable as an async context manager::

        async with RoomMeshBridge(subscriber, room_bridge, session_id="abc"):
            ...
    """

    def __init__(
        self,
        subscriber: SleipnirSubscriber,
        room_bridge: RoomBridge,
        session_id: str | None = None,
        patterns: list[str] | None = None,
    ) -> None:
        self._subscriber = subscriber
        self._room_bridge = room_bridge
        self._session_id = session_id
        self._patterns = patterns if patterns is not None else list(MESH_PATTERNS)
        self._subscription: Subscription | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to mesh event patterns and begin bridging."""
        if self._subscription is not None:
            logger.warning("RoomMeshBridge.start() called while already running — ignored")
            return
        self._subscription = await self._subscriber.subscribe(
            self._patterns,
            self._handle_event,
        )
        logger.info(
            "RoomMeshBridge started: session_id=%s, patterns=%s",
            self._session_id,
            self._patterns,
        )

    async def stop(self) -> None:
        """Unsubscribe and release resources."""
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None
        logger.info("RoomMeshBridge stopped: session_id=%s", self._session_id)

    async def __aenter__(self) -> RoomMeshBridge:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _handle_event(self, event: SleipnirEvent) -> None:
        """Translate a single mesh Sleipnir event into room wire events."""
        logger.info(
            "RoomMeshBridge: received event type=%s corr=%s",
            event.event_type,
            event.correlation_id,
        )
        if not self._matches_session(event):
            logger.info("RoomMeshBridge: session mismatch, dropping")
            return

        peer_id = self._resolve_peer_id(event)
        if not peer_id:
            logger.debug("RoomMeshBridge: cannot determine peer_id for event %s", event.event_type)
            return

        await self._ensure_peer_registered(event, peer_id)

        ravn_type = event.payload.get("ravn_type", "")
        ravn_event_payload = event.payload.get("ravn_event", {})

        # Mesh topic derived from event_type: "ravn.mesh.<topic>"
        parts = event.event_type.split(".", 2)
        mesh_topic = parts[2] if len(parts) == 3 else ""

        if "outcome" in ravn_type.lower():
            await self._translate_outcome(peer_id, mesh_topic, ravn_event_payload)
            return

        activity_type = self._ravn_type_to_activity(ravn_type)
        if activity_type:
            await self._translate_activity(peer_id, ravn_type, ravn_event_payload)

    def _resolve_peer_id(self, event: SleipnirEvent) -> str:
        """Resolve the UI participant for a Sleipnir event.

        Internal runtime helpers such as ``drive_loop`` should not appear as
        separate room peers. When possible, map those events back to the mesh
        participant that owns the reported persona.
        """
        peer_id = _extract_peer_id(event)
        if not peer_id or peer_id not in _INTERNAL_SOURCE_IDS:
            return peer_id

        persona = _extract_persona(event, peer_id)
        participants = list(self._room_bridge.participants.values())
        matching = [participant for participant in participants if participant.persona == persona]
        if not matching:
            logger.info(
                "RoomMeshBridge: dropping internal source peer_id=%s persona=%s",
                peer_id,
                persona,
            )
            return ""

        exact_display = [
            participant
            for participant in matching
            if participant.display_name == persona and participant.participant_type == "ravn"
        ]
        preferred = exact_display[0] if exact_display else matching[0]
        logger.info(
            "RoomMeshBridge: remapped internal source peer_id=%s persona=%s -> %s",
            peer_id,
            persona,
            preferred.peer_id,
        )
        return preferred.peer_id

    def _matches_session(self, event: SleipnirEvent) -> bool:
        """Return True if the event belongs to this bridge's session context."""
        if self._session_id is None:
            return True

        if event.correlation_id == self._session_id:
            return True

        if event.payload.get("ravn_session_id") == self._session_id:
            return True

        if event.payload.get("ravn_root_correlation_id") == self._session_id:
            return True

        return False

    async def _ensure_peer_registered(self, event: SleipnirEvent, peer_id: str) -> None:
        """Auto-register *peer_id* as a mesh participant if not already known."""
        if self._room_bridge.has_participant(peer_id):
            return

        persona = _extract_persona(event, peer_id)
        await self._room_bridge.register_mesh_peer(
            peer_id=peer_id,
            persona=persona,
            display_name=persona,
        )
        logger.info("RoomMeshBridge: auto-registered peer_id=%s persona=%s", peer_id, persona)

    async def _translate_outcome(
        self,
        peer_id: str,
        mesh_topic: str,
        ravn_event_payload: dict,
    ) -> None:
        """Translate an OUTCOME mesh event into a ``room_outcome`` wire event."""
        # Build a frame in the shape that RoomBridge.handle_ravn_frame() expects
        frame: dict = {
            "type": "outcome",
            "data": ravn_event_payload,
            "metadata": {
                "event_type": ravn_event_payload.get("event_type", mesh_topic),
            },
        }
        await self._room_bridge.handle_ravn_frame(peer_id, frame)

    async def _translate_activity(
        self,
        peer_id: str,
        ravn_type: str,
        ravn_event_payload: dict,
    ) -> None:
        """Translate a tool/thought mesh event into a ``room_activity`` wire event."""
        frame = _build_activity_frame(ravn_type, ravn_event_payload)
        if not frame:
            return
        await self._room_bridge.handle_ravn_frame(peer_id, frame)

    @staticmethod
    def _ravn_type_to_activity(ravn_type: str) -> str:
        """Map a RavnEventType string to a room activity type, or '' if unknown."""
        ravn_type_lower = ravn_type.lower()
        for fragment, activity in _RAVN_TYPE_TO_ACTIVITY.items():
            if fragment in ravn_type_lower:
                return activity
        return ""

