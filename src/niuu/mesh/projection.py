"""Project Ravn-owned semantics into transport-neutral collaboration events."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from niuu.mesh.events import MeshEvent, MeshEventType
from niuu.mesh.surface import strip_surface_marker

_ACTIVITY_BY_EVENT: dict[MeshEventType, str] = {
    MeshEventType.THOUGHT: "thinking",
    MeshEventType.TOOL_START: "tool_executing",
    MeshEventType.TOOL_RESULT: "idle",
    MeshEventType.TASK_STARTED: "busy",
    MeshEventType.TASK_COMPLETE: "idle",
    MeshEventType.DECISION: "thinking",
}


def _base(event: MeshEvent, kind: str) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "kind": kind,
        "sourceEventId": event.event_id,
        "sourceEventType": str(event.type),
        "source": event.source,
        "sessionId": event.session_id,
        "correlationId": event.correlation_id,
        "rootCorrelationId": event.root_correlation_id,
    }
    if event.trace_context:
        projected["traceContext"] = dict(event.trace_context)
    task_id = event.task_id or event.payload.get("task_id")
    if task_id:
        projected["taskId"] = str(task_id)
    return projected


def _activity_detail(event: MeshEvent) -> Any:
    if event.type == MeshEventType.THOUGHT:
        return event.payload.get("text", "")
    if event.type == MeshEventType.TOOL_START:
        return event.payload.get("tool_name", "")
    if event.type == MeshEventType.TOOL_RESULT:
        return event.payload.get("result", "")
    if event.type == MeshEventType.TASK_STARTED:
        return event.payload.get("title", "")
    return event.payload


def _agent_event(event: MeshEvent) -> dict[str, Any]:
    projected = _base(event, "agent_event")
    projected["event"] = {
        "type": str(event.type),
        "payload": dict(event.payload),
        "taskId": event.task_id or "",
        "urgency": event.urgency,
    }
    timeline = _timeline_event(event)
    if timeline is not None:
        projected["timeline"] = timeline
    return projected


def _timeline_event(event: MeshEvent) -> dict[str, Any] | None:
    """Project Ravn tool semantics into the coarse Chronicle vocabulary."""
    if event.type == MeshEventType.TOOL_RESULT and event.payload.get("is_error"):
        result = str(event.payload.get("result") or "")
        return {"type": "error", "label": _preview(result, 120)}
    if event.type != MeshEventType.TOOL_START:
        return None

    tool_name = str(event.payload.get("tool_name") or "")
    tool_input = event.payload.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if tool_name in {"Edit", "Write", "NotebookEdit"}:
        action = "modified" if tool_name == "Edit" else "created"
        return {"type": "file", "label": file_path or tool_name, "action": action}
    if tool_name not in {"Bash", "BashTool"}:
        return None

    command = str(tool_input.get("command") or "")
    if "git commit" in command:
        return {"type": "git", "label": command[:80] or "git commit"}
    return {"type": "terminal", "label": command[:80] or "bash"}


def _preview(content: str, limit: int) -> str:
    preview = " ".join(line.strip() for line in content.splitlines() if line.strip()).strip()
    if len(preview) <= limit:
        return preview
    return preview[: limit - 3].rstrip() + "..."


def _help_notification(event: MeshEvent, persona: str) -> dict[str, Any]:
    payload = event.payload
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    notification = _base(event, "notification")
    notification.update(
        {
            "notificationType": "help_needed",
            "persona": str(payload.get("persona") or persona or event.source),
            "reason": str(payload.get("reason") or "unknown"),
            "summary": str(payload.get("summary") or "Agent needs help"),
            "attempted": list(payload.get("attempted") or []),
            "recommendation": str(payload.get("recommendation") or ""),
            "urgency": event.urgency,
        }
    )
    if context:
        notification["context"] = dict(context)
    notification["replyContext"] = {
        "help_summary": notification["summary"],
        "help_reason": notification["reason"],
        "help_attempted": notification["attempted"],
        "help_recommendation": notification["recommendation"],
        "help_context": dict(context),
        "workflow_parent_event_id": str(context.get("workflow_parent_event_id") or ""),
        "workflow_node_id": str(context.get("workflow_node_id") or ""),
        "correlation_id": event.correlation_id,
        "root_correlation_id": str(context.get("root_correlation_id") or event.root_correlation_id),
        "session_id": str(context.get("session_id") or event.session_id),
        "task_id": str(event.task_id or context.get("task_id") or ""),
        "trace_context": dict(event.trace_context),
    }
    return notification


def _outcome(event: MeshEvent, persona: str) -> dict[str, Any]:
    payload = event.payload
    explicit_fields = payload.get("fields")
    fields = dict(explicit_fields) if isinstance(explicit_fields, dict) else dict(payload)
    fields.pop("collaboration_routing_only", None)
    projected = _base(event, "outcome")
    projected.update(
        {
            "persona": persona or event.source,
            "eventType": str(payload.get("event_type") or ""),
            "fields": fields,
            "valid": bool(payload.get("valid", True)),
        }
    )
    summary = payload.get("summary") or fields.get("summary")
    verdict = payload.get("verdict") or fields.get("verdict")
    if summary:
        projected["summary"] = summary
    if verdict:
        projected["verdict"] = verdict
    if payload.get("routing_only"):
        projected["routingOnly"] = True
    if isinstance(explicit_fields, dict):
        context = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "fields",
                "event_type",
                "valid",
                "summary",
                "verdict",
                "routing_only",
                "collaboration_routing_only",
            }
        }
        if context:
            projected["context"] = context
    return projected


def _delegation(event: MeshEvent, persona: str) -> dict[str, Any] | None:
    if event.type != MeshEventType.TOOL_START:
        return None
    if event.payload.get("tool_name") != "route_work":
        return None
    tool_input = event.payload.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    prompt = str(tool_input.get("prompt") or "")
    projected = _base(event, "delegation")
    projected.update(
        {
            "fromPersona": persona or event.source,
            "eventType": str(tool_input.get("event_type") or "work"),
            "direction": "delegate",
            "preview": prompt[:500] + ("..." if len(prompt) > 500 else ""),
        }
    )
    return projected


def _without_surface_marker(event: MeshEvent) -> MeshEvent:
    """The escalation marker is for the drive loop, so a room never sees it."""
    payload = dict(event.payload)
    for key in ("text", "summary"):
        if isinstance(payload.get(key), str):
            payload[key] = strip_surface_marker(payload[key])
    for key in ("fields", "outcome"):
        nested = payload.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("summary"), str):
            payload[key] = {**nested, "summary": strip_surface_marker(nested["summary"])}
    return replace(event, payload=payload)


def project_mesh_event(event: MeshEvent, *, persona: str = "") -> list[dict[str, Any]]:
    """Return collaboration events whose meaning is decided inside Ravn."""
    if event.type == MeshEventType.REVOCATION:
        # A control notice between peers, not something an agent said.
        return []
    event = _without_surface_marker(event)
    if event.type in (MeshEventType.RESPONSE, MeshEventType.ERROR):
        projected = _base(event, "message")
        projected.update(
            {
                "content": str(
                    event.payload.get("text")
                    if event.type == MeshEventType.RESPONSE
                    else event.payload.get("message", "")
                ),
                "error": event.type == MeshEventType.ERROR,
                "metadata": {},
                "visibility": "public",
            }
        )
        if event.type == MeshEventType.ERROR and event.payload.get("failure_kind"):
            projected["failureKind"] = str(event.payload["failure_kind"])
        if event.type == MeshEventType.ERROR:
            # Which workflow node failed, so the room can attribute the
            # failure to a stage rather than only to a peer.
            projected["metadata"] = {
                key: str(event.payload[key])
                for key in ("workflow_node_id", "persona", "failure_kind")
                if event.payload.get(key)
            }
        return [projected]

    if event.type == MeshEventType.HELP_NEEDED:
        return [_help_notification(event, persona)]

    if event.type == MeshEventType.OUTCOME:
        return [_outcome(event, persona)]

    if event.type == MeshEventType.USAGE:
        projected = _base(event, "usage")
        projected["usage"] = dict(event.payload)
        return [projected]

    # The work first, then the activity it leaves the peer in. A room keeps a peer's work in
    # one message until the peer goes idle; told "idle" before a tool result, it closed that
    # message and opened a new one holding the result alone, shown as an empty message.
    events: list[dict[str, Any]] = []
    delegation = _delegation(event, persona)
    if delegation is not None:
        events.append(delegation)

    events.append(_agent_event(event))

    activity = _ACTIVITY_BY_EVENT.get(event.type)
    if activity:
        projected = _base(event, "activity")
        projected.update({"activityType": activity, "detail": _activity_detail(event)})
        events.append(projected)
    return events
