"""Resident Valkyrie learning install/adoption loop tests."""

from __future__ import annotations

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
from ravn.odin.review import ReviewItem, ReviewKind, review_decided_event
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import (
    learned_tool_storage,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import (
    LearnedToolArtifact,
    LearnedToolManifest,
    OperationalSignal,
)
from ravn.valkyrie_evolution.resident_learning import (
    EVOLUTION_SKILL_INVENTORY_EVENT,
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
        resident_evolution={"autonomy_mode": "yolo"},
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
                        "event_types": [
                            "flock.learning.*",
                            "learning.adoption.recorded",
                        ],
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
        {
            "stream_name": "flock-k8s-events",
            "subject": "flock.k8s.>",
            "event_types": [
                "flock.learning.*",
                "learning.adoption.recorded",
            ],
        }
    ]
    assert kwargs["core_subscriptions"] == [{"subject": "obs.cmd.valhalla.>"}]


@pytest.mark.asyncio
async def test_local_build_is_registered_in_existing_skill_lifecycle(tmp_path) -> None:
    bus = InProcessBus()
    manager = _manager(tmp_path, "resident")
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="workshop",
            environment_type="workshop",
            valkyrie_id="valkyrie:workshop",
            domain="workshop",
            flock_ids=["workshop-flock"],
            autonomy_mode="yolo",
        ),
        skills=manager,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "resident" / "tools",
    )
    artifact = ResidentLearningArtifact(
        learning_id="learned-tool:status_probe",
        title="status_probe",
        summary="Inspect live status",
        content="",
        artifact_type="agent_tool",
        scope="environment",
        confidence=0.9,
        source_environment_id="workshop",
        source_valkyrie_id="valkyrie:workshop",
        learned_tool_manifest={
            "name": "status_probe",
            "description": "Inspect live status",
            "input_schema": {"type": "object"},
            "required_permission": "tool:run",
        },
    )

    await runtime.register_installed_artifact(artifact)

    shown = await manager.show("status_probe")
    assert shown["metadata"]["status"] == "active"
    assert shown["metadata"]["source"] == "flock-learning:learned-tool:status_probe"


@pytest.mark.asyncio
async def test_start_enrolls_persisted_learned_tools_in_existing_lifecycle(tmp_path) -> None:
    bus = InProcessBus()
    manager = _manager(tmp_path, "resident")
    tools_dir = tmp_path / "resident" / "tools"
    _, artifacts_dir = learned_tool_storage(tools_dir.parent)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir,
        artifact=LearnedToolArtifact(
            artifact_id="learned-tool:fleet_status:abc123",
            manifest=LearnedToolManifest(
                name="fleet_status",
                description="Inspect the current fleet status.",
                input_schema={"type": "object"},
                required_permission="tool:run",
            ),
            tool_code="def run(input):\n    return {'ok': True}\n",
            provenance={
                "scope": "environment",
                "source_environment_id": "workshop",
                "source_valkyrie_id": "valkyrie:workshop",
            },
        ),
    )
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="workshop",
            environment_type="workshop",
            valkyrie_id="valkyrie:workshop",
            domain="workshop",
            flock_ids=["workshop-flock"],
            autonomy_mode="yolo",
        ),
        skills=manager,
        publisher=bus,
        subscriber=bus,
        tools_dir=tools_dir,
    )

    await runtime.start()

    shown = await manager.show("fleet_status")
    assert shown["metadata"]["status"] == "active"
    assert shown["metadata"]["scope"] == "environment"
    assert shown["metadata"]["source"] == ("flock-learning:learned-tool:fleet_status:abc123")
    assert await runtime._enroll_persisted_learned_tools() == 0

    await manager.archive("fleet_status")
    assert await runtime._enroll_persisted_learned_tools() == 0
    assert manager.status("fleet_status") == "archived"
    await runtime.stop()


@pytest.mark.asyncio
async def test_resident_installs_learning_and_exposes_it_as_a_signal_hint(tmp_path) -> None:
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
    assert (
        "YOLO override installed after non-blocking Odin findings" in peer.decisions()[0].rationale
    )
    odin = next(event for event in events if event.event_type == registry.ODIN_COURT_DECIDED)
    assert odin.payload["decision"] == "learning_adoption_allowed"
    assert odin.payload["install_authorization"] == "yolo_override"
    assert "YOLO override" in odin.payload["install_authorization_rationale"]
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

    assert result["usedAdoptedLearning"] is False
    assert result["decision"] == "capability_hint_available"
    assert result["skillName"] == "valkyrie-inspect-kubernetes-pod-oomkilled"
    assert result["capabilityCandidates"][0]["match"] == "exact_capability_name"
    refreshed = await peer_skills.show("valkyrie-inspect-kubernetes-pod-oomkilled")
    assert refreshed["metadata"]["run_count"] == 0
    assert not any(event.event_type == registry.VALKYRIE_JUDGMENT_PROPOSED for event in events)
    projection = ValkyrieDashboardProjection()
    for event in events:
        projection.record_event(event)
    dashboard = projection.dashboard()
    learning = next(item for item in dashboard["learnings"] if item["id"] == "live-learn-k8s-oom")
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
    assert (
        await printer_skills.get_runnable_skill("valkyrie-inspect-kubernetes-pod-oomkilled") is None
    )
    assert any(
        event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload["action"] == "rejected"
        and event.payload["relevant"] is False
        for event in events
    )
    assert not any(event.event_type == registry.ODIN_COURT_DECIDED for event in events)

    await printer_peer.stop()


@pytest.mark.asyncio
async def test_guarded_peer_holds_for_operator_when_odin_needs_approval(tmp_path) -> None:
    from ravn.odin.review import JsonReviewStore, ReviewRequester

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
        review_requester=ReviewRequester(
            publisher=bus,
            store=JsonReviewStore(tmp_path / "review_outbox.json"),
            source="valkyrie:k8s-guarded",
        ),
    )
    await guarded_peer.start()

    await exchange.propose(_candidate())
    await bus.flush()
    await bus.flush()

    assert [decision.action for decision in guarded_peer.decisions()] == ["held"]
    assert "Held for operator review" in guarded_peer.decisions()[0].rationale
    assert (
        await guarded_skills.get_runnable_skill("valkyrie-inspect-kubernetes-pod-oomkilled") is None
    )
    odin = next(event for event in events if event.event_type == registry.ODIN_COURT_DECIDED)
    assert odin.payload["decision"] == "learning_adoption_blocked"
    assert odin.payload["authority_boundary"] == "human_review_required"
    requested = next(
        event for event in events if event.event_type == registry.ODIN_REVIEW_REQUESTED
    )
    assert requested.payload["kind"] == "flock_learning"
    assert requested.payload["requested_action"] == "adopt"
    assert requested.payload["evidence"]["artifact"]["content"]
    assert not any(event.event_type == "valkyrie.evolution.activated" for event in events)

    await guarded_peer.stop()


@pytest.mark.asyncio
async def test_yolo_peer_rejects_unusable_kubectl_only_learning(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await _subscribe_recorder(bus, ["learning.*", "odin.*", "valkyrie.evolution.*"], events)
    peer_skills = _manager(tmp_path, "cluster-yolo")
    peer = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-yolo",
            valkyrie_id="valkyrie:k8s-yolo",
            domain="k8s",
            flock_ids=["k8s-valkyries"],
            autonomy_mode="yolo",
        ),
        skills=peer_skills,
        publisher=bus,
        subscriber=bus,
    )

    decision = await peer.evaluate_and_apply(
        ResidentLearningArtifact(
            learning_id="learn-kubectl-only",
            title="valkyrie-kubectl-only",
            summary="Inspect pod warnings with kubectl.",
            content=(
                "# skill: valkyrie-kubectl-only\n\n"
                "metadata:\n"
                "  capability: inspect.kubernetes.pod.backoff\n"
                "  safety_class: read_only\n\n"
                "Use `kubectl describe pod` and `kubectl logs` to inspect the pod."
            ),
            artifact_type="ravn_skill_tool",
            scope="flock",
            confidence=0.8,
            source_environment_id="cluster-a",
            source_valkyrie_id="valkyrie:k8s-a",
            promotion_id="promo-kubectl-only",
            flock_id="k8s-valkyries",
            domain="k8s",
            redaction_status="redacted",
        )
    )

    assert decision.action == "rejected"
    assert "Odin review blocked" in decision.rationale
    assert await peer_skills.get_runnable_skill("valkyrie-kubectl-only") is None
    odin = next(event for event in events if event.event_type == registry.ODIN_COURT_DECIDED)
    assert odin.payload["decision"] == "learning_adoption_blocked"
    assert any(
        str(finding).startswith("unavailable runtime dependency")
        for item in odin.payload["dissent"]
        for finding in item.get("findings", [])
    )
    assert not any(event.event_type == "valkyrie.evolution.activated" for event in events)


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

    item = ReviewItem.new(
        kind=ReviewKind.FLOCK_LEARNING.value,
        requested_action="adopt",
        environment_id="cluster-a",
        valkyrie_id="",
        title="valkyrie-inspect-kubernetes-pod-oomkilled",
        summary="Operator adopted k8s OOM learning",
        audience="flock",
        flock_id="k8s-valkyries",
        domain="k8s",
        evidence={
            "artifact": {
                "learning_id": "learn-k8s-oom-command",
                "title": "valkyrie-inspect-kubernetes-pod-oomkilled",
                "summary": "Operator adopted k8s OOM learning",
                "content": _skill_content(),
                "artifact_type": "ravn_skill_tool",
                "scope": "flock",
                "confidence": 0.91,
                "source_environment_id": "cluster-a",
                "source_valkyrie_id": "valkyrie:k8s-a",
                "promotion_id": "promo-k8s-oom-command",
                "flock_id": "k8s-valkyries",
                "domain": "k8s",
                "redaction_status": "redacted",
            }
        },
        requested_by="operator",
    )
    item.decide(decision="approved", operator_id="operator", reason="adopt now")
    await bus.publish(review_decided_event(item, source="ravn:odin-review"))
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
    activated = next(
        event for event in events if event.event_type == "valkyrie.evolution.activated"
    )
    # The activation event carries the full artifact so the dashboard skill
    # mirror can serve "view the learned skill" without touching the resident.
    assert activated.payload["skill_content"] == _skill_content()
    assert activated.payload["summary_text"] == "Operator adopted k8s OOM learning"
    assert activated.payload["tool_code"] == ""
    assert activated.payload["test_code"] == ""
    assert activated.payload["requirements"] == []
    assert activated.payload["learned_tool_manifest"] == {}
    resolved = next(event for event in events if event.event_type == registry.ODIN_REVIEW_RESOLVED)
    assert resolved.payload["apply_outcome"] == "applied"
    assert resolved.payload["item_id"] == item.item_id

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

    rollback_item = ReviewItem.new(
        kind=ReviewKind.FLOCK_LEARNING.value,
        requested_action="retract",
        environment_id="cluster-a",
        valkyrie_id="",
        title="valkyrie-inspect-kubernetes-pod-oomkilled",
        summary="Operator rolled back k8s OOM learning",
        audience="flock",
        flock_id="k8s-valkyries",
        domain="k8s",
        evidence={
            "artifact": {
                "learning_id": "learn-k8s-oom-rollback",
                "title": "valkyrie-inspect-kubernetes-pod-oomkilled",
                "summary": "Operator rolled back k8s OOM learning",
                "content": _skill_content(),
                "artifact_type": "ravn_skill_tool",
                "scope": "flock",
                "source_environment_id": "cluster-a",
                "source_valkyrie_id": "valkyrie:k8s-a",
                "promotion_id": "learn-k8s-oom-rollback",
                "flock_id": "k8s-valkyries",
                "domain": "k8s",
                "redaction_status": "redacted",
            }
        },
        requested_by="operator",
    )
    rollback_item.decide(decision="approved", operator_id="operator", reason="roll it back")
    await bus.publish(review_decided_event(rollback_item, source="ravn:odin-review"))
    await bus.flush()
    await bus.flush()

    assert await peer_skills.get_runnable_skill("valkyrie-inspect-kubernetes-pod-oomkilled") is None
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


@pytest.mark.asyncio
async def test_start_publishes_inventory_snapshot_and_stop_cancels_heartbeat(tmp_path) -> None:
    # start() must snapshot the resident's live inventory once subscriptions
    # are up so a REPLAY_SECONDS=0 dashboard sees existing skills; the heartbeat
    # task tears down cleanly on stop().
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await _subscribe_recorder(bus, ["valkyrie.evolution.*"], events)
    skills = _manager(tmp_path, "cluster-b")
    await skills.create(
        name="valkyrie-preexisting-skill",
        content="# skill: valkyrie-preexisting-skill\n\nAlready here.\n",
        description="Already here.",
        scope="environment",
        environment_id="cluster-b",
        source="flock-learning:peer-learning-1",
        source_environment_id="cluster-a",
        source_valkyrie_id="valkyrie:k8s-a",
    )
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-b",
            valkyrie_id="valkyrie:k8s-b",
            domain="k8s",
            autonomy_mode="yolo",
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "tools",
        # A short interval proves the heartbeat is a live task we can cancel.
        skill_inventory_interval_seconds=0.05,
    )

    await runtime.start()
    await bus.flush()

    inventory = [e for e in events if e.event_type == EVOLUTION_SKILL_INVENTORY_EVENT]
    skill_event = next(
        e for e in inventory if e.payload["skill_name"] == "valkyrie-preexisting-skill"
    )
    assert skill_event.payload["learning_origin"] == "peer"
    assert skill_event.payload["learning_scope"] == "environment"
    assert skill_event.payload["source_environment_id"] == "cluster-a"
    assert skill_event.payload["source_valkyrie_id"] == "valkyrie:k8s-a"
    assert runtime._inventory_task is not None

    await runtime.stop()
    assert runtime._inventory_task is None


@pytest.mark.asyncio
async def test_disabled_heartbeat_still_snapshots_on_start(tmp_path) -> None:
    # A zero interval disables the heartbeat task but the startup snapshot (and
    # opportunistic refreshes) still fire.
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await _subscribe_recorder(bus, ["valkyrie.evolution.*"], events)
    skills = _manager(tmp_path, "cluster-b")
    await skills.create(
        name="valkyrie-preexisting-skill",
        content="# skill: valkyrie-preexisting-skill\n\nAlready here.\n",
        description="Already here.",
        scope="environment",
        environment_id="cluster-b",
    )
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-b",
            valkyrie_id="valkyrie:k8s-b",
            domain="k8s",
            autonomy_mode="yolo",
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "tools",
        skill_inventory_interval_seconds=0,
    )

    await runtime.start()
    await bus.flush()

    inventory = [e for e in events if e.event_type == EVOLUTION_SKILL_INVENTORY_EVENT]
    assert any(e.payload["skill_name"] == "valkyrie-preexisting-skill" for e in inventory)
    assert runtime._inventory_task is None

    await runtime.stop()
