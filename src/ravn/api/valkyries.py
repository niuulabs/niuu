"""Resident Valkyrie dashboard projection for the Ravn API.

The current implementation is a deterministic dev projection over the
resident Valkyrie Environment demo.  It intentionally uses the same HTTP and
SSE contract as the web console so start-dev exercises real service wiring
without inventing a second lifecycle.
"""

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

from ravn.demo.valkyrie_environment import DEMO_STARTED_AT, build_valkyrie_environment_demo
from sleipnir.domain.events import SleipnirEvent

Dashboard = dict[str, Any]
logger = logging.getLogger(__name__)
RAW_SIGNAL_TELEMETRY_LIMIT = 1_000
CONTROL_TELEMETRY_LIMIT = 2_000

K8S_CLUSTERS: tuple[dict[str, Any], ...] = (
    {"id": "valhalla", "name": "Valhalla", "health": "watch", "signals": 18, "unresolved": 2},
    {"id": "ymir", "name": "Ymir", "health": "watch", "signals": 22, "unresolved": 2},
    {"id": "eitri", "name": "Eitri", "health": "watch", "signals": 8, "unresolved": 1},
    {"id": "glitnir", "name": "Glitnir", "health": "healthy", "signals": 6, "unresolved": 0},
    {"id": "jarnvidr", "name": "Jarnvidr", "health": "watch", "signals": 9, "unresolved": 1},
    {"id": "noatun", "name": "Noatun", "health": "healthy", "signals": 5, "unresolved": 0},
    {"id": "valaskjalf", "name": "Valaskjalf", "health": "watch", "signals": 7, "unresolved": 1},
)


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


def _timestamp(offset_seconds: int) -> str:
    return (DEMO_STARTED_AT + timedelta(seconds=offset_seconds)).isoformat()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _live_report(last_observed_at: str, poll_count: int = 0) -> dict[str, Any]:
    base_messages = {
        "valhalla": 39_062,
        "ymir": 32_746,
        "eitri": 4_200,
        "glitnir": 3_100,
        "jarnvidr": 3_850,
        "noatun": 2_750,
        "valaskjalf": 3_400,
    }
    transports = []
    for index, cluster in enumerate(K8S_CLUSTERS):
        cluster_id = cluster["id"]
        messages = base_messages[cluster_id] + poll_count
        transports.append(
            {
                "id": f"transport-{cluster_id}",
                "label": f"{cluster['name']} k8s",
                "environmentId": f"env-k8s-{cluster_id}",
                "account": f"obs-{cluster_id}",
                "streamName": f"obs-{cluster_id}-events",
                "subjectPrefix": f"obs.{cluster_id}",
                "messageCount": messages,
                "signalCount": cluster["signals"] * 180,
                "activityCount": max(messages - 3_000, 0),
                "judgmentCount": 40 + index,
                "actionCount": 16 + index,
                "rejectedCount": 8 + index,
                "consumerFilterSubjects": [
                    f"obs.{cluster_id}.ravn.mesh.rpc.valkyrie_{cluster_id}_k8s",
                    f"obs.{cluster_id}.signal.kubernetes.event",
                    f"obs.{cluster_id}.ravn.mesh.valkyrie.judgment.>",
                    f"flock.k8s.{cluster_id}.>",
                ],
                "health": cluster["health"],
                "lastMessageAt": _timestamp(42 + index),
                "notes": [
                    f"Local operational signals stay on obs.{cluster_id}.",
                    "Judgments, actions, activity, and promoted learning project into "
                    "the k8s flock stream.",
                ],
            },
        )
    return {
        "title": "K8s flock routing",
        "status": "watch",
        "lastObservedAt": last_observed_at,
        "totalMessages": sum(entry["messageCount"] for entry in transports),
        "sharedStream": "flock-k8s-*-events",
        "routeSubject": "flock.k8s.>",
        "projectionMode": "mixed",
        "transports": transports,
        "findings": [
            "Existing NATS and Sleipnir paths are the bus; the flock view is a "
            "JetStream projection.",
            "Durable consumers are split per filter subject so RPC, local signals, "
            "and flock subjects can coexist.",
            "The UI should show both local environment health and flock-sharing health.",
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
            "flockMessages": 0,
        },
        "byEnvironment": [],
        "recentPolls": [],
        "recentTasks": [],
        "recentOutcomes": [],
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
    return str(event.get("event_type") or "") == "valkyrie.runtime.started"


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
    runtime: list[dict[str, Any]] = []
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
        env_id = str(
            payload.get("environment_id")
            or payload.get("environmentId")
            or event.get("tenant_id")
            or "unknown"
        )
        entry = _environment_telemetry_entry(by_environment, env_id)
        entry["lastObservedAt"] = max(entry["lastObservedAt"], timestamp)
        totals["eventsObserved"] += 1

        if event_type == "valkyrie.signal_poll.completed":
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
        elif event_type == "valkyrie.runtime.started":
            runtime.append(
                {
                    "environmentId": env_id,
                    "valkyrieId": payload.get("valkyrie_id", ""),
                    "sourceCount": payload.get("source_count", 0),
                    "driveLoopEnabled": bool(payload.get("drive_loop_enabled")),
                    "initiativeEnabled": bool(payload.get("initiative_enabled")),
                    "pollIntervalSeconds": payload.get("poll_interval_seconds", 0),
                    "observedAt": timestamp,
                }
            )
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
            recent_tasks.append(
                {
                    "environmentId": env_id,
                    "taskId": payload.get("task_id", ""),
                    "title": payload.get("title", ""),
                    "status": "dropped",
                    "reason": payload.get("reason", ""),
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
            if event_type == "learning.dream.started":
                totals["dreamCyclesStarted"] += 1
                entry["dreamCycles"] += 1
            elif event_type == "learning.dream.completed":
                totals["dreamCyclesCompleted"] += 1
            elif event_type == "learning.dream.failed":
                totals["dreamCyclesFailed"] += 1
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
    if totals["dreamCyclesStarted"] == 0:
        gaps.append("No verified dream-cycle events observed.")
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
        "runtime": sorted(
            runtime,
            key=lambda item: item.get("observedAt", ""),
            reverse=True,
        ),
        "llm": llm,
        "gaps": gaps,
    }


def _k8s_environment_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, cluster in enumerate(K8S_CLUSTERS):
        cluster_id = cluster["id"]
        entries.append(
            {
                "id": f"env-k8s-{cluster_id}",
                "name": f"{cluster['name']} k8s",
                "kind": "kubernetes",
                "health": cluster["health"],
                "flockId": "flock-k8s",
                "topologyNodeIds": [f"environment:k8s-cluster-{cluster_id}"],
                "signalCount": cluster["signals"],
                "unresolvedSignalCount": cluster["unresolved"],
                "wakefulCount": 1,
                "dreamingCount": 0,
                "lastSignalAt": _timestamp(18 + index * 3),
            },
        )
    return entries


def _k8s_valkyrie_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, cluster in enumerate(K8S_CLUSTERS):
        cluster_id = cluster["id"]
        entries.append(
            {
                "id": f"valkyrie-{cluster_id}-k8s",
                "name": f"{cluster['name']} Valkyrie",
                "environmentId": f"env-k8s-{cluster_id}",
                "flockId": "flock-k8s",
                "persona": "k8s-valkyrie",
                "specialty": "cluster event triage and flock learning exchange",
                "wakefulness": "watching",
                "autonomyMode": "delegated",
                "status": "online",
                "confidence": round(0.82 + min(index, 5) * 0.02, 2),
                "inboxSubjects": ["signal.kubernetes.*", "flock.k8s.*"],
                "toolCount": 12,
                "lastDreamAt": _timestamp(34 + index),
                "lastActionAt": _timestamp(22 + index),
            },
        )
    return entries


def _initial_dashboard() -> Dashboard:
    artifact = build_valkyrie_environment_demo()
    event_count = artifact.summary["event_count"]
    updated_at = _timestamp(event_count)

    return {
        "environments": [
            *_k8s_environment_entries(),
            {
                "id": "env-host-inbox",
                "name": "Host inbox",
                "kind": "host",
                "health": "healthy",
                "flockId": "flock-personal-ops",
                "topologyNodeIds": ["environment:host-inbox"],
                "signalCount": 10,
                "unresolvedSignalCount": 1,
                "wakefulCount": 1,
                "dreamingCount": 0,
                "lastSignalAt": _timestamp(31),
            },
            {
                "id": "env-printer-cell",
                "name": "Printer cell",
                "kind": "printer",
                "health": "degraded",
                "flockId": "flock-printers",
                "topologyNodeIds": ["environment:printer-cell-a"],
                "signalCount": 11,
                "unresolvedSignalCount": 2,
                "wakefulCount": 1,
                "dreamingCount": 0,
                "lastSignalAt": _timestamp(39),
            },
        ],
        "valkyries": [
            *_k8s_valkyrie_entries(),
            {
                "id": "valkyrie-host-email",
                "name": "Kara",
                "environmentId": "env-host-inbox",
                "flockId": "flock-personal-ops",
                "persona": "inbox-host-valkyrie",
                "specialty": "mail attention routing and reply drafting",
                "wakefulness": "awake",
                "autonomyMode": "supervised",
                "status": "online",
                "confidence": 0.83,
                "inboxSubjects": ["ravn.environment.signal.email.*"],
                "toolCount": 7,
                "lastDreamAt": _timestamp(25),
                "lastActionAt": _timestamp(29),
            },
            {
                "id": "valkyrie-printer-eir",
                "name": "Eir",
                "environmentId": "env-printer-cell",
                "flockId": "flock-printers",
                "persona": "printer-pi-valkyrie",
                "specialty": "printer telemetry and material readiness",
                "wakefulness": "watching",
                "autonomyMode": "delegated",
                "status": "blocked",
                "confidence": 0.79,
                "inboxSubjects": ["ravn.environment.signal.printer_telemetry.*"],
                "toolCount": 8,
                "lastDreamAt": _timestamp(36),
                "lastActionAt": _timestamp(37),
            },
        ],
        "flocks": [
            {
                "id": "flock-k8s",
                "name": "K8s Valkyrie flock",
                "domain": "kubernetes operations",
                "natsSubject": "flock.k8s.>",
                "environmentIds": [
                    f"env-k8s-{cluster['id']}" for cluster in K8S_CLUSTERS
                ],
                "valkyrieIds": [
                    f"valkyrie-{cluster['id']}-k8s" for cluster in K8S_CLUSTERS
                ],
                "learningIds": ["learning-k8s-rollout-noise"],
                "health": "watch",
                "lastExchangeAt": _timestamp(38),
            },
            {
                "id": "flock-personal-ops",
                "name": "Personal ops flock",
                "domain": "host and inbox operations",
                "natsSubject": "flock.personal.>",
                "environmentIds": ["env-host-inbox"],
                "valkyrieIds": ["valkyrie-host-email"],
                "learningIds": ["learning-inbox-vendor-thread"],
                "health": "healthy",
                "lastExchangeAt": _timestamp(31),
            },
            {
                "id": "flock-printers",
                "name": "Printer cell flock",
                "domain": "3D printer operations",
                "natsSubject": "flock.printers.>",
                "environmentIds": ["env-printer-cell"],
                "valkyrieIds": ["valkyrie-printer-eir"],
                "learningIds": ["learning-printer-resin-low"],
                "health": "degraded",
                "lastExchangeAt": _timestamp(39),
            },
        ],
        "signals": [
            {
                "id": "signal-k8s-noisy-rollout",
                "environmentId": "env-k8s-valhalla",
                "source": "kube-event-stream",
                "subject": "ravn.environment.signal.kubernetes.pulled",
                "summary": "Expected image pull noise during checkout rollout was suppressed.",
                "severity": "info",
                "status": "resolved",
                "receivedAt": _timestamp(1),
                "assignedValkyrieId": "valkyrie-valhalla-k8s",
                "labels": ["rollout", "noise"],
            },
            {
                "id": "signal-k8s-checkout-probe",
                "environmentId": "env-k8s-valhalla",
                "source": "kube-event-stream",
                "subject": "ravn.environment.signal.kubernetes.probe",
                "summary": "Checkout API readiness probes are failing above learned baseline.",
                "severity": "warning",
                "status": "acting",
                "receivedAt": _timestamp(18),
                "assignedValkyrieId": "valkyrie-valhalla-k8s",
                "labels": ["checkout", "probe"],
            },
            {
                "id": "signal-host-important-mail",
                "environmentId": "env-host-inbox",
                "source": "mailbox-watch",
                "subject": "ravn.environment.signal.email.important",
                "summary": "Vendor thread looks action-worthy and has a draft response prepared.",
                "severity": "notice",
                "status": "triaged",
                "receivedAt": _timestamp(31),
                "assignedValkyrieId": "valkyrie-host-email",
                "labels": ["email", "draft"],
            },
            {
                "id": "signal-printer-resin-low",
                "environmentId": "env-printer-cell",
                "source": "printer-pi-telemetry",
                "subject": "ravn.environment.signal.printer_telemetry.material",
                "summary": "Resin is below learned threshold for the next queued print.",
                "severity": "critical",
                "status": "acting",
                "receivedAt": _timestamp(39),
                "assignedValkyrieId": "valkyrie-printer-eir",
                "labels": ["resin", "queue"],
            },
        ],
        "operationalStates": [
            {
                "id": "state-k8s-checkout",
                "environmentId": "env-k8s-valhalla",
                "name": "Checkout rollout",
                "desired": "Available replicas match rollout target",
                "observed": (
                    "Readiness failures above baseline; waiting on Valhalla action proposal"
                ),
                "drift": "minor",
                "maintainedBy": ["valkyrie-valhalla-k8s"],
                "updatedAt": _timestamp(21),
            },
            {
                "id": "state-host-inbox-attention",
                "environmentId": "env-host-inbox",
                "name": "Inbox attention",
                "desired": "Only actionable mail reaches operator",
                "observed": "One vendor thread routed for review with draft",
                "drift": "none",
                "maintainedBy": ["valkyrie-host-email"],
                "updatedAt": _timestamp(31),
            },
            {
                "id": "state-k8s-ymir",
                "environmentId": "env-k8s-ymir",
                "name": "Ymir control plane",
                "desired": "Hub services remain schedulable and healthy",
                "observed": "BackoffLimitExceeded and disk-pressure signals are under watch",
                "drift": "minor",
                "maintainedBy": ["valkyrie-ymir-k8s"],
                "updatedAt": _timestamp(37),
            },
            {
                "id": "state-printer-materials",
                "environmentId": "env-printer-cell",
                "name": "Material readiness",
                "desired": "Queued prints have enough resin and clean vats",
                "observed": "Resin low blocks next queued print",
                "drift": "major",
                "maintainedBy": ["valkyrie-printer-eir"],
                "updatedAt": _timestamp(39),
            },
        ],
        "judgments": [
            {
                "id": "judgment-k8s-probe",
                "environmentId": "env-k8s-valhalla",
                "signalId": "signal-k8s-checkout-probe",
                "valkyrieId": "valkyrie-valhalla-k8s",
                "verdict": "act",
                "confidence": 0.88,
                "rationale": (
                    "Probe failures correlate with one rollout and match a known remediation path."
                ),
                "createdAt": _timestamp(19),
            },
            {
                "id": "judgment-host-mail",
                "environmentId": "env-host-inbox",
                "signalId": "signal-host-important-mail",
                "valkyrieId": "valkyrie-host-email",
                "verdict": "escalate",
                "confidence": 0.82,
                "rationale": "Needs human approval before sending a reply.",
                "createdAt": _timestamp(32),
            },
        ],
        "courtDecisions": [
            {
                "id": "decision-k8s-rollout",
                "environmentId": "env-k8s-valhalla",
                "title": "Patch checkout rollout probe budget",
                "status": "approved",
                "risk": "medium",
                "decidedBy": ["valkyrie-valhalla-k8s"],
                "createdAt": _timestamp(22),
            },
            {
                "id": "decision-printer-resin",
                "environmentId": "env-printer-cell",
                "title": "Pause queue until resin is replenished",
                "status": "executed",
                "risk": "low",
                "decidedBy": ["valkyrie-printer-eir"],
                "createdAt": _timestamp(40),
            },
        ],
        "actions": [
            {
                "id": "action-k8s-rollout-check",
                "environmentId": "env-k8s-valhalla",
                "title": "Collect rollout events and pod logs",
                "status": "succeeded",
                "risk": "low",
                "ownerValkyrieId": "valkyrie-valhalla-k8s",
                "startedAt": _timestamp(20),
                "finishedAt": _timestamp(21),
            },
            {
                "id": "action-printer-pause",
                "environmentId": "env-printer-cell",
                "title": "Pause printer queue",
                "status": "succeeded",
                "risk": "low",
                "ownerValkyrieId": "valkyrie-printer-eir",
                "startedAt": _timestamp(40),
                "finishedAt": _timestamp(41),
            },
        ],
        "huddles": [
            {
                "id": "huddle-valhalla-now",
                "environmentId": "env-k8s-valhalla",
                "title": "Checkout rollout huddle",
                "status": "open",
                "participantIds": ["valkyrie-valhalla-k8s"],
                "joined": False,
                "messages": [
                    {
                        "id": "message-k8s-1",
                        "huddleId": "huddle-valhalla-now",
                        "authorId": "valkyrie-valhalla-k8s",
                        "authorName": "Valhalla Valkyrie",
                        "body": "Readiness failures are isolated to checkout-api v42.",
                        "createdAt": _timestamp(21),
                    }
                ],
                "lastActivityAt": _timestamp(22),
            },
            {
                "id": "huddle-printer-resin",
                "environmentId": "env-printer-cell",
                "title": "Printer queue hold",
                "status": "quiet",
                "participantIds": ["valkyrie-printer-eir"],
                "joined": False,
                "messages": [
                    {
                        "id": "message-printer-1",
                        "huddleId": "huddle-printer-resin",
                        "authorId": "valkyrie-printer-eir",
                        "authorName": "Eir",
                        "body": "Queue is paused until resin is replenished.",
                        "createdAt": _timestamp(40),
                    }
                ],
                "lastActivityAt": _timestamp(40),
            },
        ],
        "learnings": [
            {
                "id": "learning-k8s-rollout-noise",
                "title": "Suppress expected rollout image-pull noise",
                "summary": (
                    f"Derived from {event_count} demo events across the ODIN "
                    "signal-to-learning chain."
                ),
                "scope": "flock",
                "status": "canary",
                "sourceEnvironmentId": "env-k8s-valhalla",
                "sourceValkyrieId": "valkyrie-valhalla-k8s",
                "targetFlockId": "flock-k8s",
                "confidence": 0.9,
                "evaluation": "Passed replay against k8s cluster A and B event fixtures.",
                "negativeTransferRisk": "medium",
                "redaction": "none",
                "promotedTool": "rollout-noise-classifier",
                "createdAt": _timestamp(38),
            },
            {
                "id": "learning-inbox-vendor-thread",
                "title": "Vendor thread attention heuristic",
                "summary": "Repeated sender plus contract keywords should draft but not send.",
                "scope": "environment",
                "status": "candidate",
                "sourceEnvironmentId": "env-host-inbox",
                "sourceValkyrieId": "valkyrie-host-email",
                "targetFlockId": "flock-personal-ops",
                "confidence": 0.76,
                "evaluation": "Needs operator feedback on drafted reply.",
                "negativeTransferRisk": "low",
                "redaction": "partial",
                "createdAt": _timestamp(33),
            },
            {
                "id": "learning-printer-resin-low",
                "title": "Pause queue on material deficit",
                "summary": (
                    "Printer cells may pause locally when telemetry predicts failed queued work."
                ),
                "scope": "flock",
                "status": "adopted",
                "sourceEnvironmentId": "env-printer-cell",
                "sourceValkyrieId": "valkyrie-printer-eir",
                "targetFlockId": "flock-printers",
                "confidence": 0.84,
                "evaluation": "Action is reversible and low risk.",
                "negativeTransferRisk": "low",
                "redaction": "none",
                "promotedTool": "printer-queue-pauser",
                "createdAt": _timestamp(41),
            },
        ],
        "liveReport": _live_report(updated_at),
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
        return deepcopy(self._dashboard)

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
        self._dashboard["liveReport"] = _live_report(observed_at, self._poll_count)
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
        subscriber: Any,
        event_types: list[str],
    ) -> None:
        self._projection = projection
        self._subscriber = subscriber
        self._event_types = event_types
        self._subscription: Any | None = None

    async def start(self) -> None:
        await self._subscriber.start()
        self._subscription = await self._subscriber.subscribe(self._event_types, self._handle)
        logger.info(
            "valkyrie_dashboard: subscribed to telemetry events: %s",
            ", ".join(self._event_types),
        )

    async def stop(self) -> None:
        if self._subscription is not None:
            with contextlib.suppress(Exception):
                await self._subscription.unsubscribe()
            self._subscription = None
        if hasattr(self._subscriber, "stop"):
            await self._subscriber.stop()

    async def _handle(self, event: SleipnirEvent) -> None:
        self._projection.record_event(event)


def build_nats_telemetry_subscription_from_env(
    projection: ValkyrieDashboardProjection,
) -> ValkyrieTelemetrySubscription | None:
    """Build the optional dashboard telemetry NATS consumer from environment vars."""
    servers_raw = os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_URL", "").strip()
    if not servers_raw:
        return None

    from sleipnir.adapters.nats_transport import NatsSubscriber  # noqa: PLC0415

    servers = [entry.strip() for entry in servers_raw.split(",") if entry.strip()]
    stream_name = os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_STREAM", "ravn_environment")
    subject_prefix = os.environ.get(
        "RAVN_VALKYRIE_TELEMETRY_SUBJECT_PREFIX",
        "ravn.environment",
    )
    consumer_group = os.environ.get(
        "RAVN_VALKYRIE_TELEMETRY_CONSUMER_GROUP",
        "ravn-valkyrie-dashboard",
    )
    password = os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_PASSWORD", "")
    return ValkyrieTelemetrySubscription(
        projection=projection,
        subscriber=NatsSubscriber(
            servers=servers,
            stream_name=stream_name,
            subject_prefix=subject_prefix,
            consumer_group=consumer_group,
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
            user=os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_USER", ""),
            password=password,
            token=os.environ.get("RAVN_VALKYRIE_TELEMETRY_NATS_TOKEN", ""),
            nkeys_seed_file=os.environ.get("RAVN_VALKYRIE_TELEMETRY_NKEYS_SEED_FILE", ""),
            nkeys_seed=os.environ.get("RAVN_VALKYRIE_TELEMETRY_NKEYS_SEED", ""),
        ),
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
