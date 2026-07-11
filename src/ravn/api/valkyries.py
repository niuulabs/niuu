"""Resident Valkyrie dashboard projection for the Ravn API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ravn.api.valkyrie_config import (
    ValkyrieDashboardConfig,
    configured_environment_records,
)
from ravn.api.valkyrie_requests import (
    LEARNING_FEEDBACK_VERDICTS,
    AutonomyUpdateRequest,
    HuddleJoinRequest,
    HuddleSendRequest,
    LearningDecisionRequest,
    LearningFeedbackRequest,
    LearningReviseRequest,
)
from ravn.domain.valkyrie_history import canonical_environment_id
from ravn.odin.review import ReviewItem, ReviewKind, review_decided_event
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

Dashboard = dict[str, Any]
logger = logging.getLogger(__name__)
RAW_SIGNAL_TELEMETRY_LIMIT = 1_000
CONTROL_TELEMETRY_LIMIT = 2_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(entry) for entry in value if str(entry).strip()]
    if isinstance(value, str) and value.strip():
        return [entry.strip() for entry in value.split(",") if entry.strip()]
    return []


def _field(record: dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return default


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


def _environment_id(record: dict[str, Any]) -> str:
    explicit = str(_field(record, "environmentId", "environment_id", default="")).strip()
    if explicit:
        return explicit
    raw_id = _slug(str(_field(record, "id", "name", default="environment")))
    kind = str(_field(record, "kind", default="generic"))
    if raw_id.startswith("env-"):
        return raw_id
    if kind == "kubernetes":
        return f"env-k8s-{raw_id}"
    return f"env-{raw_id}"


def _canonical_environment_id(value: Any) -> str:
    # One canonicalization policy, defined beside the history records it keys.
    return canonical_environment_id(value)


def _valkyrie_id(record: dict[str, Any], environment_id: str) -> str:
    explicit = str(_field(record, "valkyrieId", "valkyrie_id", default="")).strip()
    if explicit:
        return explicit
    return f"valkyrie-{environment_id.removeprefix('env-')}"


def _rollup_health(health_values: list[str]) -> str:
    order = ["critical", "degraded", "watch", "healthy"]
    for health in order:
        if health in health_values:
            return health
    return "healthy"


def _first_transport_value(
    transports: list[dict[str, Any]],
    key: str,
    default: str,
) -> str:
    for transport in transports:
        value = str(transport.get(key) or "").strip()
        if value:
            return value
    return default


def _live_report(
    last_observed_at: str,
    poll_count: int = 0,
    environments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    del poll_count
    transports = []
    for environment in environments or []:
        transport = environment.get("transport")
        if not isinstance(transport, dict):
            continue
        transports.append(
            {
                "id": str(transport.get("id") or f"transport-{environment['id']}"),
                "label": str(transport.get("label") or environment["name"]),
                "environmentId": environment["id"],
                "account": str(transport.get("account") or ""),
                "streamName": str(transport.get("streamName") or ""),
                "subjectPrefix": str(transport.get("subjectPrefix") or ""),
                "messageCount": _as_int(transport.get("messageCount"), 0),
                "signalCount": _as_int(transport.get("signalCount"), 0),
                "activityCount": _as_int(transport.get("activityCount"), 0),
                "judgmentCount": _as_int(transport.get("judgmentCount"), 0),
                "actionCount": _as_int(transport.get("actionCount"), 0),
                "rejectedCount": _as_int(transport.get("rejectedCount"), 0),
                "consumerFilterSubjects": _as_string_list(
                    transport.get("consumerFilterSubjects")
                    or transport.get("consumer_filter_subjects")
                ),
                "health": str(transport.get("health") or environment.get("health") or "watch"),
                "lastMessageAt": str(
                    transport.get("lastMessageAt")
                    or transport.get("last_message_at")
                    or environment.get("lastSignalAt")
                    or ""
                ),
                "notes": _as_string_list(transport.get("notes")),
            },
        )
    return {
        "title": "K8s flock routing",
        "status": _rollup_health([entry["health"] for entry in transports]),
        "lastObservedAt": last_observed_at,
        "totalMessages": sum(entry["messageCount"] for entry in transports),
        "sharedStream": _first_transport_value(transports, "streamName", "unconfigured"),
        "routeSubject": _first_transport_value(transports, "subjectPrefix", "unconfigured"),
        "projectionMode": "mixed" if transports else "local",
        "transports": transports,
        "findings": [
            (
                "Dashboard inventory is deployment-configured; activity appears only "
                "after verified telemetry is observed."
            ),
        ],
    }


def _empty_telemetry(last_observed_at: str) -> dict[str, Any]:
    return {
        "source": "demo_projection",
        "verified": False,
        "lastObservedAt": last_observed_at,
        "totals": {
            "eventsObserved": 0,
            "rawSignalEvents": 0,
            "logEvents": 0,
            "pollsCompleted": 0,
            "pollFailures": 0,
            "signalsCollected": 0,
            "signalsPublished": 0,
            "duplicateSignals": 0,
            "tasksEnqueued": 0,
            "tasksStarted": 0,
            "tasksCompleted": 0,
            "tasksFailed": 0,
            "tasksDropped": 0,
            "judgments": 0,
            "actions": 0,
            "learningEvents": 0,
            "dreamCyclesStarted": 0,
            "dreamCyclesCompleted": 0,
            "dreamCyclesFailed": 0,
            "dreamCyclesNoop": 0,
            "flockMessages": 0,
            "llmCalls": 0,
            "llmTokens": 0,
            "budgetDrops": 0,
            "wakefulnessChanges": 0,
            "toolRequests": 0,
            "skillProposals": 0,
        },
        "byEnvironment": [],
        "recentPolls": [],
        "recentTasks": [],
        "recentOutcomes": [],
        "recentEvents": [],
        "recentLogs": [],
        "recentLearning": [],
        "recentToolNeeds": [],
        "runtime": [],
        "llm": {
            "status": "unknown",
            "model": "",
            "reflectionModel": "",
            "postSessionReflectionEnabled": False,
            "lastObservedAt": "",
        },
        "gaps": [
            "No verified Sleipnir telemetry events have reached this API process yet.",
            "Seeded signals, judgments, actions, huddles, and learnings are demo projection data.",
            (
                "Deploy runtime telemetry and wire the API/dashboard consumer before treating "
                "counts as live."
            ),
        ],
    }


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


def _learning_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
    *,
    status: str = "",
) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    details = fields or payload
    gap = details.get("gap") if isinstance(details.get("gap"), dict) else {}
    evidence = gap.get("evidence") if isinstance(gap.get("evidence"), dict) else {}
    source_signal_ids = _as_string_list(
        gap.get("signal_ids") or payload.get("signal_ids") or payload.get("signal_id") or ""
    )
    if not source_signal_ids and payload.get("signal_id"):
        source_signal_ids = [str(payload.get("signal_id"))]
    artifact_content = str(
        details.get("skill_content")
        or payload.get("skill_content")
        or payload.get("artifact_content")
        or payload.get("content")
        or ""
    )
    review_payload = {}
    if event_type == "odin.court.decided":
        dissent = payload.get("dissent") if isinstance(payload.get("dissent"), list) else []
        review_detail = next(
            (item for item in dissent if isinstance(item, dict) and item.get("reviewer")),
            {},
        )
        review_payload = {
            "outcome": str(payload.get("outcome") or review_detail.get("outcome") or ""),
            "approved": bool(payload.get("approved") or review_detail.get("approved")),
            "rationale": str(payload.get("rationale") or review_detail.get("rationale") or ""),
            "reviewer": str(payload.get("reviewer") or review_detail.get("reviewer") or ""),
            "findings": _as_string_list(
                payload.get("findings") or review_detail.get("findings") or []
            ),
            "requiredForActivation": bool(payload.get("required_for_activation")),
        }
    return {
        "id": str(
            payload.get("proposal_id")
            or payload.get("learning_id")
            or payload.get("dream_id")
            or payload.get("artifact_name")
            or payload.get("skill_name")
            or details.get("skill_name")
            or details.get("request_id")
            or event.get("event_id")
            or f"{event_type}:{_event_timestamp(event)}"
        ),
        "eventType": event_type,
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload) or str(gap.get("source_valkyrie_id") or ""),
        "dreamId": str(payload.get("dream_id") or ""),
        "title": str(
            details.get("title")
            or payload.get("artifact_name")
            or payload.get("skill_name")
            or details.get("skill_name")
            or gap.get("capability_name")
            or details.get("artifact_type")
            or event.get("summary")
            or event_type
        ),
        "status": status or str(details.get("status") or event_type.rsplit(".", 1)[-1]),
        "artifactType": str(details.get("artifact_type") or payload.get("artifact_type") or ""),
        "riskClass": str(
            details.get("risk_class")
            or details.get("negative_transfer_risk")
            or payload.get("negative_transfer_risk")
            or "low"
        ),
        "policyDecision": str(details.get("policy_decision") or ""),
        "scope": str(
            details.get("target_scope")
            or payload.get("to_scope")
            or payload.get("scope")
            or "flock"
        ),
        "sourceEnvironmentId": str(
            gap.get("environment_id") or _event_environment_id(event, payload)
        ),
        "sourceValkyrieId": str(
            payload.get("source_valkyrie_id")
            or gap.get("source_valkyrie_id")
            or _event_valkyrie_id(payload)
            or "valkyrie:local"
        ),
        "targetFlockId": str(payload.get("flock_id") or details.get("flock_id") or "flock-local"),
        "confidence": _payload_float(details, "confidence")
        or _payload_float(payload, "confidence"),
        "evaluation": str(
            details.get("evaluation")
            or payload.get("rationale")
            or payload.get("summary")
            or evidence.get("summary")
            or event.get("summary")
            or ""
        ),
        "negativeTransferRisk": str(
            details.get("negative_transfer_risk") or payload.get("negative_transfer_risk") or "low"
        ),
        "redaction": str(
            details.get("redaction")
            or payload.get("redaction")
            or payload.get("redaction_status")
            or "none"
        ),
        "promotedTool": str(
            payload.get("artifact_name")
            or payload.get("skill_name")
            or details.get("skill_name")
            or details.get("artifact_name")
            or ""
        ),
        "proposalsCreated": _payload_int(payload, "proposals_created"),
        "proposalsApplied": _payload_int(payload, "proposals_applied"),
        "proposalsDeferred": _payload_int(payload, "proposals_deferred"),
        "observedAt": _event_timestamp(event),
        "summary": str(event.get("summary") or ""),
        "artifactContent": artifact_content,
        "artifactPath": str(details.get("artifact_path") or payload.get("artifact_path") or ""),
        "sourceSignalIds": source_signal_ids,
        "sourceEvidence": evidence,
        "dreamRationale": str(gap.get("reason") or payload.get("rationale") or ""),
        "odinReview": review_payload,
        "feedback": (
            dict(payload["feedback"]) if isinstance(payload.get("feedback"), dict) else None
        ),
        "repetition": _payload_int(payload, "repetition")
        or _payload_int(details, "repetition")
        or 1,
        "supersedes": str(details.get("supersedes") or payload.get("supersedes") or ""),
        "history": [
            {
                "eventType": event_type,
                "status": status or str(details.get("status") or event_type.rsplit(".", 1)[-1]),
                "summary": str(event.get("summary") or ""),
                "observedAt": _event_timestamp(event),
                "residentValkyrieId": str(payload.get("resident_valkyrie_id") or ""),
                "residentValkyrieName": str(payload.get("resident_valkyrie_name") or ""),
                "action": str(payload.get("action") or ""),
                "relevant": payload.get("relevant"),
                "installedSkillName": str(payload.get("installed_skill_name") or ""),
                "odinDecision": str(payload.get("decision") or ""),
            }
        ],
    }


def _learning_status_rank(status: str) -> int:
    return {
        "rolled_back": 7,
        "adopted": 6,
        "canary": 5,
        "candidate": 4,
        "rejected": 3,
        "completed": 2,
        "started": 1,
    }.get(status, 0)


def _merge_learning_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for entry in entries:
        learning_id = str(entry.get("id") or "")
        if not learning_id:
            continue
        existing = merged.get(learning_id)
        if existing is None:
            current = deepcopy(entry)
            history = current.get("history") if isinstance(current.get("history"), list) else []
            current["history"] = list(history)
            current["odinReviews"] = [current["odinReview"]] if current.get("odinReview") else []
            merged[learning_id] = current
            continue

        if _learning_status_rank(str(entry.get("status") or "")) > _learning_status_rank(
            str(existing.get("status") or "")
        ):
            existing["status"] = entry.get("status") or existing.get("status")

        if str(entry.get("observedAt") or "") > str(existing.get("observedAt") or ""):
            existing["observedAt"] = entry.get("observedAt")
            existing["eventType"] = entry.get("eventType") or existing.get("eventType")

        fill_keys = (
            "title",
            "artifactType",
            "riskClass",
            "policyDecision",
            "scope",
            "sourceEnvironmentId",
            "sourceValkyrieId",
            "targetFlockId",
            "evaluation",
            "negativeTransferRisk",
            "redaction",
            "promotedTool",
            "summary",
            "artifactContent",
            "artifactPath",
            "dreamRationale",
            "supersedes",
        )
        for key in fill_keys:
            if not existing.get(key) and entry.get(key):
                existing[key] = entry[key]

        if not existing.get("confidence") and entry.get("confidence"):
            existing["confidence"] = entry["confidence"]
        if not existing.get("sourceSignalIds") and entry.get("sourceSignalIds"):
            existing["sourceSignalIds"] = entry["sourceSignalIds"]
        if not existing.get("sourceEvidence") and entry.get("sourceEvidence"):
            existing["sourceEvidence"] = entry["sourceEvidence"]
        if not existing.get("feedback") and entry.get("feedback"):
            existing["feedback"] = entry["feedback"]
        existing["repetition"] = max(
            _as_int(existing.get("repetition"), 1),
            _as_int(entry.get("repetition"), 1),
        )

        review = entry.get("odinReview")
        if isinstance(review, dict) and review.get("reviewer"):
            existing["odinReview"] = review
            reviews = existing.setdefault("odinReviews", [])
            if review not in reviews:
                reviews.append(review)

        history = entry.get("history") if isinstance(entry.get("history"), list) else []
        existing.setdefault("history", []).extend(history)

    for entry in merged.values():
        entry["history"] = sorted(
            entry.get("history") or [],
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )
        resident_decisions = []
        for history in entry["history"]:
            if history.get("eventType") != registry.LEARNING_ADOPTION_RECORDED:
                continue
            resident_decisions.append(
                {
                    "residentValkyrieId": history.get("residentValkyrieId", ""),
                    "residentValkyrieName": history.get("residentValkyrieName", ""),
                    "action": history.get("action") or history.get("status") or "",
                    "relevant": history.get("relevant"),
                    "installedSkillName": history.get("installedSkillName", ""),
                    "observedAt": history.get("observedAt", ""),
                    "summary": history.get("summary", ""),
                }
            )
        entry["residentDecisions"] = resident_decisions
        entry["adoptedResidents"] = [
            decision
            for decision in resident_decisions
            if str(decision.get("action") or "").lower() in {"adopted", "adopt"}
        ]
        entry["rejectedResidents"] = [
            decision
            for decision in resident_decisions
            if str(decision.get("action") or "").lower() in {"rejected", "reject"}
        ]

    return list(merged.values())


def _tool_need_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
    *,
    capability: str,
    status: str,
) -> dict[str, Any]:
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    details = fields or outcome or payload
    return {
        "id": str(event.get("event_id") or f"{capability}:{_event_timestamp(event)}"),
        "eventType": str(event.get("event_type") or ""),
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload),
        "taskId": str(payload.get("task_id") or ""),
        "capability": capability,
        "status": status,
        "summary": str(details.get("summary") or event.get("summary") or capability),
        "observedAt": _event_timestamp(event),
    }


def _capability_gap_from_details(details: dict[str, Any], payload: dict[str, Any]) -> str:
    """Return the requested capability when a judgment/action exposes a gap.

    Resident Valkyries should not need a hard-coded catalog of every action they
    might one day perform. A judgment can surface the next missing capability as
    `action_capability` or, for older persona outputs, as a structured-looking
    `recommended_action`. The dashboard treats that as evolution pressure.
    """
    capability = str(
        details.get("action_capability") or payload.get("action_capability") or ""
    ).strip()
    if capability and capability.lower() not in {"none", "n/a", "no_action", "observe"}:
        return capability

    recommended = str(
        details.get("recommended_action") or payload.get("recommended_action") or ""
    ).strip()
    if not recommended or recommended.lower() in {"none", "n/a", "observe", "watch"}:
        return ""
    if "." in recommended or "_" in recommended:
        return recommended
    return ""


def _learning_status_for_event(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == registry.LEARNING_ADOPTION_RECORDED:
        action = str(payload.get("action") or "").lower()
        if action in {"adopted", "adopt"}:
            return "adopted"
        if action in {"canary", "canary_started"}:
            return "canary"
        if action in {"rejected", "reject"}:
            return "rejected"
        if action in {"overridden", "override"}:
            return "adopted"
        if action in {"regressed", "rollback", "rolled_back"}:
            return "rolled_back"
    if event_type == registry.LEARNING_PROMOTED:
        return str(payload.get("status") or "")
    if event_type == "valkyrie.evolution.built":
        return "candidate"
    if event_type == "valkyrie.evolution.activated":
        if payload.get("learning_id"):
            return "adopted"
        return "canary"
    if event_type == "valkyrie.evolution.proven":
        return "adopted"
    if event_type == "valkyrie.evolution.held":
        return "rejected"
    if event_type == "odin.court.decided":
        decision = str(payload.get("decision") or "").lower()
        if decision == "learning_adoption_blocked":
            return "rejected"
        if decision == "learning_adoption_allowed":
            return "canary"
        outcome = str(payload.get("outcome") or "").lower()
        if outcome == "rejected":
            return "rejected"
        if outcome == "approved":
            return "canary"
    if "rolled_back" in event_type:
        return "rolled_back"
    return ""


LEARNING_SCOPES = ("private", "environment", "domain", "flock", "shared")


def _next_learning_scope(scope: str) -> str:
    try:
        index = LEARNING_SCOPES.index(scope)
    except ValueError:
        return "environment"
    return LEARNING_SCOPES[min(index + 1, len(LEARNING_SCOPES) - 1)]


def _raw_learning_id(learning_id: str) -> str:
    return learning_id.removeprefix("live-")


def _previous_learning_scope(scope: str) -> str:
    try:
        index = LEARNING_SCOPES.index(scope)
    except ValueError:
        return "private"
    return LEARNING_SCOPES[max(index - 1, 0)]


def _available_learning_scopes(scope: str) -> list[str]:
    try:
        index = LEARNING_SCOPES.index(scope)
    except ValueError:
        index = 0
    return list(LEARNING_SCOPES[: index + 2])


def _learning_active_for_status(status: str) -> bool:
    return status in {"adopted", "canary"}


def _decision_summary(action: str, request: LearningDecisionRequest | None) -> str:
    reason = request.reason if request and request.reason else "no reason supplied"
    return f"{action.replace('_', ' ')} by {request.operatorId if request else 'system'}: {reason}"


def _decision_request_for_learning(
    learning_id: str,
    request: LearningFeedbackRequest | LearningReviseRequest,
) -> LearningDecisionRequest:
    """Adapt feedback/revise bodies to the shared learning-decision envelope."""
    return LearningDecisionRequest(
        learningId=learning_id,
        reason=request.reason,
        operatorId=request.operatorId,
        targetScope=getattr(request, "targetScope", ""),
    )


def _learning_feedback_action(
    verdict: str,
    status: str,
    *,
    current_scope: str = "",
    target_scope: str = "",
) -> str:
    """Map a feedback verdict onto the learning lifecycle action it triggers.

    ``feedback`` is the no-lifecycle action: the verdict is recorded and
    broadcast, and reinforcement happens resident-side. ``wrong_tier``
    defaults to promote so an unsupported target scope surfaces the
    promote path's 422.
    """
    if verdict == "dismissed":
        return "reject"
    if verdict == "bad_action":
        return "rollback" if status in {"adopted", "canary"} else "reject"
    if verdict != "wrong_tier":
        return "feedback"
    if target_scope not in LEARNING_SCOPES or current_scope not in LEARNING_SCOPES:
        return "promote"
    if LEARNING_SCOPES.index(target_scope) < LEARNING_SCOPES.index(current_scope):
        return "demote"
    return "promote"


def _learning_edits(request: LearningReviseRequest) -> dict[str, str]:
    """Map the non-empty revise fields onto learning record keys."""
    edits: dict[str, str] = {}
    if request.title.strip():
        edits["title"] = request.title
    if request.summary.strip():
        edits["summary"] = request.summary
    if request.content.strip():
        edits["artifactContent"] = request.content
    return edits


def _capability_from_signal_payload(event_type: str, payload: dict[str, Any]) -> str:
    namespace = event_type.removeprefix("signal.").removesuffix(".event")
    if not namespace:
        namespace = str(payload.get("namespace") or payload.get("source") or "generic")
    reason = str(payload.get("reason") or payload.get("kind") or "unknown")
    kind = str(payload.get("kind") or namespace)
    return f"inspect.{_slug(namespace)}.{_slug(kind)}.{_slug(reason)}"


def _learning_capability(learning: dict[str, Any]) -> str:
    artifact_content = str(learning.get("artifactContent") or "")
    match = re.search(r"^metadata:\n(?:.*\n)*?\s*capability:\s*([^\n]+)", artifact_content, re.M)
    if match:
        return match.group(1).strip().strip("`")
    for value in (
        str(learning.get("promotedTool") or ""),
        str(learning.get("title") or ""),
    ):
        if value.startswith("valkyrie-inspect-"):
            return "inspect." + value.removeprefix("valkyrie-inspect-").replace("-", ".")
        if value.startswith("inspect."):
            return value
    return ""


def _merge_learning_record(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    if existing is None:
        return incoming
    merged = {**existing, **incoming}
    for key in (
        "artifactContent",
        "artifactPath",
        "artifactType",
        "promotedTool",
        "dreamRationale",
        "evaluation",
        "summary",
        "supersedes",
    ):
        if not incoming.get(key) and existing.get(key):
            merged[key] = existing[key]
    for key in ("odinReview", "sourceEvidence", "feedback"):
        if not incoming.get(key) and existing.get(key):
            merged[key] = existing[key]
    merged["repetition"] = max(
        _as_int(existing.get("repetition"), 1),
        _as_int(incoming.get("repetition"), 1),
    )
    merged["sourceSignalIds"] = list(
        dict.fromkeys(
            [
                *_as_string_list(existing.get("sourceSignalIds") or []),
                *_as_string_list(incoming.get("sourceSignalIds") or []),
            ]
        )
    )
    merged["history"] = [
        *(existing.get("history") if isinstance(existing.get("history"), list) else []),
        *(incoming.get("history") if isinstance(incoming.get("history"), list) else []),
    ][-30:]
    merged["active"] = _learning_active_for_status(str(merged.get("status") or ""))
    merged["currentScope"] = str(merged.get("scope") or merged.get("currentScope") or "private")
    merged["targetScope"] = _next_learning_scope(merged["currentScope"])
    merged["availableScopes"] = _available_learning_scopes(merged["currentScope"])
    return merged


def _dashboard_learning_from_telemetry(entry: dict[str, Any]) -> dict[str, Any]:
    scope = str(entry.get("scope") or "flock")
    status = str(entry.get("status") or "candidate")
    return {
        "id": str(entry.get("id") or ""),
        "title": str(entry.get("title") or entry.get("promotedTool") or "Generated capability"),
        "summary": str(entry.get("summary") or entry.get("evaluation") or ""),
        "scope": scope,
        "status": status,
        "sourceEnvironmentId": str(
            entry.get("sourceEnvironmentId") or entry.get("environmentId") or ""
        ),
        "sourceValkyrieId": str(entry.get("sourceValkyrieId") or entry.get("valkyrieId") or ""),
        "targetFlockId": str(entry.get("targetFlockId") or ""),
        "confidence": _as_float(entry.get("confidence"), 0.0),
        "evaluation": str(entry.get("evaluation") or entry.get("summary") or ""),
        "negativeTransferRisk": str(entry.get("negativeTransferRisk") or "low"),
        "redaction": str(entry.get("redaction") or "none"),
        "promotedTool": str(entry.get("promotedTool") or ""),
        "createdAt": str(entry.get("observedAt") or _now()),
        "active": status in {"adopted", "canary"},
        "currentScope": scope,
        "targetScope": _next_learning_scope(scope),
        "availableScopes": _available_learning_scopes(scope),
        "artifactContent": str(entry.get("artifactContent") or ""),
        "artifactPath": str(entry.get("artifactPath") or ""),
        "artifactType": str(entry.get("artifactType") or ""),
        "sourceSignalIds": _as_string_list(entry.get("sourceSignalIds") or []),
        "sourceEvidence": (
            entry.get("sourceEvidence") if isinstance(entry.get("sourceEvidence"), dict) else {}
        ),
        "dreamRationale": str(entry.get("dreamRationale") or ""),
        "odinReview": entry.get("odinReview") if isinstance(entry.get("odinReview"), dict) else {},
        "history": entry.get("history") if isinstance(entry.get("history"), list) else [],
        "canaryEnvironmentId": "",
        "operatorDecisions": [],
        "commandDelivery": entry.get("commandDelivery")
        if isinstance(entry.get("commandDelivery"), dict)
        else {},
        "feedback": entry.get("feedback") if isinstance(entry.get("feedback"), dict) else None,
        "repetition": _as_int(entry.get("repetition"), 1) or 1,
        "supersedes": str(entry.get("supersedes") or ""),
    }


def _runtime_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    wakefulness = ""
    if event_type == "valkyrie.state.changed":
        wakefulness = str(payload.get("new_state") or "")
    elif event_type == "valkyrie.dream.started":
        wakefulness = "dreaming"
    return {
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload),
        "valkyrieName": _event_valkyrie_name(payload),
        "residentPersonality": str(payload.get("resident_personality") or ""),
        "charter": str(payload.get("charter") or ""),
        "signalTaskSeverities": _as_string_list(payload.get("signal_task_severities")),
        "sourceCount": payload.get("source_count", 0),
        "driveLoopEnabled": bool(payload.get("drive_loop_enabled")),
        "initiativeEnabled": bool(payload.get("initiative_enabled")),
        "pollIntervalSeconds": payload.get("poll_interval_seconds", 0),
        "wakefulness": wakefulness,
        "lastDreamAt": timestamp
        if event_type in {"valkyrie.dream.completed", "valkyrie.dream.noop"}
        else "",
        "observedAt": timestamp,
    }


def _merge_runtime_entry(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    if existing is None:
        return dict(incoming)
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "observedAt":
            merged[key] = max(str(merged.get(key) or ""), str(value or ""))
        elif value not in ("", 0, False, None, []):
            merged[key] = value
    return merged


def _telemetry_activity(
    telemetry: dict[str, Any],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    activity_by_env: dict[str, str] = {}
    activity_by_valkyrie: dict[str, dict[str, str]] = {}

    for entry in telemetry.get("byEnvironment", []):
        if not isinstance(entry, dict):
            continue
        env_id = str(entry.get("environmentId") or "")
        observed_at = str(entry.get("lastObservedAt") or "")
        if env_id and observed_at:
            activity_by_env[env_id] = max(activity_by_env.get(env_id, ""), observed_at)

    for entry in telemetry.get("recentEvents", []):
        if not isinstance(entry, dict):
            continue
        observed_at = str(entry.get("observedAt") or "")
        if not observed_at:
            continue
        env_id = str(entry.get("environmentId") or "")
        if env_id:
            activity_by_env[env_id] = max(activity_by_env.get(env_id, ""), observed_at)
        valkyrie_id = str(entry.get("valkyrieId") or "")
        if not valkyrie_id:
            continue
        existing = activity_by_valkyrie.get(valkyrie_id)
        if existing is None or observed_at > existing.get("observedAt", ""):
            activity_by_valkyrie[valkyrie_id] = {
                "observedAt": observed_at,
                "valkyrieName": str(entry.get("valkyrieName") or ""),
            }

    return activity_by_env, activity_by_valkyrie


def _runtime_event_key(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    env_id = str(payload.get("environment_id") or payload.get("environmentId") or "unknown")
    valkyrie_id = str(payload.get("valkyrie_id") or payload.get("valkyrieId") or "unknown")
    return f"{env_id}:{valkyrie_id}"


class ValkyrieRoomClient:
    """Call Skuld's real room endpoints for dashboard huddle operations."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def require_capability(self, participant_id: str, capability: str) -> None:
        """Raise 403 unless the joined participant holds *capability*."""
        await self._post(
            "/api/room/require-capability",
            {"participant_id": participant_id, "capability": capability},
        )

    async def join_huddle(
        self,
        huddle: dict[str, Any],
        request: HuddleJoinRequest,
    ) -> dict[str, Any]:
        environment_id = str(huddle.get("environmentId") or huddle.get("environment_id") or "")
        if not environment_id:
            raise HTTPException(status_code=422, detail="Huddle has no environment id")
        participant_id = request.participantId.strip()
        if not participant_id:
            raise HTTPException(status_code=422, detail="participantId is required")
        payload = {
            "participant_id": participant_id,
            "display_name": request.displayName or participant_id,
            "environment_id": environment_id,
            "role": _huddle_role_for_action(request.action),
            "room_id": str(huddle.get("id") or ""),
            "capabilities": request.capabilities,
            "surfaces": ["skuld.room", "ravn.valkyrie.dashboard"],
        }
        authorities = _as_string_list(
            huddle.get("environmentActionAuthorities")
            or huddle.get("environment_action_authorities")
            or []
        )
        if authorities:
            payload["environment_action_authorities"] = authorities
        return await self._post("/api/room/join", payload)

    async def leave_huddle(self, huddle: dict[str, Any]) -> dict[str, Any]:
        participant_id = str(huddle.get("joinedParticipantId") or "").strip()
        if not participant_id:
            raise HTTPException(status_code=422, detail="Huddle has no joined participant id")
        return await self._post(
            "/api/room/leave",
            {"participant_id": participant_id, "reason": f"left {huddle.get('id') or ''}"},
        )

    async def send_huddle_message(
        self,
        huddle: dict[str, Any],
        request: HuddleSendRequest,
    ) -> dict[str, Any]:
        participant_id, _ = _resolve_huddle_message_author(huddle, request)
        metadata = {
            "room_id": str(huddle.get("id") or request.huddleId),
            "huddle_id": request.huddleId,
            "environment_id": str(huddle.get("environmentId") or ""),
        }
        action = str(huddle.get("joinedAction") or "").strip()
        if action:
            metadata["action"] = action
        target_flock_id = str(
            huddle.get("targetFlockId") or huddle.get("target_flock_id") or ""
        ).strip()
        if target_flock_id:
            metadata["target_flock_id"] = target_flock_id
        directed_to = [target for target in request.directedTo if str(target).strip()]
        if directed_to:
            result = None
            for target in directed_to:
                result = await self._post(
                    "/api/room/direct",
                    {
                        "target_peer_id": target,
                        "content": request.body,
                        "source": "ravn.valkyrie.dashboard",
                        "participant_id": participant_id,
                        "metadata": metadata,
                    },
                )
            return result or {"status": "sent"}
        return await self._post(
            "/api/room/message",
            {
                "content": request.body,
                "source": "ravn.valkyrie.dashboard",
                "participant_id": participant_id,
                "metadata": metadata,
            },
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.post(path, json=payload)
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else {"status": "ok", "data": data}
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=exc.response.text[:500],
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Skuld room request failed: {exc}",
            ) from exc


def build_skuld_room_client_from_env() -> ValkyrieRoomClient | None:
    base_url = os.environ.get("RAVN_VALKYRIE_SKULD_ROOM_URL", "").strip()
    if not base_url:
        return None
    return ValkyrieRoomClient(
        base_url,
        timeout_seconds=_env_float(os.environ.get("RAVN_VALKYRIE_SKULD_ROOM_TIMEOUT_SECONDS"), 5.0),
    )


def _huddle_role_for_action(action: str) -> str:
    normalized = action.strip().lower().replace("-", "_")
    roles = {
        "observe": "observer",
        "reply": "observer",
        "message": "observer",
        "teach": "teacher",
        "correct": "teacher",
        "approve": "approver",
        "authorize": "approver",
        "debug": "debugger",
        "own": "owner",
        "change_autonomy": "owner",
    }
    if normalized not in roles:
        raise HTTPException(status_code=422, detail=f"Unsupported huddle action: {action}")
    return roles[normalized]


def _validate_huddle_join_scope(huddle: dict[str, Any], request: HuddleJoinRequest) -> None:
    if request.huddleId != str(huddle.get("id") or ""):
        raise HTTPException(status_code=422, detail="Huddle id mismatch")
    if not request.participantId.strip():
        raise HTTPException(status_code=422, detail="participantId is required")
    huddle_flock = str(huddle.get("targetFlockId") or huddle.get("target_flock_id") or "").strip()
    requested_flock = request.targetFlockId.strip()
    if huddle_flock and not requested_flock:
        raise HTTPException(status_code=422, detail="targetFlockId is required for flock huddles")
    if huddle_flock and requested_flock and huddle_flock != requested_flock:
        raise HTTPException(
            status_code=409,
            detail=f"Huddle belongs to flock {huddle_flock}, not {requested_flock}",
        )
    _huddle_role_for_action(request.action)


def _resolve_huddle_message_author(
    huddle: dict[str, Any],
    request: HuddleSendRequest,
) -> tuple[str, str]:
    if request.huddleId != str(huddle.get("id") or ""):
        raise HTTPException(status_code=422, detail="Huddle id mismatch")
    if not request.body.strip():
        raise HTTPException(status_code=422, detail="Message body is required")
    participant_id = str(huddle.get("joinedParticipantId") or "").strip()
    if not huddle.get("joined") or not participant_id:
        raise HTTPException(status_code=422, detail="Join huddle before sending messages")
    requested_author_id = request.authorId.strip()
    if not requested_author_id:
        raise HTTPException(status_code=422, detail="authorId is required")
    if requested_author_id and requested_author_id != participant_id:
        raise HTTPException(
            status_code=409,
            detail=f"Huddle is joined as {participant_id}, not {requested_author_id}",
        )
    display_name = str(huddle.get("joinedDisplayName") or participant_id).strip() or participant_id
    return participant_id, display_name


class OdinReviewCommandPublisher:
    """Publish operator review decisions onto the existing Sleipnir bus.

    Every operator intervention — learning verdicts, autonomy changes,
    inbox approvals — rides the same ``odin.review.decided`` envelope.
    """

    def __init__(
        self,
        publisher: SleipnirPublisher | None = None,
        *,
        source: str = "ravn:odin-review",
        start_timeout_seconds: float = 5.0,
    ) -> None:
        self._publisher = publisher
        self._source = source
        self._start_timeout_seconds = max(start_timeout_seconds, 1.0)

    async def start(self) -> None:
        if self._publisher is not None and hasattr(self._publisher, "start"):
            await asyncio.wait_for(
                self._publisher.start(),  # type: ignore[attr-defined]
                timeout=self._start_timeout_seconds,
            )

    async def stop(self) -> None:
        if self._publisher is not None and hasattr(self._publisher, "stop"):
            await self._publisher.stop()  # type: ignore[attr-defined]

    async def publish_review_decision(
        self,
        item: ReviewItem,
    ) -> tuple[dict[str, Any], SleipnirEvent | None]:
        return await self.publish_event(review_decided_event(item, source=self._source))

    async def publish_event(
        self,
        event: SleipnirEvent,
    ) -> tuple[dict[str, Any], SleipnirEvent | None]:
        if self._publisher is None:
            return (
                {
                    "published": False,
                    "eventType": event.event_type,
                    "eventId": event.event_id,
                    "message": (
                        "No Sleipnir publisher configured; recorded in dashboard projection only."
                    ),
                    "observedAt": _now(),
                },
                None,
            )
        if hasattr(self._publisher, "publish_with_results"):
            targets = await self._publisher.publish_with_results(event)  # type: ignore[attr-defined]
        else:
            await self._publisher.publish(event)
            targets = [
                {
                    "label": "default",
                    "published": True,
                    "message": "published",
                }
            ]
        failed_targets = [target for target in targets if not bool(target.get("published"))]
        published_count = len(targets) - len(failed_targets)
        return (
            {
                "published": bool(targets) and not failed_targets,
                "eventType": event.event_type,
                "eventId": event.event_id,
                "message": (
                    "Published to all configured resident Valkyrie command targets."
                    if targets and not failed_targets
                    else (
                        f"Published to {published_count}/{len(targets)} configured "
                        "resident Valkyrie command targets."
                    )
                ),
                "targetCount": len(targets),
                "publishedTargets": published_count,
                "failedTargets": len(failed_targets),
                "targets": targets,
                "observedAt": _now(),
            },
            event,
        )


def _review_item_for_learning_action(
    action: str,
    before: dict[str, Any],
    learning: dict[str, Any],
    request: LearningDecisionRequest,
    *,
    operator_id: str,
    feedback: dict[str, Any] | None = None,
    revision: dict[str, Any] | None = None,
) -> ReviewItem:
    """Project a dashboard learning action onto the unified review envelope.

    The action vocabulary maps to one of two kinds: scope changes are
    ``skill_promotion`` items applied by the learning's source resident;
    everything else is a ``flock_learning`` item broadcast to the flock and
    relevance-filtered by each resident. Operator feedback and revision
    payloads travel in the evidence so residents can record them locally.
    """
    raw_learning_id = _raw_learning_id(str(learning.get("id") or request.learningId))
    source_environment_id = str(
        learning.get("sourceEnvironmentId") or before.get("sourceEnvironmentId") or ""
    )
    title = str(learning.get("promotedTool") or learning.get("title") or raw_learning_id)
    summary = request.reason or str(learning.get("summary") or title)
    correlation_id = f"valkyrie-learning:{raw_learning_id}:{action}"

    if action in {"promote", "demote"}:
        from_scope = str(before.get("scope") or before.get("currentScope") or "private")
        to_scope = str(
            request.targetScope
            or learning.get("scope")
            or learning.get("currentScope")
            or "private"
        )
        item = ReviewItem.new(
            kind=ReviewKind.SKILL_PROMOTION.value,
            requested_action=action,
            environment_id=source_environment_id or "unknown",
            valkyrie_id=str(learning.get("sourceValkyrieId") or ""),
            title=title,
            summary=summary,
            audience="environment" if not learning.get("sourceValkyrieId") else "valkyrie",
            domain=str(learning.get("domain") or learning.get("domainScope") or ""),
            evidence={
                "skill_name": title,
                "learning_id": raw_learning_id,
                "from_scope": from_scope,
                "to_scope": to_scope,
                "confidence": _as_float(learning.get("confidence"), 0.0),
                **({"feedback": feedback} if feedback else {}),
            },
            requested_by=operator_id,
            correlation_id=correlation_id,
        )
        item.decide(decision="approved", operator_id=operator_id, reason=request.reason)
        return item

    requested_action = {
        "adopt": "adopt",
        "override": "adopt",
        "reject": "adopt",
        "canary": "canary",
        "rollback": "retract",
    }.get(action, action)
    canary_environment = str(request.canaryEnvironmentId or "")
    audience = "environment" if (action == "canary" and canary_environment) else "flock"
    item = ReviewItem.new(
        kind=ReviewKind.FLOCK_LEARNING.value,
        requested_action=requested_action,
        environment_id=(
            canary_environment if audience == "environment" else source_environment_id or "unknown"
        ),
        valkyrie_id="",
        title=title,
        summary=summary,
        audience=audience,
        flock_id=str(learning.get("targetFlockId") or ""),
        domain=str(learning.get("domain") or learning.get("domainScope") or ""),
        evidence={
            "artifact": {
                "learning_id": raw_learning_id,
                "title": title,
                "summary": str(learning.get("summary") or title),
                "content": str(learning.get("artifactContent") or ""),
                "artifact_type": str(learning.get("artifactType") or "ravn_skill_tool"),
                "scope": str(request.targetScope or learning.get("scope") or "flock"),
                "confidence": _as_float(learning.get("confidence"), 0.0),
                "source_environment_id": source_environment_id,
                "source_valkyrie_id": str(learning.get("sourceValkyrieId") or ""),
                "promotion_id": raw_learning_id,
                "flock_id": str(learning.get("targetFlockId") or ""),
                "domain": str(learning.get("domain") or learning.get("domainScope") or ""),
                "redaction_status": str(learning.get("redaction") or "redacted"),
                "artifact_path": str(learning.get("artifactPath") or ""),
                "supersedes": _raw_learning_id(str(learning.get("supersedes") or "")),
            },
            "ui_learning_id": str(learning.get("id") or request.learningId),
            "status_before": str(before.get("status") or ""),
            **({"feedback": feedback} if feedback else {}),
            **({"revision": revision} if revision else {}),
        },
        requested_by=operator_id,
        correlation_id=correlation_id,
    )
    item.decide(
        decision="rejected" if action == "reject" else "approved",
        operator_id=operator_id,
        reason=request.reason,
    )
    return item


@dataclass(frozen=True)
class _CommandTarget:
    label: str
    publisher: SleipnirPublisher


class _FanoutSleipnirPublisher:
    """Publish one command event to every configured resident command target."""

    def __init__(self, targets: list[_CommandTarget]) -> None:
        self._targets = targets

    async def start(self) -> None:
        await asyncio.gather(
            *(
                target.publisher.start()  # type: ignore[attr-defined]
                for target in self._targets
                if hasattr(target.publisher, "start")
            )
        )

    async def stop(self) -> None:
        await asyncio.gather(
            *(
                target.publisher.stop()  # type: ignore[attr-defined]
                for target in self._targets
                if hasattr(target.publisher, "stop")
            ),
            return_exceptions=True,
        )

    async def publish(self, event: SleipnirEvent) -> None:
        results = await self.publish_with_results(event)
        failures = [result for result in results if not bool(result["published"])]
        if failures:
            labels = ", ".join(str(result["label"]) for result in failures)
            raise RuntimeError(f"failed to publish to command targets: {labels}")

    async def publish_with_results(self, event: SleipnirEvent) -> list[dict[str, Any]]:
        results = await asyncio.gather(
            *(self._publish_one(target, event) for target in self._targets),
        )
        return list(results)

    async def _publish_one(
        self,
        target: _CommandTarget,
        event: SleipnirEvent,
    ) -> dict[str, Any]:
        try:
            await target.publisher.publish(event)
        except Exception as exc:  # noqa: BLE001
            return {
                "label": target.label,
                "published": False,
                "message": str(exc),
            }
        return {
            "label": target.label,
            "published": True,
            "message": "published",
        }


def _merge_observed_runtime(dashboard: Dashboard) -> Dashboard:
    telemetry = dashboard.get("telemetry") if isinstance(dashboard.get("telemetry"), dict) else {}
    runtime = telemetry.get("runtime") if isinstance(telemetry.get("runtime"), list) else []
    activity_by_env, activity_by_valkyrie = _telemetry_activity(telemetry)
    observed_by_id = {
        str(entry.get("valkyrieId") or ""): entry
        for entry in runtime
        if isinstance(entry, dict) and str(entry.get("valkyrieId") or "")
    }
    observed_by_env: dict[str, dict[str, Any]] = {}
    for entry in runtime:
        if not isinstance(entry, dict):
            continue
        env_id = _canonical_environment_id(entry.get("environmentId"))
        if not env_id:
            continue
        observed_by_env[env_id] = entry
    for environment in dashboard.get("environments", []):
        if not isinstance(environment, dict):
            continue
        environment_id = str(environment.get("id") or "")
        observed = observed_by_env.get(environment_id)
        observed_at = str(observed.get("observedAt") or "") if observed else ""
        activity_at = activity_by_env.get(environment_id, "")
        last_seen_at = max(observed_at, activity_at)
        environment["identitySource"] = "observed" if last_seen_at else "configured"
        if last_seen_at:
            environment["lastSignalAt"] = max(
                str(environment.get("lastSignalAt") or ""),
                last_seen_at,
            )
            environment["wakefulCount"] = max(_as_int(environment.get("wakefulCount"), 0), 1)

    known_valkyrie_ids = set()
    for valkyrie in dashboard.get("valkyries", []):
        if not isinstance(valkyrie, dict):
            continue
        valkyrie_id = str(valkyrie.get("id") or "")
        known_valkyrie_ids.add(valkyrie_id)
        observed = observed_by_id.get(valkyrie_id)
        activity = activity_by_valkyrie.get(valkyrie_id)
        if observed is None and activity is None:
            valkyrie["identitySource"] = "configured"
            continue
        if observed and observed.get("valkyrieName"):
            valkyrie["name"] = str(observed["valkyrieName"])
        elif activity and activity.get("valkyrieName"):
            valkyrie["name"] = str(activity["valkyrieName"])
        if observed and observed.get("residentPersonality"):
            valkyrie["specialty"] = str(observed["residentPersonality"])
        if observed and observed.get("charter"):
            valkyrie["charter"] = str(observed["charter"])
        if observed and observed.get("signalTaskSeverities"):
            valkyrie["signalTaskSeverities"] = _as_string_list(observed["signalTaskSeverities"])
        valkyrie["identitySource"] = "observed"
        valkyrie["status"] = "online"
        observed_at = str(observed.get("observedAt") or "") if observed else ""
        activity_at = str(activity.get("observedAt") or "") if activity else ""
        valkyrie["lastObservedAt"] = max(
            str(valkyrie.get("lastObservedAt") or ""),
            observed_at,
            activity_at,
        )
        if observed and observed.get("wakefulness"):
            valkyrie["wakefulness"] = str(observed["wakefulness"])
        if observed and observed.get("lastDreamAt"):
            valkyrie["lastDreamAt"] = str(observed["lastDreamAt"])

    for valkyrie_id, observed in observed_by_id.items():
        if valkyrie_id in known_valkyrie_ids:
            continue
        dashboard.setdefault("valkyries", []).append(
            {
                "id": valkyrie_id,
                "name": str(observed.get("valkyrieName") or valkyrie_id),
                "environmentId": str(observed.get("environmentId") or "unknown"),
                "flockId": "",
                "persona": "observed-valkyrie",
                "specialty": str(observed.get("residentPersonality") or "observed resident"),
                "charter": str(observed.get("charter") or ""),
                "signalTaskSeverities": _as_string_list(observed.get("signalTaskSeverities")),
                "wakefulness": str(observed.get("wakefulness") or "watching"),
                "autonomyMode": "autonomous" if observed.get("driveLoopEnabled") else "guarded",
                "status": "online",
                "confidence": 0.0,
                "inboxSubjects": [],
                "toolCount": 0,
                "lastDreamAt": str(observed.get("lastDreamAt") or ""),
                "lastActionAt": "",
                "identitySource": "observed",
                "lastObservedAt": str(observed.get("observedAt") or ""),
            }
        )
    return dashboard


def _environment_telemetry_entry(entries: dict[str, dict[str, Any]], env_id: str) -> dict[str, Any]:
    entry = entries.get(env_id)
    if entry is None:
        entry = {
            "environmentId": env_id,
            "lastObservedAt": "",
            "pollsCompleted": 0,
            "pollFailures": 0,
            "signalsCollected": 0,
            "signalsPublished": 0,
            "duplicateSignals": 0,
            "tasksEnqueued": 0,
            "tasksStarted": 0,
            "tasksCompleted": 0,
            "tasksFailed": 0,
            "tasksDropped": 0,
            "judgments": 0,
            "actions": 0,
            "learningEvents": 0,
            "dreamCycles": 0,
        }
        entries[env_id] = entry
    return entry


def _aggregate_telemetry(
    events: list[dict[str, Any]],
    *,
    observed_at: str,
) -> dict[str, Any]:
    if not events:
        return _empty_telemetry(observed_at)

    totals = _empty_telemetry(observed_at)["totals"]
    by_environment: dict[str, dict[str, Any]] = {}
    recent_polls: list[dict[str, Any]] = []
    recent_tasks: list[dict[str, Any]] = []
    recent_outcomes: list[dict[str, Any]] = []
    recent_events: list[dict[str, Any]] = []
    recent_logs: list[dict[str, Any]] = []
    recent_learning: list[dict[str, Any]] = []
    recent_tool_needs: list[dict[str, Any]] = []
    seen_tool_needs: set[tuple[str, str, str]] = set()
    runtime_by_key: dict[str, dict[str, Any]] = {}
    llm = {
        "status": "unknown",
        "model": "",
        "reflectionModel": "",
        "postSessionReflectionEnabled": False,
        "lastObservedAt": "",
    }

    for raw_event in events:
        event = _event_dict(raw_event)
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        timestamp = _event_timestamp(event)
        env_id = _event_environment_id(event, payload)
        entry = _environment_telemetry_entry(by_environment, env_id)
        entry["lastObservedAt"] = max(entry["lastObservedAt"], timestamp)
        totals["eventsObserved"] += 1
        # Routine cadence chatter has its own projections (recentPolls, the
        # runtime presence merge). Letting it into the activity feed evicts
        # the judgments and actions the timeline exists to show.
        if event_type not in {
            "valkyrie.signal_poll.completed",
            "valkyrie.presence.heartbeat",
        }:
            recent_events.append(_event_log_entry(event, payload))

        def append_tool_need(*, capability: str, status: str) -> None:
            task_id = str(payload.get("task_id") or event.get("correlation_id") or "")
            need_key = (env_id, task_id, capability)
            if need_key in seen_tool_needs:
                return
            seen_tool_needs.add(need_key)
            totals["toolRequests"] += 1
            recent_tool_needs.append(
                _tool_need_entry(
                    event,
                    payload,
                    capability=capability,
                    status=status,
                )
            )

        if event_type.startswith("ravn.log.") or event_type.startswith("valkyrie.log."):
            totals["logEvents"] += 1
            recent_logs.append(_structured_log_entry(event, payload))
        elif event_type.startswith("ravn.llm.") or event_type.startswith("llm."):
            totals["llmCalls"] += 1
            totals["llmTokens"] += _payload_int(payload, "total_tokens")
            if not totals["llmTokens"]:
                totals["llmTokens"] += _payload_int(payload, "tokens")
        elif event_type == "valkyrie.signal_poll.completed":
            collected = _payload_int(payload, "collected_count")
            published = _payload_int(payload, "published_count")
            duplicates = _payload_int(payload, "duplicate_count")
            enqueued = _payload_int(payload, "enqueued_task_count")
            # Polls are excluded from the activity feed, but they still prove
            # the resident is alive — merge identity into the presence map so
            # a poll-only resident never shows as configured/offline.
            poll_valkyrie_id = _event_valkyrie_id(payload)
            if poll_valkyrie_id:
                poll_runtime_key = f"{env_id}:{poll_valkyrie_id}"
                runtime_by_key[poll_runtime_key] = _merge_runtime_entry(
                    runtime_by_key.get(poll_runtime_key),
                    {
                        "environmentId": env_id,
                        "valkyrieId": poll_valkyrie_id,
                        "valkyrieName": _event_valkyrie_name(payload),
                        "observedAt": timestamp,
                    },
                )
            totals["pollsCompleted"] += 1
            totals["signalsCollected"] += collected
            totals["signalsPublished"] += published
            totals["duplicateSignals"] += duplicates
            totals["tasksEnqueued"] += enqueued
            entry["pollsCompleted"] += 1
            entry["signalsCollected"] += collected
            entry["signalsPublished"] += published
            entry["duplicateSignals"] += duplicates
            entry["tasksEnqueued"] += enqueued
            recent_polls.append(
                {
                    "environmentId": env_id,
                    "sourceId": payload.get("source_id", ""),
                    "status": "completed",
                    "collected": collected,
                    "published": published,
                    "duplicates": duplicates,
                    "tasksEnqueued": enqueued,
                    "durationMs": _payload_int(payload, "duration_ms"),
                    "observedAt": timestamp,
                }
            )
        elif event_type == "valkyrie.signal_poll.failed":
            totals["pollFailures"] += 1
            entry["pollFailures"] += 1
            recent_polls.append(
                {
                    "environmentId": env_id,
                    "sourceId": payload.get("source_id", ""),
                    "status": "failed",
                    "error": payload.get("error", ""),
                    "observedAt": timestamp,
                }
            )
        elif _is_runtime_event(event):
            runtime_entry = _runtime_entry(event, payload, timestamp)
            runtime_key = (
                f"{runtime_entry['environmentId']}:{runtime_entry['valkyrieId'] or 'unknown'}"
            )
            runtime_by_key[runtime_key] = _merge_runtime_entry(
                runtime_by_key.get(runtime_key),
                runtime_entry,
            )
            if (
                event_type == "valkyrie.runtime.started"
                or payload.get("llm_model")
                or payload.get("reflection_model")
            ):
                llm = {
                    "status": "configured",
                    "model": str(payload.get("llm_model") or llm.get("model") or ""),
                    "reflectionModel": str(
                        payload.get("reflection_model") or llm.get("reflectionModel") or ""
                    ),
                    "postSessionReflectionEnabled": bool(
                        payload.get(
                            "post_session_reflection_enabled",
                            llm.get("postSessionReflectionEnabled"),
                        )
                    ),
                    "lastObservedAt": timestamp,
                }
            if event_type == "valkyrie.state.changed":
                totals["wakefulnessChanges"] += 1
                recent_learning.append(_learning_entry(event, payload, status="wakefulness"))
            elif event_type.startswith("valkyrie.dream."):
                totals["learningEvents"] += 1
                entry["learningEvents"] += 1
                recent_learning.append(
                    _learning_entry(event, payload, status=event_type.rsplit(".", 1)[-1])
                )
                if event_type == "valkyrie.dream.started":
                    totals["dreamCyclesStarted"] += 1
                    entry["dreamCycles"] += 1
                elif event_type == "valkyrie.dream.completed":
                    totals["dreamCyclesCompleted"] += 1
                elif event_type == "valkyrie.dream.noop":
                    totals["dreamCyclesNoop"] += 1
                    totals["dreamCyclesCompleted"] += 1
                elif event_type == "valkyrie.dream.failed":
                    totals["dreamCyclesFailed"] += 1
        elif event_type.startswith("signal."):
            totals["rawSignalEvents"] += 1
        elif event_type == "ravn.task.started":
            totals["tasksStarted"] += 1
            entry["tasksStarted"] += 1
            recent_tasks.append(
                {
                    "environmentId": env_id,
                    "taskId": payload.get("task_id", ""),
                    "title": payload.get("title", ""),
                    "status": "started",
                    "triggeredBy": payload.get("triggered_by", ""),
                    "persona": payload.get("persona", ""),
                    "observedAt": timestamp,
                }
            )
        elif event_type == "ravn.task.completed":
            outcome = str(payload.get("outcome") or "")
            totals["tasksCompleted"] += 1
            entry["tasksCompleted"] += 1
            if outcome not in {"success", "completed", "complete"}:
                totals["tasksFailed"] += 1
                entry["tasksFailed"] += 1
            recent_tasks.append(
                {
                    "environmentId": env_id,
                    "taskId": payload.get("task_id", ""),
                    "title": payload.get("title", ""),
                    "status": (
                        "completed" if outcome in {"success", "completed", "complete"} else "failed"
                    ),
                    "outcome": outcome,
                    "triggeredBy": payload.get("triggered_by", ""),
                    "persona": payload.get("persona", ""),
                    "observedAt": timestamp,
                }
            )
        elif event_type == "ravn.task.dropped":
            totals["tasksDropped"] += 1
            entry["tasksDropped"] += 1
            reason = str(payload.get("reason") or "")
            if "budget" in reason.lower() or "cap" in reason.lower():
                totals["budgetDrops"] += 1
            recent_tasks.append(
                {
                    "environmentId": env_id,
                    "taskId": payload.get("task_id", ""),
                    "title": payload.get("title", ""),
                    "status": "dropped",
                    "reason": reason,
                    "triggeredBy": payload.get("triggered_by", ""),
                    "persona": payload.get("persona", ""),
                    "observedAt": timestamp,
                }
            )
        elif event_type.startswith("valkyrie.judgment."):
            totals["judgments"] += 1
            entry["judgments"] += 1
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
            details = fields or outcome or payload
            capability = _capability_gap_from_details(details, payload)
            if capability:
                append_tool_need(
                    capability=capability,
                    status=str(details.get("decision") or details.get("verdict") or "needed"),
                )
            recent_outcomes.append(
                {
                    "environmentId": env_id,
                    "type": "judgment",
                    "eventType": event_type,
                    "taskId": payload.get("task_id", ""),
                    "valkyrieId": payload.get("valkyrie_id", ""),
                    "verdict": details.get("verdict", payload.get("verdict", "")),
                    "tier": details.get("tier", payload.get("tier", "")),
                    "confidence": details.get("confidence", payload.get("confidence", 0)),
                    "recommendedAction": details.get(
                        "recommended_action",
                        payload.get("recommended_action", ""),
                    ),
                    "summary": details.get(
                        "summary",
                        payload.get("summary", event.get("summary", "")),
                    ),
                    "valid": payload.get("valid", True),
                    "observedAt": timestamp,
                }
            )
        elif event_type.startswith("valkyrie.action."):
            totals["actions"] += 1
            entry["actions"] += 1
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
            details = fields or outcome or payload
            capability = _capability_gap_from_details(details, payload)
            if capability:
                append_tool_need(capability=capability, status=event_type.rsplit(".", 1)[-1])
            recent_outcomes.append(
                {
                    "environmentId": env_id,
                    "type": "action",
                    "eventType": event_type,
                    "taskId": payload.get("task_id", ""),
                    "valkyrieId": payload.get("valkyrie_id", ""),
                    "verdict": details.get("verdict", payload.get("verdict", "")),
                    "tier": details.get("tier", payload.get("tier", "")),
                    "confidence": details.get("confidence", payload.get("confidence", 0)),
                    "recommendedAction": details.get(
                        "recommended_action",
                        details.get("action_capability", payload.get("recommended_action", "")),
                    ),
                    "summary": details.get(
                        "summary",
                        payload.get("summary", event.get("summary", "")),
                    ),
                    "valid": payload.get("valid", True),
                    "observedAt": timestamp,
                }
            )
        elif event_type.startswith("learning.") or event_type.startswith("flock.learning."):
            totals["learningEvents"] += 1
            entry["learningEvents"] += 1
            recent_learning.append(
                _learning_entry(
                    event,
                    payload,
                    status=_learning_status_for_event(event_type, payload),
                )
            )
            if event_type == "learning.dream.started":
                totals["dreamCyclesStarted"] += 1
                entry["dreamCycles"] += 1
            elif event_type == "learning.dream.completed":
                totals["dreamCyclesCompleted"] += 1
            elif event_type == "learning.dream.noop":
                totals["dreamCyclesNoop"] += 1
                totals["dreamCyclesCompleted"] += 1
            elif event_type == "learning.dream.failed":
                totals["dreamCyclesFailed"] += 1
        elif event_type == "valkyrie.capability_gap.detected":
            capability = str(payload.get("capability_name") or event.get("summary") or "unknown")
            append_tool_need(capability=capability, status="needed")
            recent_learning.append(_learning_entry(event, payload, status="candidate"))
        elif event_type.startswith("valkyrie.evolution."):
            status = _learning_status_for_event(event_type, payload)
            totals["skillProposals"] += 1
            totals["learningEvents"] += 1
            entry["learningEvents"] += 1
            recent_learning.append(_learning_entry(event, payload, status=status))
            capability = str(
                payload.get("capability_name")
                or payload.get("skill_name")
                or payload.get("artifact_name")
                or event.get("summary")
                or event_type
            )
            recent_tool_needs.append(
                _tool_need_entry(
                    event,
                    payload,
                    capability=capability,
                    status=status or event_type.rsplit(".", 1)[-1],
                )
            )
        elif event_type == "odin.court.decided":
            totals["learningEvents"] += 1
            entry["learningEvents"] += 1
            recent_learning.append(
                _learning_entry(
                    event,
                    payload,
                    status=_learning_status_for_event(event_type, payload),
                )
            )
        elif (
            event_type.startswith("self_improvement.")
            or event_type.startswith("skill.")
            or event_type.startswith("tool.")
            or event_type == "skill_manage"
        ):
            totals["skillProposals"] += 1
            recent_learning.append(_learning_entry(event, payload))
            capability = str(
                payload.get("capability")
                or payload.get("tool")
                or payload.get("artifact_type")
                or event_type
            )
            totals["toolRequests"] += 1
            recent_tool_needs.append(
                _tool_need_entry(
                    event,
                    payload,
                    capability=capability,
                    status=event_type.rsplit(".", 1)[-1],
                )
            )
        elif event_type.startswith("flock."):
            totals["flockMessages"] += 1

    gaps: list[str] = []
    if totals["judgments"] == 0:
        if totals["tasksCompleted"] > 0:
            gaps.append("Tasks completed but no verified valkyrie.judgment.* events observed.")
        else:
            gaps.append("No verified valkyrie.judgment.* events observed.")
    if totals["actions"] == 0:
        gaps.append("No verified valkyrie.action.* events observed.")
    if totals["learningEvents"] == 0:
        gaps.append("No verified learning or flock.learning events observed.")
    runtime = sorted(
        runtime_by_key.values(),
        key=lambda item: item.get("observedAt", ""),
        reverse=True,
    )
    if totals["dreamCyclesStarted"] == 0 and totals["dreamCyclesNoop"] == 0:
        gaps.append("No verified dream-cycle events observed.")
    elif (
        totals["dreamCyclesStarted"] > 0
        and totals["learningEvents"] <= totals["dreamCyclesStarted"]
    ):
        gaps.append("Dream cycles are running, but no improvement artifacts were extracted yet.")
    if totals["toolRequests"] == 0:
        gaps.append("No verified tool/action capability requests observed.")
    elif totals["skillProposals"] == 0:
        gaps.append(
            "Capability gaps are visible, but no skill or self-improvement proposals "
            "have been observed yet."
        )
    if totals["skillProposals"] == 0 and totals["learningEvents"] == 0:
        gaps.append("No verified skill or self-improvement proposals observed.")
    if not runtime:
        gaps.append("No valkyrie.runtime.started events observed.")

    return {
        "source": "sleipnir_events",
        "verified": True,
        "lastObservedAt": max(_event_timestamp(_event_dict(event)) for event in events),
        "totals": totals,
        "byEnvironment": sorted(
            by_environment.values(),
            key=lambda item: (item["environmentId"] == "unknown", item["environmentId"]),
        ),
        "recentPolls": sorted(
            recent_polls,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:30],
        "recentTasks": sorted(
            recent_tasks,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:30],
        "recentOutcomes": sorted(
            recent_outcomes,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:30],
        "recentEvents": sorted(
            recent_events,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:120],
        "recentLogs": sorted(
            recent_logs,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:120],
        "recentLearning": sorted(
            _merge_learning_entries(recent_learning),
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:60],
        "recentToolNeeds": sorted(
            recent_tool_needs,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )[:60],
        "runtime": runtime,
        "llm": llm,
        "gaps": gaps,
    }


def _configured_environment_entries(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in records:
        environment_id = _environment_id(record)
        name = str(_field(record, "name", default=environment_id))
        entries.append(
            {
                "id": environment_id,
                "name": name,
                "kind": str(_field(record, "kind", default="generic")),
                "health": str(_field(record, "health", default="watch")),
                "flockId": str(_field(record, "flockId", "flock_id", default="")),
                "topologyNodeIds": _as_string_list(
                    _field(record, "topologyNodeIds", "topology_node_ids", default=[])
                ),
                "signalCount": _as_int(_field(record, "signalCount", "signal_count", default=0)),
                "unresolvedSignalCount": _as_int(
                    _field(record, "unresolvedSignalCount", "unresolved_signal_count", default=0)
                ),
                "wakefulCount": _as_int(_field(record, "wakefulCount", "wakeful_count", default=1)),
                "dreamingCount": _as_int(
                    _field(record, "dreamingCount", "dreaming_count", default=0)
                ),
                "lastSignalAt": str(_field(record, "lastSignalAt", "last_signal_at", default="")),
                "actionAuthorities": _as_string_list(
                    _field(record, "actionAuthorities", "action_authorities", default=[])
                ),
                "transport": _field(record, "transport", default={}),
            },
        )
    return entries


def _configured_valkyrie_entries(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in records:
        environment_id = _environment_id(record)
        valkyrie = _field(record, "valkyrie", default={})
        if not isinstance(valkyrie, dict):
            valkyrie = {}
        combined = {**record, **valkyrie}
        valkyrie_id = _valkyrie_id(combined, environment_id)
        entries.append(
            {
                "id": valkyrie_id,
                "name": str(_field(combined, "valkyrieName", "valkyrie_name", default=valkyrie_id)),
                "environmentId": environment_id,
                "flockId": str(_field(combined, "flockId", "flock_id", default="")),
                "persona": str(_field(combined, "persona", default="valkyrie")),
                "specialty": str(_field(combined, "specialty", default="resident operations")),
                "wakefulness": str(_field(combined, "wakefulness", default="watching")),
                "autonomyMode": str(
                    _field(combined, "autonomyMode", "autonomy_mode", default="guarded")
                ),
                "status": str(_field(combined, "status", default="online")),
                "confidence": _as_float(_field(combined, "confidence", default=0.0)),
                "inboxSubjects": _as_string_list(
                    _field(combined, "inboxSubjects", "inbox_subjects", default=[])
                ),
                "toolCount": _as_int(_field(combined, "toolCount", "tool_count", default=0)),
                "lastDreamAt": str(_field(combined, "lastDreamAt", "last_dream_at", default="")),
                "lastActionAt": str(_field(combined, "lastActionAt", "last_action_at", default="")),
                "charter": str(_field(combined, "charter", default="")),
                "identitySource": "configured",
            },
        )
    return entries


def _configured_flock_entries(
    records: list[dict[str, Any]],
    environments: list[dict[str, Any]],
    valkyries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    flocks: dict[str, dict[str, Any]] = {}
    environment_by_id = {entry["id"]: entry for entry in environments}
    valkyrie_by_environment = {entry["environmentId"]: entry for entry in valkyries}
    for record in records:
        environment_id = _environment_id(record)
        flock_id = str(_field(record, "flockId", "flock_id", default=""))
        if not flock_id:
            continue
        flock = _field(record, "flock", default={})
        if not isinstance(flock, dict):
            flock = {}
        entry = flocks.setdefault(
            flock_id,
            {
                "id": flock_id,
                "name": str(_field(flock, "name", default=flock_id)),
                "domain": str(_field(flock, "domain", default="")),
                "natsSubject": str(_field(flock, "natsSubject", "nats_subject", default="")),
                "environmentIds": [],
                "valkyrieIds": [],
                "learningIds": _as_string_list(
                    _field(flock, "learningIds", "learning_ids", default=[])
                ),
                "health": "healthy",
                "lastExchangeAt": str(
                    _field(flock, "lastExchangeAt", "last_exchange_at", default="")
                ),
            },
        )
        if environment_id in environment_by_id:
            entry["environmentIds"].append(environment_id)
        valkyrie = valkyrie_by_environment.get(environment_id)
        if valkyrie is not None:
            entry["valkyrieIds"].append(valkyrie["id"])
    for entry in flocks.values():
        entry["environmentIds"] = sorted(set(entry["environmentIds"]))
        entry["valkyrieIds"] = sorted(set(entry["valkyrieIds"]))
        entry["health"] = _rollup_health(
            [
                environment_by_id[environment_id]["health"]
                for environment_id in entry["environmentIds"]
                if environment_id in environment_by_id
            ]
        )
    return sorted(flocks.values(), key=lambda entry: entry["id"])


def _configured_huddle_entries(
    environments: list[dict[str, Any]],
    valkyries: list[dict[str, Any]],
    updated_at: str,
) -> list[dict[str, Any]]:
    """One standing operator huddle per environment.

    Every environment has a resident room (Environment.room_id); mirroring it
    here as an always-open huddle lets operators join and message the resident
    through the existing huddle routes without a huddle-opening event having
    fired first. No targetFlockId: these are environment rooms, so joining
    them never requires flock scope.
    """
    resident_by_environment = {entry["environmentId"]: entry for entry in valkyries}
    huddles: list[dict[str, Any]] = []
    for environment in environments:
        resident = resident_by_environment.get(environment["id"])
        huddles.append(
            {
                "id": f"huddle-{environment['id']}",
                "environmentId": environment["id"],
                "targetFlockId": "",
                "title": f"{environment['name']} huddle",
                "status": "open",
                "participantIds": [resident["id"]] if resident else [],
                "joined": False,
                "joinedParticipantId": "",
                "joinedDisplayName": "",
                "joinedAction": "",
                "messages": [],
                "lastActivityAt": updated_at,
            }
        )
    return huddles


def _initial_dashboard(config: ValkyrieDashboardConfig | None = None) -> Dashboard:
    records = configured_environment_records(config or ValkyrieDashboardConfig())
    environments = _configured_environment_entries(records)
    valkyries = _configured_valkyrie_entries(records)
    flocks = _configured_flock_entries(records, environments, valkyries)
    updated_at = _now()

    return {
        "environments": environments,
        "valkyries": valkyries,
        "flocks": flocks,
        "signals": [],
        "operationalStates": [],
        "judgments": [],
        "courtDecisions": [],
        "actions": [],
        "huddles": _configured_huddle_entries(environments, valkyries, updated_at),
        "learnings": [],
        "liveReport": _live_report(updated_at, environments=environments),
        "telemetry": _empty_telemetry(updated_at),
        "updatedAt": updated_at,
    }


def _signal_events(dashboard: Dashboard) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for signal in dashboard["signals"]:
        events.append(
            {
                "type": "signal",
                "id": f"event-{signal['id']}",
                "environmentId": signal["environmentId"],
                "summary": signal["summary"],
                "severity": signal["severity"],
                "timestamp": signal["receivedAt"],
            }
        )
    for learning in dashboard["learnings"]:
        events.append(
            {
                "type": "learning",
                "id": f"event-{learning['id']}",
                "environmentId": learning["sourceEnvironmentId"],
                "flockId": learning.get("targetFlockId"),
                "summary": learning["title"],
                "severity": "notice",
                "timestamp": learning["createdAt"],
            }
        )
    return events


class ValkyrieDashboardProjection:
    def __init__(self, config: ValkyrieDashboardConfig | None = None) -> None:
        self._dashboard = _initial_dashboard(config)
        self._poll_count = 0
        self._raw_signal_events: list[dict[str, Any]] = []
        self._control_events: list[dict[str, Any]] = []
        self._runtime_events: dict[str, dict[str, Any]] = {}
        self._learning_decisions: dict[str, dict[str, Any]] = {}
        #: Operator edits applied in place; re-applied after live re-ingest.
        self._learning_revisions: dict[str, dict[str, str]] = {}
        #: Superseding candidates authored by revise; survive live re-ingest.
        self._authored_learnings: dict[str, dict[str, Any]] = {}
        self._seen_event_ids: set[str] = set()

    def dashboard(self) -> Dashboard:
        self._refresh_live_report()
        return _merge_observed_runtime(deepcopy(self._dashboard))

    def record_event(
        self,
        event: SleipnirEvent | dict[str, Any],
        *,
        refresh: bool = False,
    ) -> None:
        event_data = _event_dict(event)
        event_id = str(event_data.get("event_id") or "")
        if event_id:
            if event_id in self._seen_event_ids:
                return
            self._seen_event_ids.add(event_id)
            if len(self._seen_event_ids) > CONTROL_TELEMETRY_LIMIT + RAW_SIGNAL_TELEMETRY_LIMIT:
                self._seen_event_ids = set(
                    list(self._seen_event_ids)[
                        -(CONTROL_TELEMETRY_LIMIT + RAW_SIGNAL_TELEMETRY_LIMIT) :
                    ]
                )
        if _is_runtime_event(event_data):
            runtime_key = _runtime_event_key(event_data)
            existing = self._runtime_events.get(runtime_key)
            if existing is None:
                self._runtime_events[runtime_key] = event_data
            else:
                existing_payload = (
                    existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
                )
                incoming_payload = (
                    event_data.get("payload") if isinstance(event_data.get("payload"), dict) else {}
                )
                merged_payload = {
                    **existing_payload,
                    **{
                        key: value
                        for key, value in incoming_payload.items()
                        if value not in ("", 0, False, None, [])
                    },
                }
                merged_event = {**existing, **event_data, "payload": merged_payload}
                if str(existing.get("timestamp") or "") > str(event_data.get("timestamp") or ""):
                    merged_event["timestamp"] = existing.get("timestamp")
                self._runtime_events[runtime_key] = merged_event
        if _is_raw_signal_event(event_data):
            self._raw_signal_events.append(event_data)
            self._raw_signal_events = self._raw_signal_events[-RAW_SIGNAL_TELEMETRY_LIMIT:]
        else:
            self._control_events.append(event_data)
            self._control_events = self._control_events[-CONTROL_TELEMETRY_LIMIT:]
        if refresh:
            self._touch()
        else:
            self._dashboard["updatedAt"] = _now()

    def environments(self) -> list[dict[str, Any]]:
        return self.dashboard()["environments"]

    def environment(self, environment_id: str) -> dict[str, Any]:
        environment = next(
            (entry for entry in self._dashboard["environments"] if entry["id"] == environment_id),
            None,
        )
        if environment is None:
            raise HTTPException(status_code=404, detail="Environment not found")
        return deepcopy(environment)

    def flocks(self) -> list[dict[str, Any]]:
        return self.dashboard()["flocks"]

    def flock(self, flock_id: str) -> dict[str, Any]:
        flock = next(
            (entry for entry in self._dashboard["flocks"] if entry["id"] == flock_id),
            None,
        )
        if flock is None:
            raise HTTPException(status_code=404, detail="Flock not found")
        return deepcopy(flock)

    def huddle_for_room(self, huddle_id: str) -> dict[str, Any]:
        huddle = deepcopy(self._require_huddle(huddle_id))
        environment = next(
            (
                entry
                for entry in self._dashboard["environments"]
                if entry["id"] == huddle.get("environmentId")
            ),
            {},
        )
        if isinstance(environment, dict):
            huddle.setdefault(
                "environmentActionAuthorities",
                environment.get("actionAuthorities", []),
            )
        return huddle

    def join_huddle(self, request: HuddleJoinRequest) -> dict[str, Any]:
        huddle_id = request.huddleId
        huddle = self._require_huddle(huddle_id)
        _validate_huddle_join_scope(huddle, request)
        huddle["joined"] = True
        participant_id = request.participantId.strip()
        huddle["joinedParticipantId"] = participant_id
        huddle["joinedDisplayName"] = request.displayName.strip() or participant_id
        huddle["joinedAction"] = request.action
        if request.targetFlockId:
            huddle["targetFlockId"] = request.targetFlockId
        if participant_id not in huddle["participantIds"]:
            huddle["participantIds"].append(participant_id)
        huddle["lastActivityAt"] = _now()
        self._touch()
        return deepcopy(huddle)

    def leave_huddle(self, huddle_id: str) -> dict[str, Any]:
        huddle = self._require_huddle(huddle_id)
        participant_id = str(huddle.get("joinedParticipantId") or "").strip()
        huddle["joined"] = False
        huddle["participantIds"] = [
            entry for entry in huddle["participantIds"] if entry != participant_id
        ]
        huddle["joinedParticipantId"] = ""
        huddle["joinedDisplayName"] = ""
        huddle["joinedAction"] = ""
        huddle["lastActivityAt"] = _now()
        self._touch()
        return deepcopy(huddle)

    def send_huddle_message(self, request: HuddleSendRequest) -> dict[str, Any]:
        huddle = self._require_huddle(request.huddleId)
        participant_id, display_name = _resolve_huddle_message_author(huddle, request)
        message = {
            "id": f"message-{len(huddle['messages']) + 1}",
            "huddleId": request.huddleId,
            "authorId": participant_id,
            "authorName": display_name,
            "body": request.body,
            "createdAt": _now(),
        }
        huddle["messages"].append(message)
        huddle["joined"] = True
        author_id = str(message["authorId"] or "").strip()
        if author_id and author_id not in huddle["participantIds"]:
            huddle["participantIds"].append(author_id)
        huddle["lastActivityAt"] = message["createdAt"]
        self._touch()
        return deepcopy(message)

    def learning(self, learning_id: str) -> dict[str, Any]:
        self._refresh_live_report()
        return deepcopy(self._require_learning(learning_id))

    def decide_learning(
        self,
        learning_id: str,
        status: str,
        request: LearningDecisionRequest | None = None,
        *,
        action: str = "",
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        learning["status"] = status
        learning["active"] = _learning_active_for_status(status)
        if action == "override":
            learning["override"] = True
        self._append_learning_history(
            learning,
            event_type=f"valkyrie.learning.{action or status}",
            status=status,
            summary=_decision_summary(action or status, request),
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, action or status, request)
        self._touch()
        return deepcopy(learning)

    def promote_learning(
        self,
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        target_scope = request.targetScope or _next_learning_scope(
            str(learning.get("scope") or "private")
        )
        if target_scope not in LEARNING_SCOPES:
            raise HTTPException(status_code=422, detail="Unsupported learning scope")
        current_index = LEARNING_SCOPES.index(str(learning.get("scope") or "private"))
        target_index = LEARNING_SCOPES.index(target_scope)
        if target_index > current_index + 1:
            raise HTTPException(status_code=422, detail="Learning scope can only advance one step")
        learning["scope"] = target_scope
        learning["currentScope"] = target_scope
        learning["targetScope"] = _next_learning_scope(target_scope)
        learning["availableScopes"] = _available_learning_scopes(target_scope)
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.promoted",
            status=str(learning.get("status") or "candidate"),
            summary=f"Promoted learning to {target_scope}",
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "promote", request)
        self._touch()
        return deepcopy(learning)

    def demote_learning(
        self,
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        target_scope = request.targetScope or _previous_learning_scope(
            str(learning.get("scope") or "private")
        )
        if target_scope not in LEARNING_SCOPES:
            raise HTTPException(status_code=422, detail="Unsupported learning scope")
        current_index = LEARNING_SCOPES.index(str(learning.get("scope") or "private"))
        target_index = LEARNING_SCOPES.index(target_scope)
        if target_index < current_index - 1:
            raise HTTPException(status_code=422, detail="Learning scope can only retreat one step")
        if target_index > current_index:
            raise HTTPException(status_code=422, detail="Use promote to advance learning scope")
        learning["scope"] = target_scope
        learning["currentScope"] = target_scope
        learning["targetScope"] = _next_learning_scope(target_scope)
        learning["availableScopes"] = _available_learning_scopes(target_scope)
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.demoted",
            status=str(learning.get("status") or "candidate"),
            summary=f"Demoted learning to {target_scope}",
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "demote", request)
        self._touch()
        return deepcopy(learning)

    def canary_learning(
        self,
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        learning["status"] = "canary"
        learning["active"] = True
        learning["canaryEnvironmentId"] = request.canaryEnvironmentId or str(
            learning.get("sourceEnvironmentId") or ""
        )
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.canary_started",
            status="canary",
            summary=f"Started canary in {learning['canaryEnvironmentId'] or 'source environment'}",
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "canary", request)
        self._touch()
        return deepcopy(learning)

    def rollback_learning(
        self,
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        learning["status"] = "rolled_back"
        learning["active"] = False
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.rolled_back",
            status="rolled_back",
            summary=_decision_summary("rollback", request),
            request=request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "rollback", request)
        self._touch()
        return deepcopy(learning)

    def record_learning_feedback(
        self,
        learning_id: str,
        request: LearningFeedbackRequest,
    ) -> dict[str, Any]:
        """Attach one operator feedback verdict to a learning record."""
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        learning["feedback"] = {
            "verdict": request.verdict,
            "reason": request.reason,
            "operatorId": request.operatorId,
            "recordedAt": _now(),
        }
        decision_request = _decision_request_for_learning(learning_id, request)
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.feedback",
            status=str(learning.get("status") or ""),
            summary=f"Operator feedback: {request.verdict}",
            request=decision_request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "feedback", decision_request)
        self._touch()
        return deepcopy(learning)

    def revise_learning_in_place(
        self,
        learning_id: str,
        request: LearningReviseRequest,
    ) -> dict[str, Any]:
        """Apply operator edits to a not-yet-active learning in place."""
        self._refresh_live_report()
        learning = self._require_learning(learning_id)
        edits = _learning_edits(request)
        learning.update(edits)
        self._learning_revisions[learning_id] = {
            **self._learning_revisions.get(learning_id, {}),
            **edits,
        }
        decision_request = _decision_request_for_learning(learning_id, request)
        self._append_learning_history(
            learning,
            event_type="valkyrie.learning.revised",
            status=str(learning.get("status") or ""),
            summary=f"Revised in place: {', '.join(sorted(edits))}",
            request=decision_request,
        )
        self._remember_learning_decision(learning)
        self._record_learning_control_event(learning, "revised", decision_request)
        self._touch()
        return deepcopy(learning)

    def revise_learning_supersede(
        self,
        learning_id: str,
        request: LearningReviseRequest,
    ) -> dict[str, Any]:
        """Author a superseding candidate for an active learning.

        Active (adopted/canary) learnings are never mutated in place — the
        revision becomes a new candidate that re-enters the review flow.
        """
        self._refresh_live_report()
        old = self._require_learning(learning_id)
        revision_number = 1 + sum(
            1
            for entry in self._dashboard.get("learnings", [])
            if isinstance(entry, dict)
            and str(entry.get("id") or "").startswith(f"{learning_id}:rev")
        )
        new_id = f"{learning_id}:rev{revision_number}"
        candidate = deepcopy(old)
        candidate.update(_learning_edits(request))
        candidate.update(
            {
                "id": new_id,
                "status": "candidate",
                "active": False,
                "supersedes": learning_id,
                "feedback": None,
                "canaryEnvironmentId": "",
                "commandDelivery": {},
                "createdAt": _now(),
            }
        )
        decision_request = _decision_request_for_learning(new_id, request)
        self._append_learning_history(
            candidate,
            event_type="valkyrie.learning.revised",
            status="candidate",
            summary=f"Supersedes {learning_id}",
            request=decision_request,
        )
        self._dashboard["learnings"].insert(0, candidate)
        self._authored_learnings[new_id] = candidate
        old_request = _decision_request_for_learning(learning_id, request)
        self._append_learning_history(
            old,
            event_type="valkyrie.learning.revised",
            status=str(old.get("status") or ""),
            summary=f"Superseded by {new_id}",
            request=old_request,
        )
        self._remember_learning_decision(old)
        self._record_learning_control_event(candidate, "revised", decision_request)
        self._touch()
        return deepcopy(candidate)

    def replay_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        self._refresh_live_report()
        event_type = str(signal.get("event_type") or signal.get("eventType") or "")
        payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else signal
        payload = payload if isinstance(payload, dict) else {}
        capability = _capability_from_signal_payload(event_type, payload)
        learning = self._active_learning_for_capability(capability)
        signal_id = str(payload.get("signal_id") or payload.get("signalId") or "operator-signal")
        skill_name = (
            str(learning.get("promotedTool") or learning.get("title") or "") if learning else ""
        )
        decision = {
            "signalId": signal_id,
            "capabilityName": capability,
            "decision": (
                "inspect_with_adopted_learning" if learning else "defer_and_request_capability"
            ),
            "skillName": skill_name,
            "learningId": str(learning.get("id") or "") if learning else "",
            "confidence": 0.86 if learning else 0.42,
            "rationale": (
                "An active adopted/canary learning now matches this signal capability."
                if learning
                else "No active adopted learning matches this signal capability."
            ),
            "usedAdoptedLearning": learning is not None,
            "observedAt": _now(),
        }
        self.record_event(
            {
                "event_type": registry.VALKYRIE_JUDGMENT_PROPOSED,
                "source": "ravn:valkyrie-dashboard-replay",
                "summary": f"{decision['decision']} for {decision['signalId']}",
                "urgency": 0.4,
                "domain": "infrastructure",
                "timestamp": decision["observedAt"],
                "correlation_id": "valkyrie-dashboard-replay",
                "payload": {
                    "signal_id": decision["signalId"],
                    "capability_name": capability,
                    "decision": decision["decision"],
                    "confidence": decision["confidence"],
                    "skill_name": decision["skillName"],
                    "learning_id": decision["learningId"],
                    "rationale": decision["rationale"],
                },
            }
        )
        return decision

    def record_learning_command_delivery(
        self,
        learning_id: str,
        delivery: dict[str, Any],
    ) -> dict[str, Any]:
        learning = self._require_learning(learning_id)
        learning["commandDelivery"] = delivery
        self._remember_learning_decision(learning)
        self._touch()
        return deepcopy(learning)

    def _active_learning_for_capability(self, capability: str) -> dict[str, Any] | None:
        for learning in self._dashboard.get("learnings", []):
            if not isinstance(learning, dict) or not learning.get("active"):
                continue
            if str(learning.get("status") or "") not in {"adopted", "canary"}:
                continue
            if _learning_capability(learning) == capability:
                return learning
        return None

    def _append_learning_history(
        self,
        learning: dict[str, Any],
        *,
        event_type: str,
        status: str,
        summary: str,
        request: LearningDecisionRequest | None,
    ) -> None:
        history = learning.get("history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "eventType": event_type,
                "status": status,
                "summary": summary,
                "operatorId": request.operatorId if request else "system",
                "reason": request.reason if request else "",
                "observedAt": _now(),
            }
        )
        learning["history"] = history[-30:]

    def _record_learning_control_event(
        self,
        learning: dict[str, Any],
        action: str,
        request: LearningDecisionRequest | None,
    ) -> None:
        self._control_events.append(
            {
                "event_type": f"valkyrie.learning.{action}",
                "source": "ravn:valkyrie-dashboard",
                "summary": _decision_summary(action, request),
                "urgency": 0.35,
                "domain": "infrastructure",
                "timestamp": _now(),
                "correlation_id": "valkyrie-learning-ui",
                "payload": {
                    "learning_id": learning.get("id", ""),
                    "title": learning.get("title", ""),
                    "status": learning.get("status", ""),
                    "scope": learning.get("scope", ""),
                    "active": learning.get("active", False),
                    "operator_id": request.operatorId if request else "system",
                    "reason": request.reason if request else "",
                    "target_scope": request.targetScope if request else "",
                    "canary_environment_id": request.canaryEnvironmentId if request else "",
                },
            }
        )
        self._control_events = self._control_events[-CONTROL_TELEMETRY_LIMIT:]

    def _remember_learning_decision(self, learning: dict[str, Any]) -> None:
        learning_id = str(learning.get("id") or "")
        if not learning_id:
            return
        history = learning.get("history") if isinstance(learning.get("history"), list) else []
        decision_history = [
            entry
            for entry in history
            if isinstance(entry, dict)
            and str(entry.get("eventType") or "").startswith("valkyrie.learning.")
        ]
        self._learning_decisions[learning_id] = {
            "status": str(learning.get("status") or ""),
            "active": bool(learning.get("active")),
            "scope": str(learning.get("scope") or learning.get("currentScope") or ""),
            "currentScope": str(learning.get("currentScope") or learning.get("scope") or ""),
            "targetScope": str(learning.get("targetScope") or ""),
            "availableScopes": (
                learning.get("availableScopes")
                if isinstance(learning.get("availableScopes"), list)
                else []
            ),
            "canaryEnvironmentId": str(learning.get("canaryEnvironmentId") or ""),
            "override": bool(learning.get("override")),
            "commandDelivery": (
                learning.get("commandDelivery")
                if isinstance(learning.get("commandDelivery"), dict)
                else {}
            ),
            "feedback": (
                learning.get("feedback") if isinstance(learning.get("feedback"), dict) else {}
            ),
            "supersedes": str(learning.get("supersedes") or ""),
            "decisionHistory": decision_history[-30:],
        }

    def update_autonomy(self, request: AutonomyUpdateRequest) -> Dashboard:
        valid_modes = {"guarded", "autonomous", "yolo"}
        if request.mode not in valid_modes:
            raise HTTPException(status_code=422, detail="Unsupported autonomy mode")
        valkyrie = next(
            (entry for entry in self._dashboard["valkyries"] if entry["id"] == request.valkyrieId),
            None,
        )
        if valkyrie is None:
            raise HTTPException(status_code=404, detail="Valkyrie not found")
        valkyrie["autonomyMode"] = request.mode
        self._touch()
        return self.dashboard()

    def events(self) -> list[dict[str, Any]]:
        return _signal_events(self._dashboard)

    def telemetry_events(
        self,
        *,
        limit: int = 200,
        event_type: str = "",
        environment_id: str = "",
        valkyrie_id: str = "",
        contains: str = "",
    ) -> list[dict[str, Any]]:
        self._refresh_live_report()
        telemetry_events = [*self._raw_signal_events, *self._control_events]
        retained_event_ids = {
            str(event.get("event_id") or "")
            for event in telemetry_events
            if isinstance(event, dict) and str(event.get("event_id") or "")
        }
        telemetry_events.extend(
            event
            for event in self._runtime_events.values()
            if str(event.get("event_id") or "") not in retained_event_ids
        )
        filtered = []
        for event in telemetry_events:
            normalized = _event_dict(event)
            payload = normalized.get("payload")
            filtered.append(
                _event_log_entry(
                    normalized,
                    payload if isinstance(payload, dict) else {},
                )
            )
        if event_type:
            filtered = [
                event for event in filtered if str(event.get("eventType") or "") == event_type
            ]
        if environment_id:
            canonical_environment_id = _canonical_environment_id(environment_id)
            filtered = [
                event
                for event in filtered
                if str(event.get("environmentId") or "") == canonical_environment_id
            ]
        if valkyrie_id:
            filtered = [
                event for event in filtered if str(event.get("valkyrieId") or "") == valkyrie_id
            ]
        if contains:
            needle = contains.lower()
            filtered = [event for event in filtered if needle in json.dumps(event).lower()]
        bounded_limit = max(1, min(int(limit or 200), 1_000))
        filtered = sorted(
            filtered,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        )
        return deepcopy(filtered[:bounded_limit])

    def logs(self) -> list[dict[str, Any]]:
        telemetry = self.dashboard().get("telemetry", {})
        if not isinstance(telemetry, dict):
            return []
        logs = telemetry.get("recentLogs", [])
        return deepcopy(logs if isinstance(logs, list) else [])

    def _touch(self) -> None:
        self._dashboard["updatedAt"] = _now()
        self._refresh_live_report()

    def _refresh_live_report(self) -> None:
        self._poll_count += 1
        observed_at = _now()
        telemetry_events = [*self._raw_signal_events, *self._control_events]
        retained_event_ids = {
            str(event.get("event_id") or "")
            for event in telemetry_events
            if isinstance(event, dict) and str(event.get("event_id") or "")
        }
        telemetry_events.extend(
            event
            for event in self._runtime_events.values()
            if str(event.get("event_id") or "") not in retained_event_ids
        )
        self._dashboard["liveReport"] = _live_report(
            observed_at,
            self._poll_count,
            environments=self._dashboard["environments"],
        )
        self._dashboard["telemetry"] = _aggregate_telemetry(
            telemetry_events,
            observed_at=observed_at,
        )
        seeded_signals = [
            entry
            for entry in self._dashboard.get("signals", [])
            if isinstance(entry, dict) and not str(entry.get("id") or "").startswith("live-")
        ]
        self._dashboard["signals"] = sorted(
            [*_signals_from_events(telemetry_events), *seeded_signals],
            key=lambda item: item.get("receivedAt", ""),
            reverse=True,
        )[:60]
        seeded_decisions = [
            entry
            for entry in self._dashboard.get("courtDecisions", [])
            if isinstance(entry, dict) and not str(entry.get("id") or "").startswith("live-")
        ]
        self._dashboard["courtDecisions"] = sorted(
            [*_court_decisions_from_events(telemetry_events), *seeded_decisions],
            key=lambda item: item.get("createdAt", ""),
            reverse=True,
        )[:60]
        seeded_states = [
            entry
            for entry in self._dashboard.get("operationalStates", [])
            if isinstance(entry, dict) and not str(entry.get("id") or "").startswith("live-")
        ]
        self._dashboard["operationalStates"] = sorted(
            [*_operational_states_from_events(telemetry_events), *seeded_states],
            key=lambda item: item.get("updatedAt", ""),
            reverse=True,
        )[:60]
        self._sync_live_learnings()
        self._dashboard["updatedAt"] = observed_at

    def _sync_live_learnings(self) -> None:
        telemetry = self._dashboard.get("telemetry")
        if not isinstance(telemetry, dict):
            return
        recent = telemetry.get("recentLearning")
        if not isinstance(recent, list):
            return
        seeded = [
            entry
            for entry in self._dashboard.get("learnings", [])
            if isinstance(entry, dict) and not str(entry.get("id") or "").startswith("live-")
        ]
        by_id = {str(entry.get("id") or ""): entry for entry in seeded}
        for item in sorted(recent, key=lambda entry: str(entry.get("observedAt") or "")):
            if not isinstance(item, dict):
                continue
            learning = _dashboard_learning_from_telemetry(item)
            if not learning["id"]:
                continue
            learning["id"] = f"live-{learning['id']}"
            existing = by_id.get(learning["id"])
            by_id[learning["id"]] = _merge_learning_record(existing, learning)
            revision = self._learning_revisions.get(learning["id"])
            if revision:
                by_id[learning["id"]].update(revision)
            decision = self._learning_decisions.get(learning["id"])
            if decision:
                for key in (
                    "status",
                    "active",
                    "scope",
                    "currentScope",
                    "targetScope",
                    "availableScopes",
                    "canaryEnvironmentId",
                    "override",
                    "commandDelivery",
                    "feedback",
                ):
                    if key in decision and decision[key] not in ("", [], {}):
                        by_id[learning["id"]][key] = decision[key]
                decision_history = (
                    decision.get("decisionHistory")
                    if isinstance(decision.get("decisionHistory"), list)
                    else []
                )
                if decision_history:
                    existing_history = (
                        by_id[learning["id"]].get("history")
                        if isinstance(by_id[learning["id"]].get("history"), list)
                        else []
                    )
                    telemetry_history = [
                        entry
                        for entry in existing_history
                        if not (
                            isinstance(entry, dict)
                            and str(entry.get("eventType") or "").startswith("valkyrie.learning.")
                        )
                    ]
                    by_id[learning["id"]]["history"] = [
                        *telemetry_history,
                        *decision_history,
                    ][-30:]
        for learning_id, authored in self._authored_learnings.items():
            # Superseding candidates are authored here, not observed in
            # telemetry — keep the live records across re-ingest.
            by_id[learning_id] = authored
        self._dashboard["learnings"] = sorted(
            by_id.values(),
            key=lambda entry: str(entry.get("createdAt") or ""),
            reverse=True,
        )

    def _require_huddle(self, huddle_id: str) -> dict[str, Any]:
        huddle = next(
            (entry for entry in self._dashboard["huddles"] if entry["id"] == huddle_id),
            None,
        )
        if huddle is None:
            raise HTTPException(status_code=404, detail="Huddle not found")
        return huddle

    def _require_learning(self, learning_id: str) -> dict[str, Any]:
        learning = next(
            (entry for entry in self._dashboard["learnings"] if entry["id"] == learning_id),
            None,
        )
        if learning is None:
            raise HTTPException(status_code=404, detail="Learning not found")
        learning.setdefault("feedback", None)
        learning.setdefault("repetition", 1)
        learning.setdefault("supersedes", "")
        return learning


class ValkyrieTelemetrySubscription:
    """Feed live Sleipnir/NATS telemetry events into the dashboard projection."""

    def __init__(
        self,
        *,
        projection: ValkyrieDashboardProjection,
        subscribers: list[tuple[str, Any]],
        event_types: list[str],
        retry_interval_seconds: int = 30,
        startup_delay_seconds: float = 5.0,
        subscriber_start_timeout_seconds: float = 5.0,
        review_ingest: Any | None = None,
        history_ingest: Any | None = None,
        skills_ingest: Any | None = None,
    ) -> None:
        self._projection = projection
        self._review_ingest = review_ingest
        self._history_ingest = history_ingest
        self._skills_ingest = skills_ingest
        self._subscribers = subscribers
        self._event_types = event_types
        self._subscriptions: list[tuple[str, Any]] = []
        self._retry_interval_seconds = max(retry_interval_seconds, 1)
        self._startup_delay_seconds = max(startup_delay_seconds, 0.0)
        self._subscriber_start_timeout_seconds = max(subscriber_start_timeout_seconds, 1.0)
        self._bootstrap_task: asyncio.Task[None] | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._started_labels: set[str] = set()
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        self._bootstrap_task = asyncio.create_task(self._bootstrap_subscribers())
        await asyncio.sleep(0)

    async def stop(self) -> None:
        self._stopping = True
        if self._bootstrap_task is not None:
            self._bootstrap_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._bootstrap_task
            self._bootstrap_task = None
        if self._retry_task is not None:
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_task
            self._retry_task = None
        for _, subscription in self._subscriptions:
            with contextlib.suppress(Exception):
                await subscription.unsubscribe()
        self._subscriptions.clear()
        self._started_labels.clear()
        for _, subscriber in self._subscribers:
            if hasattr(subscriber, "stop"):
                with contextlib.suppress(Exception):
                    await subscriber.stop()

    async def _bootstrap_subscribers(self) -> None:
        if self._startup_delay_seconds > 0:
            await asyncio.sleep(self._startup_delay_seconds)
        if self._stopping:
            return
        results = await asyncio.gather(
            *(self._start_subscriber(label, subscriber) for label, subscriber in self._subscribers),
        )
        failed = [
            subscriber_spec
            for subscriber_spec, started in zip(self._subscribers, results, strict=True)
            if not started
        ]
        if failed:
            self._retry_task = asyncio.create_task(self._retry_failed(failed))
        if not self._subscriptions:
            logger.warning("valkyrie_dashboard: no telemetry streams subscribed yet")

    async def _handle(self, event: SleipnirEvent) -> None:
        self._projection.record_event(event)
        if self._history_ingest is not None:
            try:
                await self._history_ingest(event)
            except Exception:
                logger.exception(
                    "valkyrie_dashboard: history ingest failed for %s",
                    event.event_type,
                )
        if self._skills_ingest is not None:
            try:
                await self._skills_ingest(event)
            except Exception:
                logger.exception(
                    "valkyrie_dashboard: skill mirror ingest failed for %s",
                    event.event_type,
                )
        if self._review_ingest is None:
            return
        try:
            await self._review_ingest(event)
        except Exception:
            logger.exception(
                "valkyrie_dashboard: review queue ingest failed for %s",
                event.event_type,
            )

    async def _start_subscriber(self, label: str, subscriber: Any) -> bool:
        if label in self._started_labels:
            return True
        try:
            subscription = await asyncio.wait_for(
                self._start_subscriber_subscription(subscriber),
                timeout=self._subscriber_start_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "valkyrie_dashboard: telemetry stream %s did not start: %r",
                label,
                exc,
            )
            if hasattr(subscriber, "stop"):
                with contextlib.suppress(Exception):
                    await subscriber.stop()
            return False
        self._subscriptions.append((label, subscription))
        self._started_labels.add(label)
        logger.info(
            "valkyrie_dashboard: subscribed to %s telemetry events: %s",
            label,
            ", ".join(self._event_types),
        )
        return True

    async def _start_subscriber_subscription(self, subscriber: Any) -> Any:
        await subscriber.start()
        return await subscriber.subscribe(self._event_types, self._handle)

    async def _retry_failed(self, failed: list[tuple[str, Any]]) -> None:
        pending = list(failed)
        while pending and not self._stopping:
            await asyncio.sleep(self._retry_interval_seconds)
            if self._stopping:
                return
            results = await asyncio.gather(
                *(self._start_subscriber(label, subscriber) for label, subscriber in pending),
            )
            next_pending = [
                subscriber_spec
                for subscriber_spec, started in zip(pending, results, strict=True)
                if not started
            ]
            pending = next_pending


def _safe_consumer_suffix(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-").lower()


def _env_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("invalid integer environment value %r; using %s", value, default)
        return default


def _env_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("invalid float environment value %r; using %s", value, default)
        return default


def _telemetry_stream_specs() -> list[dict[str, str]]:
    streams_raw = os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_STREAMS", "").strip()
    default_stream = os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_STREAM", "ravn_environment")
    default_prefix = os.environ.get(
        "RAVN_VALKYRIE_TELEMETRY_SUBJECT_PREFIX",
        "ravn.environment",
    )
    default_user = os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_USER", "")
    default_password_env = "RAVN_VALKYRIE_TELEMETRY_NATS_PASSWORD"

    if not streams_raw:
        return [
            {
                "stream_name": default_stream,
                "subject_prefix": default_prefix,
                "user": default_user,
                "password_env": default_password_env,
            }
        ]

    specs: list[dict[str, str]] = []
    for raw_entry in streams_raw.replace("\n", ",").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        stream_name = parts[0]
        if not stream_name:
            continue
        specs.append(
            {
                "stream_name": stream_name,
                "subject_prefix": parts[1] if len(parts) > 1 and parts[1] else default_prefix,
                "user": parts[2] if len(parts) > 2 and parts[2] else default_user,
                "password_env": (parts[3] if len(parts) > 3 and parts[3] else default_password_env),
            }
        )
    return specs


def _command_stream_specs() -> list[dict[str, str]]:
    streams_raw = os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_STREAMS", "").strip()
    if not streams_raw:
        return []
    default_stream = os.environ.get(
        "RAVN_VALKYRIE_COMMAND_NATS_STREAM",
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_STREAM", "ravn_environment"),
    )
    default_prefix = os.environ.get(
        "RAVN_VALKYRIE_COMMAND_SUBJECT_PREFIX",
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_SUBJECT_PREFIX", "ravn.environment"),
    )
    default_user = os.environ.get(
        "RAVN_VALKYRIE_COMMAND_NATS_USER",
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_USER", ""),
    )
    default_password_env = (
        "RAVN_VALKYRIE_COMMAND_NATS_PASSWORD"
        if os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_PASSWORD")
        else "RAVN_VALKYRIE_TELEMETRY_NATS_PASSWORD"
    )

    specs: list[dict[str, str]] = []
    for raw_entry in streams_raw.replace("\n", ",").split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        stream_name = parts[0] if parts and parts[0] else default_stream
        subject_prefix = parts[1] if len(parts) > 1 and parts[1] else default_prefix
        user = parts[2] if len(parts) > 2 and parts[2] else default_user
        password_env = parts[3] if len(parts) > 3 and parts[3] else default_password_env
        specs.append(
            {
                "stream_name": stream_name,
                "subject_prefix": subject_prefix,
                "user": user,
                "password_env": password_env,
                "mode": "core" if stream_name.lower() == "core" else "jetstream",
                "label": (
                    f"core/{subject_prefix}"
                    if stream_name.lower() == "core"
                    else f"{stream_name}/{subject_prefix}"
                ),
            }
        )
    return specs


def build_nats_telemetry_subscription_from_env(
    projection: ValkyrieDashboardProjection,
    review_ingest: Any | None = None,
    history_ingest: Any | None = None,
    skills_ingest: Any | None = None,
) -> ValkyrieTelemetrySubscription | None:
    """Build the optional dashboard telemetry NATS consumer from environment vars."""
    servers_raw = os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_URL", "").strip()
    if not servers_raw:
        return None

    from sleipnir.adapters.nats_transport import NatsSubscriber  # noqa: PLC0415

    servers = [entry.strip() for entry in servers_raw.split(",") if entry.strip()]
    consumer_group = os.environ.get(
        "RAVN_VALKYRIE_TELEMETRY_CONSUMER_GROUP",
        "ravn-valkyrie-dashboard",
    )
    replay_seconds = _env_int(
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_REPLAY_SECONDS"),
        0,
    )
    retry_interval_seconds = _env_int(
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_RETRY_SECONDS"),
        30,
    )
    startup_delay_seconds = _env_float(
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_STARTUP_DELAY_SECONDS"),
        5.0,
    )
    subscriber_start_timeout_seconds = _env_float(
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_START_TIMEOUT_SECONDS"),
        5.0,
    )
    connect_timeout_seconds = _env_float(
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_CONNECT_TIMEOUT_SECONDS"),
        2.0,
    )
    jetstream_domain = os.environ.get(
        "RAVN_VALKYRIE_TELEMETRY_NATS_JETSTREAM_DOMAIN",
        "",
    )
    max_reconnect_attempts = _env_int(
        os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_MAX_RECONNECT_ATTEMPTS"),
        0,
    )
    replay_from_time = (
        datetime.now(UTC) - timedelta(seconds=replay_seconds) if replay_seconds > 0 else None
    )
    subscribers = []
    for spec in _telemetry_stream_specs():
        stream_name = spec["stream_name"]
        consumer_suffix = _safe_consumer_suffix(stream_name)
        subscribers.append(
            (
                f"{stream_name}/{spec['subject_prefix']}",
                NatsSubscriber(
                    servers=servers,
                    stream_name=stream_name,
                    jetstream_domain=jetstream_domain,
                    subject_prefix=spec["subject_prefix"],
                    consumer_group=f"{consumer_group}-{consumer_suffix}",
                    replay_from_time=replay_from_time,
                    connect_timeout_s=connect_timeout_seconds,
                    max_reconnect_attempts=max_reconnect_attempts,
                    ensure_stream=False,
                    tls_ca_file=os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_CA_FILE", ""),
                    tls_cert_file=os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_CERT_FILE", ""),
                    tls_key_file=os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_KEY_FILE", ""),
                    tls_hostname=os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_HOSTNAME", ""),
                    tls_handshake_first=_env_bool(
                        os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_HANDSHAKE_FIRST")
                    ),
                    tls_insecure_skip_verify=_env_bool(
                        os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_INSECURE_SKIP_VERIFY")
                    ),
                    user=spec["user"],
                    password=os.environ.get(spec["password_env"], ""),
                    token=os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_TOKEN", ""),
                    nkeys_seed_file=os.environ.get(
                        "RAVN_VALKYRIE_TELEMETRY_NKEYS_SEED_FILE",
                        "",
                    ),
                    nkeys_seed=os.environ.get("RAVN_VALKYRIE_TELEMETRY_NKEYS_SEED", ""),
                ),
            )
        )

    return ValkyrieTelemetrySubscription(
        projection=projection,
        review_ingest=review_ingest,
        history_ingest=history_ingest,
        skills_ingest=skills_ingest,
        subscribers=subscribers,
        retry_interval_seconds=retry_interval_seconds,
        startup_delay_seconds=startup_delay_seconds,
        subscriber_start_timeout_seconds=subscriber_start_timeout_seconds,
        # Subscribe once to the environment stream and let the projection decide
        # what to count. Multiple JetStream push consumers with the same config
        # can silently miss delivery in the live NATS setup.
        event_types=["*"],
    )


def build_nats_review_command_publisher_from_env() -> OdinReviewCommandPublisher:
    """Build the optional learning-command publisher from environment vars."""
    servers_raw = (
        os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_URL", "").strip()
        or os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_URL", "").strip()
    )
    if not servers_raw:
        return OdinReviewCommandPublisher()

    from sleipnir.adapters.nats_transport import NatsCorePublisher, NatsPublisher  # noqa: PLC0415

    command_inherits_telemetry = not os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_URL", "").strip()
    servers = [entry.strip() for entry in servers_raw.split(",") if entry.strip()]
    stream_specs = _command_stream_specs()
    shared_options = {
        "servers": servers,
        "jetstream_domain": os.environ.get(
            "RAVN_VALKYRIE_COMMAND_NATS_JETSTREAM_DOMAIN",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_JETSTREAM_DOMAIN", ""),
        ),
        "ensure_stream": _env_bool(os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_ENSURE_STREAM")),
        "connect_timeout_s": _env_float(
            os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_CONNECT_TIMEOUT_SECONDS"),
            2.0,
        ),
        "max_reconnect_attempts": _env_int(
            os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_MAX_RECONNECT_ATTEMPTS"),
            0,
        ),
        "tls_ca_file": os.environ.get(
            "RAVN_VALKYRIE_COMMAND_TLS_CA_FILE",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_CA_FILE", "")
            if command_inherits_telemetry
            else "",
        ),
        "tls_cert_file": os.environ.get(
            "RAVN_VALKYRIE_COMMAND_TLS_CERT_FILE",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_CERT_FILE", "")
            if command_inherits_telemetry
            else "",
        ),
        "tls_key_file": os.environ.get(
            "RAVN_VALKYRIE_COMMAND_TLS_KEY_FILE",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_KEY_FILE", "")
            if command_inherits_telemetry
            else "",
        ),
        "tls_hostname": os.environ.get(
            "RAVN_VALKYRIE_COMMAND_TLS_HOSTNAME",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_HOSTNAME", "")
            if command_inherits_telemetry
            else "",
        ),
        "tls_handshake_first": _env_bool(
            os.environ.get(
                "RAVN_VALKYRIE_COMMAND_TLS_HANDSHAKE_FIRST",
                os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_HANDSHAKE_FIRST", "")
                if command_inherits_telemetry
                else "",
            )
        ),
        "tls_insecure_skip_verify": _env_bool(
            os.environ.get(
                "RAVN_VALKYRIE_COMMAND_TLS_INSECURE_SKIP_VERIFY",
                os.environ.get("RAVN_VALKYRIE_TELEMETRY_TLS_INSECURE_SKIP_VERIFY", "")
                if command_inherits_telemetry
                else "",
            )
        ),
        "token": os.environ.get(
            "RAVN_VALKYRIE_COMMAND_NATS_TOKEN",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_TOKEN", ""),
        ),
        "nkeys_seed_file": os.environ.get("RAVN_VALKYRIE_COMMAND_NKEYS_SEED_FILE", ""),
        "nkeys_seed": os.environ.get("RAVN_VALKYRIE_COMMAND_NKEYS_SEED", ""),
    }
    if stream_specs:
        core_options = {
            key: value
            for key, value in shared_options.items()
            if key not in {"jetstream_domain", "ensure_stream"}
        }
        targets = [
            _CommandTarget(
                label=spec["label"],
                publisher=(
                    NatsCorePublisher(
                        **core_options,
                        subject_prefix=spec["subject_prefix"],
                        user=spec["user"],
                        password=os.environ.get(spec["password_env"], ""),
                    )
                    if spec.get("mode") == "core"
                    else NatsPublisher(
                        **shared_options,
                        stream_name=spec["stream_name"],
                        subject_prefix=spec["subject_prefix"],
                        user=spec["user"],
                        password=os.environ.get(spec["password_env"], ""),
                    )
                ),
            )
            for spec in stream_specs
        ]
        return OdinReviewCommandPublisher(
            _FanoutSleipnirPublisher(targets),
            start_timeout_seconds=_env_float(
                os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_START_TIMEOUT_SECONDS"),
                5.0,
            ),
        )

    publisher = NatsPublisher(
        stream_name=os.environ.get(
            "RAVN_VALKYRIE_COMMAND_NATS_STREAM",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_STREAM", "ravn_environment"),
        ),
        subject_prefix=os.environ.get(
            "RAVN_VALKYRIE_COMMAND_SUBJECT_PREFIX",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_SUBJECT_PREFIX", "ravn.environment"),
        ),
        **shared_options,
        user=os.environ.get(
            "RAVN_VALKYRIE_COMMAND_NATS_USER",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_USER", ""),
        ),
        password=os.environ.get(
            "RAVN_VALKYRIE_COMMAND_NATS_PASSWORD",
            os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_PASSWORD", ""),
        ),
    )
    return OdinReviewCommandPublisher(
        publisher,
        start_timeout_seconds=_env_float(
            os.environ.get("RAVN_VALKYRIE_COMMAND_NATS_START_TIMEOUT_SECONDS"),
            5.0,
        ),
    )


def _env_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def create_valkyrie_router(
    projection: ValkyrieDashboardProjection | None = None,
    review_command_publisher: OdinReviewCommandPublisher | None = None,
    room_client: ValkyrieRoomClient | None = None,
    review_service: Any | None = None,
    history_service: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ravn/valkyrie", tags=["Ravn Valkyries"])
    store = projection or ValkyrieDashboardProjection()
    command_publisher = review_command_publisher or OdinReviewCommandPublisher()
    skuld_room = room_client or build_skuld_room_client_from_env()

    def _require_history() -> Any:
        if history_service is None:
            raise HTTPException(
                status_code=503,
                detail="Valkyrie history store is not configured on this API process",
            )
        return history_service

    async def _record_in_review_ledger(item: ReviewItem) -> None:
        """Operator-initiated decisions land in the same central ledger."""
        if review_service is None:
            return
        try:
            await review_service.record_decided(item)
        except Exception:
            logger.exception("valkyrie controls: review ledger record failed")

    async def finish_learning_action(
        action: str,
        before: dict[str, Any],
        learning: dict[str, Any],
        request: LearningDecisionRequest,
        *,
        feedback: dict[str, Any] | None = None,
        revision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = _review_item_for_learning_action(
            action,
            before,
            learning,
            request,
            operator_id=request.operatorId,
            feedback=feedback,
            revision=revision,
        )
        try:
            delivery, event = await command_publisher.publish_review_decision(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("valkyrie review command publish failed: %s", exc)
            delivery = {
                "published": False,
                "eventType": "",
                "eventId": "",
                "message": f"Sleipnir/NATS publish failed: {exc}",
                "observedAt": _now(),
            }
            event = None
        if event is not None:
            store.record_event(event)
        await _record_in_review_ledger(item)
        return store.record_learning_command_delivery(str(learning["id"]), delivery)

    @router.get("/dashboard")
    async def get_dashboard() -> Dashboard:
        return store.dashboard()

    @router.get("/environments")
    async def list_environments() -> list[dict[str, Any]]:
        return store.environments()

    @router.get("/environments/{environment_id}")
    async def get_environment(environment_id: str) -> dict[str, Any]:
        return store.environment(environment_id)

    @router.get("/flocks")
    async def list_flocks() -> list[dict[str, Any]]:
        return store.flocks()

    @router.get("/flocks/{flock_id}")
    async def get_flock(flock_id: str) -> dict[str, Any]:
        return store.flock(flock_id)

    @router.post("/huddles/{huddle_id}/join")
    async def join_huddle(huddle_id: str, request: HuddleJoinRequest) -> dict[str, Any]:
        if request.huddleId != huddle_id:
            raise HTTPException(status_code=422, detail="Huddle id mismatch")
        huddle = store.huddle_for_room(huddle_id)
        _validate_huddle_join_scope(huddle, request)
        if skuld_room is None:
            raise HTTPException(status_code=503, detail="Skuld room bridge is not configured")
        if skuld_room is not None:
            await skuld_room.join_huddle(huddle, request)
        return store.join_huddle(request)

    @router.post("/huddles/{huddle_id}/leave")
    async def leave_huddle(huddle_id: str) -> dict[str, Any]:
        if skuld_room is not None:
            await skuld_room.leave_huddle(store.huddle_for_room(huddle_id))
        return store.leave_huddle(huddle_id)

    @router.post("/huddles/{huddle_id}/messages")
    async def send_huddle_message(
        huddle_id: str,
        request: HuddleSendRequest,
    ) -> dict[str, Any]:
        if request.huddleId != huddle_id:
            raise HTTPException(status_code=422, detail="Huddle id mismatch")
        huddle = store.huddle_for_room(huddle_id)
        _resolve_huddle_message_author(huddle, request)
        directed_to = [target for target in request.directedTo if str(target).strip()]
        if directed_to and skuld_room is None:
            # A directed message that never leaves this projection would be a
            # silent failure — refuse instead of pretending it was delivered.
            raise HTTPException(
                status_code=503,
                detail=(
                    "Skuld room bridge is not configured; the direct message "
                    "cannot reach the resident"
                ),
            )
        if skuld_room is not None:
            await skuld_room.send_huddle_message(huddle, request)
        return store.send_huddle_message(request)

    @router.get("/learnings/{learning_id}")
    async def get_learning(learning_id: str) -> dict[str, Any]:
        return store.learning(learning_id)

    @router.post("/learnings/{learning_id}/adopt")
    async def adopt_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.decide_learning(learning_id, "adopted", request, action="adopt")
        return await finish_learning_action("adopt", before, learning, request)

    @router.post("/learnings/{learning_id}/reject")
    async def reject_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.decide_learning(learning_id, "rejected", request, action="reject")
        return await finish_learning_action("reject", before, learning, request)

    @router.post("/learnings/{learning_id}/override")
    async def override_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.decide_learning(learning_id, "adopted", request, action="override")
        return await finish_learning_action("override", before, learning, request)

    @router.post("/learnings/{learning_id}/canary")
    async def canary_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.canary_learning(learning_id, request)
        return await finish_learning_action("canary", before, learning, request)

    @router.post("/learnings/{learning_id}/promote")
    async def promote_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.promote_learning(learning_id, request)
        return await finish_learning_action("promote", before, learning, request)

    @router.post("/learnings/{learning_id}/demote")
    async def demote_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.demote_learning(learning_id, request)
        return await finish_learning_action("demote", before, learning, request)

    @router.post("/learnings/{learning_id}/rollback")
    async def rollback_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.rollback_learning(learning_id, request)
        return await finish_learning_action("rollback", before, learning, request)

    @router.post("/learnings/{learning_id}/feedback")
    async def learning_feedback(
        learning_id: str,
        request: LearningFeedbackRequest,
    ) -> dict[str, Any]:
        if request.verdict not in LEARNING_FEEDBACK_VERDICTS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown feedback verdict {request.verdict!r}; "
                    f"expected one of {', '.join(LEARNING_FEEDBACK_VERDICTS)}"
                ),
            )
        if request.verdict == "wrong_tier" and not request.targetScope:
            raise HTTPException(
                status_code=422,
                detail="wrong_tier feedback requires targetScope",
            )
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        decision_request = _decision_request_for_learning(learning_id, request)
        action = _learning_feedback_action(
            request.verdict,
            str(before.get("status") or ""),
            current_scope=str(before.get("scope") or before.get("currentScope") or "private"),
            target_scope=request.targetScope,
        )
        # Lifecycle first: adjacency violations must 422 before feedback lands.
        if action == "rollback":
            store.rollback_learning(learning_id, decision_request)
        elif action == "reject":
            store.decide_learning(learning_id, "rejected", decision_request, action="reject")
        elif action == "promote":
            store.promote_learning(learning_id, decision_request)
        elif action == "demote":
            store.demote_learning(learning_id, decision_request)
        learning = store.record_learning_feedback(learning_id, request)
        feedback = dict(learning.get("feedback") or {})
        return await finish_learning_action(
            action,
            before,
            learning,
            decision_request,
            feedback=feedback,
        )

    @router.post("/learnings/{learning_id}/revise")
    async def revise_learning(
        learning_id: str,
        request: LearningReviseRequest,
    ) -> dict[str, Any]:
        edits = _learning_edits(request)
        if not edits:
            raise HTTPException(
                status_code=422,
                detail="Revision needs at least one of title, summary, or content",
            )
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        supersede = str(before.get("status") or "") in {"adopted", "canary"}
        if supersede:
            learning = store.revise_learning_supersede(learning_id, request)
            superseded_id = learning_id
        else:
            learning = store.revise_learning_in_place(learning_id, request)
            superseded_id = ""
        decision_request = _decision_request_for_learning(str(learning["id"]), request)
        revision = {
            "title": request.title,
            "summary": request.summary,
            "content": request.content,
            "reason": request.reason,
            "revision_id": _raw_learning_id(str(learning["id"])),
            "superseded_id": _raw_learning_id(superseded_id) if superseded_id else "",
        }
        updated = await finish_learning_action(
            "revise",
            before,
            learning,
            decision_request,
            revision=revision,
        )
        return {"learning": updated, "supersededId": superseded_id}

    @router.post("/proof/replay-signal")
    async def replay_signal(signal: dict[str, Any]) -> dict[str, Any]:
        return store.replay_signal(signal)

    async def _require_operator_capability(participant_id: str, capability: str) -> None:
        """Enforce room capabilities on operator control endpoints.

        When Skuld rooms are configured the participant must hold the
        capability (403 otherwise). Without a room client (local dev,
        proofs) controls stay open but the gap is logged loudly.
        """
        if skuld_room is None:
            logger.warning(
                "valkyrie controls: no Skuld room client configured; "
                "operator capabilities are not enforced",
            )
            return
        if not participant_id:
            raise HTTPException(
                status_code=403,
                detail=f"participantId with the {capability!r} capability is required",
            )
        await skuld_room.require_capability(participant_id, capability)

    @router.post("/autonomy")
    async def update_autonomy(request: AutonomyUpdateRequest) -> Dashboard:
        await _require_operator_capability(request.participantId, "change_autonomy")
        before = store.dashboard()
        previous_mode = next(
            (
                str(entry.get("autonomyMode") or "")
                for entry in before.get("valkyries", [])
                if entry.get("id") == request.valkyrieId
            ),
            "",
        )
        dashboard = store.update_autonomy(request)
        valkyrie = next(
            (
                entry
                for entry in dashboard.get("valkyries", [])
                if entry.get("id") == request.valkyrieId
            ),
            {"id": request.valkyrieId},
        )
        operator_id = request.participantId or "operator"
        item = ReviewItem.new(
            kind=ReviewKind.AUTONOMY_CHANGE.value,
            requested_action="set_autonomy_mode",
            environment_id=str(valkyrie.get("environmentId") or ""),
            valkyrie_id=str(valkyrie.get("id") or request.valkyrieId),
            title=f"Set {request.valkyrieId} autonomy to {request.mode}",
            summary=request.reason or f"Operator set autonomy to {request.mode}",
            urgency=0.4,
            evidence={"mode": request.mode, "previous_mode": previous_mode},
            requested_by=operator_id,
            correlation_id=f"valkyrie-autonomy:{request.valkyrieId}",
        )
        item.decide(decision="approved", operator_id=operator_id, reason=request.reason)
        try:
            _delivery, event = await command_publisher.publish_review_decision(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("valkyrie autonomy command publish failed: %s", exc)
            event = None
        if event is not None:
            store.record_event(event)
        await _record_in_review_ledger(item)
        return store.dashboard()

    @router.get("/decisions")
    async def list_decisions(
        environment_id: str = "",
        valkyrie_id: str = "",
        operational_state: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        history = _require_history()
        rows, total = await history.store.list_decisions(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            operational_state=operational_state,
            limit=limit,
            offset=offset,
        )
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    @router.get("/decisions/{decision_id}")
    async def get_decision(decision_id: str) -> dict[str, Any]:
        history = _require_history()
        detail = await history.decision_detail(decision_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Decision not found")
        return detail

    @router.get("/signals/history")
    async def list_signal_history(
        environment_id: str = "",
        severity: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        history = _require_history()
        rows, total = await history.store.list_signals(
            environment_id=environment_id,
            severity=severity,
            limit=limit,
            offset=offset,
        )
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    @router.get("/learnings/stats/skills")
    async def learning_skill_stats(environment_id: str = "") -> dict[str, Any]:
        history = _require_history()
        skills = await history.skill_stats(environment_id=environment_id)
        return {"skills": skills}

    @router.get("/telemetry/events")
    async def list_telemetry_events(
        limit: int = 200,
        event_type: str = "",
        environment_id: str = "",
        valkyrie_id: str = "",
        contains: str = "",
    ) -> list[dict[str, Any]]:
        return store.telemetry_events(
            limit=limit,
            event_type=event_type,
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            contains=contains,
        )

    @router.post("/telemetry/events")
    async def record_telemetry_event(event: dict[str, Any], minimal: bool = False) -> Dashboard:
        store.record_event(event)
        if history_service is not None:
            try:
                await history_service.ingest_event(event)
            except Exception:
                logger.exception("valkyrie telemetry: history ingest failed")
        if review_service is not None:
            try:
                await review_service.ingest_event(event)
            except Exception:
                logger.exception("valkyrie telemetry: review queue ingest failed")
        if minimal:
            return {
                "accepted": True,
                "eventType": str(event.get("event_type") or event.get("eventType") or ""),
                "eventId": str(event.get("event_id") or event.get("eventId") or ""),
                "observedAt": _now(),
            }
        return store.dashboard()

    @router.get("/logs")
    async def list_logs() -> list[dict[str, Any]]:
        return store.logs()

    @router.get("/signals")
    async def signal_stream(replay_once: bool = False) -> StreamingResponse:
        async def generate():
            events = store.events()
            while True:
                for event in events:
                    yield f"data: {json.dumps(event)}\n\n"
                    if not replay_once:
                        await asyncio.sleep(0.75)
                if replay_once:
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(5)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return router
