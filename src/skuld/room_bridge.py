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
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
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
    ) -> None:
        self._config = config
        self._channels = channels
        self._append_turn = append_turn
        self._report_timeline_event = report_timeline_event
        self._observe_peer_event = observe_peer_event
        self._participants: dict[str, ParticipantMeta] = {}
        self._websockets: dict[str, WebSocket] = {}
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
    ) -> ParticipantMeta:
        """Register a new Ravn participant and broadcast ``participant_joined``.

        If a participant with *peer_id* already exists (reconnect), the
        existing record is reused and a fresh ``participant_joined`` is broadcast.
        """
        subs = tuple(subscribes_to or ())
        emit_types = tuple(emits or ())
        tool_names = tuple(tools or ())

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
            )
            self._participants[peer_id] = meta
        else:
            old = self._participants[peer_id]
            meta = ParticipantMeta(
                peer_id=peer_id,
                persona=persona,
                color=old.color,
                participant_type=old.participant_type,
                display_name=display_name or old.display_name,
                subscribes_to=subs or old.subscribes_to,
                emits=emit_types or old.emits,
                tools=tool_names or old.tools,
                status=old.status,
            )
            self._participants[peer_id] = meta

        self._websockets[peer_id] = websocket
        logger.info("RoomBridge: participant registered peer_id=%s persona=%s", peer_id, persona)

        await self._channels.broadcast({"type": "participant_joined", "participant": asdict(meta)})
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
        )
        self._participants[peer_id] = meta
        logger.info("RoomBridge: mesh peer registered peer_id=%s persona=%s", peer_id, persona)

        await self._channels.broadcast({"type": "participant_joined", "participant": asdict(meta)})
        return meta

    async def unregister(self, peer_id: str) -> None:
        """Remove a participant and broadcast ``participant_left``."""
        self._participants.pop(peer_id, None)
        self._websockets.pop(peer_id, None)
        logger.info("RoomBridge: participant unregistered peer_id=%s", peer_id)

        await self._channels.broadcast({"type": "participant_left", "participantId": peer_id})

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
        self._participants[meta.peer_id] = ParticipantMeta(
            peer_id=meta.peer_id,
            persona=meta.persona,
            color=meta.color,
            participant_type=meta.participant_type,
            display_name=meta.display_name,
            gateway_url=meta.gateway_url,
            subscribes_to=meta.subscribes_to,
            emits=meta.emits,
            tools=meta.tools,
            status=activity_type,
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
            payload = json.dumps({"type": "directed_message", "content": content})
            await ws.send_text(payload)
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

    def get_room_state_event(self) -> dict:
        """Return a ``room_state`` event with the current participant list."""
        return {
            "type": "room_state",
            "participants": [asdict(p) for p in self._participants.values()],
        }

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
