"""Telemetry normalization and dashboard projection for resident Valkyries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ravn.domain.valkyrie_history import canonical_environment_id


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
        "source": "unavailable",
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
            "No runtime-derived signals, judgments, actions, huddles, or learnings are available.",
            (
                "Deploy runtime telemetry and wire the API/dashboard consumer before treating "
                "counts as live."
            ),
        ],
    }
