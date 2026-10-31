"""Responses SSE -> Anthropic SSE translation, against streams recorded from the backend."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from bifrost.ports.provider import ProviderError
from bifrost.translation.models import TextBlock, ThinkingBlock, ToolUseBlock
from bifrost.translation.responses_stream import (
    iter_responses_events,
    new_message_id,
    responses_stream_to_anthropic,
    responses_stream_to_message,
    responses_to_anthropic_events,
)

FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt_responses"
MODEL = "gpt-6-sol"
MESSAGE_ID = "msg_test"


async def _lines(text: str) -> AsyncIterator[str]:
    for line in text.splitlines():
        yield line


def _fixture(name: str) -> AsyncIterator[str]:
    return _lines((FIXTURES / f"{name}.sse").read_text())


def _sse(*events: dict[str, Any]) -> AsyncIterator[str]:
    return _lines("\n\n".join(f"event: {e['type']}\ndata: {json.dumps(e)}" for e in events))


def _created() -> dict[str, Any]:
    return {"type": "response.created", "response": {"status": "in_progress"}}


def _completed(input_tokens: int = 3, output_tokens: int = 4) -> dict[str, Any]:
    return {
        "type": "response.completed",
        "response": {"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}},
    }


async def _translate(lines: AsyncIterator[str], *, thinking: bool = False) -> list[dict[str, Any]]:
    """Anthropic events (data payloads) for a Responses stream."""
    return [
        data
        async for _, data in responses_to_anthropic_events(
            iter_responses_events(lines),
            message_id=MESSAGE_ID,
            model=MODEL,
            include_thinking=thinking,
        )
    ]


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


class TestRecordedStreams:
    async def test_text_answer(self):
        events = await _translate(_fixture("text"))

        assert _types(events)[:2] == ["message_start", "content_block_start"]
        assert _types(events)[-3:] == ["content_block_stop", "message_delta", "message_stop"]
        assert events[1]["content_block"] == {"type": "text", "text": ""}
        text = "".join(e["delta"]["text"] for e in events if e["type"] == "content_block_delta")
        assert text == "Hello to you."
        assert events[-2]["delta"] == {"stop_reason": "end_turn", "stop_sequence": None}
        assert events[-2]["usage"] == {
            "input_tokens": 22,
            "output_tokens": 8,
            "reasoning_tokens": 0,
        }

    async def test_tool_call(self):
        events = await _translate(_fixture("tool_call"))

        start = next(e for e in events if e["type"] == "content_block_start")
        assert start["content_block"]["type"] == "tool_use"
        assert start["content_block"]["name"] == "get_weather"
        assert start["content_block"]["id"].startswith("call_")
        arguments = "".join(
            e["delta"]["partial_json"] for e in events if e["type"] == "content_block_delta"
        )
        assert json.loads(arguments) == {"city": "Paris"}
        assert events[-2]["delta"]["stop_reason"] == "tool_use"

    async def test_reasoning_summary_is_a_thinking_block_when_asked_for(self):
        events = await _translate(_fixture("reasoning"), thinking=True)

        starts = [e["content_block"]["type"] for e in events if e["type"] == "content_block_start"]
        assert starts == ["thinking", "text"]
        thinking = [e for e in events if e["type"] == "content_block_delta"][0]
        assert thinking["delta"]["type"] == "thinking_delta"
        assert thinking["delta"]["thinking"]
        stops = [e["index"] for e in events if e["type"] == "content_block_stop"]
        assert stops == [0, 1]

    async def test_signature_is_sent_before_the_thinking_block_stops(self):
        events = await _translate(_fixture("reasoning_tool_call"), thinking=True)

        sequence = [
            e["delta"]["type"] if e["type"] == "content_block_delta" else e["type"]
            for e in events
            if e["type"].startswith("content_block")
        ]
        assert sequence[:5] == [
            "content_block_start",
            "thinking_delta",
            "signature_delta",
            "content_block_stop",
            "content_block_start",
        ]
        signature = next(
            e["delta"]["signature"]
            for e in events
            if e["type"] == "content_block_delta" and e["delta"]["type"] == "signature_delta"
        )
        # The backend's `.added` copy of the payload is a different, shorter one.
        assert signature == "REDACTED-FINAL"

    async def test_reasoning_without_a_summary_is_still_one_signed_block(self):
        events = await _translate(_fixture("reasoning_tool_call_no_summary"), thinking=False)

        starts = [e["content_block"]["type"] for e in events if e["type"] == "content_block_start"]
        assert starts == ["thinking", "tool_use", "tool_use"]
        assert events[1]["content_block"] == {"type": "thinking", "thinking": ""}
        assert events[2]["delta"] == {"type": "signature_delta", "signature": "REDACTED-FINAL"}
        assert events[3] == {"type": "content_block_stop", "index": 0}

    async def test_reasoning_is_signed_even_when_no_summary_was_asked_for(self):
        events = await _translate(_fixture("reasoning"), thinking=False)

        starts = [e["content_block"]["type"] for e in events if e["type"] == "content_block_start"]
        assert starts == ["thinking", "text"]
        deltas = [e["delta"] for e in events if e["type"] == "content_block_delta"]
        assert [d["type"] for d in deltas][0] == "signature_delta"
        assert not any(d["type"] == "thinking_delta" for d in deltas)
        assert "encrypted_content" not in json.dumps(events)

    async def test_incomplete_maps_to_max_tokens(self):
        events = await _translate(_fixture("incomplete"))

        assert events[-2]["delta"]["stop_reason"] == "max_tokens"
        assert events[-2]["usage"]["output_tokens"] == 16

    async def test_sse_formatting_ends_with_done(self):
        out = [
            event
            async for event in responses_stream_to_anthropic(
                _fixture("text"), message_id=MESSAGE_ID, model=MODEL, include_thinking=False
            )
        ]

        assert out[0].startswith("event: message_start\ndata: ")
        assert json.loads(out[0].split("data: ", 1)[1])["message"]["id"] == MESSAGE_ID
        assert out[-2] == 'event: message_stop\ndata: {"type": "message_stop"}\n\n'
        assert out[-1] == "data: [DONE]\n\n"


class TestMessageAssembly:
    async def test_text_message(self):
        message = await responses_stream_to_message(
            _fixture("text"), message_id=MESSAGE_ID, model=MODEL, include_thinking=False
        )

        assert message.id == MESSAGE_ID
        assert message.model == MODEL
        assert message.stop_reason == "end_turn"
        assert message.usage.input_tokens == 22
        assert message.usage.output_tokens == 8
        assert message.content == [TextBlock(text="Hello to you.")]

    async def test_tool_message(self):
        message = await responses_stream_to_message(
            _fixture("tool_call"), message_id=MESSAGE_ID, model=MODEL, include_thinking=False
        )

        assert message.stop_reason == "tool_use"
        [block] = message.content
        assert isinstance(block, ToolUseBlock)
        assert block.name == "get_weather"
        assert block.input == {"city": "Paris"}

    async def test_thinking_message(self):
        message = await responses_stream_to_message(
            _fixture("reasoning"), message_id=MESSAGE_ID, model=MODEL, include_thinking=True
        )

        assert [type(b) for b in message.content] == [ThinkingBlock, TextBlock]
        assert message.content[0].thinking == "**Counting strawberry letters**"
        assert message.content[0].signature == "REDACTED"

    async def test_signed_thinking_precedes_the_tool_calls_it_led_to(self):
        message = await responses_stream_to_message(
            _fixture("reasoning_tool_call"),
            message_id=MESSAGE_ID,
            model=MODEL,
            include_thinking=True,
        )

        assert [type(b) for b in message.content] == [ThinkingBlock, ToolUseBlock, ToolUseBlock]
        assert message.content[0].thinking == "**Checking both weather forecasts**"
        assert message.content[0].signature == "REDACTED-FINAL"
        assert message.stop_reason == "tool_use"

    async def test_signature_is_kept_when_no_summary_was_asked_for(self):
        message = await responses_stream_to_message(
            _fixture("reasoning_tool_call_no_summary"),
            message_id=MESSAGE_ID,
            model=MODEL,
            include_thinking=False,
        )

        assert message.content[0] == ThinkingBlock(thinking="", signature="REDACTED-FINAL")

    async def test_tool_arguments_that_are_not_json_are_an_error(self):
        lines = _sse(
            _created(),
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "call_id": "c", "name": "n"},
            },
            {"type": "response.function_call_arguments.delta", "delta": "{not json"},
            {"type": "response.output_item.done"},
            _completed(),
        )

        with pytest.raises(ProviderError, match="not valid JSON"):
            await responses_stream_to_message(
                lines, message_id=MESSAGE_ID, model=MODEL, include_thinking=False
            )

    async def test_tool_arguments_that_are_not_an_object_are_an_error(self):
        lines = _sse(
            _created(),
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "call_id": "c", "name": "n"},
            },
            {"type": "response.function_call_arguments.delta", "delta": "[1]"},
            {"type": "response.output_item.done"},
            _completed(),
        )

        with pytest.raises(ProviderError, match="not a JSON object"):
            await responses_stream_to_message(
                lines, message_id=MESSAGE_ID, model=MODEL, include_thinking=False
            )

    async def test_tool_call_without_arguments_has_empty_input(self):
        lines = _sse(
            _created(),
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "call_id": "c", "name": "n"},
            },
            {"type": "response.output_item.done"},
            _completed(),
        )

        message = await responses_stream_to_message(
            lines, message_id=MESSAGE_ID, model=MODEL, include_thinking=False
        )

        assert message.content == [ToolUseBlock(id="c", name="n", input={})]


class TestProtocolVariants:
    async def test_arguments_only_in_done_event_are_still_delivered(self):
        events = await _translate(
            _sse(
                _created(),
                {
                    "type": "response.output_item.added",
                    "item": {"type": "function_call", "call_id": "c", "name": "n"},
                },
                {"type": "response.function_call_arguments.done", "arguments": '{"a":1}'},
                {"type": "response.output_item.done"},
                _completed(),
            )
        )

        deltas = [e["delta"]["partial_json"] for e in events if e["type"] == "content_block_delta"]
        assert deltas == ['{"a":1}']

    async def test_text_delta_without_content_part_starts_a_block(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.output_text.delta", "delta": "hi"},
                _completed(),
            )
        )

        assert _types(events) == [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]

    async def test_refusal_is_surfaced_as_text(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.content_part.added", "part": {"type": "refusal"}},
                {"type": "response.refusal.delta", "delta": "I can't."},
                {"type": "response.content_part.done"},
                _completed(),
            )
        )

        deltas = [e["delta"]["text"] for e in events if e["type"] == "content_block_delta"]
        assert deltas == ["I can't."]

    async def test_content_filter_incomplete_ends_the_turn(self):
        events = await _translate(
            _sse(
                _created(),
                {
                    "type": "response.incomplete",
                    "response": {"incomplete_details": {"reason": "content_filter"}},
                },
            )
        )

        assert events[-2]["delta"]["stop_reason"] == "end_turn"

    async def test_unrelated_events_and_parts_are_ignored(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.in_progress"},
                {"type": "response.metadata", "metadata": {}},
                {"type": "response.content_part.added", "part": {"type": "something_else"}},
                _completed(),
            )
        )

        assert _types(events) == ["message_start", "message_delta", "message_stop"]

    async def test_non_data_lines_and_done_marker_are_skipped(self):
        lines = _lines(
            ": keep-alive\n\nevent: response.created\n"
            f"data: {json.dumps(_created())}\n\n"
            f"data: {json.dumps(_completed())}\n\ndata: [DONE]\n\nignored"
        )

        assert _types(await _translate(lines)) == ["message_start", "message_delta", "message_stop"]

    async def test_thinking_text_with_no_open_summary_block_is_ignored(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.reasoning_summary_text.delta", "delta": "x"},
                _completed(),
            ),
            thinking=True,
        )

        assert _types(events) == ["message_start", "message_delta", "message_stop"]


class TestBlockTransitions:
    async def test_blocks_left_open_are_closed_before_the_next_starts(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.reasoning_summary_part.added"},
                {"type": "response.reasoning_summary_text.delta", "delta": "a"},
                {"type": "response.reasoning_summary_part.added"},
                {"type": "response.reasoning_summary_text.delta", "delta": "b"},
                {"type": "response.content_part.added", "part": {"type": "output_text"}},
                {"type": "response.output_text.delta", "delta": "t"},
                _completed(),
            ),
            thinking=True,
        )

        kinds = [
            e["content_block"]["type"] if e["type"] == "content_block_start" else "stop"
            for e in events
            if e["type"] in ("content_block_start", "content_block_stop")
        ]
        assert kinds == ["thinking", "stop", "text", "stop"]

    async def test_summary_parts_of_one_reasoning_item_share_a_block(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.output_item.added", "item": {"type": "reasoning"}},
                {"type": "response.reasoning_summary_part.added"},
                {"type": "response.reasoning_summary_text.delta", "delta": "a"},
                {"type": "response.reasoning_summary_part.done"},
                {"type": "response.reasoning_summary_part.added"},
                {"type": "response.reasoning_summary_text.delta", "delta": "b"},
                {"type": "response.reasoning_summary_part.done"},
                {
                    "type": "response.output_item.done",
                    "item": {"type": "reasoning", "encrypted_content": "ENC"},
                },
                _completed(),
            ),
            thinking=True,
        )

        deltas = [e["delta"] for e in events if e["type"] == "content_block_delta"]
        assert deltas == [
            {"type": "thinking_delta", "thinking": "a"},
            {"type": "thinking_delta", "thinking": "\n\n"},
            {"type": "thinking_delta", "thinking": "b"},
            {"type": "signature_delta", "signature": "ENC"},
        ]
        assert _types(events).count("content_block_start") == 1

    async def test_each_reasoning_item_gets_its_own_signed_block(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.output_item.added", "item": {"type": "reasoning"}},
                {
                    "type": "response.output_item.done",
                    "item": {"type": "reasoning", "encrypted_content": "ONE"},
                },
                {"type": "response.output_item.added", "item": {"type": "reasoning"}},
                {
                    "type": "response.output_item.done",
                    "item": {"type": "reasoning", "encrypted_content": "TWO"},
                },
                _completed(),
            ),
        )

        signatures = [
            e["delta"]["signature"]
            for e in events
            if e["type"] == "content_block_delta" and e["delta"]["type"] == "signature_delta"
        ]
        assert signatures == ["ONE", "TWO"]
        assert [e["index"] for e in events if e["type"] == "content_block_stop"] == [0, 1]

    async def test_reasoning_item_without_encrypted_content_stays_unsigned(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.output_item.added", "item": {"type": "reasoning"}},
                {"type": "response.reasoning_summary_part.added"},
                {"type": "response.reasoning_summary_text.delta", "delta": "a"},
                {"type": "response.output_item.done", "item": {"type": "reasoning"}},
                _completed(),
            ),
            thinking=True,
        )

        deltas = [e["delta"]["type"] for e in events if e["type"] == "content_block_delta"]
        assert deltas == ["thinking_delta"]
        assert _types(events).count("content_block_stop") == 1

    async def test_a_block_left_open_is_closed_before_a_reasoning_item_takes_over(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.output_text.delta", "delta": "partial"},
                {"type": "response.output_item.added", "item": {"type": "reasoning"}},
                {
                    "type": "response.output_item.done",
                    "item": {"type": "reasoning", "encrypted_content": "ENC"},
                },
                _completed(),
            ),
        )

        kinds = [
            e["content_block"]["type"] if e["type"] == "content_block_start" else "stop"
            for e in events
            if e["type"] in ("content_block_start", "content_block_stop")
        ]
        assert kinds == ["text", "stop", "thinking", "stop"]

    async def test_a_text_block_still_open_at_reasoning_done_is_closed_first(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.output_text.delta", "delta": "partial"},
                {
                    "type": "response.output_item.done",
                    "item": {"type": "reasoning", "encrypted_content": "ENC"},
                },
                _completed(),
            ),
        )

        kinds = [
            e["content_block"]["type"] if e["type"] == "content_block_start" else "stop"
            for e in events
            if e["type"] in ("content_block_start", "content_block_stop")
        ]
        assert kinds == ["text", "stop", "thinking", "stop"]

    async def test_reasoning_item_without_summary_or_payload_opens_no_block(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.output_item.added", "item": {"type": "reasoning"}},
                {"type": "response.output_item.done", "item": {"type": "reasoning"}},
                _completed(),
            ),
        )

        assert _types(events) == ["message_start", "message_delta", "message_stop"]

    async def test_text_delta_while_another_block_is_open_starts_a_text_block(self):
        events = await _translate(
            _sse(
                _created(),
                {"type": "response.reasoning_summary_part.added"},
                {"type": "response.output_text.delta", "delta": "t"},
                _completed(),
            ),
            thinking=True,
        )

        assert _types(events).count("content_block_stop") == 2

    async def test_line_parser_stops_at_the_done_marker(self):
        lines = _lines(f"data: {json.dumps(_created())}\n\ndata: [DONE]\n\ndata: {{not read")

        assert [e["type"] async for e in iter_responses_events(lines)] == ["response.created"]


class TestFailures:
    async def test_response_failed_raises_with_code_and_message(self):
        lines = _sse(
            _created(),
            {
                "type": "response.failed",
                "response": {"error": {"code": "server_error", "message": "boom"}},
            },
        )

        with pytest.raises(ProviderError, match="server_error: boom"):
            await _translate(lines)

    @pytest.mark.parametrize(
        "event",
        [
            {"type": "error", "code": "rate_limit_exceeded", "message": "slow down"},
            {"type": "error", "error": {"code": "rate_limit_exceeded", "message": "slow down"}},
        ],
    )
    async def test_error_event_raises(self, event):
        with pytest.raises(ProviderError, match="rate_limit_exceeded: slow down"):
            await _translate(_sse(_created(), event))

    async def test_failed_without_details_still_raises(self):
        with pytest.raises(ProviderError, match="unknown: no message"):
            await _translate(_sse(_created(), {"type": "response.failed", "response": {}}))

    async def test_unknown_incomplete_reason_raises(self):
        lines = _sse(
            _created(),
            {
                "type": "response.incomplete",
                "response": {"incomplete_details": {"reason": "mystery"}},
            },
        )

        with pytest.raises(ProviderError, match="ended the response early: mystery"):
            await _translate(lines)

    async def test_stream_cut_before_completion_raises(self):
        with pytest.raises(ProviderError, match="without response.completed"):
            await _translate(_sse(_created(), {"type": "response.output_text.delta", "delta": "x"}))

    async def test_events_before_created_raise(self):
        with pytest.raises(ProviderError, match="before response.created"):
            await _translate(_sse({"type": "response.output_text.delta", "delta": "x"}))

    async def test_malformed_json_raises(self):
        with pytest.raises(ProviderError, match="malformed JSON"):
            await _translate(_lines("data: {nope"))

    async def test_non_object_payload_raises(self):
        with pytest.raises(ProviderError, match="not an object"):
            await _translate(_lines("data: [1]"))


def test_message_ids_are_unique_anthropic_style():
    first, second = new_message_id(), new_message_id()

    assert first != second
    assert first.startswith("msg_")
