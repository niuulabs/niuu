"""Tests for the deterministic resident Valkyrie Environment demo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ravn.demo.valkyrie_environment import (
    build_valkyrie_environment_demo,
    publish_with_publisher,
    write_demo_artifact,
)
from sleipnir.domain import registry


class RecordingPublisher:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event) -> None:
        self.events.append(event)


def _events_by_id():
    artifact = build_valkyrie_environment_demo()
    return artifact, {event.event_id: event for event in artifact.events}


def test_demo_is_deterministic_and_covers_required_environment_archetypes() -> None:
    first = build_valkyrie_environment_demo()
    second = build_valkyrie_environment_demo()

    assert [event.to_dict() for event in first.events] == [
        event.to_dict() for event in second.events
    ]
    assert first.summary["deterministic"] is True
    assert set(first.summary["environment_ids"]) == {
        "host-inbox",
        "k8s-cluster-a",
        "k8s-cluster-b",
        "printer-cell-a",
    }
    assert first.summary["flock_ids"] == ["k8s"]


def test_demo_emits_full_valkyrie_loop_event_types() -> None:
    artifact = build_valkyrie_environment_demo()
    event_types = {event.event_type for event in artifact.events}

    assert registry.SIGNAL_KUBERNETES_EVENT in event_types
    assert registry.SIGNAL_INBOX_MESSAGE in event_types
    assert registry.SIGNAL_PRINTER_EVENT in event_types
    assert registry.ENVIRONMENT_STATE_CHANGED in event_types
    assert registry.VALKYRIE_STATE_UPDATED in event_types
    assert registry.VALKYRIE_JUDGMENT_PROPOSED in event_types
    assert registry.VALKYRIE_ACTION_PROPOSED in event_types
    assert registry.VALKYRIE_ACTION_COMPLETED in event_types
    assert registry.ODIN_COURT_DECIDED in event_types
    assert registry.ATTENTION_DECISION_MADE in event_types
    assert registry.FEEDBACK_RECORDED in event_types
    assert registry.LEARNING_PROMOTED in event_types
    assert registry.LEARNING_ADOPTION_RECORDED in event_types
    assert registry.ROOM_OPENED in event_types
    assert registry.ROOM_MESSAGE_RECORDED in event_types
    assert registry.ROOM_TRANSCRIPT_RECORDED in event_types


def test_k8s_lineage_links_signal_state_judgment_action_court_huddle_feedback_learning() -> None:
    artifact, events = _events_by_id()
    lineage = artifact.lineage["demo:k8s-a:pod-failure"]

    assert lineage["signal_refs"] == ["demo-k8s-a-pod-failure-signal"]
    assert "demo-k8s-a-state-degraded" in lineage["state_refs"]
    assert "demo-k8s-a-judgment" in lineage["judgment_refs"]
    assert "demo-k8s-a-action-proposed" in lineage["action_refs"]
    assert "demo-k8s-a-action-completed" in lineage["action_refs"]
    assert "demo-k8s-a-court" in lineage["court_decision_refs"]
    assert "demo-k8s-a-room-opened" in lineage["room_refs"]
    assert "demo-k8s-a-feedback" in lineage["feedback_refs"]
    assert "demo-k8s-a-learning-environment" in lineage["learning_refs"]
    assert "demo-k8s-a-learning-flock" in lineage["learning_refs"]

    assert events["demo-k8s-a-action-completed"].causation_id == "demo-k8s-a-room-message-human"
    assert events["demo-k8s-a-learning-flock"].payload["nats_subject"] == (
        "flock.k8s.learning.promoted"
    )


def test_huddle_flow_includes_human_join_respond_leave_and_replay_snapshot() -> None:
    _, events = _events_by_id()

    assert events["demo-k8s-a-room-opened"].payload["room_id"] == "huddle-k8s-a-payments"
    assert events["demo-k8s-a-human-joined"].payload["participant_id"] == "human:jozef"
    assert events["demo-k8s-a-room-snapshot"].payload["signal_refs"] == [
        "demo-k8s-a-pod-failure-signal"
    ]
    assert "Proceed within delegated boundary" in events["demo-k8s-a-room-message-human"].payload[
        "content"
    ]
    assert events["demo-k8s-a-human-left"].payload["reason"] == "remediation confirmed"


def test_flock_learning_transfer_records_canary_adoption_and_rejection() -> None:
    _, events = _events_by_id()

    canary = events["demo-k8s-b-learning-canary"]
    adopted = events["demo-k8s-b-learning-adopted"]
    rejected = events["demo-k8s-b-learning-rejected"]

    assert canary.payload["action"] == "canary"
    assert adopted.payload["action"] == "adopted"
    assert adopted.payload["canary_passed"] is True
    assert rejected.payload["action"] == "rejected"
    assert rejected.payload["nats_subject"] == "flock.k8s.learning.rejected"


def test_demo_writes_jsonl_lineage_and_summary_artifacts(tmp_path: Path) -> None:
    artifact = build_valkyrie_environment_demo()
    paths = write_demo_artifact(artifact, tmp_path)

    event_lines = paths["events"].read_text().splitlines()
    assert len(event_lines) == artifact.summary["event_count"]
    first_event = json.loads(event_lines[0])
    assert first_event["event_id"] == "demo-k8s-a-noisy-signal"

    summary = json.loads(paths["summary"].read_text())
    assert summary["event_count"] == len(artifact.events)

    lineage = json.loads(paths["lineage"].read_text())
    assert "demo:k8s-a:pod-failure" in lineage


@pytest.mark.asyncio
async def test_demo_can_publish_through_existing_publisher_port() -> None:
    artifact = build_valkyrie_environment_demo()
    publisher = RecordingPublisher()

    count = await publish_with_publisher(artifact.events, publisher)

    assert count == len(artifact.events)
    assert [event.event_id for event in publisher.events] == [
        event.event_id for event in artifact.events
    ]
