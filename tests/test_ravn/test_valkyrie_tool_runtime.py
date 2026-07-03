"""Tests for resident-built tool implementations: runtime, builders, loop."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import ravn.adapters.tools.terminal_docker as terminal_docker
import ravn.valkyrie_evolution.learned_tools as learned_tools_mod
import ravn.valkyrie_evolution.tool_runtime as tool_runtime_mod
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.odin.review import ReviewItem, ReviewKind, ReviewStatus, review_decided_event
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import (
    NETWORK_ALLOWED_DOCKER_NETWORK,
    NETWORK_DENIED_DOCKER_NETWORK,
    REACH_ENFORCEMENT_ENFORCED,
    REACH_ENFORCEMENT_UNAVAILABLE,
    ForgeSandboxLearnedToolRunner,
    LearnedToolError,
    LocalLearnedToolRunner,
    learned_tool_artifact_path,
    learned_tool_path,
    learned_tool_venvs_dir,
    load_learned_tool,
    reach_allows_network,
    read_learned_tool_artifact,
    superseded_artifact_path,
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
from ravn.valkyrie_evolution.tool_runtime import (
    ToolRunResult,
    ToolVenvError,
    ensure_tool_venv,
    run_tool,
    tool_path_for_skill,
    tool_venv_python,
    write_tool,
)
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


# ---------------------------------------------------------------------------
# Per-tool dependency venvs (P6.1)
# ---------------------------------------------------------------------------


async def test_tool_runs_with_its_dedicated_venv_python(tmp_path) -> None:
    """Happy path against a REAL venv: provision once, execute with its python."""
    venvs_dir = learned_tool_venvs_dir(tmp_path)
    python = ensure_tool_venv(venvs_dir=venvs_dir, tool_name="stdlib_probe", requirements=[])
    assert python.is_file()
    assert python == tool_venv_python(venvs_dir / "stdlib_probe")

    path = write_tool(
        tools_dir=tmp_path,
        skill_name="stdlib_probe",
        tool_code="import sys\n\ndef run(signal):\n    return {'executable': sys.executable}\n",
    )
    result = await run_tool(path, {}, python_executable=python)
    assert result.ok
    assert "stdlib_probe" in result.result["executable"]

    # An unchanged requirement list is a no-op: the venv is not rebuilt.
    marker = python.parent / "provisioned-once.marker"
    marker.write_text("keep", encoding="utf-8")
    again = ensure_tool_venv(venvs_dir=venvs_dir, tool_name="stdlib_probe", requirements=[])
    assert again == python
    assert marker.is_file()


def test_ensure_tool_venv_pip_failure_is_loud(tmp_path) -> None:
    """A nonexistent local-path requirement fails pip fast — and loudly."""
    with pytest.raises(ToolVenvError, match="pip install"):
        ensure_tool_venv(
            venvs_dir=tmp_path / "venvs",
            tool_name="broken_deps",
            requirements=[str(tmp_path / "definitely-not-a-real-package")],
        )
    # No half-provisioned venv is left behind to masquerade as a working one.
    assert not (tmp_path / "venvs" / "broken_deps").exists()


def _fake_provisioning_subprocess(calls: list[list[str]], *, pip_returncode: int = 0) -> Any:
    def _run(argv: list[str], **_: Any) -> SimpleNamespace:
        argv = [str(part) for part in argv]
        calls.append(argv)
        if "venv" in argv:
            python = tool_venv_python(Path(argv[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("#!fake-python\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=pip_returncode, stdout="", stderr="no such package")

    return _run


def test_ensure_tool_venv_stamp_makes_reprovisioning_a_noop(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(tool_runtime_mod.subprocess, "run", _fake_provisioning_subprocess(calls))
    python = ensure_tool_venv(venvs_dir=tmp_path, tool_name="dep_tool", requirements=["pkg-a==1.0"])
    assert python.is_file()
    assert len(calls) == 2  # venv creation + pip install
    assert "pkg-a==1.0" in calls[1]

    same = ensure_tool_venv(venvs_dir=tmp_path, tool_name="dep_tool", requirements=["pkg-a==1.0"])
    assert same == python
    assert len(calls) == 2  # stamp matched: no subprocess at all


def test_ensure_tool_venv_rebuilds_when_requirements_change(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(tool_runtime_mod.subprocess, "run", _fake_provisioning_subprocess(calls))
    ensure_tool_venv(venvs_dir=tmp_path, tool_name="dep_tool", requirements=["pkg-a==1.0"])
    ensure_tool_venv(venvs_dir=tmp_path, tool_name="dep_tool", requirements=["pkg-b==2.0"])
    assert len(calls) == 4  # a changed list rebuilds: venv + pip again
    assert "pkg-b==2.0" in calls[3]
    stamp = tmp_path / "dep_tool" / tool_runtime_mod.TOOL_VENV_REQUIREMENTS_STAMP
    assert stamp.read_text(encoding="utf-8") == "pkg-b==2.0"


def test_ensure_tool_venv_mocked_pip_failure_raises_and_cleans_up(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        tool_runtime_mod.subprocess,
        "run",
        _fake_provisioning_subprocess(calls, pip_returncode=1),
    )
    with pytest.raises(ToolVenvError, match="no such package"):
        ensure_tool_venv(venvs_dir=tmp_path, tool_name="dep_tool", requirements=["pkg-a"])
    assert not (tmp_path / "dep_tool").exists()


def test_ensure_tool_venv_rejects_empty_tool_name(tmp_path) -> None:
    with pytest.raises(ToolVenvError, match="empty tool name"):
        ensure_tool_venv(venvs_dir=tmp_path, tool_name="   ", requirements=[])


async def test_local_runner_refuses_requirements_without_venvs_dir(tmp_path) -> None:
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="needs_deps",
        tool_code="def run(signal):\n    return {}\n",
    )
    runner = LocalLearnedToolRunner()
    result = await runner.run(
        path, {}, entry_point="run", timeout_seconds=5.0, requirements=["httpx>=0.27"]
    )
    assert not result.ok
    assert "venvs_dir" in result.error
    assert "without its dependencies" in result.error


async def test_local_runner_provisions_venv_and_runs_with_its_python(tmp_path, monkeypatch) -> None:
    provisioned: list[dict[str, Any]] = []

    def fake_ensure(**kwargs: Any) -> Path:
        provisioned.append(kwargs)
        return Path(sys.executable)

    monkeypatch.setattr(learned_tools_mod, "ensure_tool_venv", fake_ensure)
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="needs_deps",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = LocalLearnedToolRunner(venvs_dir=tmp_path / "venvs")
    result = await runner.run(
        path, {}, entry_point="run", timeout_seconds=5.0, requirements=["httpx>=0.27"]
    )
    assert result.ok
    assert provisioned[0]["tool_name"] == "needs_deps"
    assert provisioned[0]["requirements"] == ["httpx>=0.27"]
    assert provisioned[0]["venvs_dir"] == tmp_path / "venvs"


async def test_local_runner_venv_provisioning_failure_is_loud(tmp_path, monkeypatch) -> None:
    def fake_ensure(**_: Any) -> Path:
        raise ToolVenvError("pip install failed for httpx")

    monkeypatch.setattr(learned_tools_mod, "ensure_tool_venv", fake_ensure)
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="needs_deps",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = LocalLearnedToolRunner(venvs_dir=tmp_path / "venvs")
    result = await runner.run(
        path, {}, entry_point="run", timeout_seconds=5.0, requirements=["httpx"]
    )
    assert not result.ok
    assert "provisioning failed" in result.error
    assert "pip install failed" in result.error


async def test_learned_tool_threads_requirements_and_reach_to_its_runner(tmp_path) -> None:
    class _RecordingRunner:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(
            self,
            tool_path: Path,
            payload: dict[str, Any],
            *,
            entry_point: str,
            timeout_seconds: float,
            requirements: Any = (),
            declared_reach: Any = (),
        ) -> ToolRunResult:
            self.calls.append(
                {"requirements": list(requirements), "declared_reach": list(declared_reach)}
            )
            return ToolRunResult(ok=True, result={"ok": True})

    artifact = replace(_learned_tool_artifact(), requirements=["httpx>=0.27"])
    tool_path = write_learned_tool(tools_dir=tmp_path, artifact=artifact)
    recorder = _RecordingRunner()
    tool = load_learned_tool(artifact=artifact, tool_path=tool_path, runner=recorder)

    result = await tool.execute({"query": "up"})

    assert not result.is_error
    assert recorder.calls[0]["requirements"] == ["httpx>=0.27"]
    assert recorder.calls[0]["declared_reach"] == artifact.manifest.declared_reach


async def test_learned_tool_surfaces_runner_failures_with_stderr(tmp_path) -> None:
    class _FailingRunner:
        async def run(self, tool_path: Path, payload: dict[str, Any], **_: Any) -> ToolRunResult:
            return ToolRunResult(ok=False, error="venv exploded", stderr="trace: boom")

    artifact = _learned_tool_artifact()
    tool_path = write_learned_tool(tools_dir=tmp_path, artifact=artifact)
    tool = load_learned_tool(artifact=artifact, tool_path=tool_path, runner=_FailingRunner())

    result = await tool.execute({"query": "up"})

    assert result.is_error
    assert "venv exploded" in result.content
    assert "trace: boom" in result.content


# ---------------------------------------------------------------------------
# Reach-enforced execution (P5b)
# ---------------------------------------------------------------------------


def test_reach_allows_network_derivation() -> None:
    network = [ToolReachGrant(kind="network", access="read")]
    http_prefixed = [ToolReachGrant(kind="HTTP_GET", access="read")]
    compute_only = [ToolReachGrant(kind="pure_compute", access="read")]
    filesystem = [ToolReachGrant(kind="filesystem", access="read_write")]

    assert reach_allows_network(network) is True
    assert reach_allows_network(http_prefixed) is True
    assert reach_allows_network(compute_only) is False
    assert reach_allows_network(filesystem) is False
    assert reach_allows_network([]) is False


async def test_local_runner_is_honest_about_reach_and_warns_once(tmp_path, caplog) -> None:
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="no_net_probe",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = LocalLearnedToolRunner()
    assert runner.enforces_reach is False

    with caplog.at_level(logging.WARNING, logger="ravn.valkyrie_evolution.learned_tools"):
        first = await runner.run(path, {}, entry_point="run", timeout_seconds=5.0)
        second = await runner.run(path, {}, entry_point="run", timeout_seconds=5.0)
    assert first.ok and second.ok
    warnings = [rec for rec in caplog.records if "NOT enforced" in rec.message]
    assert len(warnings) == 1  # one-time honesty warning, not a flood


async def test_local_runner_does_not_warn_for_network_granting_tools(tmp_path, caplog) -> None:
    path = write_tool(
        tools_dir=tmp_path,
        skill_name="net_probe",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = LocalLearnedToolRunner()
    with caplog.at_level(logging.WARNING, logger="ravn.valkyrie_evolution.learned_tools"):
        result = await runner.run(
            path,
            {},
            entry_point="run",
            timeout_seconds=5.0,
            declared_reach=[ToolReachGrant(kind="network", access="read")],
        )
    assert result.ok
    assert not [rec for rec in caplog.records if "NOT enforced" in rec.message]


class _FakeDockerShell:
    """Stands in for DockerPersistentShell without touching docker."""

    def __init__(
        self,
        *,
        config: Any = None,
        workspace_root: Any = None,
        timeout_seconds: Any = None,
    ) -> None:
        if config is not None:
            self._config = config
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def run(self, command: str) -> tuple[str, int]:
        return '{"ok": true}', 0


async def test_forge_runner_scopes_container_network_to_declared_reach(
    tmp_path, monkeypatch
) -> None:
    created: list[_FakeDockerShell] = []

    class _TrackingShell(_FakeDockerShell):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            created.append(self)

    monkeypatch.setattr(terminal_docker, "DockerPersistentShell", _TrackingShell)
    tool_path = write_tool(
        tools_dir=tmp_path,
        skill_name="probe",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = ForgeSandboxLearnedToolRunner(workspace_root=tmp_path)
    assert runner.enforces_reach is True

    isolated = await runner.run(
        tool_path, {}, entry_point="run", timeout_seconds=5.0, declared_reach=[]
    )
    networked = await runner.run(
        tool_path,
        {},
        entry_point="run",
        timeout_seconds=5.0,
        declared_reach=[ToolReachGrant(kind="network", access="read")],
    )
    repeat = await runner.run(
        tool_path, {}, entry_point="run", timeout_seconds=5.0, declared_reach=[]
    )

    assert isolated.ok and networked.ok and repeat.ok
    assert isolated.enforcement == REACH_ENFORCEMENT_ENFORCED
    assert networked.enforcement == REACH_ENFORCEMENT_ENFORCED
    assert [shell._config.network for shell in created] == [
        NETWORK_DENIED_DOCKER_NETWORK,
        NETWORK_ALLOWED_DOCKER_NETWORK,
    ]
    assert all(shell.started for shell in created)
    assert len(created) == 2  # shells are cached per network mode


async def test_forge_runner_records_unavailable_enforcement_for_opaque_shell(
    tmp_path, caplog
) -> None:
    shell = _FakeDockerShell()  # no _config: network mode is unknowable
    tool_path = write_tool(
        tools_dir=tmp_path,
        skill_name="probe",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = ForgeSandboxLearnedToolRunner(workspace_root=tmp_path, shell=shell)
    assert runner.enforces_reach is False

    with caplog.at_level(logging.WARNING, logger="ravn.valkyrie_evolution.learned_tools"):
        first = await runner.run(
            tool_path, {}, entry_point="run", timeout_seconds=5.0, declared_reach=[]
        )
        second = await runner.run(
            tool_path, {}, entry_point="run", timeout_seconds=5.0, declared_reach=[]
        )
    assert first.ok and second.ok
    assert first.enforcement == REACH_ENFORCEMENT_UNAVAILABLE
    assert second.enforcement == REACH_ENFORCEMENT_UNAVAILABLE
    warnings = [rec for rec in caplog.records if "cannot express network isolation" in rec.message]
    assert len(warnings) == 1


async def test_forge_runner_injected_isolated_shell_counts_as_enforced(tmp_path) -> None:
    shell = _FakeDockerShell(config=SimpleNamespace(network=NETWORK_DENIED_DOCKER_NETWORK))
    tool_path = write_tool(
        tools_dir=tmp_path,
        skill_name="probe",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = ForgeSandboxLearnedToolRunner(workspace_root=tmp_path, shell=shell)
    assert runner.enforces_reach is True

    result = await runner.run(
        tool_path, {}, entry_point="run", timeout_seconds=5.0, declared_reach=[]
    )
    assert result.ok
    assert result.enforcement == REACH_ENFORCEMENT_ENFORCED


async def test_forge_runner_injected_networked_shell_cannot_enforce_isolation(tmp_path) -> None:
    shell = _FakeDockerShell(config=SimpleNamespace(network="bridge"))
    tool_path = write_tool(
        tools_dir=tmp_path,
        skill_name="probe",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = ForgeSandboxLearnedToolRunner(workspace_root=tmp_path, shell=shell)

    no_reach = await runner.run(
        tool_path, {}, entry_point="run", timeout_seconds=5.0, declared_reach=[]
    )
    with_reach = await runner.run(
        tool_path,
        {},
        entry_point="run",
        timeout_seconds=5.0,
        declared_reach=[ToolReachGrant(kind="api", access="read")],
    )
    # A networked shell cannot deny reach the tool never declared…
    assert no_reach.enforcement == REACH_ENFORCEMENT_UNAVAILABLE
    # …but it satisfies a tool whose reach grants network access.
    assert with_reach.enforcement == REACH_ENFORCEMENT_ENFORCED


async def test_forge_runner_warns_once_about_unprovisioned_requirements(tmp_path, caplog) -> None:
    shell = _FakeDockerShell(config=SimpleNamespace(network=NETWORK_DENIED_DOCKER_NETWORK))
    tool_path = write_tool(
        tools_dir=tmp_path,
        skill_name="probe",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    runner = ForgeSandboxLearnedToolRunner(workspace_root=tmp_path, shell=shell)
    with caplog.at_level(logging.WARNING, logger="ravn.valkyrie_evolution.learned_tools"):
        await runner.run(
            tool_path, {}, entry_point="run", timeout_seconds=5.0, requirements=["httpx"]
        )
        await runner.run(
            tool_path, {}, entry_point="run", timeout_seconds=5.0, requirements=["httpx"]
        )
    warnings = [rec for rec in caplog.records if "per-tool dependencies" in rec.message]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Version chain (P6.3): supersedes linking + archive
# ---------------------------------------------------------------------------


def _versioned_artifact(artifact_id: str, version: int) -> LearnedToolArtifact:
    return LearnedToolArtifact(
        artifact_id=artifact_id,
        manifest=_learned_tool_artifact().manifest,
        tool_code=f"def run(input):\n    return {{'version': {version}}}\n",
    )


def test_second_artifact_write_links_and_archives_the_first(tmp_path) -> None:
    v1 = _versioned_artifact("learned-tool:v1", 1)
    v2 = _versioned_artifact("learned-tool:v2", 2)
    write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v1)
    path = write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v2)

    current = read_learned_tool_artifact(path)
    assert current.artifact_id == "learned-tool:v2"
    assert current.supersedes == "learned-tool:v1"

    archived_path = superseded_artifact_path(tmp_path, v1.manifest.name, "learned-tool:v1")
    archived = read_learned_tool_artifact(archived_path)
    assert archived.artifact_id == "learned-tool:v1"
    assert archived.supersedes == ""
    # The archive must never be picked up by the daemon's *.json glob.
    assert archived_path.parent != Path(tmp_path)


def test_rewriting_the_same_version_preserves_the_chain_link(tmp_path) -> None:
    write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=_versioned_artifact("id:v1", 1))
    write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=_versioned_artifact("id:v2", 2))
    # A same-id rewrite (refreshed provenance) arrives without the link set.
    path = write_learned_tool_artifact(
        artifacts_dir=tmp_path, artifact=_versioned_artifact("id:v2", 2)
    )
    assert read_learned_tool_artifact(path).supersedes == "id:v1"


def test_restoring_the_predecessor_does_not_create_a_cycle(tmp_path) -> None:
    v1 = _versioned_artifact("learned-tool:v1", 1)
    v2 = _versioned_artifact("learned-tool:v2", 2)
    write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v1)
    write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v2)

    # Rollback writes v1 back over v2: no v1 -> v2 link may appear.
    path = write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v1)
    restored = read_learned_tool_artifact(path)
    assert restored.artifact_id == "learned-tool:v1"
    assert restored.supersedes == ""
    # The rolled-back v2 is preserved in the archive for the audit trail.
    assert superseded_artifact_path(tmp_path, v2.manifest.name, "learned-tool:v2").is_file()


def test_corrupt_existing_envelope_fails_the_write_loudly(tmp_path) -> None:
    v1 = _versioned_artifact("learned-tool:v1", 1)
    path = learned_tool_artifact_path(tmp_path, v1.manifest.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LearnedToolError, match="not valid"):
        write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v1)


def test_learned_tool_artifact_round_trips_supersedes_with_legacy_default() -> None:
    artifact = replace(_learned_tool_artifact(), supersedes="learned-tool:v0")
    assert LearnedToolArtifact.from_dict(artifact.to_dict()).supersedes == "learned-tool:v0"
    # A pre-P6.3 envelope has no key on disk; the default is an empty chain.
    legacy = artifact.to_dict()
    del legacy["supersedes"]
    assert LearnedToolArtifact.from_dict(legacy).supersedes == ""


def test_same_id_rewrite_without_a_chain_stays_unlinked(tmp_path) -> None:
    v1 = _versioned_artifact("learned-tool:v1", 1)
    write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v1)
    path = write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v1)
    assert read_learned_tool_artifact(path).supersedes == ""


def test_caller_set_supersedes_is_honored_over_the_auto_link(tmp_path) -> None:
    v1 = _versioned_artifact("learned-tool:v1", 1)
    v2 = replace(_versioned_artifact("learned-tool:v2", 2), supersedes="custom:origin")
    write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v1)
    path = write_learned_tool_artifact(artifacts_dir=tmp_path, artifact=v2)
    assert read_learned_tool_artifact(path).supersedes == "custom:origin"
    # The previous version is still archived even when the caller owns the link.
    assert superseded_artifact_path(tmp_path, v1.manifest.name, "learned-tool:v1").is_file()


# ---------------------------------------------------------------------------
# Venv provisioning failure modes + forge sandbox run failure surfaces
# ---------------------------------------------------------------------------


def test_ensure_tool_venv_creation_failure_is_loud(tmp_path, monkeypatch) -> None:
    def broken_venv(argv: list[str], **_: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="ensurepip exploded")

    monkeypatch.setattr(tool_runtime_mod.subprocess, "run", broken_venv)
    with pytest.raises(ToolVenvError, match="venv creation failed"):
        ensure_tool_venv(venvs_dir=tmp_path, tool_name="dep_tool", requirements=[])


def test_ensure_tool_venv_creation_timeout_is_loud(tmp_path, monkeypatch) -> None:
    def hanging_venv(argv: list[str], **_: Any) -> SimpleNamespace:
        raise tool_runtime_mod.subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(tool_runtime_mod.subprocess, "run", hanging_venv)
    with pytest.raises(ToolVenvError, match="venv creation timed out"):
        ensure_tool_venv(venvs_dir=tmp_path, tool_name="dep_tool", requirements=[])


def test_ensure_tool_venv_pip_timeout_is_loud_and_cleans_up(tmp_path, monkeypatch) -> None:
    def fake_run(argv: list[str], **_: Any) -> SimpleNamespace:
        argv = [str(part) for part in argv]
        if "venv" in argv:
            python = tool_venv_python(Path(argv[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("#!fake-python\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise tool_runtime_mod.subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(tool_runtime_mod.subprocess, "run", fake_run)
    with pytest.raises(ToolVenvError, match="pip install timed out"):
        ensure_tool_venv(venvs_dir=tmp_path, tool_name="dep_tool", requirements=["pkg-a"])
    assert not (tmp_path / "dep_tool").exists()


class _ScriptedShell(_FakeDockerShell):
    """Fake docker shell whose run() output is scripted per test."""

    def __init__(self, output: Any, exit_code: int = 0, raises: Exception | None = None) -> None:
        super().__init__(config=SimpleNamespace(network=NETWORK_DENIED_DOCKER_NETWORK))
        self._output = output
        self._exit_code = exit_code
        self._raises = raises

    async def run(self, command: str) -> tuple[str, int]:
        if self._raises is not None:
            raise self._raises
        return self._output, self._exit_code


async def test_forge_runner_rejects_tool_paths_outside_the_workspace(tmp_path) -> None:
    inside = tmp_path / "workspace"
    inside.mkdir()
    outside_tool = write_tool(
        tools_dir=tmp_path / "elsewhere",
        skill_name="probe",
        tool_code="def run(signal):\n    return {}\n",
    )
    runner = ForgeSandboxLearnedToolRunner(
        workspace_root=inside, shell=_ScriptedShell('{"ok": true}')
    )
    result = await runner.run(outside_tool, {}, entry_point="run", timeout_seconds=5.0)
    assert not result.ok
    assert "inside workspace" in result.error


async def test_forge_runner_surfaces_shell_failures_with_enforcement(tmp_path) -> None:
    tool_path = write_tool(
        tools_dir=tmp_path,
        skill_name="probe",
        tool_code="def run(signal):\n    return {}\n",
    )

    async def run_case(shell: _ScriptedShell) -> Any:
        runner = ForgeSandboxLearnedToolRunner(workspace_root=tmp_path, shell=shell)
        return await runner.run(tool_path, {}, entry_point="run", timeout_seconds=5.0)

    crashed = await run_case(_ScriptedShell("", raises=RuntimeError("container gone")))
    assert not crashed.ok
    assert "forge sandbox execution failed" in crashed.error

    nonzero = await run_case(_ScriptedShell("traceback", exit_code=2))
    assert not nonzero.ok
    assert "status 2" in nonzero.error

    not_json = await run_case(_ScriptedShell("definitely not json"))
    assert not not_json.ok
    assert "non-JSON" in not_json.error

    not_object = await run_case(_ScriptedShell("[1, 2]"))
    assert not not_object.ok
    assert "JSON object" in not_object.error

    # Every failure surface still reports the enforcement honestly.
    for result in (crashed, nonzero, not_json, not_object):
        assert result.enforcement == REACH_ENFORCEMENT_ENFORCED


async def test_forge_runner_uses_unscopable_config_as_is(tmp_path, monkeypatch) -> None:
    created: list[Any] = []

    class _TrackingShell(_FakeDockerShell):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            created.append(self)

    monkeypatch.setattr(terminal_docker, "DockerPersistentShell", _TrackingShell)
    tool_path = write_tool(
        tools_dir=tmp_path,
        skill_name="probe",
        tool_code="def run(signal):\n    return {'ok': True}\n",
    )
    # A plain config object has no model_copy; it cannot be re-scoped per
    # network mode, so the runner uses it as-is and reports its actual mode.
    config = SimpleNamespace(network=NETWORK_DENIED_DOCKER_NETWORK, image="custom:image")
    runner = ForgeSandboxLearnedToolRunner(workspace_root=tmp_path, docker_config=config)

    result = await runner.run(tool_path, {}, entry_point="run", timeout_seconds=5.0)

    assert result.ok
    assert result.enforcement == REACH_ENFORCEMENT_ENFORCED
    assert created[0]._config is config
