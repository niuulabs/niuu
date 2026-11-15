"""Project collaboration events carried by a Niuu mesh into a room sink."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from niuu.domain.workflow_step import (
    GateDecided,
    GateOpened,
    RunEnded,
    StepOffered,
    StepOutcome,
    StepPassed,
    StepPickedUp,
    UnknownWorkflowEvent,
    WorkflowEventBody,
    WorkflowEventError,
    parse_workflow_event,
)
from niuu.mesh import mesh_event_prefix
from niuu.mesh.events import MeshEventType
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


#: The ``ravn_type`` a mesh event of the ``workflow`` type carries through the Sleipnir bridge.
_WORKFLOW_TYPE = str(MeshEventType.WORKFLOW)

#: Payload keys an event of an unknown kind may state its point in, first one present wins.
_GENERIC_DETAIL_KEYS = ("summary", "note", "reason", "message", "description")


def _words(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip()


def _node_of(body: WorkflowEventBody) -> str:
    label = getattr(body, "node_label", "")
    return str(label or getattr(body, "node_id", "") or "a step")


def _with_detail(line: str, detail: str | None) -> str:
    return f"{line}: {detail}" if detail and detail.strip() else line


def _outcome_line(body: StepOutcome) -> str:
    node = _node_of(body)
    match body.ending:
        case "verdict":
            return _with_detail(
                f"{body.member} finished {node} with verdict {_words(body.verdict or '')}",
                body.note,
            )
        case "done":
            return _with_detail(f"{body.member} finished {node}", body.note)
        case "waiting":
            waiting_on = body.waiting_on
            target = f"{waiting_on.kind} {waiting_on.ref}" if waiting_on else "something"
            return _with_detail(
                f"{body.member} is waiting on {target} to finish {node}",
                waiting_on.reason if waiting_on else None,
            )
    return _with_detail(f"{body.member} failed {node}", body.failure_reason)


def _unknown_line(unknown: UnknownWorkflowEvent) -> str:
    line = f"Workflow event {unknown.kind}"
    for key in _GENERIC_DETAIL_KEYS:
        value = unknown.payload.get(key)
        if isinstance(value, str) and value.strip():
            return _with_detail(line, value)
    return line


def describe_workflow_event(
    event: WorkflowEventBody | UnknownWorkflowEvent,
) -> tuple[str, str, str]:
    """Return ``(who, tag, line)`` for a workflow event, as the room shows it.

    ``who`` is the persona the line is from (empty when the run's root speaks), ``tag`` is the
    event name the row shows beside the line, and ``line`` is one sentence in plain words. An
    event of a kind this build does not know is shown generically with its kind as the tag,
    never dropped.
    """
    if isinstance(event, UnknownWorkflowEvent):
        return "", event.kind, _unknown_line(event)
    kind = event.KIND
    if isinstance(event, StepOffered):
        attempt = f" (attempt {event.attempt})" if event.attempt > 1 else ""
        return (
            "",
            event.topic,
            f"Offered {_node_of(event)}{attempt} to anyone who does {event.topic}",
        )
    if isinstance(event, StepPickedUp):
        return event.member, kind, f"{event.member} picked up {_node_of(event)}"
    if isinstance(event, StepPassed):
        return (
            event.member,
            kind,
            _with_detail(f"{event.member} passed on {_node_of(event)}", event.reason),
        )
    if isinstance(event, StepOutcome):
        return event.member, kind, _outcome_line(event)
    if isinstance(event, GateOpened):
        return (
            "",
            kind,
            _with_detail(
                f"A person must decide {_node_of(event)} before the run goes on", event.summary
            ),
        )
    if isinstance(event, GateDecided):
        decision = "approved" if event.decision == "approved" else "asked for changes at"
        return (
            "",
            kind,
            _with_detail(f"{event.decided_by} {decision} {_node_of(event)}", event.note),
        )
    if isinstance(event, RunEnded):
        return "", kind, _with_detail(f"The run {event.status}", event.reason)
    return "", kind, f"Workflow event {kind}"


def workflow_room_events(
    ravn_event: dict[str, Any], *, source_event_id: str, session_id: str, correlation_id: str
) -> list[dict[str, Any]]:
    """Project one ``workflow`` event into the one line the run's room shows for it.

    The line is a ``delegation``, which the room already renders as a single row of who, what
    and event name. A payload that does not fit its kind, or has no kind, is shown as such and
    logged at error: a workflow event is never dropped from the thread.
    """
    try:
        parsed = parse_workflow_event(ravn_event)
        who, tag, line = describe_workflow_event(parsed)
    except WorkflowEventError as exc:
        logger.error("workflow event could not be read, shown as received: %s", exc)
        kind = ravn_event.get("kind")
        shown = kind if isinstance(kind, str) and kind.strip() else "workflow"
        who, tag, line = "", shown, f"Workflow event {shown} could not be read: {exc}"
    projected: dict[str, Any] = {
        "kind": "delegation",
        "sourceEventId": source_event_id,
        "sourceEventType": _WORKFLOW_TYPE,
        "sessionId": session_id,
        "correlationId": correlation_id,
        "rootCorrelationId": correlation_id,
        "fromPersona": who,
        "eventType": tag,
        "direction": "workflow",
        "preview": line,
    }
    if who:
        projected["persona"] = who
    return [projected]


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
        if event.payload.get("ravn_type") == _WORKFLOW_TYPE and isinstance(ravn_event, dict):
            events = workflow_room_events(
                ravn_event,
                source_event_id=str(event.payload.get("ravn_event_id") or ""),
                session_id=str(event.payload.get("ravn_session_id") or ""),
                correlation_id=event.correlation_id or "",
            )
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
