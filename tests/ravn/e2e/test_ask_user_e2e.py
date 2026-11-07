"""E2E test: agent encounters ambiguity, asks user, continues with answer.

Covers the full ask_user interaction:
  1. Agent receives an ambiguous user request.
  2. LLM decides to call ask_user for clarification.
  3. Agent loop intercepts the call and prompts the user.
  4. User provides an answer.
  5. Agent loop injects the answer as a tool result.
  6. LLM produces a final response using the clarification.
"""

from __future__ import annotations

import io

import pytest

from niuu.mesh.events import MeshEventType
from ravn.adapters.cli_channel import CliChannel
from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.adapters.tools.ask_user import AskUserTool
from ravn.agent import RavnAgent
from ravn.domain.models import LLMResponse, StopReason, TokenUsage, ToolCall
from tests.ravn.conftest import MockLLM, make_agent, make_text_response


def _ask_user_response(question: str) -> LLMResponse:
    """Build an LLMResponse that calls ask_user."""
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id="tc-e2e-ask", name="ask_user", input={"question": question})],
        stop_reason=StopReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )


@pytest.mark.asyncio
class TestAskUserE2E:
    async def test_agent_asks_and_continues(self) -> None:
        """Full turn: ambiguity → ask_user → user answers → agent finishes."""
        answers = ["format them as a numbered list"]

        async def user_input(question: str) -> str:
            return answers.pop(0)

        llm = MockLLM(
            [
                _ask_user_response("How would you like the results formatted?"),
                make_text_response("Here are the results as a numbered list."),
            ]
        )
        agent, channel = make_agent(
            llm,
            tools=[AskUserTool()],
            user_input_fn=user_input,
        )

        result = await agent.run_turn("Show me the search results.")

        # Agent produced a meaningful final response.
        assert "list" in result.response.lower()

        # ask_user tool start was emitted.
        tool_starts = [e for e in channel.events if e.type == MeshEventType.TOOL_START]
        assert any(e.payload.get("tool_name") == "ask_user" for e in tool_starts)

        # ask_user result (the user's answer) was emitted.
        tool_results = [e for e in channel.events if e.type == MeshEventType.TOOL_RESULT]
        assert any("list" in e.payload.get("result", "") for e in tool_results)

    async def test_clarification_injected_into_session(self) -> None:
        """The user's answer must appear in the session history as a tool result."""

        async def user_input(question: str) -> str:
            return "Python 3.12"

        llm = MockLLM(
            [
                _ask_user_response("Which Python version are you targeting?"),
                make_text_response("Python 3.12 is a great choice."),
            ]
        )
        agent, _ = make_agent(llm, tools=[AskUserTool()], user_input_fn=user_input)
        await agent.run_turn("Help me write a script.")

        # Find the tool result message in the session (role="user", list content).
        session_messages = agent.session.messages
        user_tool_result_messages = [
            m for m in session_messages if m.role == "user" and isinstance(m.content, list)
        ]
        assert user_tool_result_messages, "Expected a tool-result user message in session"
        content_list = user_tool_result_messages[0].content
        tool_result_contents = [
            item.get("content", "")
            for item in content_list
            if isinstance(item, dict) and item.get("type") == "tool_result"
        ]
        assert any("Python 3.12" in c for c in tool_result_contents)

    async def test_cli_channel_renders_ask_user(self) -> None:
        """CliChannel renders the ask_user tool_start event correctly."""
        buf = io.StringIO()
        cli = CliChannel(file=buf)

        async def user_input(question: str) -> str:
            return "use JSON"

        llm = MockLLM(
            [
                _ask_user_response("What output format do you prefer?"),
                make_text_response("I will use JSON format."),
            ]
        )
        agent = RavnAgent(
            llm=llm,
            tools=[AskUserTool()],
            channel=cli,
            permission=AllowAllPermission(),
            system_prompt="You are a helpful assistant.",
            model="claude-sonnet-4-6",
            max_tokens=512,
            max_iterations=5,
            user_input_fn=user_input,
        )

        result = await agent.run_turn("Process this data.")

        output = buf.getvalue()
        # The CLI should have printed the ask_user tool start.
        assert "ask_user" in output
        assert "JSON" in result.response

    async def test_agent_continues_after_multiple_clarifications(self) -> None:
        """Agent can handle several sequential ask_user calls before completing."""
        queue = ["Alice", "30", "Engineer"]

        async def user_input(question: str) -> str:
            return queue.pop(0)

        llm = MockLLM(
            [
                _ask_user_response("What is your name?"),
                _ask_user_response("What is your age?"),
                _ask_user_response("What is your profession?"),
                make_text_response("Profile saved for Alice, 30, Engineer."),
            ]
        )
        agent, channel = make_agent(
            llm,
            tools=[AskUserTool()],
            user_input_fn=user_input,
            max_iterations=10,
        )

        result = await agent.run_turn("Create my profile.")

        assert "Alice" in result.response
        # All three answers should appear in tool result events.
        tool_results = [e for e in channel.events if e.type == MeshEventType.TOOL_RESULT]
        result_texts = " ".join(e.payload.get("result", "") for e in tool_results)
        assert "Alice" in result_texts
        assert "30" in result_texts
        assert "Engineer" in result_texts
