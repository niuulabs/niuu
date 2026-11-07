"""Tests for the CLI format event translator."""

from __future__ import annotations

import json

from niuu.mesh.events import MeshEvent
from ravn.adapters.events.cli_translator import CliFormatTranslator

_SRC = "ravn-test"
_CID = "corr-1"
_SID = "sess-1"


def make_translator() -> CliFormatTranslator:
    return CliFormatTranslator()


class TestTurnStart:
    def test_first_event_emits_assistant_start(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.thought(_SRC, "hi", _CID, _SID))
        assert events[0]["type"] == "assistant"
        assert events[0]["message"]["role"] == "assistant"

    def test_second_event_does_not_duplicate_assistant_start(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thought(_SRC, "a", _CID, _SID))
        events = t.translate(MeshEvent.thought(_SRC, "b", _CID, _SID))
        assert not any(e["type"] == "assistant" for e in events)

    def test_reset_allows_new_assistant_start(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thought(_SRC, "a", _CID, _SID))
        t.reset()
        events = t.translate(MeshEvent.thought(_SRC, "b", _CID, _SID))
        assert events[0]["type"] == "assistant"


class TestThought:
    def test_text_thought_emits_block_start_and_delta(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.thought(_SRC, "hello", _CID, _SID))
        # assistant + content_block_start + content_block_delta
        types = [e["type"] for e in events]
        assert "content_block_start" in types
        assert "content_block_delta" in types

        block_start = next(e for e in events if e["type"] == "content_block_start")
        assert block_start["content_block"]["type"] == "text"

        delta = next(e for e in events if e["type"] == "content_block_delta")
        assert delta["delta"]["type"] == "text_delta"
        assert delta["delta"]["text"] == "hello"

    def test_consecutive_text_thoughts_grouped(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thought(_SRC, "a", _CID, _SID))
        events = t.translate(MeshEvent.thought(_SRC, "b", _CID, _SID))
        # Should only have delta, no new block_start
        types = [e["type"] for e in events]
        assert "content_block_start" not in types
        assert "content_block_delta" in types

    def test_thinking_thought_emits_thinking_delta(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.thinking(_SRC, "reasoning", _CID, _SID))
        delta = next(e for e in events if e["type"] == "content_block_delta")
        assert delta["delta"]["type"] == "thinking_delta"
        assert delta["delta"]["thinking"] == "reasoning"

    def test_text_after_thinking_closes_thinking_block(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thinking(_SRC, "think", _CID, _SID))
        events = t.translate(MeshEvent.thought(_SRC, "text", _CID, _SID))
        types = [e["type"] for e in events]
        # Should have: content_block_stop (thinking) + content_block_start (text) + delta
        assert "content_block_stop" in types
        assert "content_block_start" in types

    def test_thinking_after_text_closes_text_block(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thought(_SRC, "text", _CID, _SID))
        events = t.translate(MeshEvent.thinking(_SRC, "think", _CID, _SID))
        types = [e["type"] for e in events]
        assert "content_block_stop" in types
        assert "content_block_start" in types


class TestToolStart:
    def test_emits_start_delta_stop(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.tool_start(_SRC, "Bash", {"command": "ls"}, _CID, _SID))
        # Remove assistant start
        events = [e for e in events if e["type"] != "assistant"]
        types = [e["type"] for e in events]
        assert types == ["content_block_start", "content_block_delta", "content_block_stop"]

    def test_tool_use_block_has_name_and_id(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.tool_start(_SRC, "Bash", {"command": "ls"}, _CID, _SID))
        start = next(e for e in events if e["type"] == "content_block_start")
        assert start["content_block"]["type"] == "tool_use"
        assert start["content_block"]["name"] == "Bash"
        assert start["content_block"]["id"] == "tool_001"

    def test_input_json_delta_contains_full_input(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.tool_start(_SRC, "Bash", {"command": "ls"}, _CID, _SID))
        delta = next(e for e in events if e["type"] == "content_block_delta")
        assert delta["delta"]["type"] == "input_json_delta"
        parsed = json.loads(delta["delta"]["partial_json"])
        assert parsed == {"command": "ls"}

    def test_sequential_tools_get_incrementing_ids(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.tool_start(_SRC, "A", {}, _CID, _SID))
        events = t.translate(MeshEvent.tool_start(_SRC, "B", {}, _CID, _SID))
        start = next(e for e in events if e["type"] == "content_block_start")
        assert start["content_block"]["id"] == "tool_002"

    def test_tool_after_text_closes_text_block(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thought(_SRC, "thinking", _CID, _SID))
        events = t.translate(MeshEvent.tool_start(_SRC, "Bash", {}, _CID, _SID))
        # Should contain a content_block_stop for the text block
        stops = [e for e in events if e["type"] == "content_block_stop"]
        assert len(stops) >= 1  # text block stop + tool block stop


class TestToolResult:
    def test_tool_result_is_skipped(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thought(_SRC, "a", _CID, _SID))
        events = t.translate(MeshEvent.tool_result(_SRC, "Bash", "output", _CID, _SID))
        assert events == []


class TestResponse:
    def test_emits_result_event(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thought(_SRC, "thinking", _CID, _SID))
        events = t.translate(MeshEvent.response(_SRC, "Done!", _CID, _SID))
        result = next(e for e in events if e["type"] == "result")
        assert result["subtype"] == "success"
        assert result["is_error"] is False
        assert result["result"] == "Done!"

    def test_response_closes_text_block(self) -> None:
        t = make_translator()
        t.translate(MeshEvent.thought(_SRC, "text", _CID, _SID))
        events = t.translate(MeshEvent.response(_SRC, "done", _CID, _SID))
        types = [e["type"] for e in events]
        assert "content_block_stop" in types


class TestError:
    def test_emits_error_event(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.error(_SRC, "boom", _CID, _SID))
        error = next(e for e in events if e["type"] == "error")
        assert error["error"]["message"] == "boom"


class TestTaskComplete:
    def test_success_emits_result(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.task_complete(_SRC, True, _CID, _SID))
        result = next(e for e in events if e["type"] == "result")
        assert result["is_error"] is False

    def test_failure_emits_error_result(self) -> None:
        t = make_translator()
        events = t.translate(MeshEvent.task_complete(_SRC, False, _CID, _SID))
        result = next(e for e in events if e["type"] == "result")
        assert result["is_error"] is True
        assert result["subtype"] == "error"


class TestBlockIndexing:
    def test_block_index_increments_correctly(self) -> None:
        t = make_translator()
        # Text block (index 0)
        t.translate(MeshEvent.thought(_SRC, "text", _CID, _SID))
        # Tool block (closes text at 0, tool at 1)
        events = t.translate(MeshEvent.tool_start(_SRC, "Bash", {}, _CID, _SID))
        tool_start = next(e for e in events if e["type"] == "content_block_start")
        assert tool_start["index"] == 1
        # Another text block (index 2)
        events = t.translate(MeshEvent.thought(_SRC, "more", _CID, _SID))
        block_start = next(e for e in events if e["type"] == "content_block_start")
        assert block_start["index"] == 2


class TestFullTurnSequence:
    def test_think_tool_think_response(self) -> None:
        """Full turn: thinking → tool → text → response."""
        t = make_translator()
        all_events: list[dict] = []

        all_events.extend(t.translate(MeshEvent.thinking(_SRC, "let me think", _CID, _SID)))
        all_events.extend(
            t.translate(MeshEvent.tool_start(_SRC, "Bash", {"command": "ls"}, _CID, _SID))
        )
        all_events.extend(t.translate(MeshEvent.tool_result(_SRC, "Bash", "file.py", _CID, _SID)))
        all_events.extend(t.translate(MeshEvent.thought(_SRC, "I see ", _CID, _SID)))
        all_events.extend(t.translate(MeshEvent.thought(_SRC, "the file", _CID, _SID)))
        all_events.extend(t.translate(MeshEvent.response(_SRC, "I see the file", _CID, _SID)))

        types = [e["type"] for e in all_events]

        # Should start with assistant
        assert types[0] == "assistant"
        # Should end with result
        assert types[-1] == "result"
        # Should contain tool_use blocks
        assert "content_block_start" in types
        # tool_result should be skipped (no events from it)
        assert all_events[-1]["type"] == "result"
