"""Integration tests for context management features in RavnAgent (NIU-431).

Tests the interaction between:
- IterationBudget and the agent loop
- ContextCompressor and session message flow
- PromptBuilder and system prompt construction
- Budget warnings injected into tool results
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.agent import RavnAgent, _maybe_append_budget_warning
from ravn.budget import IterationBudget
from ravn.compression import CompressionResult, ContextCompressor
from ravn.domain.exceptions import MaxIterationsError
from ravn.domain.models import (
    Message,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from ravn.ports.llm import LLMPort
from ravn.prompt_builder import PromptBuilder
from tests.ravn.fixtures.fakes import EchoTool, InMemoryChannel
from tests.test_ravn.conftest import make_simple_llm

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_call_llm(
    tool_name: str = "echo",
    tool_input: dict | None = None,
    response_after_tool: str = "Done",
) -> LLMPort:
    """LLM that requests one tool call, then returns a final response."""
    call_count = 0
    tool_input = tool_input or {"message": "hi"}

    async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: request a tool
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL,
                tool_call=ToolCall(id="tc1", name=tool_name, input=tool_input),
            )
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            )
        else:
            # Second call: return final answer
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=response_after_tool)
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            )

    llm = MagicMock(spec=LLMPort)
    llm.stream = _stream
    return llm


def _make_agent(
    llm: LLMPort | None = None,
    *,
    iteration_budget: IterationBudget | None = None,
    compressor: ContextCompressor | None = None,
    prompt_builder: PromptBuilder | None = None,
    max_iterations: int = 10,
) -> tuple[RavnAgent, InMemoryChannel]:
    channel = InMemoryChannel()
    from ravn.adapters.permission.allow_deny import AllowAllPermission

    agent = RavnAgent(
        llm=llm or make_simple_llm(),
        tools=[EchoTool()],
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="You are Ravn.",
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=max_iterations,
        iteration_budget=iteration_budget,
        compressor=compressor,
        prompt_builder=prompt_builder,
    )
    return agent, channel


# ---------------------------------------------------------------------------
# IterationBudget integration
# ---------------------------------------------------------------------------


class TestAgentIterationBudget:
    @pytest.mark.asyncio
    async def test_budget_consumed_each_iteration(self):
        """Budget.consume() called once per LLM invocation."""
        budget = IterationBudget(total=10)
        llm = _make_tool_call_llm()
        agent, _ = _make_agent(llm, iteration_budget=budget)
        await agent.run_turn("do something")
        # 2 LLM calls: one for tool-use, one for final response
        assert budget.consumed == 2

    @pytest.mark.asyncio
    async def test_exhausted_budget_raises_before_turn(self):
        """MaxIterationsError raised immediately when budget exhausted."""
        budget = IterationBudget(total=0)  # Already exhausted
        agent, _ = _make_agent(iteration_budget=budget)
        with pytest.raises(MaxIterationsError):
            await agent.run_turn("do something")

    @pytest.mark.asyncio
    async def test_exhausted_budget_raises_during_turn(self):
        """Budget exhausted mid-loop raises MaxIterationsError."""
        budget = IterationBudget(total=1)
        llm = _make_tool_call_llm()  # Needs 2 iterations
        agent, _ = _make_agent(llm, iteration_budget=budget, max_iterations=5)
        with pytest.raises(MaxIterationsError):
            await agent.run_turn("do something")

    @pytest.mark.asyncio
    async def test_budget_warning_injected_into_tool_result(self):
        """Near-limit budget warning appears in tool result content."""
        # total=10, near_limit_threshold=0.8 → near at 8/10
        budget = IterationBudget(total=10, near_limit_threshold=0.8)
        budget.consumed = 7  # One more will trigger near_limit
        llm = _make_tool_call_llm()
        agent, channel = _make_agent(llm, iteration_budget=budget)
        result = await agent.run_turn("do something")
        # After 1st iteration budget.consumed = 8 → near_limit
        # Warning should be in the tool result content in the session
        # Check last turn tool results
        assert any("Budget warning" in r.content for r in result.tool_results)

    @pytest.mark.asyncio
    async def test_no_budget_no_error(self):
        """Agent runs normally without iteration_budget."""
        agent, _ = _make_agent()
        result = await agent.run_turn("hello")
        assert result.response == "Hello!"

    @pytest.mark.asyncio
    async def test_budget_property_accessible(self):
        budget = IterationBudget(total=20)
        agent, _ = _make_agent(iteration_budget=budget)
        assert agent.iteration_budget is budget


# ---------------------------------------------------------------------------
# _maybe_append_budget_warning helper
# ---------------------------------------------------------------------------


class TestMaybeAppendBudgetWarning:
    def test_none_budget_returns_original(self):
        result = ToolResult(tool_call_id="1", content="output")
        assert _maybe_append_budget_warning(result, None) is result

    def test_no_warning_when_healthy(self):
        budget = IterationBudget(total=100)
        result = ToolResult(tool_call_id="1", content="output")
        new_result = _maybe_append_budget_warning(result, budget)
        assert new_result is result

    def test_warning_appended_near_limit(self):
        budget = IterationBudget(total=10, near_limit_threshold=0.8)
        budget.consume(9)
        result = ToolResult(tool_call_id="1", content="output")
        new_result = _maybe_append_budget_warning(result, budget)
        assert new_result is not result
        assert "Budget warning" in new_result.content
        assert "output" in new_result.content

    def test_warning_preserves_is_error(self):
        budget = IterationBudget(total=5)
        budget.consume(5)
        result = ToolResult(tool_call_id="1", content="err", is_error=True)
        new_result = _maybe_append_budget_warning(result, budget)
        assert new_result.is_error is True

    def test_exhausted_warning_content(self):
        budget = IterationBudget(total=5)
        budget.consume(5)
        result = ToolResult(tool_call_id="1", content="done")
        new_result = _maybe_append_budget_warning(result, budget)
        assert "exhausted" in new_result.content.lower()


# ---------------------------------------------------------------------------
# ContextCompressor integration
# ---------------------------------------------------------------------------


class TestAgentContextCompressor:
    @pytest.mark.asyncio
    async def test_compressor_called_before_llm(self):
        """Compressor.maybe_compress is invoked when compressor is set."""
        mock_compressor = MagicMock(spec=ContextCompressor)
        mock_compressor.maybe_compress = AsyncMock(
            return_value=(
                [Message(role="user", content="hello")],
                CompressionResult(original_count=1, final_count=1),
            )
        )
        agent, _ = _make_agent(compressor=mock_compressor)
        await agent.run_turn("hello")
        mock_compressor.maybe_compress.assert_called()

    @pytest.mark.asyncio
    async def test_last_compression_result_stored(self):
        """last_compression_result is updated after a compression pass."""
        mock_compressor = MagicMock(spec=ContextCompressor)
        mock_compressor.maybe_compress = AsyncMock(
            return_value=(
                [Message(role="user", content="summary"), Message(role="user", content="hello")],
                CompressionResult(
                    original_count=5,
                    final_count=2,
                    compression_count=1,
                    removed_message_count=3,
                ),
            )
        )
        agent, _ = _make_agent(compressor=mock_compressor)
        await agent.run_turn("hello")
        assert agent.last_compression_result is not None
        assert agent.last_compression_result.compression_count == 1

    @pytest.mark.asyncio
    async def test_no_compressor_last_result_none(self):
        agent, _ = _make_agent()
        await agent.run_turn("hello")
        assert agent.last_compression_result is None

    @pytest.mark.asyncio
    async def test_memory_summary_passed_to_compressor(self):
        """When a memory port is configured, memory_summary is forwarded to maybe_compress."""
        from unittest.mock import AsyncMock, MagicMock

        from ravn.adapters.permission.allow_deny import AllowAllPermission
        from ravn.agent import RavnAgent
        from ravn.ports.memory import MemoryPort
        from tests.ravn.fixtures.fakes import EchoTool, InMemoryChannel

        mock_memory = MagicMock(spec=MemoryPort)
        mock_memory.prefetch = AsyncMock(return_value="Relevant past: user prefers raw SQL.")

        captured_calls: list[dict] = []

        async def _capture_compress(
            messages,
            *,
            system_tokens=0,
            tool_tokens=0,
            prompt_budget_tokens=0,
            todos=None,
            memory_summary=None,
        ):
            captured_calls.append({"memory_summary": memory_summary})
            return messages, CompressionResult(
                original_count=len(messages), final_count=len(messages)
            )

        mock_compressor = MagicMock(spec=ContextCompressor)
        mock_compressor.maybe_compress = _capture_compress

        channel = InMemoryChannel()
        agent = RavnAgent(
            llm=make_simple_llm(),
            tools=[EchoTool()],
            channel=channel,
            permission=AllowAllPermission(),
            system_prompt="You are Ravn.",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            max_iterations=10,
            memory=mock_memory,
            compressor=mock_compressor,
        )
        await agent.run_turn("do something")
        assert captured_calls, "maybe_compress was never called"
        assert captured_calls[0]["memory_summary"] == "Relevant past: user prefers raw SQL."

    @pytest.mark.asyncio
    async def test_session_messages_updated_after_compression(self):
        """After compression, agent.session.messages reflects the compressed state."""
        compressed_msgs = [
            Message(role="user", content="[Conversation summary: earlier context]"),
            Message(role="user", content="hello"),
        ]
        mock_compressor = MagicMock(spec=ContextCompressor)
        mock_compressor.maybe_compress = AsyncMock(
            return_value=(
                compressed_msgs,
                CompressionResult(
                    original_count=5,
                    final_count=2,
                    compression_count=1,
                    removed_message_count=3,
                ),
            )
        )
        agent, _ = _make_agent(compressor=mock_compressor)
        await agent.run_turn("hello")
        # Session messages must reflect the compressed state so subsequent
        # iterations don't re-compress from the full uncompressed history.
        session_contents = [m.content for m in agent.session.messages]
        assert "[Conversation summary: earlier context]" in session_contents


# ---------------------------------------------------------------------------
# PromptBuilder integration
# ---------------------------------------------------------------------------


class TestAgentPromptBuilder:
    @pytest.mark.asyncio
    async def test_prompt_builder_used_when_set(self):
        """Agent calls render_blocks() when prompt_builder is provided."""
        builder = PromptBuilder()
        builder.set_identity("You are Ravn (from builder).")
        builder.set_guidance("claude-sonnet-4-6")

        call_system: list = []

        async def capturing_stream(messages, *, tools, system, model, max_tokens, thinking=None):
            call_system.append(system)
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="OK")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=5, output_tokens=5),
            )

        llm = MagicMock(spec=LLMPort)
        llm.stream = capturing_stream

        agent, _ = _make_agent(llm, prompt_builder=builder)
        await agent.run_turn("test")

        # System prompt should be a list of blocks (from render_blocks)
        assert call_system
        assert isinstance(call_system[0], list)

    @pytest.mark.asyncio
    async def test_fallback_to_string_without_builder(self):
        """Without a prompt_builder, system prompt is a plain string."""
        call_system: list = []

        async def capturing_stream(messages, *, tools, system, model, max_tokens, thinking=None):
            call_system.append(system)
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="OK")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=5, output_tokens=5),
            )

        llm = MagicMock(spec=LLMPort)
        llm.stream = capturing_stream

        agent, _ = _make_agent(llm)
        await agent.run_turn("test")
        assert isinstance(call_system[0], str)

    @pytest.mark.asyncio
    async def test_prompt_builder_with_memory(self):
        """PromptBuilder integrates memory context when memory port is set."""
        from unittest.mock import AsyncMock as _AsyncMock

        from ravn.ports.memory import MemoryPort

        memory = MagicMock(spec=MemoryPort)
        memory.prefetch = _AsyncMock(return_value="Past episode: finished task X.")
        memory.record_episode = _AsyncMock(return_value=None)

        builder = PromptBuilder()
        builder.set_identity("You are Ravn.")

        call_system: list = []

        async def capturing_stream(messages, *, tools, system, model, max_tokens, thinking=None):
            call_system.append(system)
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="OK")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=5, output_tokens=5),
            )

        llm = MagicMock(spec=LLMPort)
        llm.stream = capturing_stream

        from ravn.adapters.permission.allow_deny import AllowAllPermission

        agent = RavnAgent(
            llm=llm,
            tools=[EchoTool()],
            channel=InMemoryChannel(),
            permission=AllowAllPermission(),
            system_prompt="You are Ravn.",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            max_iterations=5,
            memory=memory,
            prompt_builder=builder,
        )
        await agent.run_turn("hello")

        # Memory prefetch was called
        memory.prefetch.assert_called_once_with("hello")
        # System should be a list of blocks (from render_blocks)
        assert isinstance(call_system[0], list)

    @pytest.mark.asyncio
    async def test_prompt_builder_memory_prefetch_failure_continues(self):
        """Memory prefetch failure with PromptBuilder still returns blocks."""
        from unittest.mock import AsyncMock as _AsyncMock

        from ravn.ports.memory import MemoryPort

        memory = MagicMock(spec=MemoryPort)
        memory.prefetch = _AsyncMock(side_effect=RuntimeError("DB error"))
        memory.record_episode = _AsyncMock(return_value=None)

        builder = PromptBuilder()
        builder.set_identity("You are Ravn.")

        async def simple_stream(messages, *, tools, system, model, max_tokens, thinking=None):
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="OK")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=5, output_tokens=5),
            )

        llm = MagicMock(spec=LLMPort)
        llm.stream = simple_stream

        from ravn.adapters.permission.allow_deny import AllowAllPermission

        agent = RavnAgent(
            llm=llm,
            tools=[EchoTool()],
            channel=InMemoryChannel(),
            permission=AllowAllPermission(),
            system_prompt="You are Ravn.",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            max_iterations=5,
            memory=memory,
            prompt_builder=builder,
        )
        # Should not raise despite memory failure
        result = await agent.run_turn("hello")
        assert result.response == "OK"


# ---------------------------------------------------------------------------
# Config round-trip
# ---------------------------------------------------------------------------


class TestContextManagementConfig:
    def test_defaults(self):
        from ravn.config import ContextManagementConfig, IterationBudgetConfig

        c = ContextManagementConfig()
        assert c.compression_threshold == 0.8
        assert c.protect_first_messages == 2
        assert c.protect_last_messages == 6
        assert c.compact_recent_turns == 3
        assert c.compression_max_tokens == 1024
        assert c.prompt_cache_max_entries == 16
        # effective_protect_last: compact_recent_turns=3 → 3*2=6
        assert c.effective_protect_last() == 6

        b = IterationBudgetConfig()
        assert b.total == 90
        assert b.near_limit_threshold == 0.8

    def test_effective_protect_last_uses_compact_recent_turns(self):
        from ravn.config import ContextManagementConfig

        c = ContextManagementConfig(compact_recent_turns=4, protect_last_messages=6)
        assert c.effective_protect_last() == 8  # 4 turns * 2

    def test_effective_protect_last_falls_back_when_zero(self):
        from ravn.config import ContextManagementConfig

        c = ContextManagementConfig(compact_recent_turns=0, protect_last_messages=10)
        assert c.effective_protect_last() == 10

    def test_settings_has_new_fields(self):
        from ravn.config import Settings

        s = Settings()
        assert s.iteration_budget.total == 90
        assert s.context_management.compression_threshold == 0.8
