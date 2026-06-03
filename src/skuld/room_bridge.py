"""RoomBridge — Ravn WebSocket ingress and event aggregation for multi-agent rooms.

Accepts WebSocket connections from Ravn daemons, translates their NDJSON event
frames into browser-facing room events, and broadcasts them via ChannelRegistry.

Only active when ``room.enabled = true`` in the broker configuration.

Wire events emitted to browsers:
    participant_joined   {participant: ParticipantMeta}
    participant_left     {participantId: str}
    room_state           {participants: list[ParticipantMeta]}
    room_message         {id, participantId, participant, content, threadId?, visibility}
    room_activity        {participantId, activityType, detail?}
    room_notification    {notificationType, participantId, participant, ...}
    room_outcome         {participantId, participant, persona, eventType, fields, verdict?}
    room_mesh_message    {participantId, participant, fromPersona, eventType, direction, preview}
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from skuld.room_models import ParticipantMeta

if TYPE_CHECKING:
    from skuld.channels import ChannelRegistry
    from skuld.config import RoomConfig

logger = logging.getLogger(__name__)

_GIT_COMMIT_PREFIXES = ("git commit", "git -c ", "git -C ")
_GIT_COMMIT_OUTPUT_RE = re.compile(r"\[[\w/-]+\s+([a-f0-9]{7,})\]\s+(.+)")

# Maps RavnEvent type strings to room activity types
_RAVN_ACTIVITY_MAP: dict[str, str] = {
    "thought": "thinking",
    "thinking": "thinking",
    "tool_start": "tool_executing",
    "tool_result": "idle",
    "task_started": "busy",
    "task_complete": "idle",
    "decision": "thinking",
}

HUMAN_ENVIRONMENT_ROLES: frozenset[str] = frozenset(
    {"observer", "teacher", "approver", "debugger", "owner"}
)

HUMAN_ROLE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "observer": ("view", "reply"),
    "teacher": ("view", "reply", "teach", "correct"),
    "approver": ("view", "reply", "approve", "authorize_action"),
    "debugger": ("view", "reply", "debug", "teach", "correct"),
    "owner": (
        "view",
        "reply",
        "approve",
        "teach",
        "correct",
        "debug",
        "change_autonomy",
        "authorize_action",
    ),
}


class RoomBridge:
    """Bridges Ravn WebSocket connections into the multi-agent room.

    Manages participant registration, color assignment, event translation,
    history persistence, and directed message routing.

    Args:
        config:   Room configuration (colors pool, max participants, etc.).
        channels: Shared channel registry used to broadcast room events.
        append_turn: Callback to persist a ConversationTurn (injected by Broker).
    """

    def __init__(
        self,
        config: RoomConfig,
        channels: ChannelRegistry,
        append_turn: Callable[[Any], None] | None = None,
        report_timeline_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        observe_peer_event: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
        publish_presence_event: Callable[[Any], Awaitable[None]] | None = None,
        environment_id: str | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._channels = channels
        self._append_turn = append_turn
        self._report_timeline_event = report_timeline_event
        self._observe_peer_event = observe_peer_event
        self._publish_presence_event = publish_presence_event
        self._environment_id = environment_id or os.environ.get("RAVN_ENVIRONMENT_ID", "local")
        self._clock = clock or time.time
        self._participants: dict[str, ParticipantMeta] = {}
        self._websockets: dict[str, WebSocket] = {}
        self._pending_help_context: dict[str, dict[str, Any]] = {}
        self._color_cycle = itertools.cycle(list(config.participant_colors))
        self._timeline_started_at = time.monotonic()
        self._known_files: set[str] = set()

    # ------------------------------------------------------------------
    # Participant management
    # ------------------------------------------------------------------

    async def register(
        self,
        peer_id: str,
        persona: str,
        websocket: WebSocket,
        *,
        display_name: str = "",
        subscribes_to: list[str] | None = None,
        emits: list[str] | None = None,
        tools: list[str] | None = None,
        environment_id: str | None = None,
        participant_kind: str = "",
        capabilities: list[str] | None = None,
        surfaces: list[str] | None = None,
        wakefulness: str = "unknown",
        attention_state: str = "available",
        heartbeat_ttl_s: float = 90.0,
        authority_role: str = "",
        room_ids: list[str] | None = None,
    ) -> ParticipantMeta:
        """Register a new Ravn participant and broadcast ``participant_joined``.

        If a participant with *peer_id* already exists (reconnect), the
        existing record is reused and a fresh ``participant_joined`` is broadcast.
        """
        subs = tuple(subscribes_to or ())
        emit_types = tuple(emits or ())
        tool_names = tuple(tools or ())
        capability_names = tuple(capabilities or tools or ())
        surface_names = tuple(surfaces or ())
        room_membership = tuple(room_ids or ())
        effective_environment_id = environment_id or self._environment_id
        effective_kind = participant_kind or "ravn"

        if peer_id not in self._participants:
            color = next(self._color_cycle)
            meta = ParticipantMeta(
                peer_id=peer_id,
                persona=persona,
                color=color,
                participant_type="ravn",
                display_name=display_name,
                subscribes_to=subs,
                emits=emit_types,
                tools=tool_names,
                status="idle",
                environment_id=effective_environment_id,
                participant_kind=effective_kind,
                capabilities=capability_names,
                surfaces=surface_names,
                wakefulness=wakefulness,
                attention_state=attention_state,
                heartbeat_ttl_s=heartbeat_ttl_s,
                last_heartbeat_at=self._clock(),
                authority_role=authority_role,
                room_ids=room_membership,
            )
            self._participants[peer_id] = meta
        else:
            old = self._participants[peer_id]
            meta = replace(
                old,
                persona=persona,
                display_name=display_name or old.display_name,
                subscribes_to=subs or old.subscribes_to,
                emits=emit_types or old.emits,
                tools=tool_names or old.tools,
                environment_id=effective_environment_id or old.environment_id,
                participant_kind=participant_kind or old.participant_kind,
                capabilities=capability_names or old.capabilities,
                surfaces=surface_names or old.surfaces,
                wakefulness=wakefulness if wakefulness != "unknown" else old.wakefulness,
                attention_state=attention_state or old.attention_state,
                heartbeat_ttl_s=heartbeat_ttl_s or old.heartbeat_ttl_s,
                last_heartbeat_at=self._clock(),
                authority_role=authority_role or old.authority_role,
                room_ids=room_membership or old.room_ids,
            )
            self._participants[peer_id] = meta

        self._websockets[peer_id] = websocket
        logger.info("RoomBridge: participant registered peer_id=%s persona=%s", peer_id, persona)

        await self._channels.broadcast({"type": "participant_joined", "participant": asdict(meta)})
        await self._publish_participant_joined(meta)
        return meta

    async def register_mesh_peer(
        self,
        peer_id: str,
        persona: str,
        *,
        display_name: str = "",
        subscribes_to: list[str] | None = None,
        emits: list[str] | None = None,
        tools: list[str] | None = None,
        participant_type: str = "ravn",
        environment_id: str | None = None,
        participant_kind: str = "",
        capabilities: list[str] | None = None,
        surfaces: list[str] | None = None,
        wakefulness: str = "watching",
        attention_state: str = "available",
        heartbeat_ttl_s: float = 90.0,
        authority_role: str = "",
        room_ids: list[str] | None = None,
    ) -> ParticipantMeta:
        """Register a mesh-discovered peer (no WebSocket connection).

        Used by SkuldMeshAdapter to add flock peers discovered via static/mDNS
        discovery so they appear in the room UI without a direct WebSocket.
        """
        if peer_id in self._participants:
            return self._participants[peer_id]

        color = next(self._color_cycle)
        meta = ParticipantMeta(
            peer_id=peer_id,
            persona=persona,
            color=color,
            participant_type=participant_type,
            display_name=display_name or persona,
            subscribes_to=tuple(subscribes_to or ()),
            emits=tuple(emits or ()),
            tools=tuple(tools or ()),
            status="idle",
            environment_id=environment_id or self._environment_id,
            participant_kind=participant_kind or participant_type,
            capabilities=tuple(capabilities or tools or ()),
            surfaces=tuple(surfaces or ()),
            wakefulness=wakefulness,
            attention_state=attention_state,
            heartbeat_ttl_s=heartbeat_ttl_s,
            last_heartbeat_at=self._clock(),
            authority_role=authority_role,
            room_ids=tuple(room_ids or ()),
        )
        self._participants[peer_id] = meta
        logger.info("RoomBridge: mesh peer registered peer_id=%s persona=%s", peer_id, persona)

        await self._channels.broadcast({"type": "participant_joined", "participant": asdict(meta)})
        await self._publish_participant_joined(meta)
        return meta

    async def join_human_environment(
        self,
        participant_id: str,
        *,
        display_name: str,
        environment_id: str,
        role: str = "observer",
        room_id: str = "",
        capabilities: list[str] | None = None,
        surfaces: list[str] | None = None,
        heartbeat_ttl_s: float = 300.0,
    ) -> ParticipantMeta:
        """Register or update a human participant inside a live Environment."""
        participant_id = participant_id.strip()
        if not participant_id:
            raise ValueError("participant_id is required")
        if not environment_id.strip():
            raise ValueError("environment_id is required")
        role = role.strip() or "observer"
        if role not in HUMAN_ENVIRONMENT_ROLES:
            raise PermissionError(f"Unknown Environment role: {role}")

        role_capabilities = HUMAN_ROLE_CAPABILITIES[role]
        requested_capabilities = tuple(capabilities or role_capabilities)
        disallowed = sorted(set(requested_capabilities) - set(role_capabilities))
        if disallowed:
            raise PermissionError(
                f"Role {role} cannot claim capabilities: {', '.join(disallowed)}"
            )

        room_membership = tuple(room_id for room_id in [room_id] if room_id)
        if participant_id in self._participants:
            old = self._participants[participant_id]
            color = old.color
            existing_rooms = tuple(
                dict.fromkeys([*old.room_ids, *room_membership])
            )
        else:
            color = next(self._color_cycle)
            existing_rooms = room_membership

        meta = ParticipantMeta(
            peer_id=participant_id,
            persona=display_name or participant_id,
            color=color,
            participant_type="human",
            display_name=display_name or participant_id,
            subscribes_to=("room.message", "room.direct", "participant.*"),
            emits=("room.message", "room.direct", "feedback.recorded"),
            tools=(),
            status="idle",
            environment_id=environment_id,
            participant_kind="human",
            capabilities=requested_capabilities,
            surfaces=tuple(surfaces or ("skuld.room",)),
            wakefulness="wakeful",
            attention_state="available",
            heartbeat_ttl_s=heartbeat_ttl_s,
            last_heartbeat_at=self._clock(),
            authority_role=role,
            room_ids=existing_rooms,
        )
        self._participants[participant_id] = meta
        await self._channels.broadcast({"type": "participant_joined", "participant": asdict(meta)})
        await self._publish_participant_joined(meta)
        return meta

    async def leave_human_environment(
        self,
        participant_id: str,
        *,
        reason: str = "left",
    ) -> None:
        """Remove a human participant from an Environment huddle."""
        meta = self._participants.get(participant_id)
        if meta is None:
            raise LookupError(f"Unknown room participant: {participant_id}")
        if meta.participant_type != "human":
            raise PermissionError(f"Participant is not human: {participant_id}")
        self._participants.pop(participant_id, None)
        await self._channels.broadcast(
            {"type": "participant_left", "participantId": participant_id}
        )
        await self._publish_participant_left(meta, reason=reason)

    def require_participant_capability(self, participant_id: str, capability: str) -> None:
        """Raise if a participant is missing a control capability."""
        meta = self._participants.get(participant_id)
        if meta is None:
            raise LookupError(f"Unknown room participant: {participant_id}")
        if capability not in meta.capabilities:
            raise PermissionError(
                f"Participant {participant_id} lacks capability: {capability}"
            )

    async def unregister(self, peer_id: str) -> None:
        """Remove a participant and broadcast ``participant_left``."""
        meta = self._participants.pop(peer_id, None)
        self._websockets.pop(peer_id, None)
        self._pending_help_context.pop(peer_id, None)
        logger.info("RoomBridge: participant unregistered peer_id=%s", peer_id)

        await self._channels.broadcast({"type": "participant_left", "participantId": peer_id})
        if meta is not None:
            await self._publish_participant_left(meta, reason="left")

    async def heartbeat(
        self,
        peer_id: str,
        *,
        status: str | None = None,
        wakefulness: str | None = None,
        attention_state: str | None = None,
    ) -> ParticipantMeta | None:
        """Record a participant heartbeat and publish canonical presence."""
        meta = self._participants.get(peer_id)
        if meta is None:
            return None
        updated = replace(
            meta,
            status=status or meta.status,
            wakefulness=wakefulness or meta.wakefulness,
            attention_state=attention_state or meta.attention_state,
            last_heartbeat_at=self._clock(),
        )
        self._participants[peer_id] = updated
        await self._channels.broadcast(
            {
                "type": "participant_heartbeat",
                "participantId": peer_id,
                "participant": asdict(updated),
            }
        )
        await self._publish_participant_heartbeat(updated)
        return updated

    async def update_participant_capabilities(
        self,
        peer_id: str,
        *,
        capabilities: list[str] | None = None,
        surfaces: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> ParticipantMeta | None:
        """Update capabilities/tools/surfaces on the existing participant model."""
        meta = self._participants.get(peer_id)
        if meta is None:
            return None
        updated = replace(
            meta,
            capabilities=tuple(capabilities) if capabilities is not None else meta.capabilities,
            surfaces=tuple(surfaces) if surfaces is not None else meta.surfaces,
            tools=tuple(tools) if tools is not None else meta.tools,
            last_heartbeat_at=self._clock(),
        )
        self._participants[peer_id] = updated
        await self._channels.broadcast(
            {
                "type": "participant_capabilities_changed",
                "participantId": peer_id,
                "participant": asdict(updated),
            }
        )
        await self._publish_participant_capabilities_changed(updated)
        return updated

    async def expire_heartbeats(self, *, now: float | None = None) -> list[ParticipantMeta]:
        """Mark participants whose heartbeat TTL has elapsed as expired."""
        now_ts = self._clock() if now is None else now
        expired: list[ParticipantMeta] = []
        for peer_id, meta in list(self._participants.items()):
            if meta.last_heartbeat_at is None or meta.status == "expired":
                continue
            if now_ts - meta.last_heartbeat_at <= meta.heartbeat_ttl_s:
                continue
            updated = replace(meta, status="expired", attention_state="offline")
            self._participants[peer_id] = updated
            expired.append(updated)
            await self._channels.broadcast(
                {
                    "type": "participant_heartbeat_expired",
                    "participantId": peer_id,
                    "participant": asdict(updated),
                }
            )
            await self._publish_participant_heartbeat(updated)
        return expired

    async def _publish_participant_joined(self, meta: ParticipantMeta) -> None:
        from sleipnir.domain.catalog import participant_joined

        await self._publish_presence(
            participant_joined(
                environment_id=meta.environment_id or self._environment_id,
                participant_id=meta.peer_id,
                participant_type=meta.participant_kind or meta.participant_type,
                display_name=meta.display_name or meta.persona,
                capabilities=list(meta.capabilities or meta.tools),
                source="skuld:room_bridge",
                correlation_id=meta.environment_id or self._environment_id,
            )
        )

    async def _publish_participant_left(self, meta: ParticipantMeta, *, reason: str) -> None:
        from sleipnir.domain.catalog import participant_left

        await self._publish_presence(
            participant_left(
                environment_id=meta.environment_id or self._environment_id,
                participant_id=meta.peer_id,
                participant_type=meta.participant_kind or meta.participant_type,
                display_name=meta.display_name or meta.persona,
                reason=reason,
                source="skuld:room_bridge",
                correlation_id=meta.environment_id or self._environment_id,
            )
        )

    async def _publish_participant_heartbeat(self, meta: ParticipantMeta) -> None:
        from sleipnir.domain.catalog import participant_heartbeat

        await self._publish_presence(
            participant_heartbeat(
                environment_id=meta.environment_id or self._environment_id,
                participant_id=meta.peer_id,
                participant_type=meta.participant_kind or meta.participant_type,
                display_name=meta.display_name or meta.persona,
                status=meta.status,
                wakefulness=meta.wakefulness,
                attention_state=meta.attention_state,
                heartbeat_ttl_s=meta.heartbeat_ttl_s,
                source="skuld:room_bridge",
                correlation_id=meta.environment_id or self._environment_id,
            )
        )

    async def _publish_participant_capabilities_changed(self, meta: ParticipantMeta) -> None:
        from sleipnir.domain.catalog import participant_capabilities_changed

        await self._publish_presence(
            participant_capabilities_changed(
                environment_id=meta.environment_id or self._environment_id,
                participant_id=meta.peer_id,
                participant_type=meta.participant_kind or meta.participant_type,
                display_name=meta.display_name or meta.persona,
                capabilities=list(meta.capabilities),
                surfaces=list(meta.surfaces),
                tools=list(meta.tools),
                source="skuld:room_bridge",
                correlation_id=meta.environment_id or self._environment_id,
            )
        )

    async def _publish_presence(self, event: Any) -> None:
        if self._publish_presence_event is None:
            return
        try:
            await self._publish_presence_event(event)
        except Exception:
            logger.warning(
                "RoomBridge: failed to publish presence event type=%s",
                getattr(event, "event_type", "-"),
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Event translation
    # ------------------------------------------------------------------

    async def handle_ravn_frame(self, peer_id: str, frame: dict) -> None:
        """Translate a raw NDJSON frame from a Ravn into room wire events.

        Frame schema (from SkuldChannel):
            session_id, type, data, metadata, source (peer_id), persona

        Routing:
            type "response"             → room_message
            type "error"                → room_message (error=True)
            type "outcome"              → room_outcome (structured result)
            type "help_needed"          → room_notification
            type "tool_start" (route_work) → room_mesh_message + room_activity
            type "thought"/"tool_start"/"tool_result" → room_activity
        """
        meta = self._participants.get(peer_id)
        if meta is None:
            logger.warning("RoomBridge: frame from unknown peer_id=%s, dropping", peer_id)
            return

        event_type = frame.get("type", "")
        data = frame.get("data", "")
        metadata = frame.get("metadata", {})

        if event_type in ("response", "error"):
            await self._handle_response_frame(meta, frame, is_error=(event_type == "error"))
            # Agent turn is complete — reset status to idle.
            await self._handle_activity_frame(
                meta, "error" if event_type == "error" else "idle", ""
            )
            await self._notify_peer_event(meta.peer_id, event_type, frame)
            return

        if event_type == "outcome":
            if not (isinstance(data, dict) and data.get("routing_only")):
                await self._handle_outcome_frame(meta, frame)
            # Outcome is emitted mid-turn; the agent may still produce a
            # response afterward — don't reset status here.
            await self._notify_peer_event(meta.peer_id, event_type, frame)
            return

        if event_type == "help_needed":
            await self._handle_help_needed_frame(meta, frame)
            # Agent is asking for help but is still working — don't reset.
            await self._notify_peer_event(meta.peer_id, event_type, frame)
            return

        # Check for inter-agent delegation (route_work tool)
        if event_type == "tool_start":
            tool_name = metadata.get("tool_name") or data
            if tool_name == "route_work":
                await self._handle_mesh_delegation_frame(meta, frame)
            await self._report_peer_tool_timeline(meta, tool_name, metadata.get("input", {}))

        if event_type == "tool_result" and metadata.get("is_error"):
            await self._emit_timeline_event(
                {
                    "type": "error",
                    "label": self._timeline_label(meta, self._preview_text(str(data), 120)),
                }
            )

        activity_type = _RAVN_ACTIVITY_MAP.get(event_type)
        if activity_type:
            await self._handle_activity_frame(meta, activity_type, data)
            await self._notify_peer_event(meta.peer_id, event_type, frame)

        # Forward internal events (thought, tool_start, tool_result) so the
        # agent detail panel can render them — the original frame is relayed
        # tagged with the participant identity.
        if event_type in ("thought", "tool_start", "tool_result"):
            await self._channels.broadcast(
                {
                    "type": "room_agent_event",
                    "participantId": meta.peer_id,
                    "frame": frame,
                }
            )

    async def _handle_response_frame(
        self,
        meta: ParticipantMeta,
        frame: dict,
        is_error: bool,
    ) -> None:
        """Translate a response/error frame into a room_message event."""
        msg_id = str(uuid.uuid4())
        content = frame.get("data", "")
        thread_id = frame.get("metadata", {}).get("thread_id")

        room_event: dict = {
            "type": "room_message",
            "id": msg_id,
            "participantId": meta.peer_id,
            "participant": asdict(meta),
            "content": content,
            "visibility": frame.get("visibility", "public"),
        }
        if is_error:
            room_event["error"] = True
        if thread_id:
            room_event["threadId"] = thread_id

        await self._channels.broadcast(room_event)
        await self._emit_timeline_event(
            {
                "type": "error" if is_error else "message",
                "label": self._timeline_label(meta, self._preview_text(content, 120)),
            }
        )

        # Persist as ConversationTurn
        if self._append_turn is not None:
            from skuld.broker import ConversationTurn  # late import — avoids circular

            turn = ConversationTurn(
                id=msg_id,
                role="assistant",
                content=content,
                participant_id=meta.peer_id,
                participant_meta=asdict(meta),
                thread_id=thread_id,
                visibility="public",
            )
            self._append_turn(turn)

    async def _handle_activity_frame(
        self,
        meta: ParticipantMeta,
        activity_type: str,
        detail: Any,
    ) -> None:
        """Translate a thought/tool frame into a room_activity event."""
        self._participants[meta.peer_id] = replace(
            meta,
            status=activity_type,
            last_heartbeat_at=self._clock(),
        )
        event: dict = {
            "type": "room_activity",
            "participantId": meta.peer_id,
            "activityType": activity_type,
        }
        if detail:
            detail_text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
            event["detail"] = detail_text[: self._config.activity_detail_max_length]

        await self._channels.broadcast(event)

    async def _notify_peer_event(
        self, peer_id: str, event_type: str, frame: dict[str, Any]
    ) -> None:
        """Report a peer event to the broker for watchdog/status bookkeeping."""
        if self._observe_peer_event is None:
            return
        await self._observe_peer_event(peer_id, event_type, frame)

    async def _handle_help_needed_frame(
        self,
        meta: ParticipantMeta,
        frame: dict,
    ) -> None:
        """Translate a help_needed event into a room_notification for ambient AI.

        Emits a high-visibility notification that surfaces in the user's chat
        without requiring them to watch logs.
        """
        data = frame.get("data", {})
        if isinstance(data, str):
            # If data is a string, wrap it
            data = {"summary": data}

        notification: dict = {
            "type": "room_notification",
            "notificationType": "help_needed",
            "participantId": meta.peer_id,
            "participant": asdict(meta),
            "persona": data.get("persona", meta.persona),
            "reason": data.get("reason", "unknown"),
            "summary": data.get("summary", "Agent needs help"),
            "attempted": data.get("attempted", []),
            "recommendation": data.get("recommendation", ""),
            "urgency": frame.get("metadata", {}).get("urgency", 0.85),
        }

        # Include context if provided (file paths, errors, etc.)
        context = data.get("context")
        if context:
            notification["context"] = context
        if isinstance(context, dict):
            self._pending_help_context[meta.peer_id] = {
                "help_summary": notification["summary"],
                "help_reason": notification["reason"],
                "help_attempted": list(notification.get("attempted") or []),
                "help_recommendation": notification.get("recommendation", ""),
                "help_context": context,
                "workflow_parent_event_id": str(context.get("workflow_parent_event_id") or ""),
                "workflow_node_id": str(context.get("workflow_node_id") or ""),
                "root_correlation_id": str(context.get("root_correlation_id") or ""),
                "session_id": str(context.get("session_id") or frame.get("session_id") or ""),
            }

        await self._channels.broadcast(notification)
        logger.info(
            "RoomBridge: help_needed notification peer_id=%s reason=%s",
            meta.peer_id,
            notification["reason"],
        )

    async def _handle_outcome_frame(
        self,
        meta: ParticipantMeta,
        frame: dict,
    ) -> None:
        """Translate an outcome event into a room_outcome event for chat visibility.

        Emits structured outcome data so users can see persona results in the chat
        without parsing logs.
        """
        data = frame.get("data", {})
        if isinstance(data, str):
            # If data is a string, wrap it
            data = {"raw": data}

        metadata = frame.get("metadata", {})
        event_type = metadata.get("event_type", "")

        outcome: dict = {
            "type": "room_outcome",
            "participantId": meta.peer_id,
            "participant": asdict(meta),
            "persona": meta.persona,
            "eventType": event_type,
            "fields": data.get("fields", data),
            "valid": data.get("valid", True),
        }

        # Include summary if available
        summary = data.get("summary") or data.get("fields", {}).get("summary")
        if summary:
            outcome["summary"] = summary

        # Include verdict if available (common field)
        verdict = data.get("verdict") or data.get("fields", {}).get("verdict")
        if verdict:
            outcome["verdict"] = verdict

        await self._channels.broadcast(outcome)
        logger.info(
            "RoomBridge: outcome broadcast peer_id=%s event_type=%s verdict=%s",
            meta.peer_id,
            event_type,
            verdict,
        )

    async def _handle_mesh_delegation_frame(
        self,
        meta: ParticipantMeta,
        frame: dict,
    ) -> None:
        """Translate a route_work tool call into a room_mesh_message for chat visibility.

        Shows inter-agent delegation in the chat so users can follow the discussion
        between agents without watching logs.
        """
        metadata = frame.get("metadata", {})
        tool_input = metadata.get("input", {})

        event_type = tool_input.get("event_type", "work")
        prompt = tool_input.get("prompt", "")

        # Truncate prompt for display (keep first 500 chars)
        display_prompt = prompt[:500] + "..." if len(prompt) > 500 else prompt

        mesh_message: dict = {
            "type": "room_mesh_message",
            "participantId": meta.peer_id,
            "participant": asdict(meta),
            "fromPersona": meta.persona,
            "eventType": event_type,
            "direction": "delegate",
            "preview": display_prompt,
        }

        await self._channels.broadcast(mesh_message)
        logger.info(
            "RoomBridge: mesh delegation peer_id=%s event_type=%s",
            meta.peer_id,
            event_type,
        )

    # ------------------------------------------------------------------
    # CLI participant helpers
    # ------------------------------------------------------------------

    async def broadcast_cli_activity(
        self,
        peer_id: str,
        activity_type: str,
        detail: str = "",
    ) -> None:
        """Broadcast a ``room_activity`` event for the local CLI participant.

        Called by the Broker when the CLI transport changes activity state
        so the room UI can show the Skuld participant as thinking/idle.
        """
        meta = self._participants.get(peer_id)
        if meta is None:
            return
        await self._handle_activity_frame(meta, activity_type, detail)

    async def broadcast_cli_message(
        self,
        peer_id: str,
        content: str,
        *,
        is_error: bool = False,
        visibility: str = "public",
    ) -> None:
        """Broadcast a ``room_message`` for the local CLI participant.

        Called by the Broker when a CLI assistant turn completes so the
        message appears in the room chat with participant color.
        """
        meta = self._participants.get(peer_id)
        if meta is None:
            return
        await self._handle_response_frame(
            meta,
            {"data": content, "metadata": {}, "visibility": visibility},
            is_error=is_error,
        )

    # ------------------------------------------------------------------
    # Directed routing
    # ------------------------------------------------------------------

    async def route_directed_message(
        self,
        target_peer_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Forward a directed message from the browser to a specific Ravn WebSocket.

        Returns True if the message was delivered, False if the target is not connected.
        """
        ws = self._websockets.get(target_peer_id)
        if ws is None:
            logger.warning(
                "RoomBridge: directed_message to unknown peer_id=%s, dropping",
                target_peer_id,
            )
            return False

        try:
            merged_metadata = dict(self._pending_help_context.get(target_peer_id, {}))
            if isinstance(metadata, dict):
                merged_metadata.update(metadata)
            payload_dict: dict[str, Any] = {"type": "directed_message", "content": content}
            if merged_metadata:
                payload_dict["metadata"] = merged_metadata
            payload = json.dumps(payload_dict)
            await ws.send_text(payload)
            self._pending_help_context.pop(target_peer_id, None)
            logger.debug("RoomBridge: directed_message sent to peer_id=%s", target_peer_id)
            return True
        except Exception:
            logger.warning(
                "RoomBridge: failed to send directed_message to peer_id=%s",
                target_peer_id,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Room state
    # ------------------------------------------------------------------

    def get_room_state_event(self, *, environment_id: str | None = None) -> dict:
        """Return a ``room_state`` event with the current participant list."""
        return {
            "type": "room_state",
            "participants": self.environment_roster(environment_id=environment_id),
        }

    def environment_roster(self, *, environment_id: str | None = None) -> list[dict[str, Any]]:
        """Return current participants, optionally filtered to one Environment."""
        participants = self._participants.values()
        if environment_id is not None:
            participants = [
                meta
                for meta in participants
                if (meta.environment_id or self._environment_id) == environment_id
            ]
        return [asdict(p) for p in participants]

    @property
    def participant_count(self) -> int:
        """Number of currently registered participants."""
        return len(self._participants)

    @property
    def participants(self) -> dict[str, ParticipantMeta]:
        """Snapshot of current participants (copy)."""
        return dict(self._participants)

    def has_participant(self, peer_id: str) -> bool:
        """Return True if *peer_id* is already a registered participant."""
        return peer_id in self._participants

    def is_connected(self, peer_id: str) -> bool:
        """Return True if *peer_id* currently has an active WebSocket."""
        return peer_id in self._websockets

    # ------------------------------------------------------------------
    # Chronicle timeline reporting
    # ------------------------------------------------------------------

    async def _emit_timeline_event(self, event: dict[str, Any]) -> None:
        """Forward a timeline event to the broker callback, if configured."""
        if self._report_timeline_event is None:
            return
        payload = {"t": int(time.monotonic() - self._timeline_started_at), **event}
        try:
            await self._report_timeline_event(payload)
        except Exception:
            logger.debug(
                "RoomBridge: failed to report timeline event: type=%s",
                event.get("type"),
                exc_info=True,
            )

    async def _report_peer_tool_timeline(
        self,
        meta: ParticipantMeta,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> None:
        """Map a peer tool_start into a chronicle timeline event."""
        event = self._classify_tool_event(tool_name, tool_input)
        if event is None:
            return
        event["label"] = self._timeline_label(meta, str(event["label"]))
        await self._emit_timeline_event(event)

    def _classify_tool_event(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Classify a peer tool_start into the coarse chronicle schema."""
        file_path = tool_input.get("file_path") or tool_input.get("path")

        if tool_name in ("Edit", "Write", "NotebookEdit"):
            if tool_name == "Edit":
                action = "modified"
                if file_path:
                    self._known_files.add(file_path)
            elif file_path and file_path in self._known_files:
                action = "modified"
            elif file_path:
                action = "created"
                self._known_files.add(file_path)
            else:
                action = "created"
            return {"type": "file", "label": file_path or tool_name, "action": action}

        if tool_name == "Read":
            if file_path:
                self._known_files.add(file_path)
            return None

        if tool_name not in ("Bash", "BashTool"):
            return None

        command = str(tool_input.get("command", ""))
        if self._is_git_commit(command):
            label = self._extract_git_commit_message(command) or command[:80] or "git commit"
            return {"type": "git", "label": label}

        return {"type": "terminal", "label": command[:80] or "bash"}

    @staticmethod
    def _preview_text(content: str, limit: int) -> str:
        """Collapse newlines and trim text for timeline labels."""
        preview = " ".join(line.strip() for line in content.splitlines() if line.strip()).strip()
        if not preview:
            preview = content.strip()
        if len(preview) > limit:
            return preview[: limit - 3].rstrip() + "..."
        return preview

    @staticmethod
    def _timeline_label(meta: ParticipantMeta, label: str) -> str:
        """Prefix timeline labels with persona for multi-agent attribution."""
        return f"{meta.persona}: {label}" if label else meta.persona

    @staticmethod
    def _is_git_commit(command: str) -> bool:
        stripped = command.lstrip()
        if stripped.startswith(_GIT_COMMIT_PREFIXES):
            return True
        return "git commit" in stripped

    @staticmethod
    def _extract_git_commit_message(output: str) -> str | None:
        match = _GIT_COMMIT_OUTPUT_RE.search(output)
        if match:
            return match.group(2)
        return None
