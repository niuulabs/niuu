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
from niuu.observability import get_observability

if TYPE_CHECKING:
    from skuld.channels import ChannelRegistry
    from skuld.config import RoomConfig

logger = logging.getLogger(__name__)

TurnAppender = Callable[[Any], None]
TimelineReporter = Callable[[dict[str, Any]], Awaitable[None]]
PeerObserver = Callable[[str, str, dict[str, Any]], Awaitable[None]]
PresencePublisher = Callable[[Any], Awaitable[None]]
UsageReporter = Callable[[dict[str, Any]], Awaitable[None]]


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
            "valid": bool(event.get("valid", True)),
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
        append_turn: TurnAppender | None = None,
        report_timeline_event: TimelineReporter | None = None,
        observe_peer_event: PeerObserver | None = None,
        publish_presence_event: PresencePublisher | None = None,
        report_usage: UsageReporter | None = None,
        environment_id: str | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._channels = channels
        self._append_turn = append_turn
        self._report_timeline_event = report_timeline_event
        self._observe_peer_event = observe_peer_event
        self._publish_presence_event = publish_presence_event
        self._report_usage = report_usage
        self._websockets: dict[str, WebSocket] = {}
        self._reported_usage_ids: set[str] = set()
        self._delivered_source_events: OrderedDict[str, None] = OrderedDict()
        self._timeline_started_at = time.monotonic()
        super().__init__(
            participant_colors=config.participant_colors,
            environment_id=environment_id or config.environment_id,
            broadcast=channels.broadcast,
            publish=self._publish_collaboration_event,
            deliver=self._deliver_to_websocket,
            is_connected=lambda peer_id: peer_id in self._websockets,
            clock=clock,
            presence_sweep_interval_s=config.presence_sweep_interval_s,
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
        if event.get("routingOnly"):
            return
        outcome: dict[str, Any] = {
            "type": "room_outcome",
            "participantId": participant.peer_id,
            "participant": asdict(participant),
            "persona": event.get("persona") or participant.persona,
            "eventType": event.get("eventType") or "",
            "fields": dict(event.get("fields") or {}),
            "valid": bool(event.get("valid", True)),
            **_source_wire_fields(event),
        }
        for key in ("summary", "verdict"):
            if event.get(key):
                outcome[key] = event[key]
        await self._channels.broadcast(outcome)
        await self._deliver_outcome_to_subscribers(outcome)

    async def _deliver_outcome_to_subscribers(self, outcome: dict[str, Any]) -> None:
        event_type = str(outcome.get("eventType") or "")
        if not event_type:
            return
        payload = {**outcome, "type": "collaboration.outcome"}
        for participant in self.participants.values():
            if not matches_subscription(event_type, participant.subscribes_to):
                continue
            await self._deliver_to_websocket(participant.peer_id, payload)

    async def _handle_delegation(self, participant: Participant, event: dict[str, Any]) -> None:
        await self._channels.broadcast(
            {
                "type": "room_mesh_message",
                "participantId": participant.peer_id,
                "participant": asdict(participant),
                "fromPersona": event.get("fromPersona") or participant.persona,
                "eventType": event.get("eventType") or "work",
                "direction": event.get("direction") or "delegate",
                "preview": event.get("preview") or "",
                **_source_wire_fields(event),
            }
        )

    async def _handle_agent_event(self, participant: Participant, event: dict[str, Any]) -> None:
        await self._channels.broadcast(
            {
                "type": "room_agent_event",
                "participantId": participant.peer_id,
                "frame": event.get("event") or {},
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
