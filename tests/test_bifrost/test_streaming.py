"""Tests for the streaming translation layer."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import suppress

import pytest

from bifrost.translation.streaming import openai_stream_to_anthropic


def _sse(data: dict) -> str:
    """Build an OpenAI-style SSE line from a dict."""

    return f"data: {json.dumps(data)}"


def _chunk(content: str | None = None, finish: str | None = None) -> str:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    choice: dict = {"index": 0, "delta": delta, "finish_reason": finish}
    return _sse({"id": "c1", "choices": [choice]})


def _reasoning_chunk(reasoning: str, field: str = "reasoning_content") -> str:
    delta = {field: reasoning}
    return _sse({"id": "c1", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})


def _tool_chunk(tc_index: int, name: str = "", args: str = "", tc_id: str = "") -> str:
    tc: dict = {"index": tc_index, "function": {}}
    if tc_id:
        tc["id"] = tc_id
        tc["type"] = "function"
    if name:
        tc["function"]["name"] = name
    if args:
        tc["function"]["arguments"] = args
    delta = {"tool_calls": [tc]}
    return _sse({"id": "c1", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})


async def _lines(*lines: str) -> AsyncIterator[str]:
    for line in lines:
        yield line


async def collect(gen) -> list[str]:
    results = []
    async for item in gen:
        results.append(item)
    return results


def _parse_events(chunks: list[str]) -> list[dict]:
    """Parse SSE chunks into a list of event dicts."""
    events = []
    current_event_type = None
    for chunk in chunks:
        for line in chunk.splitlines():
            line = line.strip()
            if line.startswith("event: "):
                current_event_type = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    events.append({"type": "__done__"})
                else:
                    with suppress(json.JSONDecodeError):
                        d = json.loads(data_str)
                        if current_event_type:
                            d["_event"] = current_event_type
                        events.append(d)
                current_event_type = None
    return events


class TestOpenAIStreamToAnthropic:
    async def _run(
        self,
        *lines: str,
        message_id="msg_test",
        model="gpt-4o",
        force_nonempty_content=False,
    ) -> list[dict]:
        gen = openai_stream_to_anthropic(
            _lines(*lines),
            message_id=message_id,
            model=model,
            force_nonempty_content=force_nonempty_content,
        )
        chunks = await collect(gen)
        return _parse_events(chunks)

    @pytest.mark.asyncio
    async def test_simple_text_stream(self):
        events = await self._run(
            _chunk(""),
            _chunk("Hello"),
            _chunk(" world"),
            _chunk(finish="stop"),
            "data: [DONE]",
        )
        types = [e.get("type") for e in events]
        assert "message_start" in types
        assert "content_block_start" in types
        assert "content_block_delta" in types
        assert "content_block_stop" in types
        assert "message_delta" in types
        assert "message_stop" in types

        text_deltas = [e["delta"]["text"] for e in events if e.get("type") == "content_block_delta"]
        assert "Hello" in text_deltas
        assert " world" in text_deltas

    @pytest.mark.asyncio
    async def test_reasoning_stream_is_preserved_as_thinking(self):
        events = await self._run(
            _reasoning_chunk("inspect ", field="reasoning"),
            _reasoning_chunk("the evidence"),
            _chunk("Final answer"),
            _chunk(finish="stop"),
            "data: [DONE]",
        )

        starts = [
            event["content_block"]["type"]
            for event in events
            if event.get("type") == "content_block_start"
        ]
        assert starts == ["thinking", "text"]
        thinking = [
            event["delta"]["thinking"]
            for event in events
            if event.get("delta", {}).get("type") == "thinking_delta"
        ]
        assert thinking == ["inspect ", "the evidence"]

    @pytest.mark.asyncio
    async def test_reasoning_becomes_text_when_final_content_is_whitespace(self):
        events = await self._run(
            _reasoning_chunk("Final answer from reasoning."),
            _chunk("\n"),
            _chunk(finish="stop"),
            "data: [DONE]",
            force_nonempty_content=True,
        )

        text = "".join(
            event["delta"]["text"]
            for event in events
            if event.get("delta", {}).get("type") == "text_delta"
        )
        assert text == "\nFinal answer from reasoning."

    @pytest.mark.asyncio
    async def test_reasoning_is_not_duplicated_when_text_content_exists(self):
        events = await self._run(
            _reasoning_chunk("Internal reasoning."),
            _chunk("Final answer."),
            _chunk(finish="stop"),
            "data: [DONE]",
            force_nonempty_content=True,
        )

        text = "".join(
            event["delta"]["text"]
            for event in events
            if event.get("delta", {}).get("type") == "text_delta"
        )
        assert text == "Final answer."

    @pytest.mark.asyncio
    async def test_stop_reason_mapped(self):
        events = await self._run(
            _chunk("x"),
            _chunk(finish="length"),
            "data: [DONE]",
        )
        msg_delta = next(e for e in events if e.get("type") == "message_delta")
        assert msg_delta["delta"]["stop_reason"] == "max_tokens"

    @pytest.mark.asyncio
    async def test_usage_only_terminal_chunk_is_preserved(self):
        events = await self._run(
            _chunk("ok", finish="stop"),
            _sse(
                {
                    "id": "c1",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 17,
                        "completion_tokens": 3,
                        "total_tokens": 20,
                    },
                }
            ),
            "data: [DONE]",
        )

        msg_delta = next(e for e in events if e.get("type") == "message_delta")
        assert msg_delta["usage"] == {"input_tokens": 17, "output_tokens": 3}

    @pytest.mark.asyncio
    async def test_message_start_has_message_id(self):
        events = await self._run(
            _chunk("x", finish="stop"),
            "data: [DONE]",
            message_id="msg_custom123",
        )
        start = next(e for e in events if e.get("type") == "message_start")
        assert start["message"]["id"] == "msg_custom123"

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        events = await self._run("data: [DONE]")
        types = [e.get("type") for e in events]
        assert "message_start" in types
        assert "message_delta" in types
        assert "message_stop" in types

    @pytest.mark.asyncio
    async def test_tool_call_stream(self):
        events = await self._run(
            _tool_chunk(0, name="get_weather", tc_id="call_1"),
            _tool_chunk(0, args='{"city": "Oslo"}'),
            _chunk(finish="tool_calls"),
            "data: [DONE]",
        )
        types = [e.get("type") for e in events]
        assert "content_block_start" in types
        tool_starts = [
            e
            for e in events
            if e.get("type") == "content_block_start"
            and e.get("content_block", {}).get("type") == "tool_use"
        ]
        assert len(tool_starts) == 1
        assert tool_starts[0]["content_block"]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_non_sse_lines_ignored(self):
        events = await self._run(
            "",
            "some random line",
            _chunk("hi", finish="stop"),
            "data: [DONE]",
        )
        msg_start = next((e for e in events if e.get("type") == "message_start"), None)
        assert msg_start is not None

    @pytest.mark.asyncio
    async def test_invalid_json_lines_ignored(self):
        events = await self._run(
            "data: not-valid-json",
            _chunk("ok", finish="stop"),
            "data: [DONE]",
        )
        assert any(e.get("type") == "content_block_delta" for e in events)

    @pytest.mark.asyncio
    async def test_done_sentinel_terminates_stream(self):
        events = await self._run(
            _chunk("a"),
            "data: [DONE]",
            # This line must NOT appear: [DONE] terminates iteration.
            _chunk("SHOULD_NOT_APPEAR", finish="stop"),
        )
        text_deltas = [e["delta"]["text"] for e in events if e.get("type") == "content_block_delta"]
        assert "SHOULD_NOT_APPEAR" not in text_deltas
