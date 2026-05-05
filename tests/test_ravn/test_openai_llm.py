"""Tests for OpenAICompatibleAdapter.

Coverage:
- Streaming SSE event parsing (text delta, tool call, usage)
- Tool call extraction from streaming and non-streaming responses
- Token usage normalisation (OpenAI format → common TokenUsage)
- Reasoning tag stripping (<think>, <reasoning>)
- Model-specific steering injection (system_prefix)
- Tool format conversion (Anthropic → OpenAI)
- System prompt handling (string, block list)
- Retry on transient errors (429, 500, 502, 503)
- Non-retryable errors raised immediately
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ravn.adapters.llm.openai import (
    OpenAICompatibleAdapter,
    _convert_tools,
    _normalise_usage,
    _strip_reasoning_tags,
    _system_to_string,
    _uses_developer_role,
)
from ravn.domain.exceptions import LLMError
from ravn.domain.models import StopReason, StreamEventType, TokenUsage

# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

_BASE_URL = "https://api.openai.com"
_MESSAGES = [{"role": "user", "content": "hello"}]
_KWARGS = dict(tools=[], system="You are a test assistant.", model="gpt-4o", max_tokens=100)


def _make_sse_lines(*chunks: str, usage: dict | None = None) -> bytes:
    """Build an SSE byte stream from JSON chunk strings."""
    parts: list[bytes] = []
    for chunk in chunks:
        parts.append(f"data: {chunk}\n\n".encode())
    if usage:
        parts.append(f"data: {json.dumps({'usage': usage})}\n\n".encode())
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def _text_chunk(content: str, finish_reason: str | None = None) -> str:
    return json.dumps(
        {
            "choices": [
                {
                    "delta": {"content": content},
                    "finish_reason": finish_reason,
                }
            ]
        }
    )


def _tool_chunk(
    idx: int = 0,
    *,
    tool_id: str = "call-1",
    name: str | None = None,
    args: str = "",
    finish_reason: str | None = None,
) -> str:
    tc_delta: dict = {"index": idx, "function": {"arguments": args}}
    if tool_id:
        tc_delta["id"] = tool_id
    if name is not None:
        tc_delta["function"]["name"] = name
    return json.dumps(
        {
            "choices": [
                {
                    "delta": {"tool_calls": [tc_delta]},
                    "finish_reason": finish_reason,
                }
            ]
        }
    )


def _non_stream_response(
    content: str = "Hello!",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
    usage: dict | None = None,
) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestStripReasoningTags:
    def test_strips_think_tags(self) -> None:
        text = "<think>internal reasoning</think>final answer"
        assert _strip_reasoning_tags(text) == "final answer"

    def test_strips_reasoning_tags(self) -> None:
        text = "<reasoning>step by step</reasoning>result"
        assert _strip_reasoning_tags(text) == "result"

    def test_case_insensitive(self) -> None:
        text = "<THINK>ignored</THINK>visible"
        assert _strip_reasoning_tags(text) == "visible"

    def test_multiline_stripped(self) -> None:
        text = "<think>\nline1\nline2\n</think>answer"
        assert _strip_reasoning_tags(text) == "answer"

    def test_no_tags_unchanged(self) -> None:
        assert _strip_reasoning_tags("just text") == "just text"

    def test_empty_string(self) -> None:
        assert _strip_reasoning_tags("") == ""

    def test_only_tags_gives_empty(self) -> None:
        result = _strip_reasoning_tags("<think>nothing</think>")
        assert result == ""


class TestNormaliseUsage:
    def test_basic_usage(self) -> None:
        usage = _normalise_usage({"prompt_tokens": 20, "completion_tokens": 10})
        assert usage.input_tokens == 20
        assert usage.output_tokens == 10
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0

    def test_cache_read_from_details(self) -> None:
        usage = _normalise_usage(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 40},
            }
        )
        assert usage.cache_read_tokens == 40

    def test_empty_dict_returns_zeros(self) -> None:
        usage = _normalise_usage({})
        assert isinstance(usage, TokenUsage)
        assert usage.total_tokens == 0

    def test_missing_details_is_safe(self) -> None:
        usage = _normalise_usage({"prompt_tokens": 5, "completion_tokens": 3})
        assert usage.cache_read_tokens == 0

    def test_null_details_is_safe(self) -> None:
        usage = _normalise_usage(
            {"prompt_tokens": 5, "completion_tokens": 3, "prompt_tokens_details": None}
        )
        assert usage.cache_read_tokens == 0

    def test_non_dict_details_is_safe(self) -> None:
        # If prompt_tokens_details is a truthy non-dict value (unexpected API response),
        # the isinstance guard prevents a crash.
        usage = _normalise_usage(
            {"prompt_tokens": 5, "completion_tokens": 3, "prompt_tokens_details": ["unexpected"]}
        )
        assert usage.cache_read_tokens == 0


class TestConvertTools:
    def test_anthropic_to_openai_format(self) -> None:
        tools = [
            {
                "name": "my_tool",
                "description": "Does something",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
        converted = _convert_tools(tools)
        assert len(converted) == 1
        assert converted[0]["type"] == "function"
        assert converted[0]["function"]["name"] == "my_tool"
        assert converted[0]["function"]["description"] == "Does something"
        assert converted[0]["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_empty_tools(self) -> None:
        assert _convert_tools([]) == []

    def test_missing_description_defaults_to_empty(self) -> None:
        tools = [{"name": "t", "input_schema": {}}]
        converted = _convert_tools(tools)
        assert converted[0]["function"]["description"] == ""


class TestSystemToString:
    def test_string_passthrough(self) -> None:
        assert _system_to_string("hello") == "hello"

    def test_block_list_concatenated(self) -> None:
        blocks = [
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ]
        result = _system_to_string(blocks)
        assert "part one" in result
        assert "part two" in result

    def test_empty_string(self) -> None:
        assert _system_to_string("") == ""

    def test_empty_list(self) -> None:
        assert _system_to_string([]) == ""


# ---------------------------------------------------------------------------
# Adapter construction
# ---------------------------------------------------------------------------


class TestOpenAIAdapterInit:
    def test_defaults(self) -> None:
        adapter = OpenAICompatibleAdapter()
        assert adapter._base_url == "https://api.openai.com" or adapter._base_url.startswith(
            "https://api.openai.com/"
        )
        assert adapter._default_model == "gpt-4o"
        assert adapter._max_retries >= 0

    def test_custom_base_url_trailing_slash_stripped(self) -> None:
        adapter = OpenAICompatibleAdapter(base_url="http://proxy/")
        assert not adapter._base_url.endswith("/")

    def test_api_key_in_headers(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="sk-test")
        headers = adapter._headers()
        assert headers["authorization"] == "Bearer sk-test"

    def test_no_auth_header_when_no_key(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="")
        headers = adapter._headers()
        assert "authorization" not in headers


# ---------------------------------------------------------------------------
# generate() — happy path
# ---------------------------------------------------------------------------


class TestOpenAIAdapterGenerate:
    @respx.mock
    async def test_generate_text_response(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_non_stream_response("Hello!"))
        )

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert result.content == "Hello!"
        assert result.stop_reason == StopReason.END_TURN

    @respx.mock
    async def test_generate_usage_normalised(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_non_stream_response(usage={"prompt_tokens": 20, "completion_tokens": 10}),
            )
        )

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert result.usage.input_tokens == 20
        assert result.usage.output_tokens == 10

    @respx.mock
    async def test_generate_tool_call_extracted(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        tool_calls_data = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "my_fn", "arguments": '{"x": 42}'},
            }
        ]
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_non_stream_response(content="", tool_calls=tool_calls_data),
            )
        )

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "my_fn"
        assert result.tool_calls[0].input == {"x": 42}
        assert result.stop_reason == StopReason.TOOL_USE

    @respx.mock
    async def test_generate_max_tokens_stop_reason(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_non_stream_response("truncated...", finish_reason="length"),
            )
        )

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert result.stop_reason == StopReason.MAX_TOKENS

    @respx.mock
    async def test_generate_strips_reasoning_tags(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_non_stream_response("<think>internal</think>actual answer"),
            )
        )

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert "think" not in result.content
        assert "actual answer" in result.content

    @respx.mock
    async def test_generate_system_prefix_injected(self) -> None:
        adapter = OpenAICompatibleAdapter(
            api_key="k", base_url=_BASE_URL, system_prefix="ALWAYS USE METRIC UNITS."
        )
        captured: list[dict] = []

        def _capture(request, route) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_non_stream_response())

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_capture)

        await adapter.generate(_MESSAGES, **_KWARGS)
        system_msg = next(m for m in captured[0]["messages"] if m["role"] == "system")
        assert "ALWAYS USE METRIC UNITS." in system_msg["content"]

    @respx.mock
    async def test_generate_error_status_raises(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL, max_retries=0)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )

        with pytest.raises(LLMError) as exc_info:
            await adapter.generate(_MESSAGES, **_KWARGS)

        assert exc_info.value.status_code == 401

    @respx.mock
    async def test_generate_block_list_system(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        captured: list[dict] = []

        def _capture(request, route) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_non_stream_response())

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_capture)

        system_blocks = [
            {"type": "text", "text": "You are a coder.", "cache_control": {"type": "ephemeral"}},
        ]
        await adapter.generate(
            _MESSAGES, tools=[], system=system_blocks, model="gpt-4o", max_tokens=100
        )
        system_msg = next(m for m in captured[0]["messages"] if m["role"] == "system")
        assert "You are a coder." in system_msg["content"]


# ---------------------------------------------------------------------------
# stream() — SSE parsing
# ---------------------------------------------------------------------------


class TestOpenAIAdapterStream:
    @respx.mock
    async def test_stream_text_delta(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        sse = _make_sse_lines(
            _text_chunk("Hello, "),
            _text_chunk("world!"),
            usage={"prompt_tokens": 5, "completion_tokens": 3},
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        assert len(text_events) == 2
        assert "".join(e.text or "" for e in text_events) == "Hello, world!"

    @respx.mock
    async def test_stream_usage_in_final_chunk(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        sse = _make_sse_lines(
            _text_chunk("hi"),
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        done_events = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
        assert len(done_events) == 1
        assert done_events[0].usage is not None
        assert done_events[0].usage.input_tokens == 10
        assert done_events[0].usage.output_tokens == 5

    @respx.mock
    async def test_stream_tool_call_extracted(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        sse = _make_sse_lines(
            # Start tool call with id + name
            _tool_chunk(0, tool_id="call-1", name="do_thing", args=""),
            # Partial args chunk
            _tool_chunk(0, tool_id="", name=None, args='{"val": '),
            # Final args chunk with finish_reason
            _tool_chunk(0, tool_id="", name=None, args='"ok"}', finish_reason="tool_calls"),
            usage={"prompt_tokens": 5, "completion_tokens": 10},
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.name == "do_thing"
        assert tool_events[0].tool_call.input == {"val": "ok"}

    @respx.mock
    async def test_stream_strips_reasoning_tags(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        sse = _make_sse_lines(
            _text_chunk("<think>internal reasoning</think>"),
            _text_chunk("visible answer"),
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        combined = "".join(e.text or "" for e in text_events)
        assert "think" not in combined
        assert "visible answer" in combined

    @respx.mock
    async def test_stream_error_status_raises(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL, max_retries=0)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(401, content=b"unauthorized")
        )

        with pytest.raises(LLMError) as exc_info:
            async for _ in adapter.stream(_MESSAGES, **_KWARGS):
                pass

        assert exc_info.value.status_code == 401

    @respx.mock
    async def test_stream_tools_converted(self) -> None:
        """Tools must be converted from Anthropic format before sending."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        captured: list[dict] = []

        sse = _make_sse_lines(
            _text_chunk("ok"),
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )

        def _capture(request, route) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, content=sse)

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_capture)

        tools = [{"name": "my_tool", "description": "desc", "input_schema": {"type": "object"}}]
        async for _ in adapter.stream(
            _MESSAGES, tools=tools, system="sys", model="gpt-4o", max_tokens=100
        ):
            pass

        sent_tools = captured[0].get("tools", [])
        assert len(sent_tools) == 1
        assert sent_tools[0]["type"] == "function"
        assert sent_tools[0]["function"]["name"] == "my_tool"


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class TestOpenAIAdapterEdgeCases:
    @respx.mock
    async def test_generate_empty_system_no_system_message(self) -> None:
        """Empty system prompt → no system message prepended to messages."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        captured: list[dict] = []

        def _capture(request, route) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_non_stream_response())

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_capture)

        await adapter.generate(_MESSAGES, tools=[], system="", model="gpt-4o", max_tokens=100)
        # No system role message when system is empty.
        roles = [m["role"] for m in captured[0]["messages"]]
        assert "system" not in roles

    @respx.mock
    async def test_generate_invalid_tool_args_handled(self) -> None:
        """Malformed tool args JSON falls back to empty dict, no exception."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        tool_calls_data = [
            {
                "id": "call-bad",
                "type": "function",
                "function": {"name": "bad_tool", "arguments": "{not valid json}"},
            }
        ]
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_non_stream_response(content="", tool_calls=tool_calls_data)
            )
        )

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].input == {}  # fallback to empty dict

    @respx.mock
    async def test_stream_malformed_json_line_skipped(self) -> None:
        """Malformed JSON SSE lines are silently skipped."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        usage_chunk = json.dumps({"usage": {"prompt_tokens": 5, "completion_tokens": 5}})
        lines = (
            b"data: {not json at all}\n\n"
            b"data: " + _text_chunk("ok").encode() + b"\n\n"
            b"data: " + usage_chunk.encode() + b"\n\n"
            b"data: [DONE]\n\n"
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=lines)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        assert any(e.text == "ok" for e in text_events)

    @respx.mock
    async def test_stream_empty_choices_skipped(self) -> None:
        """SSE chunks with empty choices are silently skipped."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        # Chunk with empty choices (heartbeat), then real chunk.
        empty_chunk = json.dumps({"choices": []})
        lines = (
            f"data: {empty_chunk}\n\n".encode()
            + f"data: {_text_chunk('hello')}\n\n".encode()
            + json.dumps({"usage": {"prompt_tokens": 5, "completion_tokens": 5}})
            .encode()
            .join([b"data: ", b"\n\ndata: [DONE]\n\n"])
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=lines)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        assert any(e.text == "hello" for e in text_events)

    @respx.mock
    async def test_stream_invalid_tool_args_fallback(self) -> None:
        """Tool call with malformed args string falls back to empty input dict."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        sse = _make_sse_lines(
            _tool_chunk(0, tool_id="c1", name="fn", args=""),
            _tool_chunk(0, tool_id="", name=None, args="{INVALID", finish_reason="tool_calls"),
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.input == {}

    @respx.mock
    async def test_transport_error_retried(self) -> None:
        """httpx.TransportError is retried and succeeds on next attempt."""
        adapter = OpenAICompatibleAdapter(
            api_key="k", base_url=_BASE_URL, max_retries=2, retry_base_delay=0.0
        )
        call_count = {"n": 0}

        def _side_effect(request, route):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json=_non_stream_response("recovered"))

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_side_effect)

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert result.content == "recovered"

    @respx.mock
    async def test_stream_retries_on_429(self) -> None:
        """Stream path retries on 429 (closes previous stream response)."""
        adapter = OpenAICompatibleAdapter(
            api_key="k", base_url=_BASE_URL, max_retries=2, retry_base_delay=0.0
        )
        call_count = {"n": 0}

        ok_sse = _make_sse_lines(
            _text_chunk("ok"),
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )

        def _side_effect(request, route):
            call_count["n"] += 1
            if call_count["n"] < 2:
                return httpx.Response(429, content=b"rate limited")
            return httpx.Response(200, content=ok_sse)

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_side_effect)

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        assert any(e.text == "ok" for e in text_events)


class TestOpenAIAdapterRetry:
    @respx.mock
    async def test_retries_on_429(self) -> None:
        adapter = OpenAICompatibleAdapter(
            api_key="k", base_url=_BASE_URL, max_retries=2, retry_base_delay=0.0
        )
        route = respx.post(f"{_BASE_URL}/v1/chat/completions")
        route.side_effect = [
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(200, json=_non_stream_response()),
        ]

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert result.content is not None

    @respx.mock
    async def test_raises_after_max_retries(self) -> None:
        adapter = OpenAICompatibleAdapter(
            api_key="k", base_url=_BASE_URL, max_retries=1, retry_base_delay=0.0
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(503, json={"error": "service unavailable"})
        )

        with pytest.raises(LLMError) as exc_info:
            await adapter.generate(_MESSAGES, **_KWARGS)

        assert "model=" in str(exc_info.value)
        assert "endpoint=" in str(exc_info.value)
        assert "status=503" in str(exc_info.value)

    @respx.mock
    async def test_non_retryable_error_raises_immediately(self) -> None:
        adapter = OpenAICompatibleAdapter(
            api_key="k", base_url=_BASE_URL, max_retries=3, retry_base_delay=0.0
        )
        # 404 is not in _RETRYABLE_STATUS_CODES — must raise immediately.
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )

        with pytest.raises(LLMError) as exc_info:
            await adapter.generate(_MESSAGES, **_KWARGS)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# REASONING_SCRATCHPAD tag stripping
# ---------------------------------------------------------------------------


class TestReasoningScratchpadStripping:
    def test_strips_reasoning_scratchpad_tags(self) -> None:
        text = "<REASONING_SCRATCHPAD>hidden reasoning</REASONING_SCRATCHPAD>answer"
        assert _strip_reasoning_tags(text) == "answer"

    def test_strips_reasoning_scratchpad_case_insensitive(self) -> None:
        text = "<reasoning_scratchpad>hidden</reasoning_scratchpad>visible"
        assert _strip_reasoning_tags(text) == "visible"

    def test_strips_multiline_scratchpad(self) -> None:
        text = "<REASONING_SCRATCHPAD>\nstep 1\nstep 2\n</REASONING_SCRATCHPAD>final"
        assert _strip_reasoning_tags(text) == "final"

    def test_all_three_tag_types_stripped(self) -> None:
        text = (
            "<think>t</think>"
            "<reasoning>r</reasoning>"
            "<REASONING_SCRATCHPAD>s</REASONING_SCRATCHPAD>result"
        )
        assert _strip_reasoning_tags(text) == "result"

    def test_mismatched_tags_not_stripped(self) -> None:
        text = "<think>hidden</reasoning>visible"
        assert _strip_reasoning_tags(text) == text

    @respx.mock
    async def test_generate_strips_scratchpad_tag(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_non_stream_response(
                    "<REASONING_SCRATCHPAD>internal</REASONING_SCRATCHPAD>clean answer"
                ),
            )
        )

        result = await adapter.generate(_MESSAGES, **_KWARGS)
        assert "REASONING_SCRATCHPAD" not in result.content
        assert "clean answer" in result.content

    @respx.mock
    async def test_stream_strips_scratchpad_tag(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        sse = _make_sse_lines(
            _text_chunk("<REASONING_SCRATCHPAD>hidden</REASONING_SCRATCHPAD>"),
            _text_chunk("visible"),
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        combined = "".join(e.text or "" for e in text_events)
        assert "REASONING_SCRATCHPAD" not in combined
        assert "visible" in combined


# ---------------------------------------------------------------------------
# Developer role swap (GPT-5/o1/o3/Codex models)
# ---------------------------------------------------------------------------


class TestDeveloperRoleSwap:
    def test_o1_uses_developer_role(self) -> None:
        assert _uses_developer_role("o1-mini") is True

    def test_o3_uses_developer_role(self) -> None:
        assert _uses_developer_role("o3-mini") is True

    def test_gpt5_uses_developer_role(self) -> None:
        assert _uses_developer_role("gpt-5") is True
        assert _uses_developer_role("gpt-5-turbo") is True

    def test_codex_uses_developer_role(self) -> None:
        assert _uses_developer_role("codex-mini") is True

    def test_gpt4_uses_system_role(self) -> None:
        assert _uses_developer_role("gpt-4o") is False

    def test_claude_uses_system_role(self) -> None:
        assert _uses_developer_role("claude-sonnet-4-6") is False

    def test_llama_uses_system_role(self) -> None:
        assert _uses_developer_role("llama3.1:8b") is False

    @respx.mock
    async def test_o1_model_sends_developer_role(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        captured: list[dict] = []

        def _capture(request, route) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_non_stream_response())

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_capture)

        await adapter.generate(
            _MESSAGES, tools=[], system="You are helpful.", model="o1-mini", max_tokens=100
        )
        first_msg = captured[0]["messages"][0]
        assert first_msg["role"] == "developer"
        assert "You are helpful." in first_msg["content"]

    @respx.mock
    async def test_gpt4_model_sends_system_role(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        captured: list[dict] = []

        def _capture(request, route) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=_non_stream_response())

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_capture)

        await adapter.generate(
            _MESSAGES, tools=[], system="You are helpful.", model="gpt-4o", max_tokens=100
        )
        first_msg = captured[0]["messages"][0]
        assert first_msg["role"] == "system"

    @respx.mock
    async def test_codex_model_stream_sends_developer_role(self) -> None:
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        captured: list[dict] = []

        sse = _make_sse_lines(
            _text_chunk("ok"),
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )

        def _capture(request, route) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, content=sse)

        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(side_effect=_capture)

        async for _ in adapter.stream(
            _MESSAGES, tools=[], system="sys", model="codex-mini", max_tokens=100
        ):
            pass

        first_msg = captured[0]["messages"][0]
        assert first_msg["role"] == "developer"


# ---------------------------------------------------------------------------
# Token estimation fallback
# ---------------------------------------------------------------------------


class TestTokenEstimationFallback:
    def test_normalise_usage_estimation_fallback_when_zero(self) -> None:
        usage = _normalise_usage({}, input_text="hello world prompt", output_text="response text")
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0

    def test_normalise_usage_real_data_not_overridden(self) -> None:
        """When real token counts are present they must not be replaced by estimates."""
        usage = _normalise_usage(
            {"prompt_tokens": 42, "completion_tokens": 17},
            input_text="hello world prompt",
            output_text="response text",
        )
        assert usage.input_tokens == 42
        assert usage.output_tokens == 17

    def test_normalise_usage_partial_estimation(self) -> None:
        """Only zero fields are estimated; non-zero fields are kept."""
        usage = _normalise_usage(
            {"prompt_tokens": 10, "completion_tokens": 0},
            input_text="x",
            output_text="response text here",
        )
        assert usage.input_tokens == 10
        assert usage.output_tokens > 0

    @respx.mock
    async def test_generate_estimates_when_no_usage(self) -> None:
        """generate() falls back to estimation when the API omits usage."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        # Response with no usage field.
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}
            ]
        }
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=body)
        )

        result = await adapter.generate(
            _MESSAGES, tools=[], system="sys", model="gpt-4o", max_tokens=100
        )
        # Estimated values must be positive.
        assert result.usage.input_tokens > 0
        assert result.usage.output_tokens > 0

    @respx.mock
    async def test_stream_emits_estimated_usage_when_no_usage_chunk(self) -> None:
        """stream() emits MESSAGE_DONE with estimated usage when no usage chunk arrives."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        # SSE stream with no usage chunk and no [DONE].
        sse_lines = f"data: {_text_chunk('Hello there!')}\n\n".encode() + b"data: [DONE]\n\n"
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse_lines)
        )

        events = []
        async for event in adapter.stream(
            _MESSAGES, tools=[], system="sys", model="gpt-4o", max_tokens=100
        ):
            events.append(event)

        done_events = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
        assert len(done_events) == 1
        assert done_events[0].usage is not None
        assert done_events[0].usage.input_tokens > 0
        assert done_events[0].usage.output_tokens > 0

    @respx.mock
    async def test_stream_no_duplicate_done_when_usage_present(self) -> None:
        """stream() emits exactly one MESSAGE_DONE when the server sends a usage chunk."""
        adapter = OpenAICompatibleAdapter(api_key="k", base_url=_BASE_URL)
        sse = _make_sse_lines(
            _text_chunk("hi"),
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        respx.post(f"{_BASE_URL}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse)
        )

        events = []
        async for event in adapter.stream(_MESSAGES, **_KWARGS):
            events.append(event)

        done_events = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
        assert len(done_events) == 1
        assert done_events[0].usage.input_tokens == 10
