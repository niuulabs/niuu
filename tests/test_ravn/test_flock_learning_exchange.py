"""Tests for Flock learning exchange and peer adoption."""

from __future__ import annotations

import pytest

from ravn.adapters.reflection.flock_learning import (
    FLOCK_LEARNING_EVENT_TYPES,
    FlockLearningCandidate,
    FlockLearningExchange,
    FlockLearningStore,
)
from ravn.adapters.reflection.learning_promotion import LearningPromotionRecord
from ravn.domain.environment import Environment, k8s_environment_fixture
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent, validate_event_type
from ting.domain.flock_flow import FlockFlowConfig


def _peer_environment() -> Environment:
    env = k8s_environment_fixture().model_copy(deep=True)
    env.id = "cluster-prod-b"
    env.name = "Production Cluster B"
    env.topology.node_id = "cluster:prod-b"
    env.topology.cluster_id = "prod-b"
    env.resident_valkyrie_ids = ["valkyrie:k8s-prod-b"]
    env.flock_memberships[0].peer_environment_ids = ["cluster-prod-a"]
    return env


def _promotion_record(**overrides) -> LearningPromotionRecord:
    data = {
        "promotion_id": "promo-k8s-oom",
        "learning_id": "learn-k8s-oom",
        "source_path": "learnings/cluster-a/oom.md",
        "promoted_path": "learnings/flock/k8s/oom.md",
        "from_scope": "environment",
        "to_scope": "flock",
        "target_mount": "shared",
        "status": "promoted",
        "policy_decision": "allow",
        "policy_reason": "YOLO policy allows Flock promotion",
        "environment_id": "cluster-prod-a",
        "source_valkyrie_id": "valkyrie:k8s-prod-a",
        "domain": "k8s",
        "flock_id": "k8s-valkyries",
        "source_episode_ids": ["ep-1", "ep-2", "ep-3"],
        "confidence": 0.86,
        "repetition_count": 3,
        "successful_reuse_count": 2,
        "feedback_score": 0.4,
        "redaction_status": "redacted",
    }
    data.update(overrides)
    return LearningPromotionRecord(**data)


def _candidate(**overrides) -> FlockLearningCandidate:
    candidate = FlockLearningCandidate.from_promotion(
        _promotion_record(),
        artifact_type="tool_skill",
        title="OOM restart watch",
        summary="OOMKilled pods usually recover after cache pressure drops.",
        content="Check pod events and memory pressure before restarting workloads.",
        tags=["k8s", "oom"],
    )
    if not overrides:
        return candidate
    data = candidate.__dict__ | overrides
    return FlockLearningCandidate(**data)


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


def test_flock_learning_events_use_registered_event_type_shape() -> None:
    for event_type in FLOCK_LEARNING_EVENT_TYPES:
        validate_event_type(event_type)


@pytest.mark.asyncio
async def test_exchange_uses_existing_environment_membership_and_ting_flow(tmp_path) -> None:
    environment = k8s_environment_fixture()
    flow = FlockFlowConfig(
        name=environment.flock_memberships[0].ting_flow,
        mesh_transport="nats",
    )
    exchange = FlockLearningExchange(store=FlockLearningStore(tmp_path / "flock.json"))

    record = await exchange.propose(_candidate())

    assert flow.name == "k8s-environment-flock"
    assert flow.mesh_transport == "nats"
    assert environment.flock_ids == ["k8s-valkyries"]
    assert exchange.ready_for_environment(environment) == [record]


@pytest.mark.asyncio
async def test_two_k8s_environments_canary_and_adopt_over_sleipnir(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await bus.subscribe(["flock.learning.*"], lambda event: _record(events, event))
    exchange = FlockLearningExchange(
        store=FlockLearningStore(tmp_path / "flock.json"),
        publisher=bus,
        source="valkyrie:k8s-prod-a",
    )
    peer = _peer_environment()

    proposed = await exchange.propose(_candidate())
    canary = await exchange.start_canary(
        proposed.exchange_id,
        environment_id=peer.id,
        evaluation_results={"pods_recovered": True, "false_positive_rate": 0.02},
        rationale="Matches cluster B staging signal.",
    )
    canary_action = canary.peer_decisions[-1].action
    adopted = await exchange.adopt(
        proposed.exchange_id,
        environment_id=peer.id,
        evaluation_results={"canary_passed": True},
        rationale="Reduced restart churn during canary.",
    )
    active = await exchange.activate(proposed.exchange_id, environment_id=peer.id)
    await bus.flush()

    assert canary_action == "canary_started"
    assert adopted.peer_decisions[-1].action == "adopted"
    assert active.status == "active"
    assert peer.id in active.active_environment_ids
    assert [event.event_type for event in events] == [
        "flock.learning.proposed",
        "flock.learning.canary_started",
        "flock.learning.adopted",
        "flock.learning.activated",
    ]
    assert events[0].payload["nats_subject"] == "ravn.environment.flock.learning.proposed"
    assert exchange.adopted_context(environment_id=peer.id, flock_id="k8s-valkyries").startswith(
        "## Adopted Flock Learnings"
    )


@pytest.mark.asyncio
async def test_propose_folds_repeated_learning_by_fingerprint(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await bus.subscribe(["flock.learning.*"], lambda event: _record(events, event))
    store = FlockLearningStore(tmp_path / "flock.json")
    exchange = FlockLearningExchange(store=store, publisher=bus)

    first = await exchange.propose(_candidate(source_episode_ids=["ep-1"]))
    folded = await exchange.propose(
        _candidate(
            learning_id="learn-k8s-oom-repeat",
            title="  OOM Restart   Watch ",
            confidence=0.91,
            source_episode_ids=["ep-9"],
        )
    )
    await bus.flush()

    assert folded.exchange_id == first.exchange_id
    assert folded.repetition == 2
    assert folded.candidate.confidence == 0.91
    assert folded.candidate.source_episode_ids == ["ep-1", "ep-9"]
    assert len(store.list()) == 1
    assert [event.event_type for event in events] == ["flock.learning.proposed"] * 2
    assert events[-1].payload["repetition"] == 2


@pytest.mark.asyncio
async def test_propose_does_not_fold_into_rejected_learning(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock.json")
    exchange = FlockLearningExchange(store=store)
    peer = _peer_environment()
    first = await exchange.propose(_candidate())
    await exchange.reject(
        first.exchange_id,
        environment_id=peer.id,
        rationale="Not applicable here.",
    )

    fresh = await exchange.propose(_candidate(learning_id="learn-k8s-oom-repeat"))

    assert fresh.exchange_id != first.exchange_id
    assert fresh.repetition == 1
    assert len(store.list()) == 2


@pytest.mark.asyncio
async def test_negative_transfer_rejection_stops_ready_to_apply_loop(tmp_path) -> None:
    exchange = FlockLearningExchange(store=FlockLearningStore(tmp_path / "flock.json"))
    peer = _peer_environment()
    proposed = await exchange.propose(_candidate())

    rejected = await exchange.reject(
        proposed.exchange_id,
        environment_id=peer.id,
        rationale="Cluster B has a different memory-pressure baseline.",
        local_override_path="learnings/environment/cluster-prod-b/oom-override.md",
        evaluation_results={"false_positive_rate": 0.4},
    )

    assert rejected.status == "rejected"
    assert rejected.peer_decisions[-1].local_override_path.endswith("oom-override.md")
    assert exchange.ready_for_environment(peer) == []


@pytest.mark.asyncio
async def test_redaction_blocks_flock_promotion_before_peer_canary(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await bus.subscribe(["flock.learning.*"], lambda event: _record(events, event))
    exchange = FlockLearningExchange(
        store=FlockLearningStore(tmp_path / "flock.json"),
        publisher=bus,
    )

    blocked = await exchange.propose(_candidate(redaction_status="unredacted"))
    await bus.flush()

    assert blocked.status == "blocked"
    assert events == [events[0]]
    assert events[0].event_type == "flock.learning.rejected"
    assert "redacted" in events[0].payload["reason"]


@pytest.mark.asyncio
async def test_adopted_tool_skill_feeds_ravn_context_and_skill_discovery(tmp_path) -> None:
    exchange = FlockLearningExchange(store=FlockLearningStore(tmp_path / "flock.json"))
    peer = _peer_environment()
    proposed = await exchange.propose(_candidate())
    await exchange.adopt(proposed.exchange_id, environment_id=peer.id, rationale="Good fit.")
    await exchange.activate(proposed.exchange_id, environment_id=peer.id)

    context = exchange.adopted_context(environment_id=peer.id, flock_id="k8s-valkyries")
    metadata = exchange.skill_discovery_metadata(
        environment_id=peer.id,
        flock_id="k8s-valkyries",
    )
    ui = exchange.ui_snapshot(flock_id="k8s-valkyries")

    assert "OOM restart watch" in context
    assert metadata == [
        {
            "name": "OOM restart watch",
            "scope": "flock",
            "flock_id": "k8s-valkyries",
            "environment_id": "cluster-prod-b",
            "source_environment_id": "cluster-prod-a",
            "learning_id": "learn-k8s-oom",
            "tags": ["k8s", "oom"],
            "content": "Check pod events and memory pressure before restarting workloads.",
        }
    ]
    assert ui["learning_count"] == 1
    assert ui["learnings"][0]["status"] == "active"
    assert ui["learnings"][0]["active_environment_ids"] == ["cluster-prod-b"]


@pytest.mark.asyncio
async def test_rollback_archives_learning_for_peer(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await bus.subscribe(["flock.learning.*"], lambda event: _record(events, event))
    exchange = FlockLearningExchange(
        store=FlockLearningStore(tmp_path / "flock.json"),
        publisher=bus,
    )
    peer = _peer_environment()
    proposed = await exchange.propose(_candidate())
    await exchange.adopt(proposed.exchange_id, environment_id=peer.id, rationale="Good fit.")

    rolled_back = await exchange.rollback(
        proposed.exchange_id,
        environment_id=peer.id,
        rationale="Regression after a cluster upgrade.",
    )
    await bus.flush()

    assert rolled_back.status == "rolled_back"
    assert exchange.ready_for_environment(peer) == []
    assert events[-1].event_type == "flock.learning.rolled_back"
    assert events[-1].payload["rationale"] == "Regression after a cluster upgrade."
