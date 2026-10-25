"""OpenAIResponsesAdapter: the Responses API spoken natively, items kept verbatim.

The compaction stream is the one recorded from the live ChatGPT Codex backend
for Bifröst's tests (``tests/test_bifrost/fixtures/chatgpt_responses``).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from ravn.adapters.llm.openai_responses import (
    OpenAIResponsesAdapter,
    from_latest_compaction,
    input_items,
)
from ravn.adapters.llm.provider_items import OPENAI_RESPONSES
from ravn.domain.exceptions import ConfigurationError, LLMError
from ravn.domain.models import Message, StreamEventType

BASE = "http://bifrost.test/api/v1/bifrost"
URL = f"{BASE}/v1/responses"
RECORDED = (
    Path(__file__).parents[1] / "test_bifrost" / "fixtures" / "chatgpt_responses" / "compaction.sse"
)
TOOLS = [{"name": "lookup", "description": "Look up.", "input_schema": {"type": "object"}}]
COMPACTION = {"type": "compaction", "id": "cmp_1", "encrypted_content": "ENC"}


def _sse(*events: dict) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _completed(**usage) -> dict:
    return {
        "type": "response.completed",
        "response": {
            "status": "completed",
            "usage": {
                "input_tokens": usage.get("input", 10),
                "output_tokens": usage.get("output", 4),
                "input_tokens_details": {"cached_tokens": 6},
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        },
    }


def _adapter(**kwargs) -> OpenAIResponsesAdapter:
    return OpenAIResponsesAdapter(base_url=BASE, agent_id="regin", retry_base_delay=0, **kwargs)


async def _collect(adapter, messages, **kwargs):
    return [
        event
        async for event in adapter.stream(
            messages,
            tools=kwargs.pop("tools", []),
            system=kwargs.pop("system", "Be terse."),
            model=kwargs.pop("model", "gpt-6-sol"),
            max_tokens=kwargs.pop("max_tokens", 1000),
            **kwargs,
        )
    ]


# ---------------------------------------------------------------------------
# History → input items
# ---------------------------------------------------------------------------


class TestInputItems:
    def test_rebuilds_a_turn_ravn_wrote(self):
        items = input_items(
            [
                {"role": "user", "content": "find the colour"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Looking."},
                        {"type": "tool_use", "id": "c1", "name": "lookup", "input": {"k": "c"}},
                    ],
                    "reasoning_details": [
                        {
                            "type": "reasoning.encrypted",
                            "data": "R",
                            "format": "openai-responses-v1",
                        },
                        {"type": "reasoning.encrypted", "data": "X", "format": "anthropic-v1"},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "c1", "content": "blue"},
                        {"type": "text", "text": "and?"},
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": "AA"},
                        },
                    ],
                },
            ]
        )
        assert items == [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "find the colour"}],
            },
            {"type": "reasoning", "summary": [], "encrypted_content": "R"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Looking."}],
            },
            {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": '{"k": "c"}'},
            {"type": "function_call_output", "call_id": "c1", "output": "blue"},
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "and?"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AA"},
                ],
            },
        ]

    def test_replays_the_providers_own_items_verbatim(self):
        native = [
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "R"},
            {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": "{}"},
        ]
        message = Message(
            role="assistant",
            content=[{"type": "tool_use", "id": "c1", "name": "lookup", "input": {}}],
            provider_items=native,
            provider_protocol=OPENAI_RESPONSES,
        )
        assert input_items([message.to_api_dict()]) == native

    def test_a_history_edited_after_the_answer_cannot_be_replayed(self):
        message = Message(
            role="assistant",
            content=[{"type": "tool_use", "id": "other", "name": "lookup", "input": {}}],
            provider_items=[{"type": "function_call", "call_id": "c1", "name": "lookup"}],
            provider_protocol=OPENAI_RESPONSES,
        )
        with pytest.raises(LLMError, match="cannot be replayed"):
            input_items([message.to_api_dict()])

    def test_cuts_at_the_latest_compaction_keeping_call_pairs(self):
        items = [
            {"type": "message", "role": "user", "content": "old"},
            {"type": "compaction", "encrypted_content": "first"},
            {"type": "function_call", "call_id": "c1", "name": "lookup"},
            {"type": "compaction", "encrypted_content": "second"},
            {"type": "function_call_output", "call_id": "c1", "output": "blue"},
        ]
        assert from_latest_compaction(items) == [
            {"type": "compaction", "encrypted_content": "second"},
            {"type": "function_call", "call_id": "c1", "name": "lookup"},
            {"type": "function_call_output", "call_id": "c1", "output": "blue"},
        ]
        assert from_latest_compaction(items[:1]) == items[:1]

    @pytest.mark.parametrize(
        "message",
        [
            {"role": "system", "content": "x"},
            {"role": "user", "content": [{"type": "document"}]},
            {"role": "assistant", "content": [{"type": "thinking"}]},
            {"role": "user", "content": [{"type": "tool_result", "content": [{"type": "image"}]}]},
            {"role": "user", "content": [{"type": "image", "source": {"type": "base64"}}]},
        ],
    )
    def test_refuses_what_it_cannot_carry(self, message):
        with pytest.raises(LLMError):
            input_items([message])

    def test_url_images_and_text_tool_results(self):
        items = input_items(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}},
                        {
                            "type": "tool_result",
                            "tool_use_id": "c",
                            "content": [{"type": "text", "text": "a"}],
                        },
                    ],
                },
                {"role": "assistant", "content": "plain"},
            ]
        )
        assert items[0]["content"] == [{"type": "input_image", "image_url": "https://x/y.png"}]
        assert items[1]["output"] == "a"
        assert items[2]["content"] == [{"type": "output_text", "text": "plain"}]

    def test_another_protocols_compaction_is_refused(self):
        adapter = _adapter()
        foreign = Message(
            role="assistant",
            content="ok",
            provider_items=[{"type": "compaction", "content": "summary"}],
            provider_protocol="anthropic_messages",
        )
        with pytest.raises(LLMError, match="compacted natively by a anthropic_messages"):
            adapter._build_request(
                [foreign.to_api_dict()], tools=[], system="", model="", max_tokens=0, thinking=None
            )


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class TestRequest:
    def test_shape(self):
        adapter = _adapter(reasoning_effort="high", request_options={"prompt_cache_key": "k"})
        body = adapter._build_request(
            [{"role": "user", "content": "hi"}],
            tools=TOOLS,
            system=[{"type": "text", "text": "A"}, {"type": "text", "text": "B"}],
            model="gpt-6-sol",
            max_tokens=500,
            thinking={"type": "enabled"},
        )
        assert body["stream"] is True and body["store"] is False
        assert body["include"] == ["reasoning.encrypted_content"]
        assert body["instructions"] == "A\n\nB"
        assert body["max_output_tokens"] == 500
        assert body["tools"][0] == {
            "type": "function",
            "name": "lookup",
            "description": "Look up.",
            "parameters": {"type": "object"},
            "strict": False,
        }
        assert body["reasoning"] == {"effort": "high", "summary": "auto"}
        assert body["prompt_cache_key"] == "k"
        assert "context_management" not in body

    def test_native_compaction_only_for_the_enabled_model(self):
        adapter = _adapter()
        adapter.enable_native_compaction(model="gpt-6-sol", threshold_tokens=106666)
        sol = adapter._build_request(
            [], tools=[], system="", model="gpt-6-sol", max_tokens=0, thinking=None
        )
        qwen = adapter._build_request(
            [], tools=[], system="", model="Qwen/Qwen3.8-27B", max_tokens=0, thinking=None
        )
        assert sol["context_management"] == [{"type": "compaction", "compact_threshold": 106666}]
        assert "context_management" not in qwen
        assert "instructions" not in qwen

    def test_threshold_must_be_positive(self):
        with pytest.raises(ConfigurationError):
            _adapter().enable_native_compaction(model="m", threshold_tokens=0)

    def test_window_starts_at_the_latest_compaction(self):
        compacted = Message(
            role="assistant",
            content="ok",
            provider_items=[COMPACTION],
            provider_protocol=OPENAI_RESPONSES,
        )
        history = [
            {"role": "user", "content": "a"},
            compacted.to_api_dict(),
            {"role": "user", "content": "b"},
        ]
        assert _adapter().compaction_window_start(history) == 1
        assert _adapter().compaction_window_start(history[:1]) == 0


# ---------------------------------------------------------------------------
# Stream
# ---------------------------------------------------------------------------


class TestStream:
    @respx.mock
    async def test_recorded_compaction_turn(self):
        route = respx.post(URL).mock(
            return_value=httpx.Response(200, content=RECORDED.read_bytes())
        )
        adapter = _adapter()
        adapter.enable_native_compaction(model="gpt-6-sol", threshold_tokens=2000)
        events = await _collect(adapter, [{"role": "user", "content": "codeword?"}])
        text = "".join(e.text for e in events if e.type == StreamEventType.TEXT_DELTA)
        assert text == "PELICAN-42"
        [items] = [e for e in events if e.type == StreamEventType.PROVIDER_ITEMS]
        assert [i["type"] for i in items.provider_items] == ["compaction", "message", "compaction"]
        assert items.provider_protocol == OPENAI_RESPONSES
        assert events[-1].type == StreamEventType.MESSAGE_DONE
        assert events[-1].usage.input_tokens == 3105
        sent = json.loads(route.calls.last.request.content)
        assert sent["context_management"] == [{"type": "compaction", "compact_threshold": 2000}]
        assert route.calls.last.request.headers["x-agent-id"] == "regin"

    @respx.mock
    async def test_tool_calls_thinking_and_usage(self):
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                content=_sse(
                    {"type": "response.reasoning_summary_text.delta", "delta": "hmm"},
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "call_id": "c1",
                            "name": "lookup",
                            "arguments": '{"k": 1}',
                        },
                    },
                    _completed(),
                ),
            )
        )
        events = await _collect(_adapter(), [{"role": "user", "content": "x"}], tools=TOOLS)
        assert [e.type for e in events] == [
            StreamEventType.THINKING,
            StreamEventType.TOOL_CALL,
            StreamEventType.PROVIDER_ITEMS,
            StreamEventType.MESSAGE_DONE,
        ]
        assert events[1].tool_call.input == {"k": 1}
        usage = events[-1].usage
        assert (usage.input_tokens, usage.cache_read_tokens, usage.thinking_tokens) == (10, 6, 2)

    @respx.mock
    @pytest.mark.parametrize(
        ("events", "match"),
        [
            (
                [{"type": "response.failed", "response": {"error": {"message": "No tool output"}}}],
                "No tool output",
            ),
            ([{"type": "error", "error": {"code": "rate_limited"}}], "rate_limited"),
            ([{"type": "response.created"}], "ended before completion"),
            (
                [
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "call_id": "c",
                            "name": "lookup",
                            "arguments": "{nope",
                        },
                    }
                ],
                "not valid JSON",
            ),
            (
                [
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "call_id": "c",
                            "name": "lookup",
                            "arguments": "[1]",
                        },
                    }
                ],
                "not an object",
            ),
        ],
    )
    async def test_failures_raise(self, events, match):
        respx.post(URL).mock(return_value=httpx.Response(200, content=_sse(*events)))
        with pytest.raises(LLMError, match=match):
            await _collect(_adapter(), [{"role": "user", "content": "x"}])

    @respx.mock
    async def test_a_refused_request_raises_with_the_body(self):
        respx.post(URL).mock(return_value=httpx.Response(400, json={"error": {"message": "bad"}}))
        with pytest.raises(LLMError, match="400"):
            await _collect(_adapter(), [{"role": "user", "content": "x"}])

    @respx.mock
    async def test_transient_errors_are_retried_then_raised(self):
        route = respx.post(URL).mock(
            side_effect=[
                httpx.Response(503),
                httpx.ConnectError("down"),
                httpx.Response(200, content=_sse(_completed())),
            ]
        )
        events = await _collect(_adapter(max_retries=2), [{"role": "user", "content": "x"}])
        assert route.call_count == 3
        assert events[-1].type == StreamEventType.MESSAGE_DONE
        respx.post(URL).mock(return_value=httpx.Response(502))
        with pytest.raises(LLMError, match="failed after 1 retries"):
            await _collect(_adapter(max_retries=1), [{"role": "user", "content": "x"}])

    @respx.mock
    async def test_generate_consumes_the_stream(self):
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                content=_sse(
                    {"type": "response.output_text.delta", "delta": "hi"},
                    {
                        "type": "response.output_item.done",
                        "item": {"type": "message", "content": []},
                    },
                    _completed(output=3),
                ),
            )
        )
        response = await _adapter(api_key="k").generate(
            [{"role": "user", "content": "x"}], tools=[], system="", model="", max_tokens=0
        )
        assert response.content == "hi"
        assert response.usage.output_tokens == 3
        assert response.provider_items == [{"type": "message", "content": []}]
        assert response.provider_protocol == OPENAI_RESPONSES
        assert _adapter().supports_thinking is True
