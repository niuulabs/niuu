"""Resident Valkyrie dashboard projection for the Ravn API.

The current implementation is a deterministic dev projection over the
resident Valkyrie Environment demo.  It intentionally uses the same HTTP and
SSE contract as the web console so start-dev exercises real service wiring
without inventing a second lifecycle.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ravn.demo.valkyrie_environment import DEMO_STARTED_AT, build_valkyrie_environment_demo

Dashboard = dict[str, Any]


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
    valhalla_messages = 39_062 + poll_count
    ymir_messages = 32_746 + poll_count
    return {
        "title": "K8s flock routing",
        "status": "watch",
        "lastObservedAt": last_observed_at,
        "totalMessages": valhalla_messages + ymir_messages,
        "sharedStream": "flock-k8s-events",
        "routeSubject": "flock.k8s.>",
        "projectionMode": "mixed",
        "transports": [
            {
                "id": "transport-valhalla",
                "label": "Valhalla k8s",
                "environmentId": "env-k8s-valhalla",
                "account": "obs-valhalla",
                "streamName": "obs-valhalla-events",
                "subjectPrefix": "obs.valhalla",
                "messageCount": valhalla_messages,
                "signalCount": 3_280,
                "activityCount": 35_679 + poll_count,
                "judgmentCount": 74,
                "actionCount": 29,
                "rejectedCount": 18,
                "consumerFilterSubjects": [
                    "obs.valhalla.ravn.mesh.rpc.valkyrie_valhalla_k8s",
                    "obs.valhalla.ravn.mesh.signal.kubernetes.event",
                    "obs.valhalla.ravn.mesh.valkyrie.judgment.>",
                    "flock.k8s.>",
                ],
                "health": "watch",
                "lastMessageAt": _timestamp(42),
                "notes": [
                    "Local operational signals stay on obs.valhalla.",
                    "Judgments, actions, activity, and promoted learning project into "
                    "the k8s flock stream.",
                ],
            },
            {
                "id": "transport-ymir",
                "label": "Ymir k8s",
                "environmentId": "env-k8s-ymir",
                "account": "obs-ymir",
                "streamName": "obs-ymir-events",
                "subjectPrefix": "obs.ymir",
                "messageCount": ymir_messages,
                "signalCount": 5_133,
                "activityCount": 27_522 + poll_count,
                "judgmentCount": 67,
                "actionCount": 24,
                "rejectedCount": 15,
                "consumerFilterSubjects": [
                    "obs.ymir.ravn.mesh.rpc.valkyrie_ymir_k8s",
                    "obs.ymir.ravn.mesh.signal.kubernetes.event",
                    "obs.ymir.ravn.mesh.valkyrie.judgment.>",
                    "flock.k8s.>",
                ],
                "health": "watch",
                "lastMessageAt": _timestamp(43),
                "notes": [
                    "Ymir remains the observability hub.",
                    "Flock stream consumers read the shared k8s projection without "
                    "copying the signal bus.",
                ],
            },
        ],
        "findings": [
            "Existing NATS and Sleipnir paths are the bus; the flock view is a "
            "JetStream projection.",
            "Durable consumers are split per filter subject so RPC, local signals, "
            "and flock subjects can coexist.",
            "The UI should show both local environment health and flock-sharing health.",
        ],
    }


def _initial_dashboard() -> Dashboard:
    artifact = build_valkyrie_environment_demo()
    event_count = artifact.summary["event_count"]
    updated_at = _timestamp(event_count)

    return {
        "environments": [
            {
                "id": "env-k8s-valhalla",
                "name": "Valhalla k8s",
                "kind": "kubernetes",
                "health": "watch",
                "flockId": "flock-k8s",
                "topologyNodeIds": ["environment:k8s-cluster-a", "environment:k8s-cluster-b"],
                "signalCount": 18,
                "unresolvedSignalCount": 2,
                "wakefulCount": 2,
                "dreamingCount": 1,
                "lastSignalAt": _timestamp(18),
            },
            {
                "id": "env-k8s-ymir",
                "name": "Ymir k8s",
                "kind": "kubernetes",
                "health": "watch",
                "flockId": "flock-k8s",
                "topologyNodeIds": ["environment:k8s-cluster-ymir"],
                "signalCount": 22,
                "unresolvedSignalCount": 2,
                "wakefulCount": 1,
                "dreamingCount": 0,
                "lastSignalAt": _timestamp(37),
            },
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
            {
                "id": "valkyrie-valhalla-sigrun",
                "name": "Sigrun",
                "environmentId": "env-k8s-valhalla",
                "flockId": "flock-k8s",
                "persona": "k8s-valkyrie",
                "specialty": "cluster event triage and safe remediation",
                "wakefulness": "watching",
                "autonomyMode": "delegated",
                "status": "online",
                "confidence": 0.91,
                "inboxSubjects": ["ravn.environment.signal.kubernetes.*"],
                "toolCount": 12,
                "lastDreamAt": _timestamp(34),
                "lastActionAt": _timestamp(22),
            },
            {
                "id": "valkyrie-valhalla-runa",
                "name": "Runa",
                "environmentId": "env-k8s-valhalla",
                "flockId": "flock-k8s",
                "persona": "k8s-valkyrie",
                "specialty": "rollout noise suppression and baseline learning",
                "wakefulness": "dreaming",
                "autonomyMode": "supervised",
                "status": "busy",
                "confidence": 0.86,
                "inboxSubjects": ["ravn.environment.learning.*"],
                "toolCount": 9,
                "lastDreamAt": _timestamp(38),
                "lastActionAt": _timestamp(20),
            },
            {
                "id": "valkyrie-ymir-k8s",
                "name": "Mist",
                "environmentId": "env-k8s-ymir",
                "flockId": "flock-k8s",
                "persona": "k8s-valkyrie",
                "specialty": "hub cluster event triage and flock learning exchange",
                "wakefulness": "watching",
                "autonomyMode": "delegated",
                "status": "online",
                "confidence": 0.87,
                "inboxSubjects": ["ravn.environment.signal.kubernetes.*", "flock.k8s.*"],
                "toolCount": 12,
                "lastDreamAt": _timestamp(35),
                "lastActionAt": _timestamp(36),
            },
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
                "environmentIds": ["env-k8s-valhalla", "env-k8s-ymir"],
                "valkyrieIds": [
                    "valkyrie-valhalla-sigrun",
                    "valkyrie-valhalla-runa",
                    "valkyrie-ymir-k8s",
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
                "assignedValkyrieId": "valkyrie-valhalla-runa",
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
                "assignedValkyrieId": "valkyrie-valhalla-sigrun",
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
                "observed": "Readiness failures above baseline; waiting on Sigrun action proposal",
                "drift": "minor",
                "maintainedBy": ["valkyrie-valhalla-sigrun"],
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
                "valkyrieId": "valkyrie-valhalla-sigrun",
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
                "decidedBy": ["valkyrie-valhalla-sigrun", "valkyrie-valhalla-runa"],
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
                "ownerValkyrieId": "valkyrie-valhalla-sigrun",
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
                "participantIds": ["valkyrie-valhalla-sigrun", "valkyrie-valhalla-runa"],
                "joined": False,
                "messages": [
                    {
                        "id": "message-k8s-1",
                        "huddleId": "huddle-valhalla-now",
                        "authorId": "valkyrie-valhalla-sigrun",
                        "authorName": "Sigrun",
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
                "sourceValkyrieId": "valkyrie-valhalla-runa",
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

    def dashboard(self) -> Dashboard:
        self._refresh_live_report()
        return deepcopy(self._dashboard)

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
        self._dashboard["liveReport"] = _live_report(observed_at, self._poll_count)
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
