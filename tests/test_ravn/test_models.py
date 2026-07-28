"""Tests for Ravn domain models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest

from ravn.domain.models import (
    LLMResponse,
    Message,
    Session,
    StopReason,
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
    TurnResult,
)


class TestTokenUsage:
    def test_total_tokens(self) -> None:
        u = TokenUsage(input_tokens=10, output_tokens=5)
        assert u.total_tokens == 15

    def test_add(self) -> None:
        a = TokenUsage(input_tokens=10, output_tokens=5, cache_read_tokens=2)
        b = TokenUsage(input_tokens=20, output_tokens=10, cache_write_tokens=3)
        result = a + b
        assert result.input_tokens == 30
        assert result.output_tokens == 15
        assert result.cache_read_tokens == 2
        assert result.cache_write_tokens == 3

    def test_defaults(self) -> None:
        u = TokenUsage(input_tokens=1, output_tokens=1)
        assert u.cache_read_tokens == 0
        assert u.cache_write_tokens == 0

    def test_frozen(self) -> None:
        u = TokenUsage(input_tokens=1, output_tokens=1)
        with pytest.raises(Exception):
            u.input_tokens = 99  # type: ignore[misc]


class TestToolCall:
    def test_fields(self) -> None:
        tc = ToolCall(id="id1", name="echo", input={"message": "hi"})
        assert tc.id == "id1"
        assert tc.name == "echo"
        assert tc.input == {"message": "hi"}

    def test_frozen(self) -> None:
        tc = ToolCall(id="id1", name="echo", input={})
        with pytest.raises(Exception):
            tc.name = "other"  # type: ignore[misc]


class TestToolResult:
    def test_defaults(self) -> None:
        r = ToolResult(tool_call_id="x", content="ok")
        assert r.is_error is False

    def test_error(self) -> None:
        r = ToolResult(tool_call_id="x", content="boom", is_error=True)
        assert r.is_error is True


class TestMessage:
    def test_user_message(self) -> None:
        m = Message(role="user", content="Hello")
        assert m.role == "user"
        assert m.content == "Hello"

    def test_content_as_list(self) -> None:
        blocks = [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "x"}]
        m = Message(role="assistant", content=blocks)
        assert m.content == blocks

    def test_api_dict_includes_reasoning_when_present(self) -> None:
        m = Message(
            role="assistant",
            content="Answer",
            reasoning="Evidence first",
        )
        assert m.to_api_dict() == {
            "role": "assistant",
            "content": "Answer",
            "reasoning": "Evidence first",
        }

    def test_api_dict_omits_empty_reasoning(self) -> None:
        assert Message(role="user", content="Hello").to_api_dict() == {
            "role": "user",
            "content": "Hello",
        }

    def test_frozen(self) -> None:
        m = Message(role="user", content="Hello")
        with pytest.raises(Exception):
            m.content = "other"  # type: ignore[misc]


class TestStopReason:
    def test_values(self) -> None:
        assert StopReason.END_TURN == "end_turn"
        assert StopReason.TOOL_USE == "tool_use"
        assert StopReason.MAX_TOKENS == "max_tokens"
        assert StopReason.STOP_SEQUENCE == "stop_sequence"


class TestStreamEvent:
    def test_text_delta(self) -> None:
        ev = StreamEvent(type=StreamEventType.TEXT_DELTA, text="hello")
        assert ev.type == StreamEventType.TEXT_DELTA
        assert ev.text == "hello"
        assert ev.tool_call is None
        assert ev.usage is None

    def test_tool_call_event(self) -> None:
        tc = ToolCall(id="1", name="echo", input={})
        ev = StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=tc)
        assert ev.tool_call is tc

    def test_message_done(self) -> None:
        u = TokenUsage(input_tokens=5, output_tokens=3)
        ev = StreamEvent(type=StreamEventType.MESSAGE_DONE, usage=u)
        assert ev.usage is u


class TestLLMResponse:
    def test_fields(self) -> None:
        r = LLMResponse(
            content="Hello",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=5, output_tokens=3),
        )
        assert r.content == "Hello"
        assert r.tool_calls == []
        assert r.stop_reason == StopReason.END_TURN


class TestTurnResult:
    def test_fields(self) -> None:
        tr = TurnResult(
            response="OK",
            tool_calls=[],
            tool_results=[],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
        assert tr.response == "OK"


class TestSession:
    def test_defaults(self) -> None:
        s = Session()
        assert isinstance(s.id, UUID)
        assert s.messages == []
        assert s.turn_count == 0
        assert s.total_usage.total_tokens == 0
        assert isinstance(s.created_at, datetime)
        assert s.created_at.tzinfo is not None

    def test_add_message(self) -> None:
        s = Session()
        m = Message(role="user", content="hi")
        s.add_message(m)
        assert len(s.messages) == 1
        assert s.messages[0] is m

    def test_record_turn(self) -> None:
        s = Session()
        s.record_turn(TokenUsage(input_tokens=10, output_tokens=5))
        assert s.turn_count == 1
        assert s.total_usage.input_tokens == 10
        s.record_turn(TokenUsage(input_tokens=20, output_tokens=8))
        assert s.turn_count == 2
        assert s.total_usage.input_tokens == 30
