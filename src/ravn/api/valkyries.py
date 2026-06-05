"""Resident Valkyrie dashboard projection for the Ravn API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from sleipnir.domain.events import SleipnirEvent

Dashboard = dict[str, Any]
logger = logging.getLogger(__name__)
RAW_SIGNAL_TELEMETRY_LIMIT = 1_000
CONTROL_TELEMETRY_LIMIT = 2_000
DASHBOARD_ENVIRONMENTS_JSON_ENV = "RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON"
DASHBOARD_ENVIRONMENTS_FILE_ENV = "RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_FILE"


class HuddleSendRequest(BaseModel):
    huddleId: str  # noqa: N815
    body: str


class LearningDecisionRequest(BaseModel):
    learningId: str  # noqa: N815
    reason: str = ""


class AutonomyUpdateRequest(BaseModel):
    valkyrieId: str  # noqa: N815
    mode: str
    reason: str = ""


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


def _catalog_raw() -> str:
    configured = os.environ.get(DASHBOARD_ENVIRONMENTS_JSON_ENV, "").strip()
    if configured:
        return configured
    path = os.environ.get(DASHBOARD_ENVIRONMENTS_FILE_ENV, "").strip()
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        logger.warning("could not read Valkyrie dashboard environment catalog %s: %s", path, exc)
        return ""


def _configured_environment_records() -> list[dict[str, Any]]:
    raw = _catalog_raw()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("invalid Valkyrie dashboard environment catalog JSON: %s", exc)
        return []
    records = parsed.get("environments") if isinstance(parsed, dict) else parsed
    if not isinstance(records, list):
        logger.warning(
            "Valkyrie dashboard environment catalog must be a list or object.environments"
        )
        return []
    return [record for record in records if isinstance(record, dict)]


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
        "valkyrie.state.updated",
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
    return str(
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
    if event_type == "valkyrie.wakefulness.changed":
        return "wakefulness"
    return "event"


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
    return {
        "id": str(
            payload.get("proposal_id")
            or payload.get("learning_id")
            or payload.get("dream_id")
            or event.get("event_id")
            or f"{event_type}:{_event_timestamp(event)}"
        ),
        "eventType": event_type,
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload),
        "dreamId": str(payload.get("dream_id") or ""),
        "title": str(
            details.get("title")
            or details.get("artifact_type")
            or event.get("summary")
            or event_type
        ),
        "status": status or str(details.get("status") or event_type.rsplit(".", 1)[-1]),
        "artifactType": str(details.get("artifact_type") or ""),
        "riskClass": str(details.get("risk_class") or ""),
        "policyDecision": str(details.get("policy_decision") or ""),
        "proposalsCreated": _payload_int(payload, "proposals_created"),
        "proposalsApplied": _payload_int(payload, "proposals_applied"),
        "proposalsDeferred": _payload_int(payload, "proposals_deferred"),
        "observedAt": _event_timestamp(event),
        "summary": str(event.get("summary") or ""),
    }


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
        details.get("action_capability")
        or payload.get("action_capability")
        or ""
    ).strip()
    if capability and capability.lower() not in {"none", "n/a", "no_action", "observe"}:
        return capability

    recommended = str(
        details.get("recommended_action")
        or payload.get("recommended_action")
        or ""
    ).strip()
    if not recommended or recommended.lower() in {"none", "n/a", "observe", "watch"}:
        return ""
    if "." in recommended or "_" in recommended:
        return recommended
    return ""


def _runtime_entry(
    event: dict[str, Any],
    payload: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    return {
        "environmentId": _event_environment_id(event, payload),
        "valkyrieId": _event_valkyrie_id(payload),
        "valkyrieName": _event_valkyrie_name(payload),
        "residentPersonality": str(payload.get("resident_personality") or ""),
        "sourceCount": payload.get("source_count", 0),
        "driveLoopEnabled": bool(payload.get("drive_loop_enabled")),
        "initiativeEnabled": bool(payload.get("initiative_enabled")),
        "pollIntervalSeconds": payload.get("poll_interval_seconds", 0),
        "observedAt": timestamp,
    }


def _merge_observed_runtime(dashboard: Dashboard) -> Dashboard:
    telemetry = dashboard.get("telemetry") if isinstance(dashboard.get("telemetry"), dict) else {}
    runtime = telemetry.get("runtime") if isinstance(telemetry.get("runtime"), list) else []
    observed_by_id = {
        str(entry.get("valkyrieId") or ""): entry
        for entry in runtime
        if isinstance(entry, dict) and str(entry.get("valkyrieId") or "")
    }
    observed_by_env: dict[str, dict[str, Any]] = {}
    for entry in runtime:
        if not isinstance(entry, dict):
            continue
        env_id = str(entry.get("environmentId") or "")
        if not env_id:
            continue
        observed_by_env[env_id] = entry
        observed_by_env[f"env-k8s-{env_id}"] = entry
        observed_by_env[f"env-host-{env_id}"] = entry
        observed_by_env[f"env-printer-{env_id}"] = entry
    for environment in dashboard.get("environments", []):
        if not isinstance(environment, dict):
            continue
        observed = observed_by_env.get(str(environment.get("id") or ""))
        environment["identitySource"] = "observed" if observed else "configured"
        if observed:
            environment["lastSignalAt"] = str(
                observed.get("observedAt") or environment.get("lastSignalAt") or ""
            )
            environment["wakefulCount"] = max(_as_int(environment.get("wakefulCount"), 0), 1)

    known_valkyrie_ids = set()
    for valkyrie in dashboard.get("valkyries", []):
        if not isinstance(valkyrie, dict):
            continue
        valkyrie_id = str(valkyrie.get("id") or "")
        known_valkyrie_ids.add(valkyrie_id)
        observed = observed_by_id.get(valkyrie_id)
        if observed is None:
            valkyrie["identitySource"] = "configured"
            continue
        if observed.get("valkyrieName"):
            valkyrie["name"] = str(observed["valkyrieName"])
        if observed.get("residentPersonality"):
            valkyrie["specialty"] = str(observed["residentPersonality"])
        valkyrie["identitySource"] = "observed"
        valkyrie["status"] = "online"
        valkyrie["lastObservedAt"] = str(observed.get("observedAt") or "")

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
                "wakefulness": "watching",
                "autonomyMode": "delegated" if observed.get("driveLoopEnabled") else "manual",
                "status": "online",
                "confidence": 0.0,
                "inboxSubjects": [],
                "toolCount": 0,
                "lastDreamAt": "",
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
            runtime_by_key[runtime_key] = runtime_entry
            llm = {
                "status": "configured",
                "model": str(payload.get("llm_model") or ""),
                "reflectionModel": str(payload.get("reflection_model") or ""),
                "postSessionReflectionEnabled": bool(
                    payload.get("post_session_reflection_enabled")
                ),
                "lastObservedAt": timestamp,
            }
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
                        "completed"
                        if outcome in {"success", "completed", "complete"}
                        else "failed"
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
                    status=str(
                        details.get("decision")
                        or details.get("verdict")
                        or "needed"
                    ),
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
            recent_learning.append(_learning_entry(event, payload))
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
        elif event_type == "valkyrie.wakefulness.changed":
            totals["wakefulnessChanges"] += 1
            recent_learning.append(_learning_entry(event, payload, status="wakefulness"))
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
            recent_learning,
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
                    _field(combined, "autonomyMode", "autonomy_mode", default="delegated")
                ),
                "status": str(_field(combined, "status", default="online")),
                "confidence": _as_float(_field(combined, "confidence", default=0.0)),
                "inboxSubjects": _as_string_list(
                    _field(combined, "inboxSubjects", "inbox_subjects", default=[])
                ),
                "toolCount": _as_int(_field(combined, "toolCount", "tool_count", default=0)),
                "lastDreamAt": str(_field(combined, "lastDreamAt", "last_dream_at", default="")),
                "lastActionAt": str(_field(combined, "lastActionAt", "last_action_at", default="")),
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


def _initial_dashboard() -> Dashboard:
    records = _configured_environment_records()
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
        "huddles": [],
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
    def __init__(self) -> None:
        self._dashboard = _initial_dashboard()
        self._poll_count = 0
        self._raw_signal_events: list[dict[str, Any]] = []
        self._control_events: list[dict[str, Any]] = []
        self._runtime_events: dict[str, dict[str, Any]] = {}

    def dashboard(self) -> Dashboard:
        self._refresh_live_report()
        return _merge_observed_runtime(deepcopy(self._dashboard))

    def record_event(self, event: SleipnirEvent | dict[str, Any]) -> None:
        event_data = _event_dict(event)
        if _is_runtime_event(event_data):
            raw_payload = event_data.get("payload")
            payload = raw_payload if isinstance(raw_payload, dict) else {}
            env_id = str(
                payload.get("environment_id")
                or payload.get("environmentId")
                or "unknown"
            )
            valkyrie_id = str(
                payload.get("valkyrie_id") or payload.get("valkyrieId") or "unknown"
            )
            self._runtime_events[f"{env_id}:{valkyrie_id}"] = event_data
        if _is_raw_signal_event(event_data):
            self._raw_signal_events.append(event_data)
            self._raw_signal_events = self._raw_signal_events[-RAW_SIGNAL_TELEMETRY_LIMIT:]
        else:
            self._control_events.append(event_data)
            self._control_events = self._control_events[-CONTROL_TELEMETRY_LIMIT:]
        self._touch()

    def environments(self) -> list[dict[str, Any]]:
        return self.dashboard()["environments"]

    def environment(self, environment_id: str) -> dict[str, Any]:
        environment = next(
            (
                entry
                for entry in self._dashboard["environments"]
                if entry["id"] == environment_id
            ),
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

    def join_huddle(self, huddle_id: str) -> dict[str, Any]:
        huddle = self._require_huddle(huddle_id)
        huddle["joined"] = True
        if "operator" not in huddle["participantIds"]:
            huddle["participantIds"].append("operator")
        huddle["lastActivityAt"] = _now()
        self._touch()
        return deepcopy(huddle)

    def leave_huddle(self, huddle_id: str) -> dict[str, Any]:
        huddle = self._require_huddle(huddle_id)
        huddle["joined"] = False
        huddle["participantIds"] = [
            entry for entry in huddle["participantIds"] if entry != "operator"
        ]
        huddle["lastActivityAt"] = _now()
        self._touch()
        return deepcopy(huddle)

    def send_huddle_message(self, request: HuddleSendRequest) -> dict[str, Any]:
        huddle = self._require_huddle(request.huddleId)
        message = {
            "id": f"message-operator-{len(huddle['messages']) + 1}",
            "huddleId": request.huddleId,
            "authorId": "operator",
            "authorName": "Operator",
            "body": request.body,
            "createdAt": _now(),
        }
        huddle["messages"].append(message)
        huddle["joined"] = True
        if "operator" not in huddle["participantIds"]:
            huddle["participantIds"].append("operator")
        huddle["lastActivityAt"] = message["createdAt"]
        self._touch()
        return deepcopy(message)

    def decide_learning(self, learning_id: str, status: str) -> dict[str, Any]:
        learning = self._require_learning(learning_id)
        learning["status"] = status
        self._touch()
        return deepcopy(learning)

    def update_autonomy(self, request: AutonomyUpdateRequest) -> Dashboard:
        valid_modes = {"manual", "supervised", "delegated", "yolo"}
        if request.mode not in valid_modes:
            raise HTTPException(status_code=422, detail="Unsupported autonomy mode")
        valkyrie = next(
            (
                entry
                for entry in self._dashboard["valkyries"]
                if entry["id"] == request.valkyrieId
            ),
            None,
        )
        if valkyrie is None:
            raise HTTPException(status_code=404, detail="Valkyrie not found")
        valkyrie["autonomyMode"] = request.mode
        self._touch()
        return self.dashboard()

    def events(self) -> list[dict[str, Any]]:
        return _signal_events(self._dashboard)

    def telemetry_events(self) -> list[dict[str, Any]]:
        telemetry = self.dashboard().get("telemetry", {})
        if not isinstance(telemetry, dict):
            return []
        events = telemetry.get("recentEvents", [])
        return deepcopy(events if isinstance(events, list) else [])

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
        retained_event_ids = {id(event) for event in telemetry_events}
        telemetry_events.extend(
            event for event in self._runtime_events.values() if id(event) not in retained_event_ids
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
        self._dashboard["updatedAt"] = observed_at

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
    ) -> None:
        self._projection = projection
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
            *(
                self._start_subscriber(label, subscriber)
                for label, subscriber in self._subscribers
            ),
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
                *(
                    self._start_subscriber(label, subscriber)
                    for label, subscriber in pending
                ),
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
                "password_env": (
                    parts[3] if len(parts) > 3 and parts[3] else default_password_env
                ),
            }
        )
    return specs


def build_nats_telemetry_subscription_from_env(
    projection: ValkyrieDashboardProjection,
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
        subscribers=subscribers,
        retry_interval_seconds=retry_interval_seconds,
        startup_delay_seconds=startup_delay_seconds,
        subscriber_start_timeout_seconds=subscriber_start_timeout_seconds,
        # Subscribe once to the environment stream and let the projection decide
        # what to count. Multiple JetStream push consumers with the same config
        # can silently miss delivery in the live NATS setup.
        event_types=["*"],
    )


def _env_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def create_valkyrie_router(
    projection: ValkyrieDashboardProjection | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ravn/valkyrie", tags=["Ravn Valkyries"])
    store = projection or ValkyrieDashboardProjection()

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
    async def join_huddle(huddle_id: str) -> dict[str, Any]:
        return store.join_huddle(huddle_id)

    @router.post("/huddles/{huddle_id}/leave")
    async def leave_huddle(huddle_id: str) -> dict[str, Any]:
        return store.leave_huddle(huddle_id)

    @router.post("/huddles/{huddle_id}/messages")
    async def send_huddle_message(
        huddle_id: str,
        request: HuddleSendRequest,
    ) -> dict[str, Any]:
        if request.huddleId != huddle_id:
            raise HTTPException(status_code=422, detail="Huddle id mismatch")
        return store.send_huddle_message(request)

    @router.post("/learnings/{learning_id}/adopt")
    async def adopt_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        return store.decide_learning(learning_id, "adopted")

    @router.post("/learnings/{learning_id}/reject")
    async def reject_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        return store.decide_learning(learning_id, "rejected")

    @router.post("/learnings/{learning_id}/override")
    async def override_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        return store.decide_learning(learning_id, "adopted")

    @router.post("/autonomy")
    async def update_autonomy(request: AutonomyUpdateRequest) -> Dashboard:
        return store.update_autonomy(request)

    @router.get("/telemetry/events")
    async def list_telemetry_events() -> list[dict[str, Any]]:
        return store.telemetry_events()

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
