"""End-to-end tests: full agent conversation flows."""

from __future__ import annotations

import io

from niuu.mesh.events import MeshEventType
from ravn.adapters.cli_channel import CliChannel
from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.agent import RavnAgent
from ravn.domain.models import (
    LLMResponse,
    StopReason,
    TokenUsage,
    ToolCall,
)
from tests.ravn.conftest import (
    EchoTool,
    MockLLM,
    make_agent,
    make_text_response,
)


class TestBasicConversation:
    async def test_user_input_produces_llm_response(self) -> None:
        llm = MockLLM([make_text_response("Hello there!")])
        agent, ch = make_agent(llm)

        result = await agent.run_turn("Hello")

        assert result.response == "Hello there!"

    async def test_response_emitted_to_channel(self) -> None:
        llm = MockLLM([make_text_response("Hi!")])
        agent, ch = make_agent(llm)

        await agent.run_turn("Hello")

        event_types = [e.type for e in ch.events]
        assert MeshEventType.THOUGHT in event_types
        assert MeshEventType.RESPONSE in event_types

    async def test_response_rendered_to_cli_channel(self) -> None:
        """Full rendering pipeline: agent → CliChannel → stdout."""
        buf = io.StringIO()
        cli = CliChannel(file=buf)
        llm = MockLLM([make_text_response("The answer is 42.")])
        agent = RavnAgent(
            llm=llm,
            tools=[],
            channel=cli,
            permission=AllowAllPermission(),
            system_prompt="You are Ravn.",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            max_iterations=5,
        )

        result = await agent.run_turn("What is the answer?")

        output = buf.getvalue()
        assert "The answer is 42." in output
        assert result.response == "The answer is 42."

    async def test_session_starts_empty(self) -> None:
        llm = MockLLM([make_text_response("Hi!")])
        agent, _ = make_agent(llm)

        assert agent.session.turn_count == 0
        assert agent.session.messages == []

    async def test_tool_result_rendered_to_cli_channel(self) -> None:
        """CliChannel renders tool start and result events correctly."""
        buf = io.StringIO()
        cli = CliChannel(file=buf)

        tool_call = ToolCall(id="tc1", name="echo", input={"message": "ping"})
        tool_response = LLMResponse(
            content="pong",
            tool_calls=[tool_call],
            stop_reason=StopReason.TOOL_USE,
            usage=TokenUsage(input_tokens=5, output_tokens=2),
        )
        final_response = make_text_response("Done!", input_tokens=5, output_tokens=2)
        llm = MockLLM([tool_response, final_response])

        agent = RavnAgent(
            llm=llm,
            tools=[EchoTool()],
            channel=cli,
            permission=AllowAllPermission(),
            system_prompt="",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            max_iterations=5,
        )

        await agent.run_turn("echo ping")
        output = buf.getvalue()
        assert "echo" in output


class TestMultiTurnConversation:
    async def test_session_maintains_history(self) -> None:
        llm = MockLLM(
            [
                make_text_response("I am Ravn."),
                make_text_response("You said hello, then asked who I am."),
            ]
        )
        agent, ch = make_agent(llm)

        await agent.run_turn("Hello")
        await agent.run_turn("Who are you?")

        assert agent.session.turn_count == 2
        # 4 messages: user + assistant × 2
        assert len(agent.session.messages) == 4

    async def test_second_turn_has_full_history(self) -> None:
        llm = MockLLM(
            [
                make_text_response("answer1"),
                make_text_response("answer2"),
            ]
        )
        agent, ch = make_agent(llm)

        await agent.run_turn("question1")
        await agent.run_turn("question2")

        # All messages in order: u1, a1, u2, a2
        roles = [m.role for m in agent.session.messages]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert agent.session.messages[0].content == "question1"
        assert agent.session.messages[2].content == "question2"

    async def test_token_usage_accumulates_across_turns(self) -> None:
        llm = MockLLM(
            [
                make_text_response("ans1", input_tokens=10, output_tokens=5),
                make_text_response("ans2", input_tokens=20, output_tokens=8),
            ]
        )
        agent, _ = make_agent(llm)

        await agent.run_turn("q1")
        await agent.run_turn("q2")

        assert agent.session.total_usage.input_tokens == 30
        assert agent.session.total_usage.output_tokens == 13

    async def test_channel_receives_events_for_each_turn(self) -> None:
        llm = MockLLM(
            [
                make_text_response("turn1"),
                make_text_response("turn2"),
            ]
        )
        agent, ch = make_agent(llm)

        await agent.run_turn("first message")
        events_after_turn1 = len(ch.events)
        await agent.run_turn("second message")
        events_after_turn2 = len(ch.events)

        assert events_after_turn2 > events_after_turn1

    async def test_tool_use_and_then_follow_up(self) -> None:
        """Multi-turn with tool use in first turn, plain text in second."""
        tool = EchoTool()
        tool_call = ToolCall(id="tc1", name="echo", input={"message": "hi"})

        tool_response = LLMResponse(
            content="",
            tool_calls=[tool_call],
            stop_reason=StopReason.TOOL_USE,
            usage=TokenUsage(input_tokens=5, output_tokens=2),
        )
        after_tool = make_text_response("echo result was: hi", input_tokens=8, output_tokens=4)
        second_turn = make_text_response("Happy to help further!", input_tokens=12, output_tokens=5)

        llm = MockLLM([tool_response, after_tool, second_turn])
        agent, ch = make_agent(llm, tools=[tool])

        result1 = await agent.run_turn("echo hi")
        result2 = await agent.run_turn("thanks")

        assert result1.response == "echo result was: hi"
        assert result2.response == "Happy to help further!"
        assert agent.session.turn_count == 2
