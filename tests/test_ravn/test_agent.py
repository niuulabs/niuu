"""Tests for the Ravn agent loop."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from niuu.domain.outcome import OutcomeField
from ravn.adapters.personas.loader import PersonaConfig, PersonaProduces
from ravn.adapters.tools.build_tool import attach_build_tool
from ravn.agent import RavnAgent, _build_assistant_content
from ravn.domain.events import RavnEventType
from ravn.domain.exceptions import MaxIterationsError
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from ravn.odin.review import JsonReviewStore, ReviewRequester
from ravn.ports.llm import LLMPort
from ravn.prompt_builder import PromptBuilder
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent
from tests.test_ravn.conftest import (
    AllowAllPermission,
    DenyAllPermission,
    EchoTool,
    FailingTool,
    InMemoryChannel,
    make_simple_llm,
)


def make_agent(
    llm: LLMPort,
    tools=None,
    *,
    channel: InMemoryChannel | None = None,
    permission=None,
    max_iterations: int = 10,
    **agent_kwargs,
) -> tuple[RavnAgent, InMemoryChannel]:
    ch = channel or InMemoryChannel()
    perm = permission or AllowAllPermission()
    agent = RavnAgent(
        llm=llm,
        tools=tools or [],
        channel=ch,
        permission=perm,
        system_prompt="You are a test assistant.",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=max_iterations,
        **agent_kwargs,
    )
    return agent, ch


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


class _FakeSandboxShell:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def run(self, command: str) -> tuple[str, int]:
        self.commands.append(command)
        return '{"backend": "forge", "ok": true}', 0


class TestRavnAgentSimpleTurn:
    async def test_simple_response(self) -> None:
        llm = make_simple_llm("Hello, world!")
        agent, channel = make_agent(llm)

        result = await agent.run_turn("Hi")

        assert result.response == "Hello, world!"
        assert result.tool_calls == []
        assert result.tool_results == []
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5

    async def test_session_updated(self) -> None:
        llm = make_simple_llm("response")
        agent, _ = make_agent(llm)

        await agent.run_turn("Hello")

        assert agent.session.turn_count == 1
        assert len(agent.session.messages) == 2
        assert agent.session.messages[0].role == "user"
        assert agent.session.messages[1].role == "assistant"

    async def test_channel_receives_thought_and_response(self) -> None:
        llm = make_simple_llm("Hi!")
        agent, channel = make_agent(llm)

        await agent.run_turn("Hey")

        event_types = [e.type for e in channel.events]
        assert RavnEventType.THOUGHT in event_types
        assert RavnEventType.RESPONSE in event_types

    async def test_cumulative_usage_tracked(self) -> None:
        llm = make_simple_llm("ok")
        agent, _ = make_agent(llm)

        await agent.run_turn("turn 1")
        await agent.run_turn("turn 2")

        assert agent.session.turn_count == 2
        assert agent.session.total_usage.input_tokens == 20

    async def test_mimir_learnings_are_not_injected_automatically(self) -> None:
        mimir = AsyncMock()
        prompt_builder = PromptBuilder()
        agent, _ = make_agent(
            make_simple_llm("done"),
            mimir=mimir,
            prompt_builder=prompt_builder,
        )

        await agent.run_turn("judge current evidence")

        mimir.list_pages.assert_not_called()
        assert "learnings_context" not in prompt_builder.section_texts()


class TestRavnAgentToolUse:
    async def test_tool_executed_and_result_fed_back(self) -> None:
        """LLM requests tool_use → tool runs → second call returns final text."""
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "ping"})

        call_count = 0

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: return tool_use
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            else:
                # Second call: return final answer
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="pong received")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=8, output_tokens=3),
                )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream

        agent, channel = make_agent(llm, tools=[tool])
        result = await agent.run_turn("echo ping")

        assert result.response == "pong received"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "echo"
        assert len(result.tool_results) == 1
        assert result.tool_results[0].content == "ping"

    async def test_tool_executes_before_draft_outcome_can_finish_turn(self) -> None:
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "evidence"})
        draft = "---outcome---\nverdict: watch\n---end---\n"
        final = "Evidence received.\n---outcome---\nverdict: act\n---end---\n"
        calls = 0

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal calls
            calls += 1
            if calls == 1:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=draft)
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
                return
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=final)
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=8, output_tokens=3),
            )

        persona = PersonaConfig(
            name="resident",
            produces=PersonaProduces(
                event_type="resident.judged",
                schema={
                    "verdict": OutcomeField(
                        type="string",
                        description="judgment",
                        enum_values=["watch", "act"],
                    )
                },
            ),
        )
        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream
        agent, _ = make_agent(
            llm,
            tools=[tool],
            persona_config=persona,
            stop_on_outcome=True,
        )

        result = await agent.run_turn("judge this signal")

        assert calls == 2
        assert result.response == final
        assert [call.name for call in result.tool_calls] == ["echo"]
        assert result.tool_results[0].content == "evidence"

    async def test_build_tool_commissions_via_build_backend_and_installs(self, tmp_path) -> None:
        """A build_request with no inline code is developed by the build backend,
        then flows through the same review/canary/install path."""
        from ravn.ports.tool_build_backend import (
            ToolBuildBackend,
            ToolBuildRequest,
            ToolBuildResult,
        )

        class _FakeBackend(ToolBuildBackend):
            def __init__(self) -> None:
                self.requests: list[ToolBuildRequest] = []

            @property
            def name(self) -> str:
                return "fake"

            async def build(self, request: ToolBuildRequest) -> ToolBuildResult:
                self.requests.append(request)
                return ToolBuildResult(
                    manifest={
                        "name": "commissioned_probe",
                        "description": "Built by the fake backend.",
                        "input_schema": {"type": "object"},
                        "required_permission": "probe:read",
                        "declared_reach": [{"kind": "pure_compute", "access": "none"}],
                        "entry_point": "run",
                    },
                    tool_code="def run(input):\n    return {'built': True}\n",
                    # Executable under the Phase-2 verifier: the runner script
                    # imports the tool module from the verify workspace cwd.
                    test_code=(
                        "def test_run():\n"
                        "    import commissioned_probe\n"
                        "    assert commissioned_probe.run({})['built'] is True\n"
                        "\n"
                        "test_run()\n"
                    ),
                    # Stdlib-only keeps verification hermetic (no pip network
                    # I/O in a unit test); requirements merge/persist flow is
                    # covered by the contract and deps-heal tests.
                    requirements=[],
                    build_evidence={"retrieval": "canonical_file"},
                    provenance={"backend": "fake", "session": "sess-9"},
                )

        backend = _FakeBackend()
        agent, _ = make_agent(make_simple_llm("unused"))
        tool = attach_build_tool(
            agent,
            tools_dir=tmp_path / "tools",
            artifacts_dir=tmp_path / "artifacts",
            build_backend=backend,
            environment_id="cluster-a",
            valkyrie_id="valkyrie:k8s-a",
        )

        result = await tool.execute(
            {
                "manifest": {"name": "commissioned_probe", "required_permission": "probe:read"},
                "build_request": "Build a probe that inspects widget health.",
                "signal_context": "widget degraded",
                "canary_input": {},
            }
        )

        assert not result.is_error
        assert len(backend.requests) == 1
        assert backend.requests[0].build_request.startswith("Build a probe")
        assert backend.requests[0].signal_context == "widget degraded"
        registered = next(item for item in agent.tools if item.name == "commissioned_probe")
        run = await registered.execute({})
        assert json.loads(run.content) == {"built": True}

        # Contract v2: the commissioned test_code + requirements + build_evidence
        # merge into the persisted artifact rather than being discarded.
        artifact_files = list((tmp_path / "artifacts").glob("*.json"))
        assert len(artifact_files) == 1
        persisted = json.loads(artifact_files[0].read_text(encoding="utf-8"))
        assert persisted["test_code"].startswith("def test_run")
        assert persisted["requirements"] == []
        assert persisted["provenance"]["build_evidence"] == {"retrieval": "canonical_file"}

    async def test_build_tool_reuses_persisted_commission_after_restart(self, tmp_path) -> None:
        from ravn.ports.tool_build_backend import (
            ToolBuildBackend,
            ToolBuildError,
            ToolBuildRequest,
            ToolBuildResult,
        )

        class _Backend(ToolBuildBackend):
            def __init__(self, *, interrupted: bool) -> None:
                self.interrupted = interrupted
                self.requests: list[ToolBuildRequest] = []

            @property
            def name(self) -> str:
                return "recoverable"

            @property
            def supports_restart_recovery(self) -> bool:
                return True

            async def build(self, request: ToolBuildRequest) -> ToolBuildResult:
                self.requests.append(request)
                if self.interrupted:
                    raise ToolBuildError("connection closed before task response")
                return ToolBuildResult(
                    manifest={
                        "name": "restart_probe",
                        "description": "Recovered build.",
                        "input_schema": {"type": "object"},
                        "required_permission": "probe:read",
                        "declared_reach": [{"kind": "pure_compute", "access": "none"}],
                        "entry_point": "run",
                    },
                    tool_code="def run(input):\n    return {'recovered': True}\n",
                    test_code=(
                        "def test_run():\n"
                        "    import restart_probe\n"
                        "    assert restart_probe.run({})['recovered'] is True\n"
                        "\n"
                        "test_run()\n"
                    ),
                )

        build_input = {
            "manifest": {"name": "restart_probe", "required_permission": "probe:read"},
            "build_request": "Build a reusable read-only probe.",
            "canary_input": {},
        }
        artifacts_dir = tmp_path / "artifacts"

        interrupted = _Backend(interrupted=True)
        first_agent, _ = make_agent(make_simple_llm("unused"))
        first_tool = attach_build_tool(
            first_agent,
            tools_dir=tmp_path / "tools",
            artifacts_dir=artifacts_dir,
            build_backend=interrupted,
            environment_id="cluster-a",
            valkyrie_id="resident-a",
        )

        first_result = await first_tool.execute(build_input)

        assert first_result.is_error
        operation_id = interrupted.requests[0].operation_id
        pending = list((artifacts_dir / "pending-commissions").glob("*.json"))
        assert len(pending) == 1
        assert json.loads(pending[0].read_text())["state"] == "submitting"

        recovered = _Backend(interrupted=False)
        second_agent, _ = make_agent(make_simple_llm("unused"))
        second_tool = attach_build_tool(
            second_agent,
            tools_dir=tmp_path / "tools",
            artifacts_dir=artifacts_dir,
            build_backend=recovered,
            environment_id="cluster-a",
            valkyrie_id="resident-a",
        )

        recovered_results = await second_tool.recover_pending()

        assert len(recovered_results) == 1
        second_result = recovered_results[0]
        assert not second_result.is_error
        assert recovered.requests[0].operation_id == operation_id
        assert list((artifacts_dir / "pending-commissions").glob("*.json")) == []

    async def test_build_tool_rejects_build_request_without_backend(self, tmp_path) -> None:
        agent, _ = make_agent(make_simple_llm("unused"))
        tool = attach_build_tool(agent, tools_dir=tmp_path / "tools")
        result = await tool.execute(
            {
                "manifest": {"name": "x", "required_permission": "x:read"},
                "build_request": "build something",
            }
        )
        assert result.is_error
        assert "no tool build backend" in result.content

    async def test_build_tool_registers_tool_for_same_turn(self, tmp_path) -> None:
        build_call = ToolCall(
            id="tc-build",
            name="build_tool",
            input={
                "manifest": {
                    "name": "diagnose_widget",
                    "description": "Summarize a widget identifier.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"widget_id": {"type": "string"}},
                        "required": ["widget_id"],
                    },
                    "required_permission": "widget:read",
                    "declared_reach": [{"kind": "pure_compute", "access": "none"}],
                },
                "tool_code": (
                    "def run(input):\n"
                    "    return {'widget_id': input.get('widget_id'), 'status': 'ok'}\n"
                ),
                "canary_input": {"widget_id": "canary"},
            },
        )
        learned_call = ToolCall(
            id="tc-learned",
            name="diagnose_widget",
            input={"widget_id": "w-123"},
        )
        tool_names_by_call: list[list[str]] = []

        async def _stream(messages, *, tools, **kwargs) -> AsyncIterator[StreamEvent]:
            tool_names_by_call.append([tool["name"] for tool in tools])
            if len(tool_names_by_call) == 1:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=build_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            elif len(tool_names_by_call) == 2:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=learned_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=6, output_tokens=2),
                )
            else:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="diagnosed")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=7, output_tokens=3),
                )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream
        agent, _ = make_agent(llm)
        attach_build_tool(agent, tools_dir=tmp_path / "tools")

        result = await agent.run_turn("need a widget diagnostic")

        assert result.response == "diagnosed"
        assert [call.name for call in result.tool_calls] == ["build_tool", "diagnose_widget"]
        assert "diagnose_widget" not in tool_names_by_call[0]
        assert "diagnose_widget" in tool_names_by_call[1]
        assert '"status": "ok"' in result.tool_results[-1].content

    async def test_build_tool_publishes_agent_tool_flock_proposal(self, tmp_path) -> None:
        bus = InProcessBus()
        events: list[SleipnirEvent] = []
        await bus.subscribe(
            [registry.FLOCK_LEARNING_PROPOSED],
            lambda event: _record(events, event),
        )

        llm = make_simple_llm("unused")
        agent, _ = make_agent(llm)
        tool = attach_build_tool(
            agent,
            tools_dir=tmp_path / "tools",
            publisher=bus,
            environment_id="cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            flock_id="flock:k8s-valkyries",
            domain="k8s",
        )

        result = await tool.execute(
            {
                "manifest": {
                    "name": "diagnose_widget",
                    "description": "Summarize a widget identifier.",
                    "input_schema": {"type": "object"},
                    "required_permission": "widget:read",
                    "declared_reach": [{"kind": "pure_compute", "access": "none"}],
                },
                "tool_code": "def run(input):\n    return {'ok': True}\n",
                "canary_input": {},
            }
        )
        await bus.flush()

        assert not result.is_error
        assert len(events) == 1
        payload = events[0].payload
        assert payload["artifact_type"] == "agent_tool"
        assert payload["source_valkyrie_id"] == "valkyrie:k8s-a"
        assert payload["learned_tool_manifest"]["name"] == "diagnose_widget"
        assert payload["tool_code"].startswith("def run")
        # P5a default preserved: without config the proposal travels at 0.74.
        assert payload["confidence"] == pytest.approx(0.74)

    async def test_build_tool_custom_flock_confidence_travels_with_proposal(self, tmp_path) -> None:
        """P5a: a configured self_registered_tool_confidence reaches the flock
        proposal event payload through the flock_confidence kwarg."""
        bus = InProcessBus()
        events: list[SleipnirEvent] = []
        await bus.subscribe(
            [registry.FLOCK_LEARNING_PROPOSED],
            lambda event: _record(events, event),
        )

        agent, _ = make_agent(make_simple_llm("unused"))
        tool = attach_build_tool(
            agent,
            tools_dir=tmp_path / "tools",
            publisher=bus,
            environment_id="cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            flock_id="flock:k8s-valkyries",
            domain="k8s",
            flock_confidence=0.9,
        )

        result = await tool.execute(
            {
                "manifest": {
                    "name": "diagnose_widget",
                    "description": "Summarize a widget identifier.",
                    "input_schema": {"type": "object"},
                    "required_permission": "widget:read",
                    "declared_reach": [{"kind": "pure_compute", "access": "none"}],
                },
                "tool_code": "def run(input):\n    return {'ok': True}\n",
                "canary_input": {},
            }
        )
        await bus.flush()

        assert not result.is_error
        assert len(events) == 1
        assert events[0].payload["confidence"] == pytest.approx(0.9)

    async def test_build_tool_holds_mutating_tool_for_operator_review(self, tmp_path) -> None:
        bus = InProcessBus()
        events: list[SleipnirEvent] = []
        await bus.subscribe(
            [registry.ODIN_REVIEW_REQUESTED],
            lambda event: _record(events, event),
        )
        agent, _ = make_agent(make_simple_llm("unused"))
        requester = ReviewRequester(
            publisher=bus,
            store=JsonReviewStore(tmp_path / "review_outbox.json"),
            source="valkyrie:k8s-a",
        )
        tool = attach_build_tool(
            agent,
            tools_dir=tmp_path / "tools",
            artifacts_dir=tmp_path / "artifacts",
            review_requester=requester,
            environment_id="cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            flock_id="flock:k8s-valkyries",
            domain="k8s",
        )

        result = await tool.execute(
            {
                "manifest": {
                    "name": "restart_widget",
                    "description": "Restart a widget safely.",
                    "input_schema": {"type": "object"},
                    "required_permission": "widget:write",
                    "declared_reach": [{"kind": "kubernetes_write", "access": "write"}],
                },
                "tool_code": "def run(input):\n    return {'restarted': True}\n",
                "canary_input": {"name": "canary"},
            }
        )
        await bus.flush()

        payload = json.loads(result.content)
        assert not result.is_error
        assert payload["review_required"] is True
        assert payload["review_filed"] is True
        assert "restart_widget" not in {item.name for item in agent.tools}
        assert len(events) == 1
        review = events[0].payload
        assert review["kind"] == "evolution_build"
        assert review["requested_action"] == "install"
        assert review["safety_class"] == "mutating"
        assert review["evidence"]["artifact"]["artifact_type"] == "agent_tool"
        assert review["evidence"]["artifact"]["canary_sample"] == {"name": "canary"}
        # P5a default preserved: the resident artifact carries 0.74.
        assert review["evidence"]["artifact"]["confidence"] == pytest.approx(0.74)

    async def test_held_review_carries_the_investigation_prompt(self, tmp_path) -> None:
        bus = InProcessBus()
        events: list[SleipnirEvent] = []
        await bus.subscribe([registry.ODIN_REVIEW_REQUESTED], lambda event: _record(events, event))
        agent, _ = make_agent(make_simple_llm("unused"))
        requester = ReviewRequester(
            publisher=bus,
            store=JsonReviewStore(tmp_path / "review_outbox.json"),
            source="valkyrie:k8s-a",
        )
        ticket = "A Kubernetes signal arrived: Pod OOMKilled in payments. Investigate it."
        tool = attach_build_tool(
            agent,
            tools_dir=tmp_path / "tools",
            artifacts_dir=tmp_path / "artifacts",
            review_requester=requester,
            autonomy_mode="guarded",
            environment_id="cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            flock_id="flock:k8s-valkyries",
            domain="k8s",
            investigation_context=lambda: ticket,
        )

        result = await tool.execute(
            {
                "manifest": {
                    "name": "inspect_pod",
                    "description": "Inspect a pod.",
                    "input_schema": {"type": "object"},
                    "required_permission": "k8s:read",
                    "declared_reach": [{"kind": "pure_compute", "access": "none"}],
                },
                "tool_code": "def run(input):\n    return {'ok': True}\n",
                "canary_input": {},
            }
        )
        await bus.flush()

        assert not result.is_error
        assert len(events) == 1
        assert events[0].payload["evidence"]["investigation_prompt"] == ticket

    async def test_build_tool_holds_read_only_credential_tool_via_boundary(self, tmp_path) -> None:
        """A read-only tool that reaches credentials is gated by the autonomy
        policy's hard boundary, not by access level — the one policy stays
        faithful where a naive risk-class would auto-install it."""
        bus = InProcessBus()
        events: list[SleipnirEvent] = []
        await bus.subscribe(
            [registry.ODIN_REVIEW_REQUESTED],
            lambda event: _record(events, event),
        )
        agent, _ = make_agent(make_simple_llm("unused"))
        requester = ReviewRequester(
            publisher=bus,
            store=JsonReviewStore(tmp_path / "review_outbox.json"),
            source="valkyrie:k8s-a",
        )
        # yolo is the most permissive mode; even it must hold a credential read.
        tool = attach_build_tool(
            agent,
            tools_dir=tmp_path / "tools",
            artifacts_dir=tmp_path / "artifacts",
            review_requester=requester,
            autonomy_mode="yolo",
            environment_id="cluster-a",
            valkyrie_id="valkyrie:k8s-a",
        )

        result = await tool.execute(
            {
                "manifest": {
                    "name": "read_vault_secret",
                    "description": "Read a secret from the vault.",
                    "input_schema": {"type": "object"},
                    "required_permission": "secret:read",
                    "declared_reach": [{"kind": "credential", "access": "read"}],
                },
                "tool_code": "def run(input):\n    return {'ok': True}\n",
                "canary_input": {},
            }
        )
        await bus.flush()

        payload = json.loads(result.content)
        assert payload["review_required"] is True
        assert payload["review_filed"] is True
        assert "read_vault_secret" not in {item.name for item in agent.tools}
        assert len(events) == 1
        assert events[0].payload["safety_class"] == "read_only"

    async def test_build_tool_can_register_forge_sandboxed_tool(self, tmp_path) -> None:
        shell = _FakeSandboxShell()
        llm = make_simple_llm("unused")
        agent, _ = make_agent(llm)
        tool = attach_build_tool(
            agent,
            tools_dir=tmp_path / ".ravn" / "learned_tools",
            execution_backend="forge",
            workspace_root=tmp_path,
            sandbox_shell=shell,
        )

        build = await tool.execute(
            {
                "manifest": {
                    "name": "sandbox_probe",
                    "description": "Run in the Forge sandbox.",
                    "input_schema": {"type": "object"},
                    "required_permission": "sandbox:read",
                    "declared_reach": [{"kind": "pure_compute", "access": "none"}],
                },
                "tool_code": "def run(input):\n    return {'backend': 'local'}\n",
                "canary_input": {},
            }
        )
        learned = next(item for item in agent.tools if item.name == "sandbox_probe")
        result = await learned.execute({})

        assert not build.is_error
        assert not result.is_error
        assert json.loads(result.content) == {"backend": "forge", "ok": True}
        assert shell.commands
        assert "python -I" in shell.commands[0]

    async def test_tool_start_event_emitted(self) -> None:
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "hi"})

        call_count = 0

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            else:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="done")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream
        agent, channel = make_agent(llm, tools=[tool])
        await agent.run_turn("go")

        event_types = [e.type for e in channel.events]
        assert RavnEventType.TOOL_START in event_types
        assert RavnEventType.TOOL_RESULT in event_types

    async def test_unknown_tool_returns_error(self) -> None:
        tool_call = ToolCall(id="tc1", name="nonexistent", input={})

        call_count = 0

        async def _stream2(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            else:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream2
        agent, channel = make_agent(llm, tools=[])
        await agent.run_turn("go")

        error_events = [e for e in channel.events if e.type == RavnEventType.TOOL_RESULT]
        assert any(e.payload.get("is_error") for e in error_events)

    async def test_permission_denied_returns_error(self) -> None:
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "hi"})

        call_count = 0

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            else:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="denied handled")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream
        agent, channel = make_agent(llm, tools=[tool], permission=DenyAllPermission())
        await agent.run_turn("go")

        tool_result_events = [e for e in channel.events if e.type == RavnEventType.TOOL_RESULT]
        assert any(e.payload.get("is_error") for e in tool_result_events)

    async def test_tool_exception_returns_error_result(self) -> None:
        tool = FailingTool()
        tool_call = ToolCall(id="tc1", name="fail", input={})

        call_count = 0

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            else:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="handled error")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream
        agent, channel = make_agent(llm, tools=[tool])
        result = await agent.run_turn("go")

        assert len(result.tool_results) == 1
        assert result.tool_results[0].is_error is True
        assert "intentional failure" in result.tool_results[0].content

    async def test_max_iterations_raises(self) -> None:
        """Agent that always returns tool_use should hit the iteration limit."""
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "loop"})

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream

        agent, _ = make_agent(llm, tools=[tool], max_iterations=3)

        with pytest.raises(MaxIterationsError) as exc_info:
            await agent.run_turn("go")

        assert exc_info.value.max_iterations == 3

    async def test_zero_max_iterations_runs_until_model_finishes(self) -> None:
        """max_iterations=0 is intentionally unbounded."""
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "loop"})
        calls = 0

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal calls
            calls += 1
            if calls <= 5:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=1, output_tokens=1),
                )
                return
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="done")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream

        agent, _ = make_agent(llm, tools=[tool], max_iterations=0)
        result = await agent.run_turn("go")

        assert result.response == "done"
        assert len(result.tool_results) == 5


class TestRavnAgentHooks:
    async def test_pre_and_post_hooks_called(self) -> None:
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "test"})
        pre_calls: list[ToolCall] = []
        post_calls: list[tuple[ToolCall, ToolResult]] = []

        async def pre_hook(tc: ToolCall) -> None:
            pre_calls.append(tc)

        async def post_hook(tc: ToolCall, tr: ToolResult) -> None:
            post_calls.append((tc, tr))

        call_count = 0

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            else:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="done")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream
        ch = InMemoryChannel()
        agent = RavnAgent(
            llm=llm,
            tools=[tool],
            channel=ch,
            permission=AllowAllPermission(),
            system_prompt="",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            max_iterations=10,
            pre_tool_hooks=[pre_hook],
            post_tool_hooks=[post_hook],
        )
        await agent.run_turn("go")

        assert len(pre_calls) == 1
        assert len(post_calls) == 1
        assert pre_calls[0].name == "echo"
        assert post_calls[0][1].content == "test"


class TestBuildAssistantContent:
    def test_text_only(self) -> None:
        resp = LLMResponse(
            content="hello",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
        blocks = _build_assistant_content(resp)
        assert len(blocks) == 1
        assert blocks[0] == {"type": "text", "text": "hello"}

    def test_tool_calls_only(self) -> None:
        tc = ToolCall(id="x", name="echo", input={"msg": "hi"})
        resp = LLMResponse(
            content="",
            tool_calls=[tc],
            stop_reason=StopReason.TOOL_USE,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
        blocks = _build_assistant_content(resp)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["id"] == "x"
        assert blocks[0]["name"] == "echo"

    def test_text_and_tool_calls(self) -> None:
        tc = ToolCall(id="y", name="run", input={})
        resp = LLMResponse(
            content="thinking...",
            tool_calls=[tc],
            stop_reason=StopReason.TOOL_USE,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
        blocks = _build_assistant_content(resp)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "tool_use"
