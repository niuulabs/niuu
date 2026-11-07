"""Tests for the Ravn agent loop."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.domain.outcome import OutcomeField
from ravn.adapters.personas.loader import PersonaConfig, PersonaProduces
from ravn.agent import RavnAgent, _build_assistant_content
from ravn.budget import IterationBudget
from ravn.domain.events import RavnEventType
from ravn.domain.exceptions import MaxIterationsError, PromptBudgetExceededError
from ravn.domain.models import (
    LLMResponse,
    Message,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from ravn.ports.llm import LLMPort
from ravn.prompt_builder import PromptBuilder
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


@pytest.mark.asyncio
async def test_outcome_repair_is_tool_free_and_does_not_replay_history() -> None:
    llm = AsyncMock(spec=LLMPort)
    usage = TokenUsage(input_tokens=100, output_tokens=50)
    llm.generate.return_value = LLMResponse(
        content="working_state:\n  observations: []",
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=usage,
    )
    agent, _ = make_agent(llm, tools=[EchoTool()], max_prompt_tokens=1000)
    history = Message(role="user", content="old observations " * 20000)
    agent.session.messages.append(history)

    result = await agent.repair_outcome("Repair working_state as a mapping")

    assert result.response == llm.generate.return_value.content
    assert result.usage == usage
    assert agent.session.messages == [history]
    assert llm.generate.call_args.args[0] == [
        {"role": "user", "content": "Repair working_state as a mapping"}
    ]
    assert llm.generate.call_args.kwargs["tools"] == []
    assert agent.prompt_budget_status["estimated_prompt_tokens"] < 1000

    llm.generate.reset_mock()
    with pytest.raises(PromptBudgetExceededError):
        await agent.repair_outcome("oversized repair " * 20000)
    llm.generate.assert_not_called()

    llm.generate.return_value = LLMResponse(
        content="",
        tool_calls=[ToolCall(id="x", name="echo", input={})],
        stop_reason=StopReason.TOOL_USE,
        usage=usage,
    )
    with pytest.raises(ValueError, match="not tool calls"):
        await agent.repair_outcome("Repair working_state")

    budget = IterationBudget(total=1)
    agent, _ = make_agent(llm, iteration_budget=budget)
    llm.generate.return_value = LLMResponse(
        content="repaired", tool_calls=[], stop_reason=StopReason.END_TURN, usage=usage
    )
    await agent.repair_outcome("Repair working_state")
    assert budget.exhausted
    llm.generate.reset_mock()
    with pytest.raises(MaxIterationsError):
        await agent.repair_outcome("Repair working_state")
    llm.generate.assert_not_called()


class _FakeSandboxShell:
    def __init__(self) -> None:
        self.commands: list[str] = []
        # An isolated sandbox: the forge runner refuses a no-network tool on
        # a shell whose network mode it cannot see.
        self._config = SimpleNamespace(network="none")

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
    async def test_tool_call_after_message_done_is_still_executed(self) -> None:
        """MESSAGE_DONE is not the last event. A server sending a usage chunk
        emits it mid-stream, so a tool call recovered at end of stream lands
        after it. Deciding TOOL_USE inside MESSAGE_DONE left the agent holding
        a tool call it never ran — one iteration, no evidence, no outcome."""
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "ping"})

        call_count = 0

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
                yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tool_call)
            else:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="pong received")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=8, output_tokens=3),
                )

        llm = AsyncMock(spec=LLMPort)
        llm.stream = _stream

        agent, _ = make_agent(llm, tools=[tool])
        result = await agent.run_turn("echo ping")

        assert result.response == "pong received"
        assert [call.name for call in result.tool_calls] == ["echo"]
        assert [r.content for r in result.tool_results] == ["ping"]

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
