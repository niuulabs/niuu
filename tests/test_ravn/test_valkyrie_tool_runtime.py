"""Tests for resident-built tool implementations: runtime, builders, loop."""

from __future__ import annotations

import json

import pytest

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.odin.review import ReviewItem, ReviewKind, ReviewStatus, review_decided_event
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import (
    LearnedToolError,
    learned_tool_path,
    load_learned_tool,
    read_learned_tool_artifact,
    tool_implementation_findings,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import (
    LearnedToolArtifact,
    LearnedToolManifest,
    OperationalSignal,
    ToolReachGrant,
)
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from ravn.valkyrie_evolution.tool_runtime import run_tool, tool_path_for_skill, write_tool
from sleipnir.adapters.in_process import InProcessBus
from tests.ravn.fixtures.skills import probe_skill_content


def _signal(capability_payload: dict | None = None) -> OperationalSignal:
    return OperationalSignal(
        signal_id="sig-1",
        event_type="signal.kubernetes.event",
        environment_id="cluster-a",
        domain="k8s",
        severity="warning",
        summary="Pod OOMKilled in payments",
        payload=capability_payload
        or {"kind": "Pod", "reason": "OOMKilled", "namespace": "payments"},
    )


# ---------------------------------------------------------------------------
# tool_runtime
# ---------------------------------------------------------------------------


async def test_run_tool_executes_entry_point_and_returns_json(tmp_path) -> None:
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="probe",
        tool_code="def run(signal):\n    return {'ok': True, 'echo': signal['payload']['kind']}\n",
    )
    result = await run_tool(path, {"payload": {"kind": "Pod"}})
    assert result.ok
    assert result.result == {"ok": True, "echo": "Pod"}


async def test_run_tool_reports_broken_tool_without_raising(tmp_path) -> None:
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="broken",
        tool_code="def run(signal):\n    raise RuntimeError('boom')\n",
    )
    result = await run_tool(path, {})
    assert not result.ok
    assert "status 1" in result.error
    assert "boom" in result.stderr


async def test_run_tool_enforces_timeout(tmp_path) -> None:
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="slow",
        tool_code="import time\n\ndef run(signal):\n    time.sleep(5)\n    return {}\n",
    )
    result = await run_tool(path, {}, timeout_seconds=0.5)
    assert not result.ok
    assert "timed out" in result.error


async def test_run_tool_rejects_non_object_output(tmp_path) -> None:
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="list-output",
        tool_code="def run(signal):\n    return [1, 2]\n",
    )
    result = await run_tool(path, {})
    assert not result.ok
    assert "JSON object" in result.error


async def test_run_tool_missing_file_fails_loudly(tmp_path) -> None:
    result = await run_tool(tmp_path / "nope.py", {})
    assert not result.ok
    assert "missing" in result.error


async def test_run_tool_does_not_leak_resident_environment(tmp_path, monkeypatch) -> None:
    # A learned tool must not inherit ambient secrets from the resident process.
    monkeypatch.setenv("RAVN_VOLUNDR_PAT", "super-secret-token")
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="env_probe",
        tool_code=(
            "import os\n\n"
            "def run(signal):\n"
            "    return {'leaked': os.environ.get('RAVN_VOLUNDR_PAT', '')}\n"
        ),
    )
    result = await run_tool(path, {})
    assert result.ok
    assert result.result == {"leaked": ""}


def test_write_tool_rejects_empty_code(tmp_path) -> None:
    with pytest.raises(ValueError, match="empty"):
        write_tool(tools_dir=tmp_path, skill_name="empty", tool_code="   ")


# ---------------------------------------------------------------------------
# learned agent tools
# ---------------------------------------------------------------------------


def _learned_tool_artifact() -> LearnedToolArtifact:
    manifest = LearnedToolManifest(
        name="mimir_metric_window",
        description="Query a bounded metric window and summarize the series.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "points": {"type": "integer"}},
        },
        required_permission="mimir:read",
        declared_reach=[
            ToolReachGrant(
                kind="network",
                target="https://mimir.internal",
                access="read",
                metadata={"reason": "query metrics"},
            )
        ],
    )
    return LearnedToolArtifact(
        artifact_id="learned-tool:mimir_metric_window",
        manifest=manifest,
        tool_code=(
            "def run(input):\n"
            "    query = input.get('query', '')\n"
            "    return {'query': query, 'points': 0, 'source': 'fixture'}\n"
        ),
        source_signal_ids=["sig-1"],
        source_session_id="session-1",
        source_gap_id="gap-1",
        source_build_id="build-1",
        provenance={"builder": "test"},
    )


def test_learned_tool_artifact_round_trips_manifest_and_reach(tmp_path) -> None:
    artifact = _learned_tool_artifact()
    path = write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=artifact)

    loaded = read_learned_tool_artifact(path)

    assert loaded.artifact_type == "agent_tool"
    assert loaded.manifest.name == "mimir_metric_window"
    assert loaded.manifest.required_permission == "mimir:read"
    assert loaded.manifest.declared_reach[0].kind == "network"
    assert loaded.manifest.declared_reach[0].target == "https://mimir.internal"
    assert loaded.source_signal_ids == ["sig-1"]


def test_learned_tool_artifact_round_trips_test_code_and_requirements() -> None:
    manifest = _learned_tool_artifact().manifest
    artifact = LearnedToolArtifact(
        artifact_id="learned-tool:with-tests",
        manifest=manifest,
        tool_code="def run(input):\n    return {'ok': True}\n",
        test_code="def test_run():\n    assert run({})['ok'] is True\n",
        requirements=["httpx>=0.27", "prometheus-api-client"],
        provenance={"backend": "forge_session"},
    )

    payload = artifact.to_dict()
    assert payload["test_code"].startswith("def test_run")
    assert payload["requirements"] == ["httpx>=0.27", "prometheus-api-client"]

    restored = LearnedToolArtifact.from_dict(payload)
    assert restored.test_code == artifact.test_code
    assert restored.requirements == artifact.requirements


def test_learned_tool_artifact_from_dict_defaults_missing_new_fields() -> None:
    # An old artifact persisted before contract v2 has neither key on disk.
    legacy = {
        "artifact_id": "learned-tool:legacy",
        "manifest": _learned_tool_artifact().manifest.to_dict(),
        "tool_code": "def run(input):\n    return {}\n",
    }
    restored = LearnedToolArtifact.from_dict(legacy)
    assert restored.test_code == ""
    assert restored.requirements == []
    # And the round trip re-emits the new fields with their empty defaults.
    assert restored.to_dict()["test_code"] == ""
    assert restored.to_dict()["requirements"] == []


async def test_learned_tool_loads_as_agent_tool_port_and_executes(tmp_path) -> None:
    artifact = _learned_tool_artifact()
    tool_path = write_learned_tool(tools_dir=tmp_path, artifact=artifact)
    tool = load_learned_tool(artifact=artifact, tool_path=tool_path)

    assert tool.name == "mimir_metric_window"
    assert tool.required_permission == "mimir:read"
    assert tool.input_schema["required"] == ["query"]
    assert learned_tool_path(tmp_path, "mimir_metric_window") == tool_path

    result = await tool.execute({"query": "up"})

    assert not result.is_error
    assert json.loads(result.content) == {"points": 0, "query": "up", "source": "fixture"}


def test_reach_grant_accepts_kind_shorthand_and_rejects_other_shapes() -> None:
    # LLMs often write declared_reach as ["pure_compute"]; a bare string is
    # the grant's kind. Anything else non-object must fail with a clear error.
    grant = ToolReachGrant.from_dict("pure_compute")
    assert grant.kind == "pure_compute"
    assert grant.access == "read"
    with pytest.raises(ValueError, match="declared_reach entries"):
        ToolReachGrant.from_dict(42)


def test_learned_tool_rejects_invalid_declared_reach(tmp_path) -> None:
    artifact = _learned_tool_artifact()
    bad = LearnedToolArtifact(
        artifact_id=artifact.artifact_id,
        manifest=LearnedToolManifest(
            name=artifact.manifest.name,
            description=artifact.manifest.description,
            input_schema=artifact.manifest.input_schema,
            required_permission=artifact.manifest.required_permission,
            declared_reach=[ToolReachGrant(kind="filesystem", access="root")],
        ),
        tool_code=artifact.tool_code,
    )

    with pytest.raises(LearnedToolError, match="unsupported reach access"):
        write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=bad)


# ---------------------------------------------------------------------------
# Tool implementation validators (shared by the reviewer and learned tools)
# ---------------------------------------------------------------------------


def test_tool_implementation_findings_does_not_gate_imports() -> None:
    # Structural validation no longer runs a Python-import allowlist: capability
    # is gated by declared_reach at review and enforced at the sandbox boundary.
    # A tool that shells out to acquire live evidence must pass structure checks.
    findings = tool_implementation_findings(
        "import subprocess\n\ndef run(signal):\n    return {}\n",
        entry_point="run",
    )
    assert findings == []


def test_tool_implementation_findings_requires_the_entry_point() -> None:
    findings = tool_implementation_findings(
        "def probe(signal):\n    return {}\n",
        entry_point="run",
    )
    assert any("does not define run" in finding for finding in findings)


def test_tool_implementation_findings_accepts_a_clean_tool() -> None:
    findings = tool_implementation_findings(
        "import json\n\ndef run(signal):\n    return {'ok': True}\n",
        entry_point="run",
    )
    assert findings == []


# ---------------------------------------------------------------------------
# Resident loop integration
# ---------------------------------------------------------------------------


def _runtime(
    tmp_path,
    name: str,
    autonomy_mode: str = "yolo",
) -> ResidentLearningRuntime:
    skill_dir = tmp_path / name / "skills"
    registry = FileSkillRegistry(
        skill_dirs=[str(skill_dir)],
        write_dir=skill_dir,
        include_builtin=False,
    )
    skills = SkillManagementRegistry(
        registry,
        metadata_path=tmp_path / name / "skill_management.json",
    )
    bus = InProcessBus()
    return ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id=f"env-{name}",
            valkyrie_id=f"valkyrie:{name}",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode=autonomy_mode,
            environment_type="k8s",
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / name / "tools",
    )


async def test_missing_capability_defers_to_the_investigation_loop(tmp_path) -> None:
    runtime = _runtime(tmp_path, "k8s-investigate")

    result = await runtime.process_signal(_signal())

    assert result["decision"] == "defer_to_investigation_with_build_tool"
    assert result["usedAdoptedLearning"] is False
    assert not (tmp_path / "k8s-investigate" / "tools").exists()


async def test_peer_adoption_installs_proposed_tool_implementation(tmp_path) -> None:
    peer = _runtime(tmp_path, "k8s-b")
    skill_name = "valkyrie-inspect-kubernetes-pod-oomkilled"
    artifact = ResidentLearningArtifact(
        learning_id="learn-oom",
        title=skill_name,
        summary="OOM probe",
        content=probe_skill_content(skill_name),
        artifact_type="ravn_skill_tool",
        scope="flock",
        confidence=0.9,
        source_environment_id="env-k8s-a",
        source_valkyrie_id="valkyrie:k8s-a",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        redaction_status="redacted",
        tool_code=(
            "def run(signal):\n"
            "    return {'matches': True, 'observed': signal.get('payload', {})}\n"
        ),
        canary_sample={"kind": "Pod", "reason": "OOMKilled"},
    )
    decision = await peer.evaluate_and_apply(artifact)
    assert decision.action == "adopted"
    peer_tool = tool_path_for_skill(tmp_path / "k8s-b" / "tools", skill_name)
    assert peer_tool.is_file()

    replay = await peer.process_signal(_signal())
    assert replay["usedAdoptedLearning"] is True
    assert replay["toolResult"]["matches"] is True


async def test_peer_adoption_reviews_canaries_and_installs_agent_tool(tmp_path) -> None:
    peer = _runtime(tmp_path, "k8s-agent-tool")
    learned = _learned_tool_artifact()
    artifact = ResidentLearningArtifact(
        learning_id=learned.artifact_id,
        title=learned.manifest.name,
        summary=learned.manifest.description,
        content="",
        artifact_type="agent_tool",
        scope="flock",
        confidence=0.9,
        source_environment_id="env-k8s-teacher",
        source_valkyrie_id="valkyrie:k8s-teacher",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        redaction_status="redacted",
        tool_code=learned.tool_code,
        tool_entry_point=learned.manifest.entry_point,
        learned_tool_manifest=learned.manifest.to_dict(),
        canary_sample={"query": "up"},
    )

    decision = await peer.evaluate_and_apply(artifact)

    assert decision.action == "adopted"
    assert decision.installed_skill_name == "mimir_metric_window"
    tool_path = tmp_path / "k8s-agent-tool" / "learned_tools" / "mimir_metric_window.py"
    artifact_path = (
        tmp_path / "k8s-agent-tool" / "learned_tool_artifacts" / "mimir_metric_window.json"
    )
    assert tool_path.is_file()
    installed = read_learned_tool_artifact(artifact_path)
    assert installed.manifest.name == "mimir_metric_window"
    assert installed.manifest.declared_reach[0].kind == "network"


async def test_review_approval_installs_self_authored_agent_tool(tmp_path) -> None:
    bus = InProcessBus()
    skill_dir = tmp_path / "k8s-reviewed-agent-tool" / "skills"
    registry = FileSkillRegistry(
        skill_dirs=[str(skill_dir)],
        write_dir=skill_dir,
        include_builtin=False,
    )
    skills = SkillManagementRegistry(
        registry,
        metadata_path=tmp_path / "k8s-reviewed-agent-tool" / "skill_management.json",
    )
    peer = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="env-k8s-reviewed-agent-tool",
            valkyrie_id="valkyrie:k8s-reviewed-agent-tool",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode="guarded",
            environment_type="k8s",
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "k8s-reviewed-agent-tool" / "tools",
    )
    await peer.start()
    learned = _learned_tool_artifact()
    item = ReviewItem.new(
        kind=ReviewKind.EVOLUTION_BUILD.value,
        requested_action="install",
        environment_id=peer.identity.environment_id,
        valkyrie_id=peer.identity.valkyrie_id,
        title=learned.manifest.name,
        summary=learned.manifest.description,
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        risk_class="medium",
        safety_class="mutating",
        dedupe_key="build_tool:env-k8s-reviewed-agent-tool:mimir_metric_window",
        evidence={
            "artifact": {
                "learning_id": learned.artifact_id,
                "title": learned.manifest.name,
                "summary": learned.manifest.description,
                "content": "",
                "artifact_type": "agent_tool",
                "scope": "environment",
                "confidence": 0.74,
                "source_environment_id": peer.identity.environment_id,
                "source_valkyrie_id": peer.identity.valkyrie_id,
                "promotion_id": learned.artifact_id,
                "flock_id": "flock:k8s-valkyries",
                "domain": "k8s",
                "redaction_status": "redacted",
                "tool_code": learned.tool_code,
                "tool_entry_point": learned.manifest.entry_point,
                "learned_tool_manifest": learned.manifest.to_dict(),
                "canary_sample": {"query": "up"},
            }
        },
        requested_by=peer.identity.valkyrie_id,
        correlation_id=learned.artifact_id,
    )
    item.decide(
        decision=ReviewStatus.APPROVED.value,
        operator_id="operator:odin",
        reason="safe enough for guarded install",
    )

    await bus.publish(review_decided_event(item, source="operator:odin"))
    await bus.flush()
    await bus.flush()

    adopted = [decision for decision in peer.decisions() if decision.action == "adopted"]
    assert adopted
    assert adopted[-1].installed_skill_name == "mimir_metric_window"
    tool_path = tmp_path / "k8s-reviewed-agent-tool" / "learned_tools" / "mimir_metric_window.py"
    artifact_path = (
        tmp_path / "k8s-reviewed-agent-tool" / "learned_tool_artifacts" / "mimir_metric_window.json"
    )
    assert tool_path.is_file()
    assert read_learned_tool_artifact(artifact_path).manifest.name == "mimir_metric_window"


async def test_failing_tool_surfaces_failure_judgment(tmp_path) -> None:
    runtime = _runtime(tmp_path, "k8s-c")
    skill_name = "valkyrie-inspect-kubernetes-pod-oomkilled"
    artifact = ResidentLearningArtifact(
        learning_id="learn-oom-c",
        title=skill_name,
        summary="OOM probe",
        content=probe_skill_content(skill_name),
        artifact_type="ravn_skill_tool",
        scope="flock",
        confidence=0.9,
        source_environment_id="env-k8s-x",
        source_valkyrie_id="valkyrie:k8s-x",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        redaction_status="redacted",
        tool_code=(
            "def run(signal):\n"
            "    return {'matches': True, 'observed': signal.get('payload', {})}\n"
        ),
        canary_sample={"kind": "Pod", "reason": "OOMKilled"},
    )
    assert (await runtime.evaluate_and_apply(artifact)).action == "adopted"

    # Regress the installed implementation in place, then replay the signal.
    tool_path = tool_path_for_skill(tmp_path / "k8s-c" / "tools", skill_name)
    tool_path.write_text("def run(signal):\n    raise RuntimeError('regression')\n")

    replay = await runtime.process_signal(_signal())
    assert replay["decision"] == "adopted_learning_failed"
    assert "error" in replay["toolResult"]


# ---------------------------------------------------------------------------
# Canary before ACK (NIU-1038)
# ---------------------------------------------------------------------------


async def test_broken_proposed_tool_is_rejected_by_canary(tmp_path) -> None:
    peer = _runtime(tmp_path, "k8s-canary")
    artifact = ResidentLearningArtifact(
        learning_id="learn-broken",
        title="valkyrie-inspect-kubernetes-pod-oomkilled",
        summary="Broken probe",
        content=probe_skill_content("valkyrie-inspect-kubernetes-pod-oomkilled"),
        artifact_type="ravn_skill_tool",
        scope="flock",
        confidence=0.9,
        source_environment_id="env-k8s-x",
        source_valkyrie_id="valkyrie:k8s-x",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        redaction_status="redacted",
        tool_code="def run(signal):\n    raise RuntimeError('does not work here')\n",
        canary_sample={"kind": "Pod", "reason": "OOMKilled"},
    )
    decision = await peer.evaluate_and_apply(artifact)
    assert decision.action == "rejected"
    assert decision.canary_passed is False
    assert "Canary execution failed" in decision.rationale
    tool_path = tool_path_for_skill(
        tmp_path / "k8s-canary" / "tools", "valkyrie-inspect-kubernetes-pod-oomkilled"
    )
    assert not tool_path.is_file()


async def test_healthy_proposed_tool_passes_canary_and_is_adopted(tmp_path) -> None:
    peer = _runtime(tmp_path, "k8s-canary-ok")
    artifact = ResidentLearningArtifact(
        learning_id="learn-ok",
        title="valkyrie-inspect-kubernetes-pod-oomkilled",
        summary="Healthy probe",
        content=probe_skill_content("valkyrie-inspect-kubernetes-pod-oomkilled"),
        artifact_type="ravn_skill_tool",
        scope="flock",
        confidence=0.9,
        source_environment_id="env-k8s-x",
        source_valkyrie_id="valkyrie:k8s-x",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        redaction_status="redacted",
        tool_code=(
            "def run(signal):\n"
            "    return {'matches': True, 'observed': signal.get('payload', {})}\n"
        ),
        canary_sample={"kind": "Pod", "reason": "OOMKilled"},
    )
    decision = await peer.evaluate_and_apply(artifact)
    assert decision.action == "adopted"
    assert decision.canary_passed is True
