"""Tests for prompt-composition audit and the hard prompt budget (NIU-1118)."""

from __future__ import annotations

import logging

import pytest

from ravn.agent import RavnAgent
from ravn.domain.exceptions import PromptBudgetExceededError
from ravn.ports.llm import LLMPort
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
    max_prompt_tokens: int = 0,
    prompt_builder: PromptBuilder | None = None,
    system_prompt: str = "You are a test assistant.",
) -> RavnAgent:
    return RavnAgent(
        llm=llm,
        tools=[EchoTool()],
        channel=InMemoryChannel(),
        permission=AllowAllPermission(),
        system_prompt=system_prompt,
        model="claude-sonnet-4-6",
        max_tokens=1024,
        max_iterations=5,
        prompt_builder=prompt_builder,
        max_prompt_tokens=max_prompt_tokens,
    )


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
