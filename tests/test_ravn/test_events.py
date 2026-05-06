"""Tests for Ravn domain events."""

from __future__ import annotations

import pytest

from ravn.domain.events import RavnEvent, RavnEventType

_SRC = "ravn-test"
_CID = "corr-1"
_SID = "sess-1"


class TestRavnEventType:
    def test_values(self) -> None:
        assert RavnEventType.THOUGHT == "thought"
        assert RavnEventType.TOOL_START == "tool_start"
        assert RavnEventType.TOOL_RESULT == "tool_result"
        assert RavnEventType.RESPONSE == "response"
        assert RavnEventType.ERROR == "error"
        assert RavnEventType.DECISION == "decision"
        assert RavnEventType.TASK_COMPLETE == "task_complete"

    def test_member_count(self) -> None:
        # 11 types: THOUGHT, TOOL_START, TOOL_RESULT, RESPONSE, ERROR,
        # DECISION, TASK_COMPLETE, TASK_STARTED, TASK_STUCK, OUTCOME, HELP_NEEDED
        assert len(RavnEventType) == 11


class TestRavnEvent:
    def test_thought_factory(self) -> None:
        ev = RavnEvent.thought(_SRC, "thinking...", _CID, _SID)
        assert ev.type == RavnEventType.THOUGHT
        assert ev.payload["text"] == "thinking..."
        assert ev.source == _SRC
        assert ev.session_id == _SID

    def test_thinking_factory(self) -> None:
        ev = RavnEvent.thinking(_SRC, "deep thought", _CID, _SID)
        assert ev.type == RavnEventType.THOUGHT
        assert ev.payload["text"] == "deep thought"
        assert ev.payload["thinking"] is True

    def test_tool_start_factory(self) -> None:
        ev = RavnEvent.tool_start(_SRC, "echo", {"message": "hi"}, _CID, _SID)
        assert ev.type == RavnEventType.TOOL_START
        assert ev.payload["tool_name"] == "echo"
        assert ev.payload["input"] == {"message": "hi"}

    def test_tool_start_with_diff(self) -> None:
        ev = RavnEvent.tool_start(_SRC, "edit", {"file": "a.py"}, _CID, _SID, diff="+ new")
        assert ev.payload["diff"] == "+ new"

    def test_tool_result_factory(self) -> None:
        ev = RavnEvent.tool_result(_SRC, "echo", "pong", _CID, _SID)
        assert ev.type == RavnEventType.TOOL_RESULT
        assert ev.payload["result"] == "pong"
        assert ev.payload["is_error"] is False
        assert ev.payload["tool_name"] == "echo"

    def test_tool_result_error(self) -> None:
        ev = RavnEvent.tool_result(_SRC, "fail", "boom", _CID, _SID, is_error=True)
        assert ev.payload["is_error"] is True

    def test_response_factory(self) -> None:
        ev = RavnEvent.response(_SRC, "Hello there!", _CID, _SID)
        assert ev.type == RavnEventType.RESPONSE
        assert ev.payload["text"] == "Hello there!"

    def test_error_factory(self) -> None:
        ev = RavnEvent.error(_SRC, "something went wrong", _CID, _SID)
        assert ev.type == RavnEventType.ERROR
        assert ev.payload["message"] == "something went wrong"

    def test_decision_required_factory(self) -> None:
        ev = RavnEvent.decision_required(_SRC, "approve?", _CID, _SID)
        assert ev.type == RavnEventType.DECISION
        assert ev.payload["prompt"] == "approve?"
        assert ev.urgency == 0.9

    def test_help_needed_factory(self) -> None:
        ev = RavnEvent.help_needed(
            source=_SRC,
            persona="reviewer",
            reason="blocked",
            summary="Cannot determine if change is safe",
            attempted=["checked callers", "searched tests"],
            recommendation="Confirm this function is unused",
            correlation_id=_CID,
            session_id=_SID,
            task_id="task-123",
            context={"file": "auth.py", "line": 47},
        )
        assert ev.type == RavnEventType.HELP_NEEDED
        assert ev.payload["persona"] == "reviewer"
        assert ev.payload["reason"] == "blocked"
        assert ev.payload["summary"] == "Cannot determine if change is safe"
        assert ev.payload["attempted"] == ["checked callers", "searched tests"]
        assert ev.payload["recommendation"] == "Confirm this function is unused"
        assert ev.payload["context"] == {"file": "auth.py", "line": 47}
        assert ev.urgency == 0.85
        assert ev.task_id == "task-123"

    def test_help_needed_caps_attempted_at_three(self) -> None:
        ev = RavnEvent.help_needed(
            source=_SRC,
            persona="coder",
            reason="needs_context",
            summary="Need more info",
            attempted=["a", "b", "c", "d", "e"],
            recommendation="Provide context",
            correlation_id=_CID,
            session_id=_SID,
        )
        assert len(ev.payload["attempted"]) == 3

    def test_task_complete_factory(self) -> None:
        ev = RavnEvent.task_complete(_SRC, True, _CID, _SID)
        assert ev.type == RavnEventType.TASK_COMPLETE
        assert ev.payload["success"] is True

    def test_frozen(self) -> None:
        ev = RavnEvent.thought(_SRC, "test", _CID, _SID)
        with pytest.raises(Exception):
            ev.source = "other"  # type: ignore[misc]

    def test_timestamp_present(self) -> None:
        ev = RavnEvent.response(_SRC, "hi", _CID, _SID)
        assert ev.timestamp is not None

    def test_task_id_optional(self) -> None:
        ev = RavnEvent.thought(_SRC, "t", _CID, _SID, task_id="sub-1")
        assert ev.task_id == "sub-1"
