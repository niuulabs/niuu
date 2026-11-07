"""Unit tests for AskUserTool and agent-loop interception."""

from __future__ import annotations

import pytest

from niuu.mesh.events import MeshEventType
from ravn.adapters.tools.ask_user import AskUserTool
from ravn.domain.models import LLMResponse, StopReason, TokenUsage, ToolCall
from tests.ravn.conftest import MockLLM, make_agent, make_text_response

# ---------------------------------------------------------------------------
# AskUserTool schema / properties
# ---------------------------------------------------------------------------


class TestAskUserToolProperties:
    def test_name(self) -> None:
        assert AskUserTool().name == "ask_user"

    def test_description_mentions_question(self) -> None:
        desc = AskUserTool().description.lower()
        assert "question" in desc or "ask" in desc

    def test_input_schema_requires_question(self) -> None:
        schema = AskUserTool().input_schema
        assert "question" in schema["properties"]
        assert "question" in schema["required"]

    def test_required_permission(self) -> None:
        assert AskUserTool().required_permission == "ask_user"

    def test_not_parallelisable(self) -> None:
        assert AskUserTool().parallelisable is False

    def test_to_api_dict_has_correct_name(self) -> None:
        api = AskUserTool().to_api_dict()
        assert api["name"] == "ask_user"
        assert "description" in api
        assert "input_schema" in api

    @pytest.mark.asyncio
    async def test_execute_returns_error_result(self) -> None:
        """execute() should never be called in practice but is not totally broken."""
        tool = AskUserTool()
        result = await tool.execute({"question": "hello?"})
        assert result.is_error
        assert "intercepted" in result.content.lower()


# ---------------------------------------------------------------------------
# Agent-loop interception
# ---------------------------------------------------------------------------


def _make_ask_user_response(question: str) -> LLMResponse:
    """Build an LLMResponse where the LLM calls ask_user."""
    tool_call = ToolCall(
        id="tc-ask-1",
        name="ask_user",
        input={"question": question},
    )
    return LLMResponse(
        content="",
        tool_calls=[tool_call],
        stop_reason=StopReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


@pytest.mark.asyncio
class TestAgentInterceptsAskUser:
    async def test_intercepts_ask_user_and_injects_answer(self) -> None:
        """Agent should call user_input_fn and inject its return value as tool result."""
        answers = ["Paris"]

        async def user_input(question: str) -> str:
            return answers.pop(0)

        llm = MockLLM(
            [
                _make_ask_user_response("What is the capital of France?"),
                make_text_response("The capital is Paris."),
            ]
        )
        agent, channel = make_agent(
            llm,
            tools=[AskUserTool()],
            user_input_fn=user_input,
        )

        result = await agent.run_turn("Where is the capital?")

        assert "Paris" in result.response
        # The agent should have emitted a TOOL_START for ask_user.
        start_events = [e for e in channel.events if e.type == MeshEventType.TOOL_START]
        assert any(e.payload.get("tool_name") == "ask_user" for e in start_events)
        # And a TOOL_RESULT with the answer.
        result_events = [e for e in channel.events if e.type == MeshEventType.TOOL_RESULT]
        assert any("Paris" in e.payload.get("result", "") for e in result_events)

    async def test_intercepts_without_tool_in_registry(self) -> None:
        """Agent intercepts ask_user even if AskUserTool is not in its tools list."""
        answers = ["blue"]

        async def user_input(question: str) -> str:
            return answers.pop(0)

        llm = MockLLM(
            [
                _make_ask_user_response("What is your favourite colour?"),
                make_text_response("Your favourite colour is blue."),
            ]
        )
        # No tools registered — ask_user should still be intercepted.
        agent, channel = make_agent(llm, tools=[], user_input_fn=user_input)

        result = await agent.run_turn("What colour?")
        assert "blue" in result.response

    async def test_no_user_input_fn_returns_error(self) -> None:
        """When user_input_fn is None, ask_user returns an error tool result."""
        llm = MockLLM(
            [
                _make_ask_user_response("What should I do?"),
                make_text_response("I could not ask the user."),
            ]
        )
        agent, channel = make_agent(llm, tools=[], user_input_fn=None)

        await agent.run_turn("help me")

        # The agent must continue (not crash).
        result_events = [
            e
            for e in channel.events
            if e.type == MeshEventType.TOOL_RESULT and e.payload.get("tool_name") == "ask_user"
        ]
        assert result_events
        assert result_events[0].payload["is_error"] is True

    async def test_multiple_ask_user_calls_in_one_turn(self) -> None:
        """Agent handles multiple ask_user calls within a single turn."""
        queue = ["Alice", "Wonderland"]

        async def user_input(question: str) -> str:
            return queue.pop(0)

        llm = MockLLM(
            [
                _make_ask_user_response("What is your name?"),
                _make_ask_user_response("Where are you from?"),
                make_text_response("Hello Alice from Wonderland!"),
            ]
        )
        agent, channel = make_agent(llm, tools=[], user_input_fn=user_input)

        result = await agent.run_turn("Who are you?")
        assert "Alice" in result.response or "Wonderland" in result.response

    async def test_ask_user_question_passed_to_fn(self) -> None:
        """The exact question string from tool input is passed to user_input_fn."""
        received: list[str] = []

        async def user_input(question: str) -> str:
            received.append(question)
            return "answer"

        llm = MockLLM(
            [
                _make_ask_user_response("What colour is the sky?"),
                make_text_response("ok"),
            ]
        )
        agent, _ = make_agent(llm, tools=[], user_input_fn=user_input)
        await agent.run_turn("x")

        assert received == ["What colour is the sky?"]
