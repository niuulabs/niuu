"""The investigation loop end to end (NIU-1051): an escalated signal spawns an
agent session that authors an instrument with build_tool, installs it, and
teaches the flock — replacing the retired classifier micro-dream.

The LLM is scripted (deterministic, no live model): on the investigation turn
it calls build_tool with a manifest + tool_code; the tool installs and proposes
to the flock; a peer ResidentLearningRuntime canaries and adopts it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.adapters.tools.build_tool import attach_build_tool
from ravn.adapters.tools.learned_tool_run import LearnedToolRunTool
from ravn.agent import RavnAgent
from ravn.domain.models import StreamEvent, StreamEventType, TokenUsage, ToolCall
from ravn.odin.review import JsonReviewStore, ReviewRequester
from ravn.ports.llm import LLMPort
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import LearnedToolResolver, read_learned_tool_artifact
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    ResidentLearningRuntime,
    capability_proposal_event,
    capability_proposal_from_artifact,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from tests.ravn.fixtures.fakes import BusRecorder
from tests.test_ravn.conftest import AllowAllPermission, InMemoryChannel

_OOM_TOOL_CODE = (
    "def run(input):\n"
    "    payload = input.get('payload', input)\n"
    "    return {\n"
    "        'capability': 'inspect.kubernetes.pod.oomkilled',\n"
    "        'matches': payload.get('reason') == 'OOMKilled',\n"
    "        'observed': {'namespace': payload.get('namespace', '')},\n"
    "        'severity': 'warning',\n"
    "        'summary': 'oom inspected',\n"
    "    }\n"
)

_OOM_TEST_CODE = (
    "import _verify_tool\n\n"
    "def test_matches_oomkilled():\n"
    "    result = _verify_tool.run(\n"
    "        {'payload': {'reason': 'OOMKilled', 'namespace': 'payments'}}\n"
    "    )\n"
    "    assert result['matches'] is True\n"
    "    assert result['observed']['namespace'] == 'payments'\n"
)


def _build_tool_call() -> ToolCall:
    return ToolCall(
        id="tc-build",
        name="build_tool",
        input={
            "manifest": {
                "name": "inspect_oomkilled_pod",
                "description": "Inspect an OOMKilled pod signal and summarize it.",
                "input_schema": {"type": "object", "properties": {"payload": {"type": "object"}}},
                "required_permission": "k8s:read",
                "declared_reach": [{"kind": "pure_compute", "access": "none"}],
            },
            "tool_code": _OOM_TOOL_CODE,
            "test_code": _OOM_TEST_CODE,
            "canary_input": {"payload": {"reason": "OOMKilled", "namespace": "payments"}},
        },
    )


class _ScriptedInvestigationLLM:
    """Calls build_tool on the first turn, then finishes the investigation."""

    def __init__(self) -> None:
        self._turns = 0

    @property
    def supports_thinking(self) -> bool:
        return False

    async def stream(self, messages, *, tools, **kwargs) -> AsyncIterator[StreamEvent]:
        self._turns += 1
        tool_names = {tool["name"] for tool in tools}
        if self._turns == 1:
            assert "build_tool" in tool_names
            yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=_build_tool_call())
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=8, output_tokens=4),
            )
            return
        yield StreamEvent(
            type=StreamEventType.TEXT_DELTA,
            text="Investigated the OOMKilled pod with the new instrument.",
        )
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=9, output_tokens=5),
        )

    async def generate(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def _teacher_agent(tmp_path, bus: InProcessBus) -> RavnAgent:
    llm = AsyncMock(spec=LLMPort)
    scripted = _ScriptedInvestigationLLM()
    llm.stream = scripted.stream
    agent = RavnAgent(
        llm=llm,
        tools=[],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt="You are a resident valkyrie investigating a signal.",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=6,
    )
    attach_build_tool(
        agent,
        tools_dir=tmp_path / "teacher" / "learned_tools",
        artifacts_dir=tmp_path / "teacher" / "learned_tool_artifacts",
        publisher=bus,
        review_requester=ReviewRequester(
            publisher=bus,
            store=JsonReviewStore(tmp_path / "teacher" / "review_outbox.json"),
            source="valkyrie:k8s-a",
        ),
        autonomy_mode="autonomous",
        environment_id="cluster-a",
        valkyrie_id="valkyrie:k8s-a",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
    )
    return agent


def _student_runtime(tmp_path, bus: InProcessBus) -> ResidentLearningRuntime:
    skill_dir = tmp_path / "student" / "skills"
    skills = SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "student" / "skill_management.json",
    )
    return ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-b",
            valkyrie_id="valkyrie:k8s-b",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode="autonomous",
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "student" / "tools",
    )


async def test_investigation_session_authors_tool_and_teaches_flock(tmp_path) -> None:
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)

    student = _student_runtime(tmp_path, bus)
    await student.start()

    teacher = _teacher_agent(tmp_path, bus)
    result = await teacher.run_turn(
        "A Kubernetes signal arrived: Pod OOMKilled in payments. Investigate it; "
        "if you lack an instrument, build one with build_tool, then use it."
    )
    await bus.flush()

    # 1. The session authored and used the instrument in the same turn.
    assert [call.name for call in result.tool_calls] == ["build_tool"]
    assert "inspect_oomkilled_pod" in {tool.name for tool in teacher.tools}

    # 2. It was installed on disk (autonomous + pure-compute auto-installs).
    installed = tmp_path / "teacher" / "learned_tools" / "inspect_oomkilled_pod.py"
    assert installed.is_file()

    # 3. It was proposed to the flock as an agent_tool.
    proposals = await recorder.of_type(registry.FLOCK_LEARNING_PROPOSED)
    agent_tool_proposals = [
        event for event in proposals if event.payload.get("artifact_type") == "agent_tool"
    ]
    assert len(agent_tool_proposals) == 1
    assert (
        agent_tool_proposals[0].payload["learned_tool_manifest"]["name"] == "inspect_oomkilled_pod"
    )

    # 4. The student canaried and adopted the instrument.
    adoptions = [
        event
        for event in await recorder.of_type(registry.LEARNING_ADOPTION_RECORDED)
        if event.payload.get("resident_valkyrie_id") == "valkyrie:k8s-b"
        and event.payload.get("action") == "adopted"
    ]
    assert len(adoptions) == 1
    student_tool = tmp_path / "student" / "learned_tools" / "inspect_oomkilled_pod.py"
    assert student_tool.is_file()

    # The teacher's own claims travel with the install for audit, kept
    # distinct from the student's own verification (never confused for it).
    student_artifact = read_learned_tool_artifact(
        tmp_path / "student" / "learned_tool_artifacts" / "inspect_oomkilled_pod.json"
    )
    assert student_artifact.provenance["verification"]["verified_by"] == "valkyrie:k8s-b"
    assert "builder_evidence" in student_artifact.provenance

    # 5. The student independently re-verified the proposal for itself (never
    #    trusting the teacher's own claim) and can actually RUN what it
    #    adopted through learned_tool_run — the headline loop this test
    #    guards: build, propose, peer-verify, install, run.
    dispatch = LearnedToolRunTool(
        resolver=LearnedToolResolver(state_dir=tmp_path / "student"),
        permission=AllowAllPermission(),
        skill_manager=student.skills,
    )
    run_result = await dispatch.execute(
        {
            "name": "inspect_oomkilled_pod",
            "input": {"payload": {"reason": "OOMKilled", "namespace": "payments"}},
        }
    )
    assert not run_result.is_error, run_result.content
    assert '"matches": true' in run_result.content

    await student.stop()


async def test_peer_proposal_without_test_code_is_declined_not_installed(tmp_path) -> None:
    """An agent_tool proposal with no test_code cannot be independently
    re-verified, so require_verified_artifact would refuse it forever if
    installed. It is declined at adoption time instead — never a capability
    the peer can see but never run."""
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)

    student = _student_runtime(tmp_path, bus)
    await student.start()

    resident_artifact = ResidentLearningArtifact(
        learning_id="learn-oom-untested",
        title="inspect_oomkilled_pod",
        summary="Inspect an OOMKilled pod.",
        content="",
        artifact_type="agent_tool",
        scope="flock",
        confidence=0.74,
        source_environment_id="cluster-a",
        source_valkyrie_id="valkyrie:k8s-a",
        promotion_id="learn-oom-untested",
        flock_id="flock:k8s-valkyries",
        domain="k8s",
        redaction_status="none",
        tool_code=_OOM_TOOL_CODE,
        tool_entry_point="run",
        learned_tool_manifest={
            "name": "inspect_oomkilled_pod",
            "description": "Inspect an OOMKilled pod signal.",
            "input_schema": {"type": "object"},
            "required_permission": "k8s:read",
        },
        test_code="",  # No tests: nothing for a peer to re-verify.
        canary_sample={"payload": {"reason": "OOMKilled", "namespace": "payments"}},
    )
    proposal = capability_proposal_from_artifact(
        resident_artifact,
        builder_evidence={},
        review_outcome="self_registered",
    )
    await bus.publish(capability_proposal_event(proposal, source="valkyrie:k8s-a"))
    await bus.flush()

    adoptions = [
        event
        for event in await recorder.of_type(registry.LEARNING_ADOPTION_RECORDED)
        if event.payload.get("resident_valkyrie_id") == "valkyrie:k8s-b"
    ]
    assert all(event.payload.get("action") != "adopted" for event in adoptions)
    decisions = student.decisions()
    assert decisions
    assert decisions[-1].action == "rejected"
    assert "no test_code" in decisions[-1].rationale
    assert not (tmp_path / "student" / "learned_tools" / "inspect_oomkilled_pod.py").exists()

    await student.stop()
