"""Telemetry normalization and dashboard projection for resident Valkyries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ravn.domain.valkyrie_history import canonical_environment_id
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent

Dashboard = dict[str, Any]
RAW_SIGNAL_TELEMETRY_LIMIT = 1_000
CONTROL_TELEMETRY_LIMIT = 2_000
LEARNING_SCOPES = ("private", "environment", "domain", "flock", "shared")
from ravn.api.valkyrie_projection_common import (  # noqa: F401
    _now,
    _as_int,
    _as_float,
    _as_string_list,
    _field,
    _slug,
    _environment_id,
    _canonical_environment_id,
    _valkyrie_id,
    _rollup_health,
    _first_transport_value,
    _live_report,
    _empty_telemetry,
)


def _event_dict(event: SleipnirEvent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, SleipnirEvent):
        return event.to_dict()
    return dict(event)


def _is_raw_signal_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("event_type") or "")
    return event_type.startswith("signal.")


def _is_runtime_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("event_type") or "")
    return event_type in {
        "valkyrie.runtime.started",
        "valkyrie.presence.announced",
        "valkyrie.presence.heartbeat",
        "valkyrie.state.changed",
        "valkyrie.state.updated",
        "valkyrie.dream.started",
        "valkyrie.dream.completed",
        "valkyrie.dream.failed",
        "valkyrie.dream.noop",
    }


def _event_timestamp(event: dict[str, Any]) -> str:
    timestamp = event.get("timestamp")
    if isinstance(timestamp, datetime):
        return timestamp.isoformat()
    if isinstance(timestamp, str):
        return timestamp
    return _now()


def _payload_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) else 0


def _payload_float(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    return value if isinstance(value, int | float) else 0.0


def _event_environment_id(event: dict[str, Any], payload: dict[str, Any]) -> str:
    return _canonical_environment_id(
        payload.get("environment_id")
        or payload.get("environmentId")
        or event.get("tenant_id")
        or "unknown"
    )


def _event_valkyrie_id(payload: dict[str, Any]) -> str:
    return str(payload.get("valkyrie_id") or payload.get("valkyrieId") or "")


def _event_valkyrie_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("valkyrie_name")
        or payload.get("valkyrieName")
        or payload.get("resident_name")
        or ""
    )


def _event_kind(event_type: str) -> str:
    if event_type.startswith("signal."):
        return "signal"
    if event_type.startswith("valkyrie.judgment."):
        return "judgment"
    if event_type.startswith("valkyrie.action."):
        return "action"
    if event_type.startswith("learning.") or event_type.startswith("flock.learning."):
        return "learning"
    if event_type.startswith("ravn.task."):
        return "task"
    if event_type.startswith("ravn.log.") or event_type.startswith("valkyrie.log."):
        return "log"
    if event_type.startswith("ravn.llm.") or event_type.startswith("llm."):
        return "llm"
    if event_type.startswith("tool.") or event_type.startswith("skill."):
        return "tool"
    if event_type.startswith("self_improvement."):
        return "learning"
    if event_type.startswith("flock."):
        return "flock"
    if event_type.startswith("valkyrie.presence."):
        return "presence"
    if event_type in {"valkyrie.runtime.started", "valkyrie.state.updated"}:
        return "runtime"
    if event_type in {"valkyrie.state.changed"} or event_type.startswith("valkyrie.dream."):
        return "wakefulness"
    return "event"


def _event_tier(payload: dict[str, Any]) -> str:
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    details = fields or outcome or payload
    return str(
        details.get("tier")
        or details.get("attention_tier")
        or payload.get("tier")
        or payload.get("attention_tier")
        or ""
    ).lower()


def _event_log_entry(event: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    summary = str(event.get("summary") or payload.get("summary") or event_type)
    event_id = event.get("event_id") or event.get("id") or f"{event_type}:{_event_timestamp(event)}"
    return {
        "id": str(event_id),
        "eventType": event_type,
        "kind": _event_kind(event_type),
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload),
        "valkyrieName": _event_valkyrie_name(payload),
        "source": str(event.get("source") or ""),
        "summary": summary,
        "urgency": _payload_float(event, "urgency"),
        "observedAt": _event_timestamp(event),
        "correlationId": str(event.get("correlation_id") or payload.get("correlation_id") or ""),
        "causationId": str(event.get("causation_id") or payload.get("causation_id") or ""),
        "tier": _event_tier(payload),
        "details": {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "status",
                "fields",
                "outcome",
                "published_signal_event_ids",
            }
        },
    }


def _signal_severity(event: dict[str, Any], payload: dict[str, Any]) -> str:
    raw = str(payload.get("severity") or payload.get("level") or "").lower()
    if raw in {"info", "notice", "warning", "critical"}:
        return raw
    urgency = _payload_float(event, "urgency")
    if urgency >= 0.8:
        return "critical"
    if urgency >= 0.5:
        return "warning"
    if urgency >= 0.25:
        return "notice"
    return "info"


def _signal_subject(event: dict[str, Any], payload: dict[str, Any]) -> str:
    subject = str(
        payload.get("subject")
        or payload.get("signal_id")
        or payload.get("resource")
        or payload.get("object")
        or event.get("correlation_id")
        or ""
    )
    if subject:
        return subject
    bits = [
        str(payload.get("namespace") or "").strip(),
        str(payload.get("kind") or "").strip(),
        str(payload.get("reason") or "").strip(),
    ]
    return ":".join(bit for bit in bits if bit) or str(event.get("event_type") or "signal")


def _signal_entry(event: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("event_type") or "")
    if not event_type.startswith("signal."):
        return None
    timestamp = _event_timestamp(event)
    event_id = str(event.get("event_id") or event.get("id") or f"{event_type}:{timestamp}")
    return {
        "id": f"live-{event_id}",
        "environmentId": _event_environment_id(event, payload),
        "source": str(event.get("source") or event_type),
        "subject": _signal_subject(event, payload),
        "summary": str(event.get("summary") or payload.get("summary") or event_type),
        "severity": _signal_severity(event, payload),
        "status": "new",
        "receivedAt": timestamp,
        "assignedValkyrieId": _event_valkyrie_id(payload),
        "labels": _as_string_list(payload.get("labels") or payload.get("label") or []),
    }


def _signals_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals = []
    seen: set[str] = set()
    for raw_event in events:
        event = _event_dict(raw_event)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        entry = _signal_entry(event, payload if isinstance(payload, dict) else {})
        if entry is None or entry["id"] in seen:
            continue
        seen.add(entry["id"])
        signals.append(entry)
    return sorted(signals, key=lambda item: item.get("receivedAt", ""), reverse=True)[:60]


def _state_drift(details: dict[str, Any], payload: dict[str, Any]) -> str:
    state = str(
        details.get("operational_state")
        or details.get("operationalState")
        or payload.get("operational_state")
        or payload.get("operationalState")
        or ""
    ).lower()
    severity = str(details.get("severity") or payload.get("severity") or "").lower()
    if state in {"degraded", "remediating", "blocked"} or severity == "critical":
        return "major"
    if state in {"watching", "investigating", "dreaming"} or severity in {"warning", "notice"}:
        return "minor"
    return "none"


def _operational_state_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    event_type = str(event.get("event_type") or "")
    if event_type not in {"valkyrie.state.updated", registry.VALKYRIE_JUDGMENT_PROPOSED}:
        return None
    details = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    if not details:
        details = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    if not details:
        details = payload
    environment_id = _event_environment_id(event, payload)
    valkyrie_id = _event_valkyrie_id(payload)
    operational_state = str(
        details.get("operational_state")
        or details.get("operationalState")
        or payload.get("operational_state")
        or payload.get("operationalState")
        or details.get("decision")
        or details.get("verdict")
        or "watching"
    )
    observed = str(
        details.get("state_summary")
        or details.get("stateSummary")
        or details.get("rationale")
        or details.get("summary")
        or payload.get("state_summary")
        or payload.get("summary")
        or event.get("summary")
        or operational_state
    )
    desired = str(
        details.get("desired_state")
        or details.get("desiredState")
        or payload.get("desired_state")
        or payload.get("desiredState")
        or "No unresolved drift requiring operator action"
    )
    return {
        "id": f"live-state-{environment_id}",
        "environmentId": environment_id,
        "name": operational_state.replace("_", " ").strip().capitalize() or "Operational state",
        "desired": desired,
        "observed": observed,
        "drift": _state_drift(details, payload),
        "maintainedBy": [valkyrie_id] if valkyrie_id else [],
        "updatedAt": _event_timestamp(event),
    }


def _operational_states_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_environment: dict[str, dict[str, Any]] = {}
    for raw_event in events:
        event = _event_dict(raw_event)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        entry = _operational_state_entry(event, payload if isinstance(payload, dict) else {})
        if entry is None:
            continue
        existing = by_environment.get(entry["environmentId"])
        if existing is None or entry.get("updatedAt", "") >= existing.get("updatedAt", ""):
            by_environment[entry["environmentId"]] = entry
    return sorted(
        by_environment.values(),
        key=lambda item: item.get("updatedAt", ""),
        reverse=True,
    )[:60]


def _court_decision_status(decision: str, payload: dict[str, Any]) -> str:
    raw = decision.lower()
    if raw in {"rejected", "reject", "denied", "deny", "blocked"}:
        return "rejected"
    if raw in {"pending", "open_huddle", "draft_for_review", "needs_review", "needs_approval"}:
        return "pending"
    if str(payload.get("escalation_path") or "").lower() == "review_queue":
        return "pending"
    if raw in {"record_only", "autonomous_action", "executed", "resolved", "ignored"}:
        return "executed"
    return "approved"


def _court_decision_risk(payload: dict[str, Any]) -> str:
    authority = str(
        payload.get("action_authorization") or payload.get("authority_boundary") or ""
    ).lower()
    tier = str(payload.get("tier") or payload.get("attention_tier") or "").lower()
    decision = str(payload.get("decision") or payload.get("outcome") or "").lower()
    if "hard" in authority or "gate" in authority:
        return "hard_gate"
    if tier == "ambient" and decision in {"record_only", "ignored", "suppress"}:
        return "low"
    if tier in {"urgent", "critical", "high"} or "review" in decision or "escalate" in decision:
        return "high"
    if tier in {"present", "notice", "warning"} or authority in {"delegated", "human"}:
        return "medium"
    return "low"


def _court_decision_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]] | None:
    event_type = str(event.get("event_type") or "")
    priority = {
        registry.ODIN_COURT_DECIDED: 1,
        registry.ATTENTION_DECISION_MADE: 2,
    }.get(event_type)
    if priority is None:
        return None
    timestamp = _event_timestamp(event)
    correlation_id = str(
        event.get("correlation_id")
        or payload.get("root_correlation_id")
        or payload.get("court_id")
        or event.get("event_id")
        or timestamp
    )
    decision = str(payload.get("decision") or payload.get("outcome") or "decided")
    title = str(event.get("summary") or payload.get("summary") or f"ODIN decision {decision}")
    decided_by = _as_string_list(payload.get("decided_by") or payload.get("decidedBy") or [])
    if not decided_by:
        decided_by = [str(event.get("source") or "odin-court")]
    return priority, {
        "id": f"live-{correlation_id}",
        "environmentId": _event_environment_id(event, payload),
        "title": title,
        "status": _court_decision_status(decision, payload),
        "risk": _court_decision_risk(payload),
        "decidedBy": decided_by,
        "createdAt": timestamp,
    }


def _court_decisions_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, tuple[int, dict[str, Any]]] = {}
    for raw_event in events:
        event = _event_dict(raw_event)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        projected = _court_decision_entry(event, payload if isinstance(payload, dict) else {})
        if projected is None:
            continue
        priority, entry = projected
        existing = by_id.get(entry["id"])
        if (
            existing is None
            or priority > existing[0]
            or (
                priority == existing[0]
                and entry.get("createdAt", "") > existing[1].get("createdAt", "")
            )
        ):
            by_id[entry["id"]] = (priority, entry)
    return sorted(
        [entry for _, entry in by_id.values()],
        key=lambda item: item.get("createdAt", ""),
        reverse=True,
    )[:60]


def _structured_log_entry(event: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    level = str(payload.get("level") or payload.get("severity") or "info").lower()
    message = str(
        payload.get("message")
        or payload.get("body")
        or payload.get("summary")
        or event.get("summary")
        or event_type
    )
    log_id = event.get("event_id") or event.get("id") or f"{event_type}:{_event_timestamp(event)}"
    component = payload.get("component") or payload.get("logger") or event.get("source") or ""
    return {
        "id": str(log_id),
        "eventType": event_type,
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload),
        "valkyrieName": _event_valkyrie_name(payload),
        "level": level,
        "component": str(component),
        "message": message,
        "taskId": str(payload.get("task_id") or ""),
        "observedAt": _event_timestamp(event),
    }
