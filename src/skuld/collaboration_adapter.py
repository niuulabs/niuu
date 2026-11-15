"""Skuld adapters for shared collaboration rooms and human-facing surfaces."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from niuu.collaboration import CollaborationEvent, CollaborationRoom, Participant
from niuu.collaboration.room import matches_subscription
from niuu.domain.peer_envelope import RelayedSpeech
from niuu.domain.peer_message import PeerAddress, PeerKind, ReplyKind, ReplyRoute
from niuu.observability import get_observability
from skuld.room_delivery import RoomDelivery, RoomDeliveryError

if TYPE_CHECKING:
    from skuld.channels import ChannelRegistry
    from skuld.config import RoomConfig

logger = logging.getLogger(__name__)

TurnAppender = Callable[[Any], None]
TimelineReporter = Callable[[dict[str, Any]], Awaitable[None]]
PeerObserver = Callable[[str, str, dict[str, Any]], Awaitable[None]]
PresencePublisher = Callable[[Any], Awaitable[None]]
UsageReporter = Callable[[dict[str, Any]], Awaitable[None]]
FrameEmitter = Callable[[dict[str, Any]], Awaitable[None]]


#: Hands a delivery to the session a room member joined from, through Forge.
SessionDeliverer = Callable[[str, RoomDelivery], Awaitable[bool]]
#: The hosting session as a peer address (its id, name and node), for reply routes.
HostIdentity = Callable[[], PeerAddress]


def _participant_address(participant: Participant) -> PeerAddress:
    """A room participant as the party that spoke."""
    name = participant.display_name or participant.persona
    if participant.session_id:
        return PeerAddress(kind=PeerKind.SESSION, id=participant.session_id, name=name)
    kind = PeerKind.PERSON if participant.participant_type == "human" else PeerKind.MESH_PEER
    return PeerAddress(kind=kind, id=participant.peer_id, name=name)


def _session_message(payload: dict[str, Any]) -> str:
    """Render a delivery as the plain message another session takes in."""
    if payload.get("type") == "directed_message":
        return str(payload.get("content") or "")
    persona = payload.get("persona") or "A participant"
    summary = payload.get("summary") or ""
    return f"{persona} reported {payload.get('eventType')}: {summary}".rstrip(": ")


def _add_work_part(parts: list[dict[str, Any]], frame: dict[str, Any]) -> None:
    """Fold one room agent frame into a participant's working turn.

    Mirrors how room clients build a participant's live stream, so a turn
    replayed from history reads the same as the one watched live.
    """
    frame_type = str(frame.get("type") or "")
    data = frame.get("data")
    metadata = frame.get("metadata") or {}
    text = data if isinstance(data, str) else json.dumps(data, default=str)
    match frame_type:
        case "thought" | "response" | "text" | "message":
            part_type = "reasoning" if frame_type == "thought" else "text"
            if parts and parts[-1].get("type") == part_type:
                parts[-1]["text"] = f"{parts[-1].get('text', '')}{text}"
                return
            parts.append({"type": part_type, "text": text})
        case "tool_start":
            parts.append(
                {
                    "type": "tool_use",
                    "id": f"tool-{uuid.uuid4()}",
                    "name": text,
                    "input": metadata.get("input") if isinstance(metadata, dict) else {},
                }
            )
        case "tool_result":
            open_tool = next(
                (
                    part
                    for part in reversed(parts)
                    if part.get("type") == "tool_use" and not part.get("_answered")
                ),
                None,
            )
            tool_use_id = open_tool["id"] if open_tool else f"tool-{uuid.uuid4()}"
            if open_tool:
                open_tool["_answered"] = True
            parts.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": text,
                    "is_error": bool(metadata.get("is_error"))
                    if isinstance(metadata, dict)
                    else False,
                }
            )
        case "error":
            parts.append({"type": "text", "text": text})


#: ``metadata.messageType`` of a kept room row (``room_mesh_message``): a client shows it as
#: the row it was live, not as a message.
ROOM_ROW_MESSAGE_TYPE = "mesh_message"


def _source_wire_fields(event: dict[str, Any]) -> dict[str, Any]:
    """Keep neutral source identity available to delivery adapters."""
    fields: dict[str, Any] = {}
    for key in (
        "sourceEventId",
        "sourceEventType",
        "sessionId",
        "taskId",
        "correlationId",
        "rootCorrelationId",
    ):
        value = event.get(key)
        if value:
            fields[key] = value
    return fields


def _room_agent_frame(agent_event: Any) -> dict[str, Any]:
    """Add the ``data``/``metadata`` envelope room clients read to a projected Ravn event.

    The collaboration projection carries a Ravn event as ``{type, payload, taskId, urgency}``;
    the room's browser clients (web-next ``useSkuldChat``) render a participant's live stream
    from ``frame.data`` and ``frame.metadata``, the shape Ravn's Skuld channel sent before the
    projection replaced it. Both shapes ride on the frame, so neither reader has to change.
    """
    if not isinstance(agent_event, dict):
        return {}
    payload = agent_event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    data: Any
    metadata: dict[str, Any]
    match str(agent_event.get("type") or ""):
        case "thought":
            data = payload.get("text", "")
            metadata = {"thinking": True} if payload.get("thinking") else {}
        case "response":
            data = payload.get("text", "")
            metadata = {}
        case "tool_start":
            data = payload.get("tool_name", "")
            metadata = {"input": payload.get("input", {})}
            if "diff" in payload:
                metadata["diff"] = payload["diff"]
        case "tool_result":
            data = payload.get("result", "")
            metadata = {
                "tool_name": payload.get("tool_name", ""),
                "is_error": payload.get("is_error", False),
            }
        case "error":
            data = payload.get("message", "")
            metadata = {}
        case "task_started":
            data = payload.get("title", "")
            metadata = {"task_id": payload.get("task_id", ""), "title": payload.get("title", "")}
        case "task_complete":
            data = payload
            metadata = {
                "task_id": agent_event.get("taskId") or "",
                "success": payload.get("success", False),
            }
        case _:
            data = payload
            metadata = {}
    return {**agent_event, "data": data, "metadata": metadata}


def _peer_observation(event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Translate a collaboration event into Skuld's peer-observation contract."""
    kind = str(event.get("kind") or "")
    base = {
        "source_event_id": event.get("sourceEventId") or "",
        "task_id": event.get("taskId") or "",
        "session_id": event.get("sessionId") or "",
        "correlation_id": event.get("correlationId") or "",
        "root_correlation_id": event.get("rootCorrelationId") or "",
    }

    if kind == "agent_event":
        agent_event = event.get("event")
        if not isinstance(agent_event, dict):
            return None
        payload = agent_event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        event_type = str(agent_event.get("type") or event.get("sourceEventType") or "")
        if not event_type:
            return None
        metadata = dict(payload)
        metadata["task_id"] = str(agent_event.get("taskId") or base["task_id"])
        metadata["urgency"] = agent_event.get("urgency", 0.5)
        return event_type, {**base, "data": dict(payload), "metadata": metadata}

    if kind == "outcome":
        fields = event.get("fields")
        if not isinstance(fields, dict):
            fields = {}
        context = event.get("context")
        if not isinstance(context, dict):
            context = {}
        event_type = str(event.get("eventType") or "")
        data = {
            **fields,
            **context,
            "event_type": event_type,
            "fields": dict(fields),
            "valid": event.get("valid", True),
        }
        for key in ("summary", "verdict"):
            if event.get(key) is not None:
                data[key] = event[key]
        if event.get("routingOnly"):
            data["routing_only"] = True
        return "outcome", {
            **base,
            "data": data,
            "metadata": {"event_type": event_type, "task_id": base["task_id"]},
        }

    if kind == "notification" and event.get("notificationType") == "help_needed":
        data = {
            key: event[key]
            for key in (
                "persona",
                "reason",
                "summary",
                "attempted",
                "recommendation",
                "context",
            )
            if key in event
        }
        return "help_needed", {
            **base,
            "data": data,
            "metadata": {"urgency": event.get("urgency", 0.85)},
        }

    if kind == "message":
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        event_type = str(event.get("sourceEventType") or "")
        if event_type not in {"response", "error"}:
            event_type = "error" if event.get("error") else "response"
        return event_type, {**base, "data": str(event.get("content") or ""), "metadata": metadata}

    return None


class SkuldCollaborationAdapter(CollaborationRoom):
    """Expose a shared room through Skuld channels and WebSockets.

    The adapter understands collaboration event kinds, not Ravn event types.
    Ravn is responsible for projecting its own events before sending them.
    """

    def __init__(
        self,
        config: RoomConfig,
        channels: ChannelRegistry,
        *,
        emit_frame: FrameEmitter,
        append_turn: TurnAppender | None = None,
        report_timeline_event: TimelineReporter | None = None,
        observe_peer_event: PeerObserver | None = None,
        publish_presence_event: PresencePublisher | None = None,
        report_usage: UsageReporter | None = None,
        environment_id: str | None = None,
        clock: Callable[[], float] | None = None,
        deliver_to_session: SessionDeliverer | None = None,
        host_identity: HostIdentity | None = None,
    ) -> None:
        self._config = config
        self._deliver_to_session = deliver_to_session
        self._host_identity = host_identity
        self._channels = channels
        self._emit_frame = emit_frame
        self._append_turn = append_turn
        self._report_timeline_event = report_timeline_event
        self._observe_peer_event = observe_peer_event
        self._publish_presence_event = publish_presence_event
        self._report_usage = report_usage
        self._websockets: dict[str, WebSocket] = {}
        self._reported_usage_ids: set[str] = set()
        # Each participant's working steps since its last reply, outcome or finished task.
        # Saved as an internal turn so a session opened later shows the work,
        # not only its outcomes.
        self._peer_work: dict[str, list[dict[str, Any]]] = {}
        self._delivered_source_events: OrderedDict[str, None] = OrderedDict()
        self._timeline_started_at = time.monotonic()
        super().__init__(
            participant_colors=config.participant_colors,
            environment_id=environment_id or config.environment_id,
            broadcast=channels.broadcast,
            publish=self._publish_collaboration_event,
            deliver=self._deliver_to_peer,
            is_connected=lambda peer_id: peer_id in self._websockets,
            clock=clock,
            presence_sweep_interval_s=config.presence_sweep_interval_s,
            agent_heartbeat_ttl_s=config.agent_heartbeat_ttl_s,
            peer_heartbeat_ttl_s=config.peer_heartbeat_ttl_s,
            join_heartbeat_ttl_s=config.join_heartbeat_ttl_s,
        )

    async def register(
        self,
        peer_id: str,
        persona: str,
        websocket: WebSocket,
        **metadata: Any,
    ) -> Participant:
        """Attach a participant WebSocket and register its shared identity."""
        self._websockets[peer_id] = websocket
        return await self.register_agent(peer_id, persona, **metadata)

    async def register_mesh_peer(
        self,
        peer_id: str,
        persona: str,
        **metadata: Any,
    ) -> Participant:
        """Register a participant discovered through a mesh adapter."""
        return await self.register_peer(peer_id, persona, **metadata)

    async def unregister(self, peer_id: str, *, reason: str = "left") -> None:
        participant = self.participant(peer_id)
        if participant is not None:
            self._save_peer_work(participant)
        self._websockets.pop(peer_id, None)
        await super().unregister(peer_id, reason=reason)

    def is_connected(self, peer_id: str) -> bool:
        return peer_id in self._websockets

    def pending_help_peer_ids(self) -> tuple[str, ...]:
        """Return peers whose Ravn-provided reply context awaits an operator."""
        return self.pending_reply_peer_ids()

    async def handle_collaboration_frame(self, peer_id: str, frame: dict[str, Any]) -> None:
        """Render one transport-neutral collaboration frame."""
        participant = self.participant(peer_id)
        if participant is None:
            logger.warning("collaboration frame from unknown peer_id=%s dropped", peer_id)
            return

        events = frame.get("events")
        if not isinstance(events, list):
            events = [frame]
        for event in events:
            if not isinstance(event, dict):
                continue
            await self._handle_event(participant, event)

    async def _handle_event(self, participant: Participant, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        delivery_key = self._delivery_key(event, kind)
        if delivery_key and not self._claim_delivery(delivery_key):
            get_observability().count(
                "skuld.collaboration.events",
                attributes={"kind": kind or "unknown", "outcome": "duplicate"},
            )
            return
        carrier = event.get("traceContext")
        if not isinstance(carrier, dict):
            carrier = {}
        telemetry = get_observability()
        with telemetry.span(
            "skuld.collaboration.event.receive",
            attributes={
                "skuld.collaboration.kind": kind or "unknown",
                "skuld.participant.id": participant.peer_id,
                "skuld.environment.id": participant.environment_id or self._environment_id,
            },
            carrier=carrier,
        ) as span:
            try:
                if kind == "message":
                    await self._handle_message(participant, event)
                elif kind == "activity":
                    await self._handle_activity(participant, event)
                elif kind == "notification":
                    await self._handle_notification(participant, event)
                elif kind == "outcome":
                    await self._handle_outcome(participant, event)
                elif kind == "delegation":
                    await self._handle_delegation(participant, event)
                elif kind == "agent_event":
                    await self._handle_agent_event(participant, event)
                elif kind == "usage":
                    await self._handle_usage(event)
                else:
                    logger.warning("unsupported collaboration event kind=%s", kind)
                    telemetry.count(
                        "skuld.collaboration.events",
                        attributes={"kind": kind or "unknown", "outcome": "unsupported"},
                    )
                    return

                observation = _peer_observation(event)
                if self._observe_peer_event is not None and observation is not None:
                    event_type, payload = observation
                    await self._observe_peer_event(participant.peer_id, event_type, payload)
                telemetry.count(
                    "skuld.collaboration.events",
                    attributes={"kind": kind, "outcome": "handled"},
                )
            except Exception:
                if delivery_key:
                    self._delivered_source_events.pop(delivery_key, None)
                telemetry.mark_error(span, "collaboration_event_failed")
                telemetry.count(
                    "skuld.collaboration.events",
                    attributes={"kind": kind or "unknown", "outcome": "failed"},
                )
                raise

    def _delivery_key(self, event: dict[str, Any], kind: str) -> str:
        source_event_id = str(event.get("sourceEventId") or "").strip()
        if not source_event_id:
            return ""
        return f"{source_event_id}:{kind}"

    def _claim_delivery(self, key: str) -> bool:
        if key in self._delivered_source_events:
            return False
        self._delivered_source_events[key] = None
        while len(self._delivered_source_events) > self._config.delivery_dedupe_max_entries:
            self._delivered_source_events.popitem(last=False)
        return True

    async def _handle_message(self, participant: Participant, event: dict[str, Any]) -> None:
        message_id = str(uuid.uuid4())
        content = str(event.get("content") or "")
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        thread_id = str(metadata.get("thread_id") or event.get("threadId") or "")
        visibility = str(event.get("visibility") or "public")
        is_error = bool(event.get("error"))
        wire_event: dict[str, Any] = {
            "type": "room_message",
            "id": message_id,
            "participantId": participant.peer_id,
            "participant": asdict(participant),
            "content": content,
            "visibility": visibility,
            **_source_wire_fields(event),
        }
        if is_error:
            wire_event["error"] = True
            failure_kind = str(event.get("failureKind") or "").strip()
            if failure_kind:
                wire_event["failureKind"] = failure_kind
        if thread_id:
            wire_event["threadId"] = thread_id
        message_type = str(metadata.get("messageType") or "")
        if message_type:
            wire_event["metadata"] = {"messageType": message_type}
        await self._channels.broadcast(wire_event)
        await self._handle_activity(
            participant,
            {
                "kind": "activity",
                "activityType": "error" if is_error else "idle",
            },
        )
        await self._emit_timeline_event(
            {
                "type": "error" if is_error else "message",
                "label": self._timeline_label(participant, self._preview(content, 120)),
            }
        )

        self._save_peer_work(participant)
        if self._append_turn is not None:
            from skuld.conversation_models import ConversationTurn

            self._append_turn(
                ConversationTurn(
                    id=message_id,
                    role="assistant",
                    content=content,
                    metadata=metadata,
                    participant_id=participant.peer_id,
                    participant_meta=asdict(participant),
                    thread_id=thread_id or None,
                    visibility=visibility,
                )
            )

        for room_id in participant.room_ids:
            await self.record_huddle_message(
                room_id=room_id,
                environment_id=participant.environment_id or self._environment_id,
                message_id=message_id,
                participant_id=participant.peer_id,
                role="assistant",
                content=content,
                visibility=visibility,
                thread_id=thread_id,
                metadata=metadata,
            )

    def _save_peer_work(self, participant: Participant) -> None:
        """Append a participant's collected working steps as one internal turn."""
        parts = self._peer_work.pop(participant.peer_id, [])
        if not parts or self._append_turn is None:
            return
        from skuld.conversation_models import ConversationTurn

        self._append_turn(
            ConversationTurn(
                id=str(uuid.uuid4()),
                role="assistant",
                content="",
                parts=[{k: v for k, v in part.items() if k != "_answered"} for part in parts],
                participant_id=participant.peer_id,
                participant_meta=asdict(participant),
                visibility="internal",
            )
        )

    async def _handle_activity(self, participant: Participant, event: dict[str, Any]) -> None:
        activity_type = str(event.get("activityType") or "idle")
        await self.update_activity(participant.peer_id, activity_type)
        wire_event: dict[str, Any] = {
            "type": "room_activity",
            "participantId": participant.peer_id,
            "activityType": activity_type,
            **_source_wire_fields(event),
        }
        detail = event.get("detail")
        if detail:
            detail_text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
            wire_event["detail"] = detail_text[: self._config.activity_detail_max_length]
        await self._channels.broadcast(wire_event)

    async def _handle_notification(self, participant: Participant, event: dict[str, Any]) -> None:
        notification_type = str(event.get("notificationType") or "notice")
        wire_event = {
            "type": "room_notification",
            "notificationType": notification_type,
            "participantId": participant.peer_id,
            "participant": asdict(participant),
            "persona": event.get("persona") or participant.persona,
            "reason": event.get("reason") or "",
            "summary": event.get("summary") or "",
            "attempted": list(event.get("attempted") or []),
            "recommendation": event.get("recommendation") or "",
            "urgency": event.get("urgency", 0.5),
            **_source_wire_fields(event),
        }
        for key in ("context", "traceContext"):
            if event.get(key):
                wire_event["trace_context" if key == "traceContext" else key] = event[key]
        reply_context = event.get("replyContext")
        if isinstance(reply_context, dict) and reply_context:
            self.set_reply_context(participant.peer_id, reply_context)
        telemetry = get_observability()
        with telemetry.span(
            "skuld.collaboration.operator_attention",
            attributes={
                "skuld.participant.id": participant.peer_id,
                "skuld.notification.type": notification_type,
                "skuld.notification.reason": str(event.get("reason") or ""),
            },
        ):
            await self._channels.broadcast(wire_event)
            telemetry.count(
                "skuld.operator.attention_requests",
                attributes={"notification_type": notification_type},
            )

    async def _handle_outcome(self, participant: Participant, event: dict[str, Any]) -> None:
        """Show a member's outcome; in a realm room, also keep it as the member's turn."""
        if event.get("routingOnly"):
            return
        event_type = str(event.get("eventType") or "")
        outcome: dict[str, Any] = {
            "type": "room_outcome",
            "participantId": participant.peer_id,
            "participant": asdict(participant),
            "persona": event.get("persona") or participant.persona,
            "eventType": event_type,
            "fields": dict(event.get("fields") or {}),
            "valid": event.get("valid", True),
            **_source_wire_fields(event),
        }
        for key in ("summary", "verdict"):
            if event.get(key):
                outcome[key] = event[key]
        self._save_peer_work(participant)
        await self._emit_frame(outcome)
        if self._config.realm_room:
            # A run's room keeps what its members did: the outcome is also the member's turn
            # in the host's conversation, so a finished run shows it, not only a live page.
            await self._handle_message(
                participant,
                {
                    "kind": "message",
                    "content": outcome_turn_content(outcome),
                    "visibility": "public",
                    "metadata": {"outcome_event_type": event_type},
                },
            )
        await self._deliver_outcome_to_subscribers(
            outcome,
            source_is_mesh=participant.participant_kind == "mesh",
        )

    async def _deliver_outcome_to_subscribers(
        self,
        outcome: dict[str, Any],
        *,
        source_is_mesh: bool,
    ) -> None:
        event_type = str(outcome.get("eventType") or "")
        if not event_type:
            return
        payload = {**outcome, "type": "collaboration.outcome"}
        failures: list[str] = []
        for participant in self.participants.values():
            if not matches_subscription(event_type, participant.subscribes_to):
                continue
            if source_is_mesh and participant.participant_kind == "mesh":
                continue
            try:
                await self._deliver_to_peer(participant.peer_id, payload)
            except RoomDeliveryError as exc:
                failures.append(str(exc))
        if failures:
            raise RoomDeliveryError("; ".join(failures))

    async def _handle_delegation(self, participant: Participant, event: dict[str, Any]) -> None:
        """Show one room row (a hand-off, a workflow step's offer, pickup or outcome) and keep it.

        The row is kept as a turn of the room, so whoever opens the room later sees the run's
        hand-offs too, not only the ones that happened while they watched. The live row carries
        the turn's id, so a client that reloads the history shows it once.
        """
        row_id = str(uuid.uuid4())
        from_persona = str(event.get("fromPersona") or participant.persona)
        event_type = str(event.get("eventType") or "work")
        direction = str(event.get("direction") or "delegate")
        preview = str(event.get("preview") or "")
        await self._channels.broadcast(
            {
                "type": "room_mesh_message",
                "id": row_id,
                "participantId": participant.peer_id,
                "participant": asdict(participant),
                "fromPersona": from_persona,
                "eventType": event_type,
                "direction": direction,
                "preview": preview,
                **_source_wire_fields(event),
            }
        )
        if self._append_turn is None:
            return
        from skuld.conversation_models import ConversationTurn

        self._append_turn(
            ConversationTurn(
                id=row_id,
                role="assistant",
                content=preview,
                metadata={
                    "messageType": ROOM_ROW_MESSAGE_TYPE,
                    "fromPersona": from_persona,
                    "eventType": event_type,
                    "direction": direction,
                },
                participant_id=participant.peer_id,
                participant_meta=asdict(participant),
                visibility="public",
            )
        )

    async def _handle_agent_event(self, participant: Participant, event: dict[str, Any]) -> None:
        frame = _room_agent_frame(event.get("event"))
        _add_work_part(self._peer_work.setdefault(participant.peer_id, []), frame)
        if frame.get("type") == "task_complete":
            self._save_peer_work(participant)
        await self._channels.broadcast(
            {
                "type": "room_agent_event",
                "participantId": participant.peer_id,
                "frame": frame,
                **_source_wire_fields(event),
            }
        )
        timeline = event.get("timeline")
        if not isinstance(timeline, dict):
            return
        timeline_event = dict(timeline)
        timeline_event["label"] = self._timeline_label(
            participant, str(timeline_event.get("label") or "")
        )
        await self._emit_timeline_event(timeline_event)

    async def _handle_usage(self, event: dict[str, Any]) -> None:
        if self._report_usage is None:
            return
        usage = event.get("usage")
        if not isinstance(usage, dict):
            return
        usage_id = str(usage.get("usage_id") or "")
        if usage_id and usage_id in self._reported_usage_ids:
            return
        if usage_id:
            self._reported_usage_ids.add(usage_id)
        model = str(usage.get("model") or "unknown")
        model_usage: dict[str, Any] = {
            "inputTokens": int(usage.get("inputTokens") or 0),
            "outputTokens": int(usage.get("outputTokens") or 0),
            "cacheReadInputTokens": int(usage.get("cacheReadInputTokens") or 0),
            "cacheCreationInputTokens": int(usage.get("cacheCreationInputTokens") or 0),
        }
        if usage.get("costUSD") is not None:
            model_usage["costUSD"] = float(usage["costUSD"])
        await self._report_usage({"modelUsage": {model: model_usage}})

    async def broadcast_cli_activity(
        self, peer_id: str, activity_type: str, detail: str = ""
    ) -> None:
        participant = self.participant(peer_id)
        if participant is None:
            return
        await self._handle_activity(
            participant,
            {"kind": "activity", "activityType": activity_type, "detail": detail},
        )

    async def post_notice(
        self,
        peer_id: str,
        content: str,
        *,
        notice_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Post a notice in the room as the host's own message (the room's Coordinator).

        ``notice_id`` keys it: a notice posted again (its sender retried) is shown once.
        """
        participant = self.participant(peer_id)
        if participant is None:
            raise LookupError(f"the room's host {peer_id} is not in the room")
        await self._handle_event(
            participant,
            {
                "kind": "message",
                "content": content,
                "visibility": "public",
                "sourceEventId": notice_id,
                # A notice is said once and stands alone: the chat never folds it into the
                # host's work the way it folds a speaker's consecutive messages.
                "metadata": {**(metadata or {}), "room_notice": True, "messageType": "notice"},
            },
        )

    async def broadcast_cli_message(
        self,
        peer_id: str,
        content: str,
        *,
        is_error: bool = False,
        visibility: str = "public",
    ) -> None:
        participant = self.participant(peer_id)
        if participant is None:
            return
        await self._handle_message(
            participant,
            {
                "kind": "message",
                "content": content,
                "error": is_error,
                "visibility": visibility,
            },
        )

    async def tell_joined_sessions(self, content: str) -> None:
        """Tell every agent that joined from another session what just happened here."""
        failures: list[str] = []
        for participant in self.participants.values():
            if not participant.session_id:
                continue
            try:
                await self._deliver_to_peer(
                    participant.peer_id, {"type": "directed_message", "content": content}
                )
            except RoomDeliveryError as exc:
                failures.append(str(exc))
        if failures:
            raise RoomDeliveryError("; ".join(failures))

    async def tell_member(self, peer_id: str, content: str, room_id: str) -> None:
        """Tell one member of a room something, said in that room by this host."""
        await self._deliver_to_peer(
            peer_id,
            {"type": "directed_message", "content": content, "metadata": {"room_id": room_id}},
        )

    async def _deliver_to_peer(self, peer_id: str, payload: dict[str, Any]) -> bool:
        """Deliver over a peer's socket here, or to the session it joined from."""
        participant = self.participant(peer_id)
        if peer_id in self._websockets or participant is None or not participant.session_id:
            return await self._deliver_to_websocket(peer_id, payload)
        if self._deliver_to_session is None or self._host_identity is None:
            raise RoomDeliveryError(
                f"Room member {peer_id} joined from session {participant.session_id}, "
                "but this broker has no route to another session; set volundr_api_url"
            )
        message = _session_message(payload)
        if participant.notice_context:
            message = f"[{participant.notice_context}] {message}"
        delivery = RoomDelivery(
            content=message,
            speech=self._speech_for(participant, payload),
            request_id=f"room-{uuid.uuid4().hex}",
        )
        try:
            delivered = await self._deliver_to_session(participant.session_id, delivery)
        except RoomDeliveryError:
            get_observability().count(
                "skuld.collaboration.deliveries", attributes={"outcome": "failed"}
            )
            raise
        get_observability().count(
            "skuld.collaboration.deliveries",
            attributes={"outcome": "delivered" if delivered else "failed"},
        )
        if not delivered:
            raise RoomDeliveryError(
                f"Could not deliver to room member {peer_id} in session {participant.session_id}"
            )
        return True

    def _speech_for(self, recipient: Participant, payload: dict[str, Any]) -> RelayedSpeech:
        """Who spoke, in which room, and where the recipient answers.

        The speaker is the participant the payload names (a directed message's
        ``participant_id``, an outcome's ``participantId``). A message nobody in the room
        spoke, such as the host telling its members what happened, is the host's own.
        """
        assert self._host_identity is not None
        host = self._host_identity()
        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        speaker_id = str(metadata.get("participant_id") or payload.get("participantId") or "")
        speaker = self.participant(speaker_id) if speaker_id else None
        return RelayedSpeech(
            speaker=host if speaker is None else _participant_address(speaker),
            room_id=self._room_for(recipient, speaker, metadata),
            reply=ReplyRoute(kind=ReplyKind.ROOM, target=host.id, node=host.node),
        )

    def _room_for(
        self, recipient: Participant, speaker: Participant | None, metadata: dict[str, Any]
    ) -> str:
        """The room a delivery was said in: named, shared with the speaker, or the only one."""
        named = str(metadata.get("room_id") or "")
        if named:
            return named
        rooms = recipient.room_ids
        if speaker is not None:
            shared = [room for room in rooms if room in speaker.room_ids]
            rooms = tuple(shared) or rooms
        if len(rooms) == 1:
            return rooms[0]
        if not rooms:
            raise RoomDeliveryError(
                f"Room member {recipient.peer_id} is in no room, so a message to it has no room "
                "to be said in"
            )
        raise RoomDeliveryError(
            f"Room member {recipient.peer_id} is in {len(rooms)} rooms ({', '.join(rooms)}) "
            "and the message names none; pass room_id in the message metadata"
        )

    async def _deliver_to_websocket(self, peer_id: str, payload: dict[str, Any]) -> bool:
        websocket = self._websockets.get(peer_id)
        if websocket is None:
            return False
        metadata = payload.get("metadata")
        carrier = metadata.get("trace_context") if isinstance(metadata, dict) else {}
        if not isinstance(carrier, dict):
            carrier = {}
        telemetry = get_observability()
        with telemetry.span(
            "skuld.collaboration.directed_message.deliver",
            attributes={
                "skuld.participant.id": peer_id,
                "skuld.message.type": str(payload.get("type") or "unknown"),
            },
            carrier=carrier,
        ) as span:
            try:
                await websocket.send_text(json.dumps(payload))
            except Exception:
                telemetry.mark_error(span, "collaboration_delivery_failed")
                telemetry.count(
                    "skuld.collaboration.deliveries",
                    attributes={"outcome": "failed"},
                )
                logger.warning("collaboration delivery failed peer_id=%s", peer_id, exc_info=True)
                return False
            telemetry.count(
                "skuld.collaboration.deliveries",
                attributes={"outcome": "delivered"},
            )
            return True

    async def _publish_collaboration_event(self, event: CollaborationEvent) -> None:
        if self._publish_presence_event is None:
            return

        from sleipnir.domain import catalog

        payload = dict(event.payload)
        common = {
            "source": "skuld:collaboration",
            "correlation_id": event.correlation_id,
        }
        factories: dict[str, Callable[..., Any]] = {
            "room.opened": catalog.room_opened,
            "room.context_snapshot.recorded": catalog.room_context_snapshot_recorded,
            "room.message.recorded": catalog.room_message_recorded,
            "room.closed": catalog.room_closed,
        }
        if event.event_type == "participant.joined":
            adapted = catalog.participant_joined(
                environment_id=payload["environment_id"],
                participant_id=payload["participant_id"],
                participant_type=payload["participant_type"],
                display_name=payload["display_name"],
                capabilities=payload["capabilities"],
                **common,
            )
        elif event.event_type == "participant.left":
            adapted = catalog.participant_left(
                environment_id=payload["environment_id"],
                participant_id=payload["participant_id"],
                participant_type=payload["participant_type"],
                display_name=payload["display_name"],
                reason=payload.get("reason", "left"),
                **common,
            )
        elif event.event_type == "participant.heartbeat":
            adapted = catalog.participant_heartbeat(
                environment_id=payload["environment_id"],
                participant_id=payload["participant_id"],
                participant_type=payload["participant_type"],
                display_name=payload["display_name"],
                status=payload.get("status", "idle"),
                wakefulness=payload["wakefulness"],
                attention_state=payload["attention_state"],
                heartbeat_ttl_s=payload["heartbeat_ttl_s"],
                **common,
            )
        elif event.event_type == "room.transcript.recorded":
            transcript_content = str(payload.pop("transcript_content", ""))
            adapted = catalog.room_transcript_recorded(**payload, **common)
            if transcript_content:
                adapted.payload["transcript_content"] = transcript_content
        elif event.event_type in factories:
            adapted = factories[event.event_type](**payload, **common)
        else:
            logger.warning("unsupported collaboration presence event type=%s", event.event_type)
            return

        try:
            await self._publish_presence_event(adapted)
        except Exception:
            logger.warning(
                "collaboration presence publish failed type=%s",
                event.event_type,
                exc_info=True,
            )

    async def _emit_timeline_event(self, event: dict[str, Any]) -> None:
        if self._report_timeline_event is None:
            return
        payload = {"t": int(time.monotonic() - self._timeline_started_at), **event}
        try:
            await self._report_timeline_event(payload)
        except Exception:
            logger.debug("timeline reporting failed", exc_info=True)

    @staticmethod
    def _preview(content: str, limit: int) -> str:
        preview = " ".join(line.strip() for line in content.splitlines() if line.strip()).strip()
        if len(preview) <= limit:
            return preview
        return preview[: limit - 3].rstrip() + "..."

    @staticmethod
    def _timeline_label(participant: Participant, label: str) -> str:
        return f"{participant.persona}: {label}" if label else participant.persona


__all__ = ["SkuldCollaborationAdapter"]

#: What an outcome carries for its receivers to act on, not what the member concluded: the
#: task it was given, its diff and where it works. The room's turn leaves them out.
_OUTCOME_CONTEXT_KEYS = frozenset(
    {"task_description", "diff_summary", "workspace_path", "session_id", "persona"}
)


def outcome_turn_content(outcome: dict[str, Any]) -> str:
    """A member's outcome as a turn: its summary, then the outcome block the chat reads.

    Each field is one ``key: value`` line (a structured value as compact JSON), the format the
    chat's outcome card and the room's worker list parse back from a stored turn. A text of
    several lines (a markdown write-up) is a ``key: |`` block, so it keeps its lines.
    """
    summary = str(outcome.get("summary") or "").strip()
    event_type = str(outcome.get("eventType") or "")
    raw = dict(outcome.get("fields") or {})
    fields: dict[str, Any] = {"event_type": event_type} if event_type else {}
    for key in ("verdict", "summary"):
        if outcome.get(key):
            fields[key] = outcome[key]
    # The member's own outcome block, when it wrote one, is what it concluded.
    structured = raw.pop("outcome", None)
    if isinstance(structured, dict):
        for key, value in structured.items():
            fields.setdefault(str(key), value)
    for key, value in raw.items():
        if key not in _OUTCOME_CONTEXT_KEYS:
            fields.setdefault(str(key), value)
    lines = [
        line
        for key, value in fields.items()
        if value not in (None, "")
        for line in _field_lines(str(key), value)
    ]
    head = summary.splitlines()[0] if summary else (event_type or "Outcome")
    return "\n".join([head, "", "---outcome---", *lines, "---end---"])


def _field_lines(key: str, value: Any) -> list[str]:
    text = value.strip() if isinstance(value, str) else ""
    if "\n" not in text:
        return [f"{key}: {_one_line(value)}"]
    return [f"{key}: |", *(f"  {line}" if line.strip() else "" for line in text.splitlines())]


def _one_line(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return " ".join(str(value).split())
