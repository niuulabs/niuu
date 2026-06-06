"""Resident Valkyrie learning install/adoption loop tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ravn.adapters.reflection.flock_learning import (
    FlockLearningCandidate,
    FlockLearningExchange,
    FlockLearningStore,
)
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.api.valkyries import ValkyrieDashboardProjection
from ravn.cli.commands import (
    _build_resident_learning_runtime,
    _resident_ravn_state_dir,
    _resolve_transport_kwargs,
)
from ravn.config import Settings
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.models import OperationalSignal
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent


def _skill_content(capability: str = "inspect.kubernetes.pod.oomkilled") -> str:
    return f"""# skill: valkyrie-{capability.replace(".", "-")}

Reusable resident Valkyrie capability for `{capability}`.

metadata:
  capability: {capability}
  source: valkyrie-dream-cycle
  safety_class: read_only

## When To Use

Use this skill when an incoming Environment signal matches capability `{capability}`.

## Procedure

1. Inspect pod events and memory pressure.
2. Produce a structured judgment.
"""


def _candidate(**overrides) -> FlockLearningCandidate:
    data = {
        "learning_id": "learn-k8s-oom",
        "title": "valkyrie-inspect-kubernetes-pod-oomkilled",
        "artifact_type": "tool_skill",
        "summary": "Use pod events and node pressure before restarting OOMKilled pods.",
        "content": _skill_content(),
        "flock_id": "k8s-valkyries",
        "source_environment_id": "cluster-a",
        "source_valkyrie_id": "valkyrie:k8s-a",
        "confidence": 0.88,
        "redaction_status": "redacted",
        "promotion_id": "promo-k8s-oom",
        "promoted_path": "learnings/flock/k8s-valkyries/oom.md",
        "tags": ["k8s", "oom"],
        "metadata": {"domain": "k8s"},
    }
    data.update(overrides)
    return FlockLearningCandidate(**data)


def _manager(tmp_path, name: str) -> SkillManagementRegistry:
    skill_dir = tmp_path / name / "skills"
    registry = FileSkillRegistry(
        skill_dirs=[str(skill_dir)],
        write_dir=skill_dir,
        include_builtin=False,
    )
    return SkillManagementRegistry(
        registry,
        metadata_path=tmp_path / name / "skill_management.json",
    )


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


async def _subscribe_recorder(
    bus: InProcessBus,
    patterns: list[str],
    events: list[SleipnirEvent],
) -> None:
    await bus.subscribe(patterns, lambda event: _record(events, event))


def test_daemon_composition_builds_file_backed_resident_learning_runtime(tmp_path) -> None:
    settings = Settings(
        mesh={"own_peer_id": "valkyrie:k8s-b"},
        environment={
            "id": "cluster-b",
            "type": "k8s",
            "flocks": ["k8s-valkyries"],
        },
        dream_cycle={"autonomy_mode": "yolo"},
        skill={"backend": "file", "include_builtin": False},
    )
    runtime = _build_resident_learning_runtime(
        settings,
        publisher=InProcessBus(),
        workspace=tmp_path,
    )

    assert runtime is not None
    assert runtime.identity.environment_id == "cluster-b"
    assert runtime.identity.valkyrie_id == "valkyrie:k8s-b"
    assert runtime.identity.flock_ids == ["k8s-valkyries"]


def test_resident_state_dir_uses_writable_workspace_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RAVN_STATE_DIR", raising=False)

    assert _resident_ravn_state_dir(tmp_path) == tmp_path / ".ravn"


def test_resident_state_dir_can_be_overridden_for_container_mounts(tmp_path, monkeypatch) -> None:
    state_dir = tmp_path / "mounted-ravn-state"
    monkeypatch.setenv("RAVN_STATE_DIR", str(state_dir))

    assert _resident_ravn_state_dir(tmp_path / "workspace") == state_dir


def test_daemon_composition_refuses_sqlite_skill_backend_for_valkyries(tmp_path) -> None:
    settings = Settings(
        mesh={"own_peer_id": "valkyrie:k8s-b"},
        environment={
            "id": "cluster-b",
            "type": "k8s",
            "flocks": ["k8s-valkyries"],
        },
        skill={"backend": "sqlite"},
    )

    assert (
        _build_resident_learning_runtime(
            settings,
            publisher=InProcessBus(),
            workspace=tmp_path,
        )
        is None
    )


def test_nats_transport_kwargs_include_flock_extra_subscription(monkeypatch) -> None:
    monkeypatch.setenv("NATS_URL", "tls://nats-valhalla.nats.svc.cluster.local:4222")
    settings = Settings(
        mesh={
            "enabled": True,
            "adapter": "nats",
            "own_peer_id": "valkyrie:valhalla",
            "nats": {
                "stream_name": "obs-valhalla-events",
                "subject_prefix": "obs.valhalla",
                "extra_subscriptions": [
                    {
                        "stream_name": "flock-k8s-events",
                        "subject": "flock.k8s.>",
                    }
                ],
                "core_subscriptions": [
                    {
                        "subject": "obs.cmd.valhalla.>",
                    }
                ],
            },
        },
        environment={"id": "valhalla", "type": "k8s", "flocks": ["k8s-valkyries"]},
    )

    kwargs = _resolve_transport_kwargs(settings, "nats")

    assert kwargs["stream_name"] == "obs-valhalla-events"
    assert kwargs["subject_prefix"] == "obs.valhalla"
    assert kwargs["extra_subscriptions"] == [
        {"stream_name": "flock-k8s-events", "subject": "flock.k8s.>"}
    ]
    assert kwargs["core_subscriptions"] == [{"subject": "obs.cmd.valhalla.>"}]


@pytest.mark.asyncio
async def test_resident_installs_relevant_flock_learning_and_uses_next_signal(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await _subscribe_recorder(
        bus,
        [
            "flock.learning.*",
            "learning.*",
            "valkyrie.evolution.*",
            "odin.*",
            "valkyrie.judgment.*",
        ],
        events,
    )
    exchange = FlockLearningExchange(
        store=FlockLearningStore(tmp_path / "flock.json"),
        publisher=bus,
        source="valkyrie:k8s-a",
    )
    peer_skills = _manager(tmp_path, "cluster-b")
    peer = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-b",
            valkyrie_id="valkyrie:k8s-b",
            domain="k8s",
            flock_ids=["k8s-valkyries"],
            autonomy_mode="yolo",
        ),
        skills=peer_skills,
        publisher=bus,
        subscriber=bus,
    )
    await peer.start()

    await exchange.propose(_candidate())
    await bus.flush()
    await bus.flush()

    installed = await peer_skills.show(
        "valkyrie-inspect-kubernetes-pod-oomkilled",
        include_archived=True,
    )
    assert installed["metadata"]["scope"] == "flock"
    assert installed["metadata"]["environment_id"] == "cluster-b"
    assert installed["metadata"]["source"] == "flock-learning:learn-k8s-oom"
    assert [decision.action for decision in peer.decisions()] == ["adopted"]
    assert any(event.event_type == registry.ODIN_COURT_DECIDED for event in events)
    assert any(
        event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload["action"] == "adopted"
        and event.payload["resident_valkyrie_id"] == "valkyrie:k8s-b"
        for event in events
    )

    signal = OperationalSignal(
        signal_id="sig-oom-next",
        event_type=registry.SIGNAL_KUBERNETES_EVENT,
        environment_id="cluster-b",
        domain="k8s",
        severity="high",
        summary="Pod OOMKilled again",
        payload={"kind": "pod", "reason": "OOMKilled", "namespace": "checkout"},
    )
    result = await peer.process_signal(signal)
    await bus.flush()

    assert result["usedAdoptedLearning"] is True
    assert result["skillName"] == "valkyrie-inspect-kubernetes-pod-oomkilled"
    refreshed = await peer_skills.show("valkyrie-inspect-kubernetes-pod-oomkilled")
    assert refreshed["metadata"]["run_count"] == 1
    assert any(
        event.event_type == registry.VALKYRIE_JUDGMENT_PROPOSED
        and event.payload["recommended_action"] == "inspect_with_adopted_learning"
        for event in events
    )
    projection = ValkyrieDashboardProjection()
    for event in events:
        projection.record_event(event)
    dashboard = projection.dashboard()
    learning = next(
        item
        for item in dashboard["learnings"]
        if item["id"] == "live-learn-k8s-oom"
    )
    assert learning["status"] == "adopted"
    assert learning["artifactType"] == "tool_skill"
    assert "capability: inspect.kubernetes.pod.oomkilled" in learning["artifactContent"]
    assert learning["odinReview"]["reviewer"] == "odin:resident-learning"
    history_types = [entry["eventType"] for entry in learning["history"]]
    assert "learning.adoption.recorded" in history_types
    assert "valkyrie.evolution.activated" in history_types

    await peer.stop()


@pytest.mark.asyncio
async def test_irrelevant_peer_rejects_learning_without_installing(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await bus.subscribe(["learning.*", "odin.*"], lambda event: _record(events, event))
    exchange = FlockLearningExchange(
        store=FlockLearningStore(tmp_path / "flock.json"),
        publisher=bus,
    )
    printer_skills = _manager(tmp_path, "printer")
    printer_peer = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="printer-pi",
            valkyrie_id="valkyrie:printer",
            domain="home",
            flock_ids=["printer-operators"],
            autonomy_mode="yolo",
        ),
        skills=printer_skills,
        publisher=bus,
        subscriber=bus,
    )
    await printer_peer.start()

    await exchange.propose(_candidate())
    await bus.flush()
    await bus.flush()

    assert [decision.action for decision in printer_peer.decisions()] == ["rejected"]
    assert "not a member" in printer_peer.decisions()[0].rationale
    assert await printer_skills.get_runnable_skill(
        "valkyrie-inspect-kubernetes-pod-oomkilled"
    ) is None
    assert any(
        event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload["action"] == "rejected"
        and event.payload["relevant"] is False
        for event in events
    )
    assert not any(event.event_type == registry.ODIN_COURT_DECIDED for event in events)

    await printer_peer.stop()


@pytest.mark.asyncio
async def test_guarded_peer_waits_for_human_when_odin_blocks_flock_scope(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await _subscribe_recorder(bus, ["learning.*", "odin.*", "valkyrie.evolution.*"], events)
    exchange = FlockLearningExchange(
        store=FlockLearningStore(tmp_path / "flock.json"),
        publisher=bus,
    )
    guarded_skills = _manager(tmp_path, "guarded")
    guarded_peer = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-guarded",
            valkyrie_id="valkyrie:k8s-guarded",
            domain="k8s",
            flock_ids=["k8s-valkyries"],
            autonomy_mode="guarded",
        ),
        skills=guarded_skills,
        publisher=bus,
        subscriber=bus,
    )
    await guarded_peer.start()

    await exchange.propose(_candidate())
    await bus.flush()
    await bus.flush()

    assert [decision.action for decision in guarded_peer.decisions()] == ["rejected"]
    assert "Odin review blocked" in guarded_peer.decisions()[0].rationale
    assert await guarded_skills.get_runnable_skill(
        "valkyrie-inspect-kubernetes-pod-oomkilled"
    ) is None
    odin = next(event for event in events if event.event_type == registry.ODIN_COURT_DECIDED)
    assert odin.payload["decision"] == "learning_adoption_blocked"
    assert odin.payload["authority_boundary"] == "human_review_required"
    assert any(
        event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload["action"] == "rejected"
        and event.payload["relevant"] is True
        for event in events
    )
    assert not any(event.event_type == "valkyrie.evolution.activated" for event in events)

    await guarded_peer.stop()


@pytest.mark.asyncio
async def test_operator_adoption_command_installs_and_acknowledges_skill(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await _subscribe_recorder(
        bus,
        ["learning.*", "valkyrie.evolution.*", "odin.*"],
        events,
    )
    peer_skills = _manager(tmp_path, "cluster-command")
    peer = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-command",
            valkyrie_id="valkyrie:k8s-command",
            domain="k8s",
            flock_ids=["k8s-valkyries"],
            autonomy_mode="yolo",
        ),
        skills=peer_skills,
        publisher=bus,
        subscriber=bus,
    )
    await peer.start()

    await bus.publish(
        SleipnirEvent(
            event_type=registry.LEARNING_ADOPTION_RECORDED,
            source="ravn:valkyrie-dashboard",
            summary="Operator adopted k8s OOM learning",
            urgency=0.3,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
            payload={
                "learning_id": "learn-k8s-oom-command",
                "promotion_id": "promo-k8s-oom-command",
                "environment_id": "cluster-a",
                "action": "adopted",
                "action_kind": "adopt",
                "operator_id": "operator",
                "artifact_name": "valkyrie-inspect-kubernetes-pod-oomkilled",
                "artifact_type": "ravn_skill_tool",
                "artifact_content": _skill_content(),
                "scope": "flock",
                "flock_id": "k8s-valkyries",
                "source_environment_id": "cluster-a",
                "source_valkyrie_id": "valkyrie:k8s-a",
                "redaction_status": "redacted",
                "confidence": 0.91,
                "domain": "k8s",
            },
        )
    )
    await bus.flush()
    await bus.flush()

    installed = await peer_skills.show("valkyrie-inspect-kubernetes-pod-oomkilled")
    assert installed["metadata"]["source"] == "flock-learning:learn-k8s-oom-command"
    assert [decision.action for decision in peer.decisions()] == ["adopted"]
    assert any(
        event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload.get("ack_kind") == "resident_learning"
        and event.payload.get("action") == "adopted"
        and event.payload.get("resident_valkyrie_id") == "valkyrie:k8s-command"
        for event in events
    )
    assert any(event.event_type == "valkyrie.evolution.activated" for event in events)

    await peer.stop()


@pytest.mark.asyncio
async def test_operator_rollback_command_archives_installed_skill(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await _subscribe_recorder(
        bus,
        ["learning.*", "valkyrie.evolution.*", "odin.*"],
        events,
    )
    peer_skills = _manager(tmp_path, "cluster-rollback")
    peer = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-rollback",
            valkyrie_id="valkyrie:k8s-rollback",
            domain="k8s",
            flock_ids=["k8s-valkyries"],
            autonomy_mode="yolo",
        ),
        skills=peer_skills,
        publisher=bus,
        subscriber=bus,
    )
    await peer.start()

    await peer.evaluate_and_apply(
        ResidentLearningArtifact(
            learning_id="learn-k8s-oom-rollback",
            title="valkyrie-inspect-kubernetes-pod-oomkilled",
            summary="Use pod events and node pressure before restarting OOMKilled pods.",
            content=_skill_content(),
            artifact_type="ravn_skill_tool",
            scope="flock",
            confidence=0.88,
            source_environment_id="cluster-a",
            source_valkyrie_id="valkyrie:k8s-a",
            promotion_id="promo-k8s-oom-rollback",
            flock_id="k8s-valkyries",
            domain="k8s",
            redaction_status="redacted",
        )
    )

    await bus.publish(
        SleipnirEvent(
            event_type=registry.LEARNING_ADOPTION_RECORDED,
            source="ravn:valkyrie-dashboard",
            summary="Operator rolled back k8s OOM learning",
            urgency=0.5,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
            payload={
                "learning_id": "learn-k8s-oom-rollback",
                "promotion_id": "learn-k8s-oom-rollback",
                "environment_id": "cluster-a",
                "action": "regressed",
                "action_kind": "rollback",
                "operator_id": "operator",
                "artifact_name": "valkyrie-inspect-kubernetes-pod-oomkilled",
                "artifact_type": "ravn_skill_tool",
                "artifact_content": _skill_content(),
                "scope": "flock",
                "flock_id": "k8s-valkyries",
                "source_environment_id": "cluster-a",
                "source_valkyrie_id": "valkyrie:k8s-a",
                "redaction_status": "redacted",
                "domain": "k8s",
            },
        )
    )
    await bus.flush()
    await bus.flush()

    assert await peer_skills.get_runnable_skill(
        "valkyrie-inspect-kubernetes-pod-oomkilled"
    ) is None
    archived = await peer_skills.show(
        "valkyrie-inspect-kubernetes-pod-oomkilled",
        include_archived=True,
    )
    assert archived["metadata"]["status"] == "archived"
    assert [decision.action for decision in peer.decisions()] == ["rolled_back"]
    assert any(event.event_type == "valkyrie.evolution.rolled_back" for event in events)
    assert any(
        event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload.get("ack_kind") == "resident_learning"
        and event.payload.get("action") == "regressed"
        for event in events
    )

    await peer.stop()
