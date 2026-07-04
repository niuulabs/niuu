"""Resident-side handling of operator feedback and revision commands."""

from __future__ import annotations

import pytest

from ravn.adapters.reflection.flock_learning import (
    FlockLearningCandidate,
    FlockLearningRecord,
    FlockLearningStore,
)
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.odin.review import ReviewItem, ReviewKind, review_decided_event
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent


def _skill_content(extra_step: str = "") -> str:
    lines = [
        "# skill: valkyrie-inspect-kubernetes-pod-oomkilled",
        "",
        "Reusable resident Valkyrie capability for `inspect.kubernetes.pod.oomkilled`.",
        "",
        "metadata:",
        "  capability: inspect.kubernetes.pod.oomkilled",
        "  source: valkyrie-dream-cycle",
        "  safety_class: read_only",
        "",
        "## Procedure",
        "",
        "1. Inspect pod events and memory pressure.",
        "2. Produce a structured judgment.",
    ]
    if extra_step:
        lines.append(extra_step)
    return "\n".join(lines) + "\n"


def _candidate(**overrides) -> FlockLearningCandidate:
    data = {
        "learning_id": "learn-feedback",
        "title": "valkyrie-inspect-kubernetes-pod-oomkilled",
        "artifact_type": "tool_skill",
        "summary": "Use pod events and node pressure before restarting OOMKilled pods.",
        "content": _skill_content(),
        "flock_id": "k8s-valkyries",
        "source_environment_id": "cluster-a",
        "source_valkyrie_id": "valkyrie:k8s-a",
        "confidence": 0.5,
        "redaction_status": "redacted",
        "metadata": {"domain": "k8s", "scope": "flock"},
    }
    data.update(overrides)
    return FlockLearningCandidate(**data)


def _artifact_evidence(**overrides) -> dict:
    data = {
        "learning_id": "learn-feedback",
        "title": "valkyrie-inspect-kubernetes-pod-oomkilled",
        "summary": "Use pod events and node pressure before restarting OOMKilled pods.",
        "content": _skill_content(),
        "artifact_type": "ravn_skill_tool",
        "scope": "flock",
        "confidence": 0.5,
        "source_environment_id": "cluster-a",
        "source_valkyrie_id": "valkyrie:k8s-a",
        "promotion_id": "learn-feedback",
        "flock_id": "k8s-valkyries",
        "domain": "k8s",
        "redaction_status": "redacted",
    }
    data.update(overrides)
    return data


def _manager(tmp_path, name: str) -> SkillManagementRegistry:
    skill_dir = tmp_path / name / "skills"
    skill_registry = FileSkillRegistry(
        skill_dirs=[str(skill_dir)],
        write_dir=skill_dir,
        include_builtin=False,
    )
    return SkillManagementRegistry(
        skill_registry,
        metadata_path=tmp_path / name / "skill_management.json",
    )


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


async def _runtime(
    tmp_path,
    *,
    store: FlockLearningStore,
    bump: float,
) -> tuple[ResidentLearningRuntime, InProcessBus, list[SleipnirEvent]]:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await bus.subscribe(
        ["learning.*", "valkyrie.evolution.*", "odin.*"],
        lambda event: _record(events, event),
    )
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-feedback",
            valkyrie_id="valkyrie:k8s-feedback",
            domain="k8s",
            flock_ids=["k8s-valkyries"],
            autonomy_mode="yolo",
        ),
        skills=_manager(tmp_path, "cluster-feedback"),
        publisher=bus,
        subscriber=bus,
        learning_store=store,
        feedback_confidence_bump=bump,
    )
    await runtime.start()
    return runtime, bus, events


def _command(requested_action: str, evidence: dict, *, reason: str = "") -> SleipnirEvent:
    item = ReviewItem.new(
        kind=ReviewKind.FLOCK_LEARNING.value,
        requested_action=requested_action,
        environment_id="cluster-a",
        valkyrie_id="",
        title="valkyrie-inspect-kubernetes-pod-oomkilled",
        summary="operator learning command",
        audience="flock",
        flock_id="k8s-valkyries",
        domain="k8s",
        evidence=evidence,
        requested_by="test-operator",
    )
    item.decide(decision="approved", operator_id="test-operator", reason=reason)
    return review_decided_event(item, source="ravn:odin-review")


@pytest.mark.asyncio
async def test_feedback_command_bumps_confidence_using_configured_value(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock.json")
    store.save(FlockLearningRecord(exchange_id="learn-feedback", candidate=_candidate()))
    runtime, bus, events = await _runtime(tmp_path, store=store, bump=0.2)

    await bus.publish(
        _command(
            "feedback",
            {
                "artifact": _artifact_evidence(),
                "feedback": {
                    "verdict": "useful",
                    "reason": "caught a real incident",
                    "operatorId": "test-operator",
                    "recordedAt": "2026-07-04T10:00:00+00:00",
                },
            },
        )
    )
    await bus.flush()
    await bus.flush()

    record = store.get("learn-feedback")
    # 0.5 + the configured (non-default) 0.2 bump — proves the value threads.
    assert record.candidate.confidence == pytest.approx(0.7)
    assert record.operator_feedback["verdict"] == "useful"
    assert record.operator_feedback["operator_id"] == "test-operator"
    assert record.operator_feedback["recorded_at"] == "2026-07-04T10:00:00+00:00"
    update = next(
        event
        for event in events
        if event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload.get("action") == "updated"
    )
    assert update.payload["confidence"] == pytest.approx(0.7)
    assert update.payload["repetition"] == 1
    assert update.payload["feedback"]["verdict"] == "useful"
    resolved = next(event for event in events if event.event_type == registry.ODIN_REVIEW_RESOLVED)
    assert resolved.payload["apply_outcome"] == "applied"

    await runtime.stop()


@pytest.mark.asyncio
async def test_feedback_confidence_reinforcement_clamps_at_one(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock.json")
    store.save(
        FlockLearningRecord(
            exchange_id="learn-feedback",
            candidate=_candidate(confidence=0.95),
        )
    )
    runtime, bus, _events = await _runtime(tmp_path, store=store, bump=0.2)

    await bus.publish(
        _command(
            "feedback",
            {
                "artifact": _artifact_evidence(confidence=0.95),
                "feedback": {"verdict": "good_action", "operatorId": "test-operator"},
            },
        )
    )
    await bus.flush()
    await bus.flush()

    assert store.get("learn-feedback").candidate.confidence == pytest.approx(1.0)

    await runtime.stop()


@pytest.mark.asyncio
async def test_feedback_bad_action_marks_unadopted_learning_rejected_locally(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock.json")
    store.save(FlockLearningRecord(exchange_id="learn-feedback", candidate=_candidate()))
    runtime, bus, events = await _runtime(tmp_path, store=store, bump=0.2)

    await bus.publish(
        _command(
            "feedback",
            {
                "artifact": _artifact_evidence(),
                "feedback": {
                    "verdict": "bad_action",
                    "reason": "suggested restarting the wrong deployment",
                    "operatorId": "test-operator",
                },
            },
        )
    )
    await bus.flush()
    await bus.flush()

    record = store.get("learn-feedback")
    decision = record.decision_for("cluster-feedback")
    assert decision is not None
    assert decision.action == "rejected"
    assert record.operator_feedback["verdict"] == "bad_action"
    # 0.5 stays untouched: bad_action never reinforces.
    assert record.candidate.confidence == pytest.approx(0.5)
    assert any(
        event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload.get("action") == "rejected"
        for event in events
    )

    await runtime.stop()


@pytest.mark.asyncio
async def test_revise_command_edits_candidate_in_place(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock.json")
    store.save(FlockLearningRecord(exchange_id="learn-feedback", candidate=_candidate()))
    runtime, bus, events = await _runtime(tmp_path, store=store, bump=0.2)

    await bus.publish(
        _command(
            "revise",
            {
                "artifact": _artifact_evidence(),
                "revision": {
                    "title": "valkyrie-inspect-kubernetes-pod-oomkilled",
                    "summary": "Check node memory pressure before any restart.",
                    "content": _skill_content("3. Compare against prior OOM events."),
                    "superseded_id": "",
                },
            },
        )
    )
    await bus.flush()
    await bus.flush()

    record = store.get("learn-feedback")
    assert record.revision == 1
    assert record.candidate.summary == "Check node memory pressure before any restart."
    assert "3. Compare against prior OOM events." in record.candidate.content
    update = next(
        event
        for event in events
        if event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload.get("action") == "updated"
    )
    assert update.payload["revision"] == 1

    await runtime.stop()


@pytest.mark.asyncio
async def test_revise_command_on_adopted_supersedes_through_install_flow(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock.json")
    runtime, bus, events = await _runtime(tmp_path, store=store, bump=0.2)
    adopted = await runtime.evaluate_and_apply(
        ResidentLearningArtifact(
            learning_id="learn-feedback",
            title="valkyrie-inspect-kubernetes-pod-oomkilled",
            summary="Use pod events and node pressure before restarting OOMKilled pods.",
            content=_skill_content(),
            artifact_type="ravn_skill_tool",
            scope="flock",
            confidence=0.5,
            source_environment_id="cluster-a",
            source_valkyrie_id="valkyrie:k8s-a",
            promotion_id="learn-feedback",
            flock_id="k8s-valkyries",
            domain="k8s",
            redaction_status="redacted",
        )
    )
    assert adopted.action == "adopted"

    revised_content = _skill_content("3. Escalate to the operator before deleting pods.")
    await bus.publish(
        _command(
            "revise",
            {
                "artifact": _artifact_evidence(
                    learning_id="learn-feedback:rev1",
                    content=revised_content,
                    supersedes="learn-feedback",
                ),
                "revision": {
                    "content": revised_content,
                    "superseded_id": "learn-feedback",
                    "revision_id": "learn-feedback:rev1",
                },
            },
        )
    )
    await bus.flush()
    await bus.flush()

    # The superseding candidate re-entered the one install pipeline and,
    # under yolo autonomy, replaced the installed skill content.
    installed = await runtime.skills.show("valkyrie-inspect-kubernetes-pod-oomkilled")
    assert "Escalate to the operator before deleting pods." in installed["skill"]["content"]
    record = store.get("learn-feedback:rev1")
    assert record.status == "adopted"
    assert record.candidate.metadata["supersedes"] == "learn-feedback"
    assert any(
        event.event_type == registry.LEARNING_ADOPTION_RECORDED
        and event.payload.get("learning_id") == "learn-feedback:rev1"
        and event.payload.get("action") == "adopted"
        for event in events
    )

    await runtime.stop()
