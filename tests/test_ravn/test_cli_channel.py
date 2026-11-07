"""Tests for the CliChannel adapter."""

from __future__ import annotations

import io

from niuu.mesh.events import MeshEvent
from ravn.adapters.cli_channel import CliChannel, _format_input

_SRC = "ravn-test"
_CID = "corr-1"
_SID = "sess-1"


def make_channel() -> tuple[CliChannel, io.StringIO]:
    buf = io.StringIO()
    return CliChannel(file=buf), buf


class TestCliChannel:
    async def test_thought_emitted_inline(self) -> None:
        ch, buf = make_channel()
        await ch.emit(MeshEvent.thought(_SRC, "hello ", _CID, _SID))
        await ch.emit(MeshEvent.thought(_SRC, "world", _CID, _SID))
        output = buf.getvalue()
        assert "hello world" in output

    async def test_response_adds_newline(self) -> None:
        ch, buf = make_channel()
        await ch.emit(MeshEvent.thought(_SRC, "text", _CID, _SID))
        await ch.emit(MeshEvent.response(_SRC, "text", _CID, _SID))
        output = buf.getvalue()
        assert output.endswith("\n")

    async def test_tool_start_printed(self) -> None:
        ch, buf = make_channel()
        await ch.emit(MeshEvent.tool_start(_SRC, "echo", {"message": "hi"}, _CID, _SID))
        output = buf.getvalue()
        assert "echo" in output

    async def test_tool_result_success(self) -> None:
        ch, buf = make_channel()
        await ch.emit(MeshEvent.tool_result(_SRC, "echo", "pong", _CID, _SID))
        output = buf.getvalue()
        assert "\u2713" in output
        assert "pong" in output

    async def test_tool_result_error(self) -> None:
        ch, buf = make_channel()
        await ch.emit(MeshEvent.tool_result(_SRC, "fail", "boom", _CID, _SID, is_error=True))
        output = buf.getvalue()
        assert "\u2717" in output

    async def test_error_event(self) -> None:
        ch, buf = make_channel()
        await ch.emit(MeshEvent.error(_SRC, "bad thing", _CID, _SID))
        output = buf.getvalue()
        assert "bad thing" in output
        assert "error" in output

    async def test_long_tool_result_truncated(self) -> None:
        ch, buf = make_channel()
        long_content = "x" * 600
        await ch.emit(MeshEvent.tool_result(_SRC, "tool", long_content, _CID, _SID))
        output = buf.getvalue()
        assert "\u2026" in output
        assert len(output) < 600

    def test_finish_adds_newline_if_in_response(self) -> None:
        ch, buf = make_channel()
        # Simulate a streaming response that hasn't ended yet.
        ch._in_response = True
        ch.finish()
        assert buf.getvalue() == "\n"

    def test_finish_noop_if_not_in_response(self) -> None:
        ch, buf = make_channel()
        ch.finish()
        assert buf.getvalue() == ""

    async def test_tool_start_after_thought_adds_newline(self) -> None:
        ch, buf = make_channel()
        await ch.emit(MeshEvent.thought(_SRC, "thinking", _CID, _SID))
        await ch.emit(MeshEvent.tool_start(_SRC, "echo", {}, _CID, _SID))
        output = buf.getvalue()
        assert "thinking" in output
        assert "echo" in output

    async def test_custom_result_truncation_limit(self) -> None:
        buf = io.StringIO()
        ch = CliChannel(file=buf, result_truncation_limit=10)
        await ch.emit(MeshEvent.tool_result(_SRC, "tool", "x" * 20, _CID, _SID))
        output = buf.getvalue()
        assert "\u2026" in output
        assert len(output) < 30

    async def test_custom_input_value_limit(self) -> None:
        buf = io.StringIO()
        ch = CliChannel(file=buf, input_value_limit=5)
        await ch.emit(MeshEvent.tool_start(_SRC, "t", {"k": "v" * 10}, _CID, _SID))
        output = buf.getvalue()
        assert "\u2026" in output


class TestFormatInput:
    def test_empty(self) -> None:
        assert _format_input({}) == ""

    def test_simple(self) -> None:
        result = _format_input({"key": "value"})
        assert "key=" in result
        assert "value" in result

    def test_long_value_truncated(self) -> None:
        result = _format_input({"k": "v" * 100})
        assert "\u2026" in result

    def test_custom_limit(self) -> None:
        result = _format_input({"k": "v" * 10}, value_limit=5)
        assert "\u2026" in result

    def test_multiple_keys(self) -> None:
        result = _format_input({"a": "1", "b": "2"})
        assert "a=" in result
        assert "b=" in result
        assert ", " in result
