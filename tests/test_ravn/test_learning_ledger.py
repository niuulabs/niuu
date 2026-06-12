"""Durable flock-learning ledger in the resident runtime (F5/F14, NIU-1034)."""

from __future__ import annotations

from ravn.adapters.reflection.flock_learning import FlockLearningStore
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.models import OperationalSignal
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from sleipnir.adapters.in_process import InProcessBus

SKILL = "valkyrie-inspect-kubernetes-pod-oomkilled"


def _artifact(**overrides) -> ResidentLearningArtifact:
    data = {
        "learning_id": "learn-oom",
        "title": SKILL,
        "summary": "OOM probe",
        "content": (
            f"# skill: {SKILL}\n\n"
            "metadata:\n"
            "  capability: inspect.kubernetes.pod.oomkilled\n"
            "  source: valkyrie-dream-cycle\n"
            "  safety_class: read_only\n"
        ),
        "artifact_type": "ravn_skill_tool",
        "scope": "flock",
        "confidence": 0.9,
        "source_environment_id": "cluster-a",
        "source_valkyrie_id": "valkyrie:k8s-a",
        "flock_id": "flock:k8s-valkyries",
        "domain": "k8s",
        "redaction_status": "redacted",
        "tool_code": "def run(signal):\n    return {'matches': True}\n",
        "canary_sample": {"kind": "Pod", "reason": "OOMKilled"},
    }
    data.update(overrides)
    return ResidentLearningArtifact(**data)


def _runtime(
    tmp_path,
    *,
    autonomy_mode: str = "yolo",
    store: FlockLearningStore | None = None,
) -> ResidentLearningRuntime:
    skill_dir = tmp_path / "skills"
    skills = SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "skill_management.json",
    )
    bus = InProcessBus()
    return ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-b",
            valkyrie_id="valkyrie:k8s-b",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode=autonomy_mode,
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "tools",
        learning_store=store or FlockLearningStore(tmp_path / "flock_learning.json"),
        legacy_probe_builder_enabled=True,
    )


async def test_adoption_is_recorded_in_the_durable_ledger(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    peer = _runtime(tmp_path, store=store)

    decision = await peer.evaluate_and_apply(_artifact())
    assert decision.action == "adopted"

    record = store.get("learn-oom")
    assert record.status == "adopted"
    assert record.active_environment_ids == ["cluster-b"]
    assert record.candidate.source_environment_id == "cluster-a"
    peer_decision = record.decision_for("cluster-b")
    assert peer_decision.action == "adopted"
    assert peer_decision.canary_passed is True


#: A tool implementation the reviewer must reject outright (forbidden import).
_BLOCKED_TOOL = "import subprocess\n\ndef run(signal):\n    return {}\n"


async def test_rejection_survives_restart_and_is_not_reevaluated(tmp_path) -> None:
    """NIU-1034: rejected learnings do not keep reappearing — even after restart."""
    store_path = tmp_path / "flock_learning.json"
    peer = _runtime(tmp_path, store=FlockLearningStore(store_path))

    first = await peer.evaluate_and_apply(_artifact(tool_code=_BLOCKED_TOOL))
    assert first.action == "rejected"
    assert first.relevant

    # Fresh runtime + fresh store instance over the same file = restart.
    restarted = _runtime(tmp_path, store=FlockLearningStore(store_path))
    second = await restarted.evaluate_and_apply(_artifact(tool_code=_BLOCKED_TOOL))
    assert second.action == "ignored"
    assert "previously declined" in second.rationale

    record = FlockLearningStore(store_path).get("learn-oom")
    assert len([d for d in record.peer_decisions if d.action == "rejected"]) == 1


async def test_guarded_hold_is_not_a_durable_decline(tmp_path) -> None:
    """A guarded peer waits for the operator; holding must not poison the ledger."""
    store_path = tmp_path / "flock_learning.json"
    guarded = _runtime(tmp_path, autonomy_mode="guarded", store=FlockLearningStore(store_path))

    held = await guarded.evaluate_and_apply(_artifact())
    assert held.action == "held"
    assert FlockLearningStore(store_path).list() == []

    # After an operator flips the resident to yolo, the same learning installs.
    operator = _runtime(tmp_path, autonomy_mode="yolo", store=FlockLearningStore(store_path))
    adopted = await operator.evaluate_and_apply(_artifact())
    assert adopted.action == "adopted"


async def test_operator_command_bypasses_the_declined_ledger(tmp_path) -> None:
    store_path = tmp_path / "flock_learning.json"
    peer = _runtime(tmp_path, store=FlockLearningStore(store_path))
    declined = await peer.evaluate_and_apply(_artifact(tool_code=_BLOCKED_TOOL))
    assert declined.action == "rejected"

    operator = _runtime(tmp_path, autonomy_mode="yolo", store=FlockLearningStore(store_path))
    forced = await operator.evaluate_and_apply(
        _artifact(operator_command=True, command_action="adopt")
    )
    assert forced.action == "adopted"


async def test_rollback_clears_active_environment_in_ledger(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    peer = _runtime(tmp_path, store=store)
    assert (await peer.evaluate_and_apply(_artifact())).action == "adopted"
    assert store.get("learn-oom").active_environment_ids == ["cluster-b"]

    retracted = await peer.retract(_artifact(command_action="rollback", operator_command=True))
    assert retracted.action == "rolled_back"
    record = store.get("learn-oom")
    assert record.active_environment_ids == []
    assert record.decision_for("cluster-b").action == "rolled_back"


async def test_irrelevant_learnings_stay_out_of_the_ledger(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    peer = _runtime(tmp_path, store=store)
    decision = await peer.evaluate_and_apply(
        _artifact(learning_id="learn-printer", flock_id="flock:printer-cell")
    )
    assert decision.action == "rejected"
    assert not decision.relevant
    assert store.list() == []


async def test_self_built_learning_lands_in_ledger_with_provenance(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    teacher = _runtime(tmp_path, store=store)
    result = await teacher.process_signal(
        OperationalSignal(
            signal_id="sig-1",
            event_type="signal.kubernetes.event",
            environment_id="cluster-b",
            domain="k8s",
            severity="warning",
            summary="Pod OOMKilled",
            payload={"kind": "Pod", "reason": "OOMKilled"},
        )
    )
    assert result["decision"] == "built_capability_for_next_signal"
    records = store.list()
    assert len(records) == 1
    assert records[0].status == "adopted"
    assert records[0].candidate.source_valkyrie_id == "valkyrie:k8s-b"
