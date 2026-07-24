"""Auto-rollback of adopted and self-built skills on regression (NIU-1041)."""

from __future__ import annotations

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.models import OperationalSignal
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from ravn.valkyrie_evolution.tool_runtime import tool_path_for_skill
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from tests.ravn.fixtures.fakes import BusRecorder
from tests.ravn.fixtures.skills import probe_skill_content

SKILL_NAME = "valkyrie-inspect-kubernetes-pod-oomkilled"
CAPABILITY = "inspect.kubernetes.pod.oomkilled"

# Succeeds when the payload carries the canary marker, fails on real signals —
# the adoption canary passes, then production traffic regresses.
FLAKY_TOOL = """def run(signal):
    payload = signal.get("payload", signal)
    if payload.get("canary"):
        return {"matches": True, "observed": payload}
    raise RuntimeError("does not transfer to this cluster")
"""

HEALTHY_TOOL = """def run(signal):
    return {"matches": True, "observed": signal.get("payload", {})}
"""


def _artifact(tool_code: str) -> ResidentLearningArtifact:
    return ResidentLearningArtifact(
        learning_id="learn-oom",
        title=SKILL_NAME,
        summary="OOM probe from cluster-a",
        content=probe_skill_content(SKILL_NAME, capability=CAPABILITY),
        artifact_type="ravn_skill_tool",
        scope="flock",
        confidence=0.9,
        source_environment_id="cluster-a",
        source_valkyrie_id="valkyrie:k8s-a",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        redaction_status="redacted",
        tool_code=tool_code,
        canary_sample={"kind": "Pod", "reason": "OOMKilled", "canary": True},
    )


def _signal(n: int = 1) -> OperationalSignal:
    return OperationalSignal(
        signal_id=f"sig-{n}",
        event_type="signal.kubernetes.event",
        environment_id="cluster-b",
        domain="k8s",
        severity="warning",
        summary="Pod OOMKilled",
        payload={"kind": "Pod", "reason": "OOMKilled", "namespace": "payments"},
    )


async def _peer(tmp_path, *, threshold: int = 3) -> tuple[ResidentLearningRuntime, BusRecorder]:
    skill_dir = tmp_path / "skills"
    skills = SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "skill_management.json",
    )
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-b",
            valkyrie_id="valkyrie:k8s-b",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode="yolo",
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "tools",
        rollback_consecutive_failures=threshold,
    )
    return runtime, recorder


async def test_regressing_tool_is_rolled_back_after_threshold(tmp_path) -> None:
    peer, recorder = await _peer(tmp_path, threshold=3)
    decision = await peer.evaluate_and_apply(_artifact(FLAKY_TOOL))
    assert decision.action == "adopted"

    results = [
        await peer.execute_selected_capability(_signal(n), skill_name=SKILL_NAME)
        for n in range(1, 4)
    ]
    assert [r["decision"] for r in results[:2]] == ["adopted_learning_failed"] * 2
    assert results[2]["decision"] == "adopted_learning_rolled_back"
    assert results[2]["consecutiveFailures"] == 3

    # Archived: the skill no longer resolves for the capability.
    follow_up = await peer.execute_selected_capability(_signal(99), skill_name=SKILL_NAME)
    assert follow_up["decision"] != "adopted_learning_failed"

    rollbacks = await recorder.of_type("valkyrie.evolution.rolled_back")
    assert len(rollbacks) == 1
    assert rollbacks[0].payload["command_action"] == "auto_rollback_regression"

    regressions = [
        event
        for event in await recorder.of_type(registry.LEARNING_ADOPTION_RECORDED)
        if event.payload.get("action") == "regressed"
    ]
    assert len(regressions) == 1
    payload = regressions[0].payload
    assert payload["learning_id"] == "learn-oom"
    assert payload["canary_passed"] is False
    assert "consecutive failures" in payload["rationale"]
    # Negative transfer fans out on the flock-scoped subject.
    assert any(
        subject.startswith("flock.") for subject in payload.get("additional_nats_subjects", [])
    )


async def test_transient_failures_do_not_roll_back(tmp_path) -> None:
    peer, recorder = await _peer(tmp_path, threshold=3)
    await peer.evaluate_and_apply(_artifact(FLAKY_TOOL))

    # Two failures, then repair the tool in place: success resets the streak.
    await peer.execute_selected_capability(_signal(1), skill_name=SKILL_NAME)
    await peer.execute_selected_capability(_signal(2), skill_name=SKILL_NAME)
    tool_path_for_skill(tmp_path / "tools", SKILL_NAME).write_text(HEALTHY_TOOL)
    recovered = await peer.execute_selected_capability(_signal(3), skill_name=SKILL_NAME)
    assert recovered["decision"] == "inspect_with_adopted_learning"

    lifecycle = (await peer._skills.show(SKILL_NAME))["metadata"]
    assert lifecycle["consecutive_failures"] == 0
    assert lifecycle["failure_count"] == 2
    assert not await recorder.of_type("valkyrie.evolution.rolled_back")


async def test_capability_defers_to_investigation_after_rollback(tmp_path) -> None:
    peer, recorder = await _peer(tmp_path, threshold=2)
    await peer.evaluate_and_apply(_artifact(FLAKY_TOOL))

    await peer.execute_selected_capability(_signal(1), skill_name=SKILL_NAME)
    rolled = await peer.execute_selected_capability(_signal(2), skill_name=SKILL_NAME)
    assert rolled["decision"] == "adopted_learning_rolled_back"

    # The skill is archived: the next signal finds no installed capability and
    # defers to the build_tool investigation loop instead of running the
    # rolled-back tool again.
    after = await peer.execute_selected_capability(_signal(3), skill_name=SKILL_NAME)
    assert after["decision"] == "selected_capability_unavailable"


async def test_rollback_judgment_escalates_with_evidence(tmp_path) -> None:
    peer, recorder = await _peer(tmp_path, threshold=1)
    await peer.evaluate_and_apply(_artifact(FLAKY_TOOL))
    result = await peer.execute_selected_capability(_signal(1), skill_name=SKILL_NAME)
    assert result["decision"] == "adopted_learning_rolled_back"

    judgments = await recorder.of_type(registry.VALKYRIE_JUDGMENT_PROPOSED)
    rollback_judgments = [
        event
        for event in judgments
        if event.payload.get("recommended_action") == "rebuild_rolled_back_capability"
    ]
    assert len(rollback_judgments) == 1
    payload = rollback_judgments[0].payload
    assert payload["tier"] == "present"
    evidence = payload["evidence"][0]
    assert evidence["learning_source"] == "flock-learning:learn-oom"
    assert evidence["consecutive_failures"] == 1
    assert "does not transfer" in evidence["tool_stderr"]
