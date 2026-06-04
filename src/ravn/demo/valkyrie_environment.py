"""Deterministic resident Valkyrie Environment demo.

This module builds a stable event chain for the resident Valkyrie MVP:

signal -> state -> judgment/action -> ODIN decision -> huddle/human feedback
-> dream learning -> Flock sharing/adoption.

It intentionally uses the canonical Sleipnir event catalog factories rather
than demo-only event shapes, so the JSONL artifact can be replayed through the
same NATS/Sleipnir paths used by the runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    attention_decided,
    attention_decision_made,
    environment_state_changed,
    feedback_recorded,
    learning_adoption_recorded,
    learning_promoted,
    odin_court_decided,
    participant_joined,
    participant_left,
    room_closed,
    room_context_snapshot_recorded,
    room_message_recorded,
    room_opened,
    room_transcript_recorded,
    signal_received,
    valkyrie_action_completed,
    valkyrie_action_proposed,
    valkyrie_judgment_proposed,
    valkyrie_state_updated,
)
from sleipnir.domain.events import SleipnirEvent

DEMO_STARTED_AT = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
DEFAULT_STREAM_NAME = "ravn_environment"
DEFAULT_SUBJECT_PREFIX = "odin.valkyrie.demo"


class AsyncPublisher(Protocol):
    async def publish(self, event: SleipnirEvent) -> None: ...


@dataclass(frozen=True)
class DemoArtifact:
    """Materialized deterministic demo output."""

    events: list[SleipnirEvent]
    lineage: dict[str, dict[str, list[str]]]
    summary: dict[str, Any]


class EventBuilder:
    """Small helper that stamps catalog events with stable IDs and timestamps."""

    def __init__(self) -> None:
        self._events: list[SleipnirEvent] = []
        self._offset = 0

    @property
    def events(self) -> list[SleipnirEvent]:
        return self._events

    def add(
        self,
        event: SleipnirEvent,
        *,
        event_id: str,
        nats_subject: str,
        tags: Sequence[str] = (),
    ) -> SleipnirEvent:
        event.event_id = event_id
        event.timestamp = DEMO_STARTED_AT + timedelta(seconds=self._offset)
        event.payload = {
            **event.payload,
            "demo_id": "resident-valkyrie-environment-mvp",
            "nats_subject": nats_subject,
            "tags": list(tags),
        }
        self._offset += 1
        self._events.append(event)
        return event


def _subject(environment_id: str, event_type: str) -> str:
    return f"odin.environment.{environment_id}.{event_type}"


def _flock_subject(flock_id: str, suffix: str) -> str:
    return f"flock.{flock_id}.{suffix}"


def _source(valkyrie_id: str) -> str:
    return f"valkyrie:{valkyrie_id}"


def _lineage_bucket() -> dict[str, list[str]]:
    return {
        "signal_refs": [],
        "state_refs": [],
        "judgment_refs": [],
        "court_decision_refs": [],
        "attention_refs": [],
        "action_refs": [],
        "room_refs": [],
        "feedback_refs": [],
        "learning_refs": [],
        "flock_adoption_refs": [],
    }


def _build_lineage(events: Iterable[SleipnirEvent]) -> dict[str, dict[str, list[str]]]:
    lineage: dict[str, dict[str, list[str]]] = {}
    for event in events:
        root = event.correlation_id or event.event_id
        bucket = lineage.setdefault(root, _lineage_bucket())
        event_type = event.event_type
        if event_type.startswith("signal."):
            bucket["signal_refs"].append(event.event_id)
        elif event_type.startswith("environment."):
            bucket["state_refs"].append(event.event_id)
        elif event_type.startswith("valkyrie.judgment."):
            bucket["judgment_refs"].append(event.event_id)
        elif event_type.startswith("odin."):
            bucket["court_decision_refs"].append(event.event_id)
        elif event_type.startswith("attention."):
            bucket["attention_refs"].append(event.event_id)
        elif event_type.startswith("valkyrie.action."):
            bucket["action_refs"].append(event.event_id)
        elif event_type.startswith("room.") or event_type.startswith("participant."):
            bucket["room_refs"].append(event.event_id)
        elif event_type.startswith("feedback."):
            bucket["feedback_refs"].append(event.event_id)
        elif event_type.startswith("learning.promoted"):
            bucket["learning_refs"].append(event.event_id)
        elif event_type.startswith("learning.adoption"):
            bucket["flock_adoption_refs"].append(event.event_id)
    return lineage


def _build_summary(events: Sequence[SleipnirEvent], lineage: dict[str, Any]) -> dict[str, Any]:
    environments = sorted(
        {
            str(event.payload.get("environment_id"))
            for event in events
            if event.payload.get("environment_id")
        }
    )
    event_counts: dict[str, int] = {}
    for event in events:
        event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1
    return {
        "demo_id": "resident-valkyrie-environment-mvp",
        "started_at": DEMO_STARTED_AT.isoformat(),
        "event_count": len(events),
        "environment_ids": environments,
        "flock_ids": ["k8s"],
        "event_counts": event_counts,
        "lineage_roots": sorted(lineage),
        "deterministic": True,
    }


def build_valkyrie_environment_demo() -> DemoArtifact:
    """Build a deterministic resident Valkyrie demo artifact."""

    builder = EventBuilder()

    # k8s cluster A: a noisy deploy event is suppressed.
    root_noise = "demo:k8s-a:noisy-deploy"
    noise_signal = builder.add(
        signal_received(
            environment_id="k8s-cluster-a",
            environment_type="k8s",
            signal_source="kube-event-stream",
            signal_kind="kubernetes",
            severity="info",
            data={
                "fixture": "noisy_deploy",
                "namespace": "checkout",
                "object": "deployment/checkout-api",
                "reason": "Pulled",
            },
            source="adapter:k8s-cluster-a",
            confidence=0.99,
            correlation_id=root_noise,
        ),
        event_id="demo-k8s-a-noisy-signal",
        nats_subject=_subject("k8s-cluster-a", registry.SIGNAL_KUBERNETES_EVENT),
        tags=("k8s", "noise"),
    )
    noisy_judgment = builder.add(
        valkyrie_judgment_proposed(
            environment_id="k8s-cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            attention_tier="suppress",
            recommended_action="record_only",
            authority_boundary="delegated",
            confidence=0.94,
            operational_state="nominal",
            rationale="Image pull events during a known rollout are normal for this cluster.",
            signal_refs=[noise_signal.event_id],
            evidence=[{"event_id": noise_signal.event_id, "reason": "expected_rollout"}],
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_noise,
            causation_id=noise_signal.event_id,
        ),
        event_id="demo-k8s-a-noisy-judgment",
        nats_subject=_subject("k8s-cluster-a", registry.VALKYRIE_JUDGMENT_PROPOSED),
        tags=("k8s", "judgment", "noise"),
    )
    builder.add(
        attention_decided(
            environment_id="k8s-cluster-a",
            target_event_id=noisy_judgment.event_id,
            attention_tier="suppress",
            route="memory_only",
            reason="No operator attention needed for expected rollout noise.",
            source="odin:attention-router",
            correlation_id=root_noise,
            causation_id=noisy_judgment.event_id,
        ),
        event_id="demo-k8s-a-noisy-attention",
        nats_subject=_subject("k8s-cluster-a", registry.ATTENTION_SUPPRESSED),
        tags=("attention", "noise"),
    )

    # k8s cluster A: real pod failure opens huddle, executes action, and learns.
    root_failure = "demo:k8s-a:pod-failure"
    pod_signal = builder.add(
        signal_received(
            environment_id="k8s-cluster-a",
            environment_type="k8s",
            signal_source="kube-event-stream",
            signal_kind="kubernetes",
            severity="critical",
            data={
                "fixture": "pod_failure",
                "namespace": "payments",
                "object": "pod/payments-api-77",
                "reason": "OOMKilled",
                "restarts": 5,
                "queue_depth": 780,
            },
            source="adapter:k8s-cluster-a",
            confidence=0.96,
            correlation_id=root_failure,
        ),
        event_id="demo-k8s-a-pod-failure-signal",
        nats_subject=_subject("k8s-cluster-a", registry.SIGNAL_KUBERNETES_EVENT),
        tags=("k8s", "failure"),
    )
    state = builder.add(
        environment_state_changed(
            environment_id="k8s-cluster-a",
            environment_type="k8s",
            previous_state="nominal",
            new_state="degraded",
            severity="critical",
            confidence=0.93,
            evidence=[{"event_id": pod_signal.event_id}, {"metric": "queue_depth", "value": 780}],
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_failure,
            causation_id=pod_signal.event_id,
        ),
        event_id="demo-k8s-a-state-degraded",
        nats_subject=_subject("k8s-cluster-a", registry.ENVIRONMENT_STATE_CHANGED),
        tags=("state", "k8s"),
    )
    builder.add(
        valkyrie_state_updated(
            environment_id="k8s-cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            previous_state="watching",
            new_state="awake",
            reason="critical signal crossed action threshold",
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_failure,
            causation_id=state.event_id,
        ),
        event_id="demo-k8s-a-valkyrie-awake",
        nats_subject=_subject("k8s-cluster-a", registry.VALKYRIE_STATE_UPDATED),
        tags=("wakefulness",),
    )
    judgment = builder.add(
        valkyrie_judgment_proposed(
            environment_id="k8s-cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            attention_tier="urgent",
            recommended_action="restart_and_raise_memory",
            authority_boundary="delegated",
            confidence=0.91,
            operational_state="payments degraded by OOMKilled loop",
            rationale="OOMKilled plus rising queue depth is a known unsafe state.",
            signal_refs=[pod_signal.event_id],
            evidence=[{"event_id": state.event_id}, {"cluster": "A", "queue_depth": 780}],
            target_surfaces=["valkyrie-ui", "skuld-room"],
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_failure,
            causation_id=state.event_id,
        ),
        event_id="demo-k8s-a-judgment",
        nats_subject=_subject("k8s-cluster-a", registry.VALKYRIE_JUDGMENT_PROPOSED),
        tags=("judgment", "k8s"),
    )
    action = builder.add(
        valkyrie_action_proposed(
            environment_id="k8s-cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            action_id="act-k8s-a-payments-memory",
            capability="k8s.patch_deployment_resources",
            action_authority="delegated",
            target={"namespace": "payments", "deployment": "payments-api", "memory": "768Mi"},
            rationale="Raise memory limit and restart pods in the degraded deployment.",
            evidence=[{"event_id": judgment.event_id}],
            target_surfaces=["valkyrie-ui"],
            dry_run=False,
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_failure,
            causation_id=judgment.event_id,
        ),
        event_id="demo-k8s-a-action-proposed",
        nats_subject=_subject("k8s-cluster-a", registry.VALKYRIE_ACTION_PROPOSED),
        tags=("action", "k8s"),
    )
    court = builder.add(
        odin_court_decided(
            environment_id="k8s-cluster-a",
            court_id="court-k8s-a-payments",
            decision="approve_action_and_open_huddle",
            authority_boundary="delegated",
            dissent=[],
            source="odin:court",
            correlation_id=root_failure,
            causation_id=action.event_id,
        ),
        event_id="demo-k8s-a-court",
        nats_subject=_subject("k8s-cluster-a", registry.ODIN_COURT_DECIDED),
        tags=("odin", "court"),
    )
    builder.add(
        attention_decision_made(
            environment_id="k8s-cluster-a",
            root_correlation_id=root_failure,
            decision="open_huddle",
            tier="urgent",
            action_authorization="delegated",
            escalation_path="valkyrie-ui",
            huddle_id="huddle-k8s-a-payments",
            judgment_refs=[judgment.event_id],
            action_refs=[action.event_id],
            dissent=[],
            evidence=[{"event_id": court.event_id}],
            rationale="Action is in delegated boundary, but operator should see degraded payments.",
            audit_ref="mimir://audit/valkyrie/demo/k8s-a-payments",
            source="odin:attention-router",
            correlation_id=root_failure,
            causation_id=court.event_id,
        ),
        event_id="demo-k8s-a-attention-decision",
        nats_subject=_subject("k8s-cluster-a", registry.ATTENTION_DECISION_MADE),
        tags=("attention", "huddle"),
    )
    room = builder.add(
        room_opened(
            environment_id="k8s-cluster-a",
            room_id="huddle-k8s-a-payments",
            purpose="Review delegated remediation for payments OOMKilled loop.",
            participants=["valkyrie:k8s-a"],
            source="skuld:rooms",
            correlation_id=root_failure,
            causation_id=court.event_id,
        ),
        event_id="demo-k8s-a-room-opened",
        nats_subject=_subject("k8s-cluster-a", registry.ROOM_OPENED),
        tags=("room", "huddle"),
    )
    joined = builder.add(
        participant_joined(
            environment_id="k8s-cluster-a",
            participant_id="human:jozef",
            participant_type="human",
            display_name="Jozef",
            capabilities=["approve", "teach", "respond"],
            source="skuld:rooms",
            correlation_id=root_failure,
            causation_id=room.event_id,
        ),
        event_id="demo-k8s-a-human-joined",
        nats_subject=_subject("k8s-cluster-a", registry.PARTICIPANT_JOINED),
        tags=("room", "human"),
    )
    snapshot = builder.add(
        room_context_snapshot_recorded(
            environment_id="k8s-cluster-a",
            room_id="huddle-k8s-a-payments",
            root_correlation_id=root_failure,
            active_state={"state": "degraded", "deployment": "payments-api"},
            signal_refs=[pod_signal.event_id],
            judgment_refs=[judgment.event_id],
            action_refs=[action.event_id],
            participant_ids=["valkyrie:k8s-a", "human:jozef"],
            transcript_targets=["mimir://rooms/huddle-k8s-a-payments"],
            source="skuld:rooms",
            correlation_id=root_failure,
            causation_id=joined.event_id,
        ),
        event_id="demo-k8s-a-room-snapshot",
        nats_subject=_subject("k8s-cluster-a", registry.ROOM_CONTEXT_SNAPSHOT_RECORDED),
        tags=("room", "replay"),
    )
    human_message = builder.add(
        room_message_recorded(
            environment_id="k8s-cluster-a",
            room_id="huddle-k8s-a-payments",
            message_id="msg-k8s-a-human-1",
            participant_id="human:jozef",
            role="operator",
            content="Proceed within delegated boundary and keep watching queue depth.",
            source="skuld:rooms",
            metadata={"feedback_hint": "good_action"},
            correlation_id=root_failure,
            causation_id=snapshot.event_id,
        ),
        event_id="demo-k8s-a-room-message-human",
        nats_subject=_subject("k8s-cluster-a", registry.ROOM_MESSAGE_RECORDED),
        tags=("room", "feedback"),
    )
    action_done = builder.add(
        valkyrie_action_completed(
            environment_id="k8s-cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            action_id="act-k8s-a-payments-memory",
            capability="k8s.patch_deployment_resources",
            outcome="success",
            evidence=[{"deployment": "payments-api", "queue_depth_after": 12}],
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_failure,
            causation_id=human_message.event_id,
        ),
        event_id="demo-k8s-a-action-completed",
        nats_subject=_subject("k8s-cluster-a", registry.VALKYRIE_ACTION_COMPLETED),
        tags=("action", "success"),
    )
    feedback = builder.add(
        feedback_recorded(
            environment_id="k8s-cluster-a",
            target_event_id=action_done.event_id,
            feedback_type="good_action",
            rating="correct",
            notes="The memory patch was safe and queue depth recovered.",
            signal_refs=[pod_signal.event_id],
            judgment_refs=[judgment.event_id],
            court_decision_id=court.event_id,
            action_id="act-k8s-a-payments-memory",
            surface_id="valkyrie-ui",
            user_id="human:jozef",
            responsible_valkyrie_id="valkyrie:k8s-a",
            environment_type="k8s",
            signal_source="kube-event-stream",
            domain_scope="flock:k8s",
            root_correlation_id=root_failure,
            source="skuld:feedback",
            correlation_id=root_failure,
            causation_id=action_done.event_id,
        ),
        event_id="demo-k8s-a-feedback",
        nats_subject=_subject("k8s-cluster-a", registry.FEEDBACK_RECORDED),
        tags=("feedback", "learning"),
    )
    transcript = builder.add(
        room_transcript_recorded(
            environment_id="k8s-cluster-a",
            room_id="huddle-k8s-a-payments",
            transcript_ref="mimir://rooms/huddle-k8s-a-payments",
            message_refs=[human_message.event_id],
            summary="Operator approved delegated remediation and confirmed outcome.",
            source="skuld:rooms",
            correlation_id=root_failure,
            causation_id=feedback.event_id,
        ),
        event_id="demo-k8s-a-room-transcript",
        nats_subject=_subject("k8s-cluster-a", registry.ROOM_TRANSCRIPT_RECORDED),
        tags=("room", "mimir"),
    )
    builder.add(
        participant_left(
            environment_id="k8s-cluster-a",
            participant_id="human:jozef",
            participant_type="human",
            display_name="Jozef",
            reason="remediation confirmed",
            source="skuld:rooms",
            correlation_id=root_failure,
            causation_id=transcript.event_id,
        ),
        event_id="demo-k8s-a-human-left",
        nats_subject=_subject("k8s-cluster-a", registry.PARTICIPANT_LEFT),
        tags=("room", "human"),
    )
    builder.add(
        room_closed(
            environment_id="k8s-cluster-a",
            room_id="huddle-k8s-a-payments",
            reason="queue depth recovered",
            transcript_ref="mimir://rooms/huddle-k8s-a-payments",
            source="skuld:rooms",
            correlation_id=root_failure,
            causation_id=transcript.event_id,
        ),
        event_id="demo-k8s-a-room-closed",
        nats_subject=_subject("k8s-cluster-a", registry.ROOM_CLOSED),
        tags=("room", "closed"),
    )
    dream = builder.add(
        valkyrie_state_updated(
            environment_id="k8s-cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            previous_state="awake",
            new_state="dreaming",
            reason="compress successful incident into private tool improvement",
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_failure,
            causation_id=feedback.event_id,
        ),
        event_id="demo-k8s-a-dream-started",
        nats_subject=_subject("k8s-cluster-a", registry.VALKYRIE_STATE_UPDATED),
        tags=("dream", "self-improvement"),
    )
    private_learning = builder.add(
        learning_promoted(
            environment_id="k8s-cluster-a",
            learning_id="learn-k8s-oom-queue-depth",
            from_scope="private",
            to_scope="environment",
            summary=(
                "Tool improvement: probe OOMKilled plus queue depth before restarting payments."
            ),
            confidence=0.89,
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_failure,
            causation_id=dream.event_id,
        ),
        event_id="demo-k8s-a-learning-environment",
        nats_subject=_subject("k8s-cluster-a", registry.LEARNING_PROMOTED),
        tags=("learning", "tool-improvement"),
    )
    promotion = builder.add(
        learning_promoted(
            environment_id="k8s-cluster-a",
            learning_id="learn-k8s-oom-queue-depth",
            from_scope="environment",
            to_scope="flock",
            summary="Share OOMKilled plus queue-depth signature with k8s Flock peers.",
            confidence=0.84,
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_failure,
            causation_id=private_learning.event_id,
        ),
        event_id="demo-k8s-a-learning-flock",
        nats_subject=_flock_subject("k8s", "learning.promoted"),
        tags=("learning", "flock", "nats"),
    )

    # k8s cluster B: canaries and adopts the useful learning, rejects a noisy one.
    root_cluster_b = "demo:k8s-b:learning-transfer"
    builder.add(
        learning_adoption_recorded(
            environment_id="k8s-cluster-b",
            learning_id="learn-k8s-oom-queue-depth",
            promotion_id=promotion.event_id,
            action="canary",
            rationale="Replay against cluster B incidents before adoption.",
            canary_passed=False,
            source=_source("valkyrie:k8s-b"),
            correlation_id=root_cluster_b,
            causation_id=promotion.event_id,
        ),
        event_id="demo-k8s-b-learning-canary",
        nats_subject=_flock_subject("k8s", "learning.canary"),
        tags=("flock", "canary"),
    )
    builder.add(
        learning_adoption_recorded(
            environment_id="k8s-cluster-b",
            learning_id="learn-k8s-oom-queue-depth",
            promotion_id=promotion.event_id,
            action="adopted",
            rationale="Canary replay passed after threshold tuned for cluster B.",
            canary_passed=True,
            local_override_path="learnings/flock/k8s/cluster-b/oom-queue-depth.md",
            source=_source("valkyrie:k8s-b"),
            correlation_id=root_cluster_b,
            causation_id="demo-k8s-b-learning-canary",
        ),
        event_id="demo-k8s-b-learning-adopted",
        nats_subject=_flock_subject("k8s", "learning.adopted"),
        tags=("flock", "adopted"),
    )
    noisy_promotion = builder.add(
        learning_promoted(
            environment_id="k8s-cluster-a",
            learning_id="learn-k8s-rollout-pull-noise",
            from_scope="environment",
            to_scope="flock",
            summary="Treat registry pull events during deploy windows as noise.",
            confidence=0.62,
            source=_source("valkyrie:k8s-a"),
            correlation_id=root_noise,
            causation_id=noisy_judgment.event_id,
        ),
        event_id="demo-k8s-a-noisy-learning-flock",
        nats_subject=_flock_subject("k8s", "learning.promoted"),
        tags=("flock", "candidate", "negative-transfer-risk"),
    )
    builder.add(
        learning_adoption_recorded(
            environment_id="k8s-cluster-b",
            learning_id="learn-k8s-rollout-pull-noise",
            promotion_id=noisy_promotion.event_id,
            action="rejected",
            rationale="Cluster B uses a different registry; suppressing this would hide failures.",
            canary_passed=False,
            source=_source("valkyrie:k8s-b"),
            correlation_id=root_cluster_b,
            causation_id=noisy_promotion.event_id,
        ),
        event_id="demo-k8s-b-learning-rejected",
        nats_subject=_flock_subject("k8s", "learning.rejected"),
        tags=("flock", "rejected", "negative-transfer"),
    )

    # Host/inbox: ignore low priority, escalate draft-needed message.
    root_inbox_low = "demo:host-inbox:low-priority"
    low_email = builder.add(
        signal_received(
            environment_id="host-inbox",
            environment_type="host",
            signal_source="gmail",
            signal_kind="inbox",
            severity="info",
            data={"fixture": "newsletter", "subject": "Weekly vendor digest"},
            source="adapter:gmail",
            confidence=0.98,
            correlation_id=root_inbox_low,
        ),
        event_id="demo-inbox-low-signal",
        nats_subject=_subject("host-inbox", registry.SIGNAL_INBOX_MESSAGE),
        tags=("inbox", "noise"),
    )
    builder.add(
        attention_decided(
            environment_id="host-inbox",
            target_event_id=low_email.event_id,
            attention_tier="suppress",
            route="memory_only",
            reason="Newsletter is not operator-actionable.",
            source="odin:attention-router",
            correlation_id=root_inbox_low,
            causation_id=low_email.event_id,
        ),
        event_id="demo-inbox-low-attention",
        nats_subject=_subject("host-inbox", registry.ATTENTION_SUPPRESSED),
        tags=("inbox", "suppressed"),
    )
    root_inbox_important = "demo:host-inbox:draft-needed"
    important_email = builder.add(
        signal_received(
            environment_id="host-inbox",
            environment_type="host",
            signal_source="gmail",
            signal_kind="inbox",
            severity="warning",
            data={
                "fixture": "draft_needed",
                "subject": "Contract review before Friday",
                "sender": "legal@example.com",
            },
            source="adapter:gmail",
            confidence=0.9,
            correlation_id=root_inbox_important,
        ),
        event_id="demo-inbox-important-signal",
        nats_subject=_subject("host-inbox", registry.SIGNAL_INBOX_MESSAGE),
        tags=("inbox", "important"),
    )
    inbox_state = builder.add(
        environment_state_changed(
            environment_id="host-inbox",
            environment_type="host",
            previous_state="quiet",
            new_state="review_needed",
            severity="warning",
            confidence=0.86,
            evidence=[{"event_id": important_email.event_id}],
            source=_source("valkyrie:inbox-host"),
            correlation_id=root_inbox_important,
            causation_id=important_email.event_id,
        ),
        event_id="demo-inbox-state-review-needed",
        nats_subject=_subject("host-inbox", registry.ENVIRONMENT_STATE_CHANGED),
        tags=("inbox", "state"),
    )
    inbox_judgment = builder.add(
        valkyrie_judgment_proposed(
            environment_id="host-inbox",
            valkyrie_id="valkyrie:inbox-host",
            attention_tier="review",
            recommended_action="draft_reply_for_review",
            authority_boundary="human_review_required",
            confidence=0.82,
            operational_state="important email needs draft",
            rationale="Contract and deadline language require operator review.",
            signal_refs=[important_email.event_id],
            evidence=[{"event_id": inbox_state.event_id}],
            target_surfaces=["valkyrie-ui", "email-draft"],
            source=_source("valkyrie:inbox-host"),
            correlation_id=root_inbox_important,
            causation_id=inbox_state.event_id,
        ),
        event_id="demo-inbox-judgment",
        nats_subject=_subject("host-inbox", registry.VALKYRIE_JUDGMENT_PROPOSED),
        tags=("inbox", "judgment"),
    )
    builder.add(
        attention_decision_made(
            environment_id="host-inbox",
            root_correlation_id=root_inbox_important,
            decision="notify",
            tier="review",
            action_authorization="human_review_required",
            escalation_path="valkyrie-ui",
            huddle_id="",
            judgment_refs=[inbox_judgment.event_id],
            action_refs=[],
            dissent=[],
            evidence=[{"event_id": inbox_judgment.event_id}],
            rationale="Draft is safe to prepare, send requires review.",
            audit_ref="mimir://audit/valkyrie/demo/inbox-contract",
            source="odin:attention-router",
            correlation_id=root_inbox_important,
            causation_id=inbox_judgment.event_id,
        ),
        event_id="demo-inbox-attention-decision",
        nats_subject=_subject("host-inbox", registry.ATTENTION_DECISION_MADE),
        tags=("inbox", "attention"),
    )

    # Printer/Pi: print complete is recorded, resin low is acted on.
    root_printer_done = "demo:printer-cell:print-complete"
    print_done = builder.add(
        signal_received(
            environment_id="printer-cell-a",
            environment_type="printer_pi",
            signal_source="printer-pi",
            signal_kind="printer",
            severity="info",
            data={"fixture": "print_complete", "printer": "saturn-4-ultra", "job": "odin-token"},
            source="adapter:printer-pi",
            confidence=0.99,
            correlation_id=root_printer_done,
        ),
        event_id="demo-printer-complete-signal",
        nats_subject=_subject("printer-cell-a", registry.SIGNAL_PRINTER_EVENT),
        tags=("printer", "record-only"),
    )
    builder.add(
        valkyrie_judgment_proposed(
            environment_id="printer-cell-a",
            valkyrie_id="valkyrie:printer-pi",
            attention_tier="ambient",
            recommended_action="record_complete_job",
            authority_boundary="delegated",
            confidence=0.95,
            operational_state="print complete",
            rationale="Completion is expected and only needs memory/audit.",
            signal_refs=[print_done.event_id],
            source=_source("valkyrie:printer-pi"),
            correlation_id=root_printer_done,
            causation_id=print_done.event_id,
        ),
        event_id="demo-printer-complete-judgment",
        nats_subject=_subject("printer-cell-a", registry.VALKYRIE_JUDGMENT_PROPOSED),
        tags=("printer", "judgment"),
    )
    root_printer_low = "demo:printer-cell:resin-low"
    resin_signal = builder.add(
        signal_received(
            environment_id="printer-cell-a",
            environment_type="printer_pi",
            signal_source="printer-pi",
            signal_kind="printer",
            severity="critical",
            data={
                "fixture": "resin_low",
                "printer": "saturn-4-ultra",
                "layer": 812,
                "resin_ml": 18,
            },
            source="adapter:printer-pi",
            confidence=0.92,
            correlation_id=root_printer_low,
        ),
        event_id="demo-printer-resin-low-signal",
        nats_subject=_subject("printer-cell-a", registry.SIGNAL_PRINTER_EVENT),
        tags=("printer", "fault"),
    )
    printer_state = builder.add(
        environment_state_changed(
            environment_id="printer-cell-a",
            environment_type="printer_pi",
            previous_state="printing",
            new_state="paused_resin_low",
            severity="critical",
            confidence=0.9,
            evidence=[{"event_id": resin_signal.event_id}],
            source=_source("valkyrie:printer-pi"),
            correlation_id=root_printer_low,
            causation_id=resin_signal.event_id,
        ),
        event_id="demo-printer-state-paused",
        nats_subject=_subject("printer-cell-a", registry.ENVIRONMENT_STATE_CHANGED),
        tags=("printer", "state"),
    )
    printer_judgment = builder.add(
        valkyrie_judgment_proposed(
            environment_id="printer-cell-a",
            valkyrie_id="valkyrie:printer-pi",
            attention_tier="urgent",
            recommended_action="pause_queue_and_notify_refill",
            authority_boundary="delegated",
            confidence=0.88,
            operational_state="print at risk from resin low",
            rationale="Pausing protects the print and avoids cured partial failure.",
            signal_refs=[resin_signal.event_id],
            evidence=[{"event_id": printer_state.event_id}],
            target_surfaces=["valkyrie-ui"],
            source=_source("valkyrie:printer-pi"),
            correlation_id=root_printer_low,
            causation_id=printer_state.event_id,
        ),
        event_id="demo-printer-resin-judgment",
        nats_subject=_subject("printer-cell-a", registry.VALKYRIE_JUDGMENT_PROPOSED),
        tags=("printer", "judgment"),
    )
    printer_action = builder.add(
        valkyrie_action_completed(
            environment_id="printer-cell-a",
            valkyrie_id="valkyrie:printer-pi",
            action_id="act-printer-resin-pause",
            capability="printer.pause_queue",
            outcome="success",
            evidence=[{"event_id": printer_judgment.event_id}, {"queue": "paused"}],
            source=_source("valkyrie:printer-pi"),
            correlation_id=root_printer_low,
            causation_id=printer_judgment.event_id,
        ),
        event_id="demo-printer-action-completed",
        nats_subject=_subject("printer-cell-a", registry.VALKYRIE_ACTION_COMPLETED),
        tags=("printer", "action"),
    )
    builder.add(
        feedback_recorded(
            environment_id="printer-cell-a",
            target_event_id=printer_action.event_id,
            feedback_type="good_action",
            rating="correct",
            notes="Pausing the queue before refill prevented a bad print.",
            signal_refs=[resin_signal.event_id],
            judgment_refs=[printer_judgment.event_id],
            action_id="act-printer-resin-pause",
            surface_id="valkyrie-ui",
            user_id="human:jozef",
            responsible_valkyrie_id="valkyrie:printer-pi",
            environment_type="printer_pi",
            signal_source="printer-pi",
            domain_scope="environment:printer-cell-a",
            root_correlation_id=root_printer_low,
            source="skuld:feedback",
            correlation_id=root_printer_low,
            causation_id=printer_action.event_id,
        ),
        event_id="demo-printer-feedback",
        nats_subject=_subject("printer-cell-a", registry.FEEDBACK_RECORDED),
        tags=("printer", "feedback"),
    )

    lineage = _build_lineage(builder.events)
    return DemoArtifact(
        events=builder.events,
        lineage=lineage,
        summary=_build_summary(builder.events, lineage),
    )


def write_demo_artifact(artifact: DemoArtifact, out_dir: Path) -> dict[str, Path]:
    """Write JSONL, lineage, and summary files for a demo run."""

    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    lineage_path = out_dir / "lineage.json"
    summary_path = out_dir / "summary.json"

    with events_path.open("w", encoding="utf-8") as handle:
        for event in artifact.events:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

    lineage_path.write_text(
        json.dumps(artifact.lineage, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(artifact.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {"events": events_path, "lineage": lineage_path, "summary": summary_path}


async def publish_with_publisher(events: Sequence[SleipnirEvent], publisher: AsyncPublisher) -> int:
    """Publish demo events with an existing Sleipnir publisher."""

    for event in events:
        await publisher.publish(event)
    return len(events)


async def publish_to_nats(
    events: Sequence[SleipnirEvent],
    *,
    servers: Sequence[str],
    stream_name: str = DEFAULT_STREAM_NAME,
    subject_prefix: str = DEFAULT_SUBJECT_PREFIX,
) -> int:
    """Publish demo events through the existing Sleipnir NATS adapter."""

    from sleipnir.adapters.nats_transport import NatsPublisher

    publisher = NatsPublisher(
        servers=list(servers),
        stream_name=stream_name,
        subject_prefix=subject_prefix,
    )
    await publisher.start()
    try:
        return await publish_with_publisher(events, publisher)
    finally:
        await publisher.stop()


def _default_out_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Path("logs") / f"valkyrie-environment-demo-{stamp}"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_default_out_dir(),
        help="Directory for events.jsonl, lineage.json, and summary.json.",
    )
    parser.add_argument(
        "--publish-nats",
        action="store_true",
        help="Publish events to NATS JetStream through Sleipnir after writing artifacts.",
    )
    parser.add_argument(
        "--nats-url",
        action="append",
        dest="nats_urls",
        default=[],
        help="NATS server URL. Repeat for multiple servers. Defaults to nats://localhost:4222.",
    )
    parser.add_argument("--stream-name", default=DEFAULT_STREAM_NAME)
    parser.add_argument("--subject-prefix", default=DEFAULT_SUBJECT_PREFIX)
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    artifact = build_valkyrie_environment_demo()
    paths = write_demo_artifact(artifact, args.out_dir)
    print(f"Wrote {artifact.summary['event_count']} events to {paths['events']}")
    print(f"Wrote lineage to {paths['lineage']}")
    print(f"Wrote summary to {paths['summary']}")

    if args.publish_nats:
        urls = args.nats_urls or ["nats://localhost:4222"]
        count = await publish_to_nats(
            artifact.events,
            servers=urls,
            stream_name=args.stream_name,
            subject_prefix=args.subject_prefix,
        )
        print(f"Published {count} events to NATS stream {args.stream_name}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))
