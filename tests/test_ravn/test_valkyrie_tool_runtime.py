"""Tests for resident-built tool implementations: runtime, builders, loop."""

from __future__ import annotations

import ast
import json

import pytest

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.domain.models import LLMResponse, StopReason, TokenUsage
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.adapters import (
    AgentToolBuilder,
    TemplateToolBuilder,
    ToolBuildError,
    WorkflowToolBuilder,
)
from ravn.valkyrie_evolution.learned_tools import (
    LearnedToolError,
    learned_tool_path,
    load_learned_tool,
    read_learned_tool_artifact,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import (
    CapabilityGap,
    EvolutionRequest,
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


def _request(capability: str = "inspect.kubernetes.pod.oomkilled") -> EvolutionRequest:
    gap = CapabilityGap(
        gap_id=f"gap:{capability}",
        capability_name=capability,
        environment_id="cluster-a",
        domain="k8s",
        reason="Resident saw signal without an installed skill",
        signal_ids=["sig-1"],
        evidence={
            "payload": {
                "kind": "Pod",
                "reason": "OOMKilled",
                "namespace": "payments",
                "message": "Container killed due to memory pressure",
            }
        },
        safety_class="read_only",
    )
    return EvolutionRequest(
        request_id=f"evolve:{capability}",
        gap=gap,
        autonomy_mode="yolo",
        target_scope="environment",
    )


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


class _FakeLLM:
    """Minimal LLMPort stand-in returning a canned generate() response."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def generate(self, messages, *, tools, system, model, max_tokens, thinking=None):
        self.calls.append({"messages": messages, "model": model})
        return LLMResponse(
            content=self.content,
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


def _valid_agent_response() -> str:
    tool_code = (
        '"""Probe for OOMKilled pods."""\n\n'
        "def run(signal: dict) -> dict:\n"
        '    payload = signal.get("payload", signal)\n'
        '    observed = {k: payload[k] for k in ("kind", "reason") if k in payload}\n'
        "    return {\n"
        '        "capability": "inspect.kubernetes.pod.oomkilled",\n'
        '        "matches": bool(observed),\n'
        '        "observed": observed,\n'
        '        "severity": str(payload.get("severity", "warning")),\n'
        '        "summary": "oom probe",\n'
        "    }\n"
    )
    return json.dumps(
        {
            "skill_markdown": "# skill: valkyrie-inspect-kubernetes-pod-oomkilled\n\nInspect.",
            "tool_code": tool_code,
        }
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
# TemplateToolBuilder
# ---------------------------------------------------------------------------


async def test_template_builder_emits_runnable_tool_implementation(tmp_path) -> None:
    builder = TemplateToolBuilder(artifact_dir=tmp_path)
    build = await builder.build(_request())

    assert build.has_tool_implementation
    ast.parse(build.tool_code)
    assert build.tool_path.endswith(".py")

    result = await run_tool(build.tool_path, {"payload": _signal().payload})
    assert result.ok
    assert result.result["matches"] is True
    assert result.result["observed"]["reason"] == "OOMKilled"
    assert result.result["capability"] == "inspect.kubernetes.pod.oomkilled"


# ---------------------------------------------------------------------------
# AgentToolBuilder
# ---------------------------------------------------------------------------


async def test_agent_builder_authors_skill_and_tool_via_llm(tmp_path) -> None:
    llm = _FakeLLM(_valid_agent_response())
    builder = AgentToolBuilder(llm=llm, model="claude-test", artifact_dir=tmp_path)
    build = await builder.build(_request())

    assert llm.calls and llm.calls[0]["model"] == "claude-test"
    assert build.has_tool_implementation
    assert "capability: inspect.kubernetes.pod.oomkilled" in build.skill_content
    result = await run_tool(build.tool_path, {"payload": _signal().payload})
    assert result.ok
    assert result.result["observed"] == {"kind": "Pod", "reason": "OOMKilled"}


async def test_agent_builder_rejects_forbidden_imports() -> None:
    response = json.dumps(
        {
            "skill_markdown": "# skill: x",
            "tool_code": "import subprocess\n\ndef run(signal):\n    return {}\n",
        }
    )
    builder = AgentToolBuilder(llm=_FakeLLM(response))
    with pytest.raises(ToolBuildError, match="forbidden modules: subprocess"):
        await builder.build(_request())


async def test_agent_builder_rejects_missing_entry_point() -> None:
    response = json.dumps(
        {"skill_markdown": "# skill: x", "tool_code": "def probe(signal):\n    return {}\n"}
    )
    builder = AgentToolBuilder(llm=_FakeLLM(response))
    with pytest.raises(ToolBuildError, match="does not define run"):
        await builder.build(_request())


async def test_agent_builder_rejects_non_json_response() -> None:
    builder = AgentToolBuilder(llm=_FakeLLM("I cannot help with that."))
    with pytest.raises(ToolBuildError, match="no JSON object"):
        await builder.build(_request())


async def test_workflow_builder_is_an_explicit_boundary() -> None:
    with pytest.raises(NotImplementedError, match="workflow"):
        await WorkflowToolBuilder().build(_request())


# ---------------------------------------------------------------------------
# Resident loop integration
# ---------------------------------------------------------------------------


def _runtime(
    tmp_path,
    name: str,
    autonomy_mode: str = "yolo",
    legacy_probe_builder_enabled: bool = True,
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
        legacy_probe_builder_enabled=legacy_probe_builder_enabled,
    )


async def test_missing_capability_can_defer_to_investigation_instead_of_probe_build(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path, "k8s-investigate", legacy_probe_builder_enabled=False)

    result = await runtime.process_signal(_signal())

    assert result["decision"] == "defer_to_investigation_with_build_tool"
    assert result["usedAdoptedLearning"] is False
    assert not (tmp_path / "k8s-investigate" / "tools").exists()


async def test_micro_dream_installs_tool_and_replay_executes_it(tmp_path) -> None:
    runtime = _runtime(tmp_path, "k8s-a")

    first = await runtime.process_signal(_signal())
    assert first["decision"] == "built_capability_for_next_signal"
    skill_name = first["skillName"]
    tool_path = tool_path_for_skill(tmp_path / "k8s-a" / "tools", skill_name)
    assert tool_path.is_file()

    replay = await runtime.process_signal(_signal())
    assert replay["decision"] == "inspect_with_adopted_learning"
    assert replay["usedAdoptedLearning"] is True
    assert replay["toolResult"]["matches"] is True
    assert replay["toolResult"]["observed"]["reason"] == "OOMKilled"


async def test_peer_adoption_installs_proposed_tool_implementation(tmp_path) -> None:
    teacher = _runtime(tmp_path, "k8s-a")
    built = await teacher.process_signal(_signal())
    skill_name = built["skillName"]
    teacher_tool = tool_path_for_skill(tmp_path / "k8s-a" / "tools", skill_name)

    peer = _runtime(tmp_path, "k8s-b")
    artifact = ResidentLearningArtifact(
        learning_id="learn-oom",
        title=skill_name,
        summary="OOM probe",
        content=(tmp_path / "k8s-a" / "skills" / f"{skill_name}.md").read_text()
        if (tmp_path / "k8s-a" / "skills" / f"{skill_name}.md").is_file()
        else _skill_content_for(skill_name),
        artifact_type="ravn_skill_tool",
        scope="flock",
        confidence=0.9,
        source_environment_id="env-k8s-a",
        source_valkyrie_id="valkyrie:k8s-a",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        redaction_status="redacted",
        tool_code=teacher_tool.read_text(),
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
    tool_path = tmp_path / "k8s-agent-tool" / "tools" / "agent_tools" / "mimir_metric_window.py"
    artifact_path = (
        tmp_path / "k8s-agent-tool" / "tools" / "agent_tool_artifacts" / "mimir_metric_window.json"
    )
    assert tool_path.is_file()
    installed = read_learned_tool_artifact(artifact_path)
    assert installed.manifest.name == "mimir_metric_window"
    assert installed.manifest.declared_reach[0].kind == "network"


def _skill_content_for(skill_name: str) -> str:
    return f"""# skill: {skill_name}

metadata:
  capability: inspect.kubernetes.pod.oomkilled
  source: valkyrie-dream-cycle
  safety_class: read_only
  tool_entry_point: run

## Procedure

1. Run the installed probe implementation.
"""


async def test_failing_tool_surfaces_failure_judgment(tmp_path) -> None:
    runtime = _runtime(tmp_path, "k8s-c")
    first = await runtime.process_signal(_signal())
    skill_name = first["skillName"]
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
        content=_skill_content_for("valkyrie-inspect-kubernetes-pod-oomkilled"),
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
        content=_skill_content_for("valkyrie-inspect-kubernetes-pod-oomkilled"),
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


async def test_micro_dream_holds_tool_that_fails_its_own_canary(tmp_path) -> None:
    class RuntimeBrokenBuilder:
        async def build(self, request):
            from ravn.valkyrie_evolution.models import BuildResult

            name = "valkyrie-broken"
            return BuildResult(
                request_id=request.request_id,
                skill_name=name,
                skill_content=_skill_content_for(name).replace(
                    "inspect.kubernetes.pod.oomkilled", request.gap.capability_name
                ),
                description="Parses fine, fails at runtime.",
                artifact_type="ravn_skill_tool",
                tool_code="def run(signal):\n    raise RuntimeError('boom')\n",
            )

    skill_dir = tmp_path / "broken" / "skills"
    registry_port = FileSkillRegistry(
        skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False
    )
    skills = SkillManagementRegistry(
        registry_port, metadata_path=tmp_path / "broken" / "skill_management.json"
    )
    bus = InProcessBus()
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="env-broken",
            valkyrie_id="valkyrie:broken",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode="yolo",
            environment_type="k8s",
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        builder=RuntimeBrokenBuilder(),
        tools_dir=tmp_path / "broken" / "tools",
    )
    result = await runtime.process_signal(_signal())
    assert result["decision"] == "capability_build_held"
    assert result["skillName"] == ""
