"""Project Ravn-owned semantics into transport-neutral collaboration events."""

from __future__ import annotations

from typing import Any

from ravn.domain.events import RavnEvent, RavnEventType

_ACTIVITY_BY_EVENT: dict[RavnEventType, str] = {
    RavnEventType.THOUGHT: "thinking",
    RavnEventType.TOOL_START: "tool_executing",
    RavnEventType.TOOL_RESULT: "idle",
    RavnEventType.TASK_STARTED: "busy",
    RavnEventType.TASK_COMPLETE: "idle",
    RavnEventType.DECISION: "thinking",
}


def _base(event: RavnEvent, kind: str) -> dict[str, Any]:
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


def _activity_detail(event: RavnEvent) -> Any:
    if event.type == RavnEventType.THOUGHT:
        return event.payload.get("text", "")
    if event.type == RavnEventType.TOOL_START:
        return event.payload.get("tool_name", "")
    if event.type == RavnEventType.TOOL_RESULT:
        return event.payload.get("result", "")
    if event.type == RavnEventType.TASK_STARTED:
        return event.payload.get("title", "")
    return event.payload


def _agent_event(event: RavnEvent) -> dict[str, Any]:
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


def _timeline_event(event: RavnEvent) -> dict[str, Any] | None:
    """Project Ravn tool semantics into the coarse Chronicle vocabulary."""
    if event.type == RavnEventType.TOOL_RESULT and event.payload.get("is_error"):
        result = str(event.payload.get("result") or "")
        return {"type": "error", "label": _preview(result, 120)}
    if event.type != RavnEventType.TOOL_START:
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


def _help_notification(event: RavnEvent, persona: str) -> dict[str, Any]:
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


def _outcome(event: RavnEvent, persona: str) -> dict[str, Any]:
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


def _delegation(event: RavnEvent, persona: str) -> dict[str, Any] | None:
    if event.type != RavnEventType.TOOL_START:
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


def project_ravn_event(event: RavnEvent, *, persona: str = "") -> list[dict[str, Any]]:
    """Return collaboration events whose meaning is decided inside Ravn."""
    if event.type in (RavnEventType.RESPONSE, RavnEventType.ERROR):
        projected = _base(event, "message")
        projected.update(
            {
                "content": str(
                    event.payload.get("text")
                    if event.type == RavnEventType.RESPONSE
                    else event.payload.get("message", "")
                ),
                "error": event.type == RavnEventType.ERROR,
                "metadata": {},
                "visibility": "public",
            }
        )
        if event.type == RavnEventType.ERROR and event.payload.get("failure_kind"):
            projected["failureKind"] = str(event.payload["failure_kind"])
        return [projected]

    if event.type == RavnEventType.HELP_NEEDED:
        return [_help_notification(event, persona)]

    if event.type == RavnEventType.OUTCOME:
        return [_outcome(event, persona)]

    if event.type == RavnEventType.USAGE:
        projected = _base(event, "usage")
        projected["usage"] = dict(event.payload)
        return [projected]

    events: list[dict[str, Any]] = []
    activity = _ACTIVITY_BY_EVENT.get(event.type)
    if activity:
        projected = _base(event, "activity")
        projected.update({"activityType": activity, "detail": _activity_detail(event)})
        events.append(projected)

    delegation = _delegation(event, persona)
    if delegation is not None:
        events.append(delegation)

    events.append(_agent_event(event))
    return events
