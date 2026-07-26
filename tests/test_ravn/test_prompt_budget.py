"""Tests for prompt-composition audit, prompt budget, and result caps (NIU-1118)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from ravn.agent import RavnAgent
from ravn.domain.exceptions import PromptBudgetExceededError
from ravn.domain.models import (
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from ravn.ports.llm import LLMPort
from ravn.ports.tool import ToolPort
from ravn.prompt_builder import PromptBuilder
from tests.test_ravn.conftest import (
    AllowAllPermission,
    EchoTool,
    InMemoryChannel,
    make_simple_llm,
)


def _agent(
    llm: LLMPort,
    *,
    tools: list[ToolPort] | None = None,
    max_prompt_tokens: int = 0,
    max_tool_result_chars: int = 0,
    prompt_builder: PromptBuilder | None = None,
    system_prompt: str = "You are a test assistant.",
    context_window_tokens: int = 0,
    token_estimate_safety_factor: float = 1.0,
) -> RavnAgent:
    return RavnAgent(
        llm=llm,
        tools=tools if tools is not None else [EchoTool()],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt=system_prompt,
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=5,
        prompt_builder=prompt_builder,
        max_prompt_tokens=max_prompt_tokens,
        context_window_tokens=context_window_tokens,
        token_estimate_safety_factor=token_estimate_safety_factor,
        max_tool_result_chars=max_tool_result_chars,
    )


class GiantResultTool(ToolPort):
    """Returns a configurable amount of content — the 229MB-mimir-result stand-in."""

    def __init__(self, size: int) -> None:
        self._size = size

    @property
    def name(self) -> str:
        return "giant_result"

    @property
    def description(self) -> str:
        return "Return a giant payload."

    @property
    def input_schema(self) -> dict:
        return {"type": "object"}

    @property
    def required_permission(self) -> str:
        return "test:read"

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        return ToolResult(tool_call_id="", content="x" * self._size)


def make_tool_then_text_llm(tool_name: str) -> LLMPort:
    """LLM that calls *tool_name* on the first iteration, then finishes."""
    calls = {"count": 0}

    async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
        calls["count"] += 1
        if calls["count"] == 1:
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL,
                tool_call=ToolCall(id="tc1", name=tool_name, input={}),
            )
        else:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="done")
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )

    llm = AsyncMock(spec=LLMPort)
    llm.stream = _stream
    return llm


class TestPromptCompositionAudit:
    async def test_audit_logs_per_section_token_counts(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = _agent(make_simple_llm("ok"))

        with caplog.at_level(logging.INFO, logger="ravn.agent"):
            await agent.run_turn("hello")

        audit_lines = [r.message for r in caplog.records if "prompt_composition:" in r.message]
        assert len(audit_lines) == 1
        assert "tool_schemas≈" in audit_lines[0]
        assert "history≈" in audit_lines[0]
        assert "system≈" in audit_lines[0]

    async def test_audit_attributes_prompt_builder_sections(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        builder = PromptBuilder()
        builder.set_memory_context("remembered context " * 10)

        agent = _agent(make_simple_llm("ok"), prompt_builder=builder)
        with caplog.at_level(logging.INFO, logger="ravn.agent"):
            await agent.run_turn("hello")

        audit = next(r.message for r in caplog.records if "prompt_composition:" in r.message)
        assert "system:identity≈" in audit
        assert "system:memory_context≈" in audit

    def test_section_texts_exposes_non_empty_sections(self) -> None:
        builder = PromptBuilder()
        builder.set_identity("identity text")
        builder.set_memory_context("")
        builder.set_learnings_context("a lesson")

        assert builder.section_texts() == {
            "identity": "identity text",
            "learnings_context": "a lesson",
        }


class TestPromptBudget:
    async def test_disabled_budget_never_blocks(self) -> None:
        agent = _agent(make_simple_llm("ok"), max_prompt_tokens=0)
        result = await agent.run_turn("hello " * 200)
        assert result.response == "ok"

    async def test_oversized_prompt_fails_loud(self) -> None:
        agent = _agent(make_simple_llm("ok"), max_prompt_tokens=10)

        with pytest.raises(PromptBudgetExceededError) as excinfo:
            await agent.run_turn("word " * 500)

        assert excinfo.value.budget_tokens == 10
        assert excinfo.value.estimated_tokens > 10
        assert "history" in excinfo.value.sections
        assert "tool_schemas" in excinfo.value.sections

    async def test_prompt_under_budget_proceeds(self) -> None:
        agent = _agent(make_simple_llm("ok"), max_prompt_tokens=100_000)
        result = await agent.run_turn("hello")
        assert result.response == "ok"

    async def test_budget_error_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        agent = _agent(make_simple_llm("ok"), max_prompt_tokens=10)

        with caplog.at_level(logging.ERROR, logger="ravn.agent"):
            with pytest.raises(PromptBudgetExceededError):
                await agent.run_turn("word " * 500)

        assert any("Prompt budget exceeded" in r.message for r in caplog.records)

    async def test_context_window_reserves_maximum_output(self) -> None:
        agent = _agent(
            make_simple_llm("ok"),
            max_prompt_tokens=5_000,
            context_window_tokens=2_048,
        )

        await agent.run_turn("hello")

        assert agent.prompt_budget_status["prompt_budget_tokens"] == 1_024
        assert agent.prompt_budget_status["output_reserve_tokens"] == 1_024
        assert agent.prompt_budget_status["context_window_tokens"] == 2_048

    async def test_safety_factor_applies_before_budget_check(self) -> None:
        baseline = _agent(
            make_simple_llm("ok"),
            max_prompt_tokens=100_000,
            token_estimate_safety_factor=1.0,
        )
        conservative = _agent(
            make_simple_llm("ok"),
            max_prompt_tokens=100_000,
            token_estimate_safety_factor=1.5,
        )

        await baseline.run_turn("hello")
        await conservative.run_turn("hello")

        assert (
            conservative.prompt_budget_status["estimated_prompt_tokens"]
            > baseline.prompt_budget_status["estimated_prompt_tokens"]
        )

    def test_rejects_output_reserve_larger_than_context_window(self) -> None:
        with pytest.raises(ValueError, match="max_tokens must be smaller"):
            _agent(
                make_simple_llm("ok"),
                context_window_tokens=1_024,
            )


class TestToolResultCap:
    async def test_oversized_result_is_truncated_with_marker(self) -> None:
        agent = _agent(
            make_tool_then_text_llm("giant_result"),
            tools=[GiantResultTool(size=50_000)],
            max_tool_result_chars=1_000,
        )

        result = await agent.run_turn("go")

        assert result.response == "done"
        truncated = result.tool_results[0].content
        assert len(truncated) < 2_000
        assert "[tool result truncated: 49000 characters" in truncated
        # The truncated version — not the original — is what entered history.
        history_blob = str([m.content for m in agent.session.messages])
        assert "tool result truncated" in history_blob
        assert "x" * 2_000 not in history_blob

    async def test_disabled_cap_keeps_full_result(self) -> None:
        agent = _agent(
            make_tool_then_text_llm("giant_result"),
            tools=[GiantResultTool(size=50_000)],
            max_tool_result_chars=0,
        )

        result = await agent.run_turn("go")

        assert len(result.tool_results[0].content) == 50_000

    async def test_result_under_cap_is_untouched(self) -> None:
        agent = _agent(
            make_tool_then_text_llm("giant_result"),
            tools=[GiantResultTool(size=100)],
            max_tool_result_chars=1_000,
        )

        result = await agent.run_turn("go")

        assert result.tool_results[0].content == "x" * 100

    async def test_truncation_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        agent = _agent(
            make_tool_then_text_llm("giant_result"),
            tools=[GiantResultTool(size=5_000)],
            max_tool_result_chars=1_000,
        )

        with caplog.at_level(logging.WARNING, logger="ravn.agent"):
            await agent.run_turn("go")

        assert any("truncated" in r.message for r in caplog.records)
