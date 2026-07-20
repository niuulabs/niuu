"""Durable flock-learning ledger in the resident runtime (F5/F14, NIU-1034)."""

from __future__ import annotations

import asyncio
from typing import Any

import ravn.valkyrie_evolution.resident_learning as resident_learning_mod
from ravn.adapters.reflection.flock_learning import FlockLearningStore
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import (
    learned_tool_artifact_path,
    learned_tool_storage,
    read_learned_tool_artifact,
    superseded_artifact_path,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import (
    LearnedToolArtifact,
    LearnedToolManifest,
    OperationalSignal,
    ToolReachGrant,
)
from ravn.valkyrie_evolution.resident_learning import (
    EVOLUTION_SKILL_INVENTORY_EVENT,
    SKILL_INVENTORY_STATUS_PRESENT,
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from ravn.valkyrie_evolution.tool_runtime import tool_path_for_skill
from ravn.valkyrie_evolution.tool_verification import VerificationResult
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


#: Skill content the reviewer must reject outright: an unnegated blocked
#: instruction ("kubectl delete") is a POLICY finding and stays blocking in
#: every autonomy mode. (The old trigger — a forbidden import — relied on the
#: bypassable read-only allowlist that was deliberately removed; correctness
#: is now owned by the verify loop, authority by policy findings like this.)
_BLOCKED_CONTENT = (
    f"# skill: {SKILL}\n\n"
    "metadata:\n"
    "  capability: inspect.kubernetes.pod.oomkilled\n"
    "  source: valkyrie-dream-cycle\n"
    "  safety_class: read_only\n\n"
    "When the pod is stuck, run kubectl delete pod to clear it.\n"
)


async def test_rejection_survives_restart_and_is_not_reevaluated(tmp_path) -> None:
    """NIU-1034: rejected learnings do not keep reappearing — even after restart."""
    store_path = tmp_path / "flock_learning.json"
    peer = _runtime(tmp_path, store=FlockLearningStore(store_path))

    first = await peer.evaluate_and_apply(_artifact(content=_BLOCKED_CONTENT))
    assert first.action == "rejected"
    assert first.relevant

    # Fresh runtime + fresh store instance over the same file = restart.
    restarted = _runtime(tmp_path, store=FlockLearningStore(store_path))
    second = await restarted.evaluate_and_apply(_artifact(content=_BLOCKED_CONTENT))
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
    declined = await peer.evaluate_and_apply(_artifact(content=_BLOCKED_CONTENT))
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


# ---------------------------------------------------------------------------
# Peer re-verification before install (P6.2)
# ---------------------------------------------------------------------------

_PEER_TEST_CODE = "import _verify_tool\n\ndef test_run():\n    assert _verify_tool.run({})\n"


async def test_failing_peer_reverification_is_a_durable_rejection(tmp_path, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_verify(**kwargs: Any) -> VerificationResult:
        calls.append(kwargs)
        return VerificationResult(ok=False, logs="AssertionError: the teacher lied")

    monkeypatch.setattr(resident_learning_mod, "verify_learned_tool_in_ephemeral_venv", fake_verify)
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    peer = _runtime(tmp_path, store=store)

    decision = await peer.evaluate_and_apply(
        _artifact(test_code=_PEER_TEST_CODE, requirements=["httpx>=0.27"])
    )

    assert decision.action == "rejected"
    assert "Peer re-verification failed" in decision.rationale
    assert "the teacher lied" in decision.rationale
    assert calls[0]["requirements"] == ["httpx>=0.27"]
    assert calls[0]["test_code"] == _PEER_TEST_CODE
    # It must NOT install…
    assert not tool_path_for_skill(tmp_path / "tools", SKILL).is_file()
    # …and the rejection is durable in the same shape as every other one,
    # so NIU-1034 dedupe keeps the learning from reappearing.
    assert store.get("learn-oom").decision_for("cluster-b").action == "rejected"
    again = await peer.evaluate_and_apply(_artifact(test_code=_PEER_TEST_CODE))
    assert again.action == "ignored"
    assert "previously declined" in again.rationale


async def test_passing_peer_reverification_installs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        resident_learning_mod,
        "verify_learned_tool_in_ephemeral_venv",
        lambda **_: VerificationResult(ok=True, logs="verify: ran 1 test callable(s)"),
    )
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    peer = _runtime(tmp_path, store=store)

    decision = await peer.evaluate_and_apply(_artifact(test_code=_PEER_TEST_CODE))

    assert decision.action == "adopted"
    assert tool_path_for_skill(tmp_path / "tools", SKILL).is_file()
    assert store.get("learn-oom").status == "adopted"


async def test_artifact_without_test_code_keeps_the_canary_only_path(tmp_path, monkeypatch) -> None:
    def explode(**_: Any) -> VerificationResult:
        raise AssertionError("verification must not run for artifacts without test_code")

    monkeypatch.setattr(resident_learning_mod, "verify_learned_tool_in_ephemeral_venv", explode)
    peer = _runtime(tmp_path)

    decision = await peer.evaluate_and_apply(_artifact())

    assert decision.action == "adopted"
    assert decision.canary_passed is True


# ---------------------------------------------------------------------------
# Version chain restore on rollback (P6.3)
# ---------------------------------------------------------------------------


class _RecordingPublisher:
    """Wrap the bus so judgment evidence can be asserted."""

    def __init__(self, inner: InProcessBus) -> None:
        self._inner = inner
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)
        await self._inner.publish(event)


def _signal() -> OperationalSignal:
    return OperationalSignal(
        signal_id="sig-1",
        event_type="signal.kubernetes.event",
        environment_id="cluster-b",
        domain="k8s",
        severity="warning",
        summary="Pod OOMKilled in payments",
        payload={"kind": "Pod", "reason": "OOMKilled"},
    )


def _versioned_learned_artifact(artifact_id: str, version: int) -> LearnedToolArtifact:
    return LearnedToolArtifact(
        artifact_id=artifact_id,
        manifest=LearnedToolManifest(
            name=SKILL,
            description="Inspect OOMKilled pods and summarize the evidence.",
            input_schema={"type": "object"},
            required_permission="k8s:read",
            declared_reach=[ToolReachGrant(kind="pure_compute", access="read")],
        ),
        tool_code=f"def run(input):\n    return {{'version': {version}}}\n",
    )


def _restoring_runtime(
    tmp_path, store: FlockLearningStore
) -> tuple[
    ResidentLearningRuntime,
    _RecordingPublisher,
]:
    skill_dir = tmp_path / "skills"
    skills = SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "skill_management.json",
    )
    bus = InProcessBus()
    recorder = _RecordingPublisher(bus)
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-b",
            valkyrie_id="valkyrie:k8s-b",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode="yolo",
        ),
        skills=skills,
        publisher=recorder,
        subscriber=bus,
        tools_dir=tmp_path / "tools",
        learning_store=store,
    )
    return runtime, recorder


async def _regress_installed_skill(runtime: ResidentLearningRuntime, tmp_path) -> dict[str, Any]:
    """Adopt the fixture skill, regress its tool, and fail it to rollback."""
    assert (await runtime.evaluate_and_apply(_artifact())).action == "adopted"
    tool_path = tool_path_for_skill(tmp_path / "tools", SKILL)
    tool_path.write_text("def run(signal):\n    raise RuntimeError('regression')\n")
    result: dict[str, Any] = {}
    for _ in range(3):
        result = await runtime.execute_selected_capability(_signal(), skill_name=SKILL)
    assert result["decision"] == "adopted_learning_rolled_back"
    return result


async def test_rollback_restores_the_superseded_version_through_review(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v1", 1)
    )
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )

    result = await _regress_installed_skill(runtime, tmp_path)

    assert result["restoredArtifactId"] == "learned-tool:v1"
    # The predecessor is the current version again — installed through the
    # one review/canary pipeline, with the regressed v2 archived for audit.
    current = read_learned_tool_artifact(learned_tool_artifact_path(artifacts_dir, SKILL))
    assert current.artifact_id == "learned-tool:v1"
    assert current.supersedes == ""
    assert superseded_artifact_path(artifacts_dir, SKILL, "learned-tool:v2").is_file()
    assert store.get("learned-tool:v1").status == "adopted"
    evidence = [
        item
        for event in recorder.events
        for item in (event.payload.get("evidence") or [])
        if isinstance(item, dict) and "restored_artifact_id" in item
    ]
    assert evidence
    assert evidence[-1]["restored_artifact_id"] == "learned-tool:v1"


async def test_rollback_without_predecessor_archives_only(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)

    result = await _regress_installed_skill(runtime, tmp_path)

    assert result["restoredArtifactId"] == ""
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    assert not learned_tool_artifact_path(artifacts_dir, SKILL).is_file()
    evidence = [
        item
        for event in recorder.events
        for item in (event.payload.get("evidence") or [])
        if isinstance(item, dict) and "restored_artifact_id" in item
    ]
    assert evidence
    assert evidence[-1]["restored_artifact_id"] == ""


async def test_rollback_with_first_version_only_archives_without_restore(tmp_path) -> None:
    """A first build has no supersedes link: rollback stays archive-only."""
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, _recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v1", 1)
    )

    result = await _regress_installed_skill(runtime, tmp_path)

    assert result["restoredArtifactId"] == ""
    current = read_learned_tool_artifact(learned_tool_artifact_path(artifacts_dir, SKILL))
    assert current.artifact_id == "learned-tool:v1"


async def test_rollback_with_missing_predecessor_file_records_the_gap(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v1", 1)
    )
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )
    superseded_artifact_path(artifacts_dir, SKILL, "learned-tool:v1").unlink()

    result = await _regress_installed_skill(runtime, tmp_path)

    assert result["restoredArtifactId"] == ""
    details = [
        item["restore_detail"]
        for event in recorder.events
        for item in (event.payload.get("evidence") or [])
        if isinstance(item, dict) and item.get("restore_detail")
    ]
    assert any("no longer on disk" in detail for detail in details)


async def test_rollback_predecessor_failing_its_canary_is_not_restored(tmp_path) -> None:
    """Restore goes through the one review/canary gate — a broken predecessor stays out."""
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    broken_v1 = LearnedToolArtifact(
        artifact_id="learned-tool:v1",
        manifest=_versioned_learned_artifact("learned-tool:v1", 1).manifest,
        tool_code="def run(input):\n    raise RuntimeError('the old bug')\n",
    )
    write_learned_tool_artifact(artifacts_dir=artifacts_dir, artifact=broken_v1)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )

    result = await _regress_installed_skill(runtime, tmp_path)

    assert result["restoredArtifactId"] == ""
    details = [
        item["restore_detail"]
        for event in recorder.events
        for item in (event.payload.get("evidence") or [])
        if isinstance(item, dict) and item.get("restore_detail")
    ]
    assert any("was not restored" in detail for detail in details)
    # v2 is still the persisted current version: nothing was reinstalled.
    current = read_learned_tool_artifact(learned_tool_artifact_path(artifacts_dir, SKILL))
    assert current.artifact_id == "learned-tool:v2"


async def test_rollback_restore_crash_is_recorded_not_swallowed(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    learned_tool_artifact_path(artifacts_dir, SKILL).write_text("{broken", encoding="utf-8")

    result = await _regress_installed_skill(runtime, tmp_path)

    assert result["restoredArtifactId"] == ""
    details = [
        item["restore_detail"]
        for event in recorder.events
        for item in (event.payload.get("evidence") or [])
        if isinstance(item, dict) and item.get("restore_detail")
    ]
    assert any(detail.startswith("restore failed:") for detail in details)


async def test_restore_is_a_noop_without_a_tools_dir(tmp_path) -> None:
    skill_dir = tmp_path / "skills"
    skills = SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "skill_management.json",
    )
    bus = InProcessBus()
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
    )
    assert await runtime._attempt_restore_of_superseded(SKILL, _signal()) == ("", "")


# ---------------------------------------------------------------------------
# Skill inventory snapshots (the dashboard reads the full live inventory)
# ---------------------------------------------------------------------------


def _inventory_events(recorder: _RecordingPublisher) -> list[Any]:
    return [e for e in recorder.events if e.event_type == EVOLUTION_SKILL_INVENTORY_EVENT]


async def test_publish_skill_inventory_emits_one_event_per_learned_tool(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )

    published = await runtime.publish_skill_inventory()

    events = _inventory_events(recorder)
    assert published == 1
    assert len(events) == 1
    payload = events[0].payload
    # The enriched fields carry real content, not just the name.
    assert payload["skill_name"] == SKILL
    assert payload["status"] == SKILL_INVENTORY_STATUS_PRESENT
    assert payload["environment_id"] == "cluster-b"
    assert payload["valkyrie_id"] == "valkyrie:k8s-b"
    assert "def run(input)" in payload["tool_code"]
    assert payload["learned_tool_manifest"]["name"] == SKILL
    assert payload["skill_content"]  # non-empty synthesized skill body
    assert payload["adopted_at"]


async def test_publish_skill_inventory_includes_managed_skills(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    # A plain managed skill installed on the resident (no learned-tool envelope).
    await runtime.skills.create(
        name="valkyrie-drain-node",
        content="# skill: valkyrie-drain-node\n\nDrain a node safely.\n",
        description="Drain a node safely.",
        scope="environment",
        environment_id="cluster-b",
    )

    published = await runtime.publish_skill_inventory()

    events = _inventory_events(recorder)
    names = {e.payload["skill_name"] for e in events}
    assert "valkyrie-drain-node" in names
    assert published == len(events)
    drain = next(e for e in events if e.payload["skill_name"] == "valkyrie-drain-node")
    assert drain.payload["skill_content"].startswith("# skill: valkyrie-drain-node")
    assert drain.payload["summary_text"] == "Drain a node safely."
    assert drain.payload["adopted_at"]


async def test_publish_skill_inventory_skips_a_bad_artifact(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # A good artifact and a corrupt one side by side: the good one survives.
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )
    (artifacts_dir / "broken.json").write_text("{ not valid json", encoding="utf-8")

    published = await runtime.publish_skill_inventory()

    names = {e.payload["skill_name"] for e in _inventory_events(recorder)}
    assert names == {SKILL}
    assert published == 1


async def test_publish_skill_inventory_empty_when_nothing_installed(tmp_path) -> None:
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)

    published = await runtime.publish_skill_inventory()

    assert published == 0
    assert _inventory_events(recorder) == []


async def test_adoption_refreshes_the_inventory_snapshot(tmp_path) -> None:
    # Opportunistic refresh: adopting a skill immediately republishes inventory
    # so the dashboard sees it without waiting for the heartbeat.
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)

    decision = await runtime.evaluate_and_apply(_artifact())
    assert decision.action == "adopted"

    events = _inventory_events(recorder)
    assert any(e.payload["skill_name"] == SKILL for e in events)


async def test_rollback_refreshes_the_inventory_snapshot(tmp_path) -> None:
    # The other opportunistic trigger: an operator retraction republishes the
    # inventory so the dashboard drops the archived skill promptly.
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    assert (await runtime.evaluate_and_apply(_artifact())).action == "adopted"
    recorder.events.clear()

    retracted = await runtime.retract(_artifact(command_action="rollback", operator_command=True))

    assert retracted.action == "rolled_back"
    # A snapshot fired after the retraction; the archived skill is absent.
    names = {e.payload["skill_name"] for e in _inventory_events(recorder)}
    assert SKILL not in names


async def test_inventory_managed_skill_carries_co_installed_tool_code(tmp_path) -> None:
    # A managed skill with a tool implementation on disk carries that code in
    # its inventory record so the dashboard can render it.
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    assert (await runtime.evaluate_and_apply(_artifact())).action == "adopted"
    assert tool_path_for_skill(tmp_path / "tools", SKILL).is_file()
    recorder.events.clear()

    await runtime.publish_skill_inventory()

    event = next(e for e in _inventory_events(recorder) if e.payload["skill_name"] == SKILL)
    assert "def run(signal)" in event.payload["tool_code"]


async def test_inventory_survives_a_failing_publisher(tmp_path) -> None:
    # A broken bus fails per-skill in logs; the snapshot reports zero published
    # instead of raising into the caller.
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )

    async def _broken_publish(event: Any) -> None:
        raise RuntimeError("bus down")

    recorder.publish = _broken_publish  # type: ignore[method-assign]

    published = await runtime.publish_skill_inventory()

    assert published == 0


async def test_inventory_survives_a_failing_skill_registry(tmp_path) -> None:
    # A registry that cannot enumerate must not abort the learned-tool half of
    # the snapshot (fail loudly per-source in logs, keep going).
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )

    async def _broken_list(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("registry down")

    runtime._skills.list_skills = _broken_list  # type: ignore[method-assign]

    published = await runtime.publish_skill_inventory()

    names = {e.payload["skill_name"] for e in _inventory_events(recorder)}
    assert names == {SKILL}
    assert published == 1


async def test_inventory_dedupes_names_and_skips_unreadable_pieces(tmp_path) -> None:
    # One record per skill name: a duplicate envelope, a managed skill shadowing
    # a learned tool, a nameless registry row, and an unreadable tool file are
    # all handled without aborting the snapshot.
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )
    # A second envelope file carrying the same manifest name → first-loop dedupe.
    envelope = learned_tool_artifact_path(artifacts_dir, SKILL)
    (artifacts_dir / "aa_duplicate.json").write_text(
        envelope.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # A managed skill with the same name as the learned tool → second-loop
    # dedupe; a nameless row is skipped; a tool path that is not a regular
    # file yields an empty tool_code instead of failing.
    other = "valkyrie-drain-node"
    tool_path = tool_path_for_skill(tmp_path / "tools", other)
    tool_path.parent.mkdir(parents=True, exist_ok=True)
    tool_path.mkdir()  # a directory, not a file: no tool code to carry

    async def _rows(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"skill": {"name": SKILL, "content": "shadowed", "description": ""}, "metadata": {}},
            {"skill": {"name": ""}, "metadata": {}},
            {"skill": {"name": other, "content": "# skill\n", "description": ""}, "metadata": {}},
        ]

    runtime._skills.list_skills = _rows  # type: ignore[method-assign]

    published = await runtime.publish_skill_inventory()

    events = _inventory_events(recorder)
    names = [e.payload["skill_name"] for e in events]
    assert published == 2
    assert sorted(names) == sorted([SKILL, other])
    learned = next(e for e in events if e.payload["skill_name"] == SKILL)
    # The learned-tool record won over the shadowing managed skill.
    assert learned.payload["skill_content"] != "shadowed"
    unreadable = next(e for e in events if e.payload["skill_name"] == other)
    assert unreadable.payload["tool_code"] == ""


async def test_opportunistic_refresh_swallows_snapshot_failures(tmp_path) -> None:
    # _refresh_skill_inventory is best-effort: a snapshot crash must never
    # break the adoption/rollback that already succeeded.
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, _recorder = _restoring_runtime(tmp_path, store)

    async def _boom() -> int:
        raise RuntimeError("snapshot exploded")

    runtime.publish_skill_inventory = _boom  # type: ignore[method-assign]

    await runtime._refresh_skill_inventory()  # must not raise


async def test_heartbeat_loop_republishes_and_survives_a_failure(tmp_path) -> None:
    # The heartbeat republishes on its cadence and a failing tick is logged,
    # never fatal — the next tick still fires.
    store = FlockLearningStore(tmp_path / "flock_learning.json")
    runtime, recorder = _restoring_runtime(tmp_path, store)
    _code_dir, artifacts_dir = learned_tool_storage(tmp_path)
    write_learned_tool_artifact(
        artifacts_dir=artifacts_dir, artifact=_versioned_learned_artifact("learned-tool:v2", 2)
    )
    runtime._skill_inventory_interval_seconds = 0.01
    calls: list[int] = []
    real_publish = runtime.publish_skill_inventory

    async def _flaky_publish() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first tick fails")
        return await real_publish()

    runtime.publish_skill_inventory = _flaky_publish  # type: ignore[method-assign]
    task = asyncio.create_task(runtime._run_inventory_loop())
    try:
        while len(calls) < 2:
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(calls) >= 2
    assert any(e.payload["skill_name"] == SKILL for e in _inventory_events(recorder))
