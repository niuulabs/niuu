"""Tests for AnthropicAdapter retry logic and edge cases."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ravn.adapters.llm.anthropic import AnthropicAdapter
from ravn.cap_telemetry import cap_hit_counts, reset_cap_hits, retry_counts
from ravn.domain.exceptions import LLMError
from ravn.domain.models import StreamEventType


@pytest.fixture(autouse=True)
def _fresh_counts() -> None:
    reset_cap_hits()


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event)


def _text_block_sse(text: str) -> list[str]:
    usage = {"output_tokens": 1, "input_tokens": 1}
    return [
        _sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        ),
        _sse(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            }
        ),
        _sse({"type": "content_block_stop", "index": 0}),
        _sse({"type": "message_delta", "delta": {}, "usage": usage}),
        _sse({"type": "message_stop"}),
    ]


def _tool_block_sse(tool_id: str, name: str, chunk: str) -> list[str]:
    usage = {"output_tokens": 1, "input_tokens": 1}
    return [
        _sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": tool_id, "name": name},
            }
        ),
        _sse(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": chunk},
            }
        ),
        _sse({"type": "content_block_stop", "index": 0}),
        _sse({"type": "message_delta", "delta": {}, "usage": usage}),
        _sse({"type": "message_stop"}),
    ]


@respx.mock
async def test_retry_on_429_then_success() -> None:
    """Adapter retries on 429, succeeds on second attempt."""
    adapter = AnthropicAdapter(
        api_key="k",
        base_url="https://api.anthropic.com",
        gateway_outage_window_seconds=5.0,
        retry_base_delay=0.001,
    )

    response_body = {
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json=response_body)

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=side_effect)

    result = await adapter.generate(
        [{"role": "user", "content": "hi"}],
        tools=[],
        system="",
        model="m",
        max_tokens=100,
    )

    assert result.content == "ok"
    assert call_count == 2


@respx.mock
async def test_retry_exhausted_raises_llm_error() -> None:
    """Adapter raises LLMError when all retries are exhausted."""
    adapter = AnthropicAdapter(
        api_key="k",
        base_url="https://api.anthropic.com",
        gateway_outage_window_seconds=0.05,
        retry_base_delay=0.01,
    )

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(500, text="server error")
    )

    with pytest.raises(LLMError):
        await adapter.generate([], tools=[], system="", model="m", max_tokens=100)


@respx.mock
async def test_transport_error_retried() -> None:
    """Adapter retries on transport errors."""
    adapter = AnthropicAdapter(
        api_key="k",
        base_url="https://api.anthropic.com",
        gateway_outage_window_seconds=5.0,
        retry_base_delay=0.001,
    )

    response_body = {
        "content": [{"type": "text", "text": "recovered"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=response_body)

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=side_effect)

    result = await adapter.generate([], tools=[], system="", model="m", max_tokens=100)
    assert result.content == "recovered"


@respx.mock
async def test_transport_error_exhausted_raises() -> None:
    """Adapter raises LLMError when transport errors exhaust all retries."""
    adapter = AnthropicAdapter(
        api_key="k",
        base_url="https://api.anthropic.com",
        gateway_outage_window_seconds=0.05,
        retry_base_delay=0.01,
    )

    def side_effect(request):
        raise httpx.ConnectError("connection refused")

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=side_effect)

    with pytest.raises(LLMError, match="llm.gateway_outage_window_seconds") as raised:
        await adapter.generate([], tools=[], system="", model="m", max_tokens=100)

    # The failure that was retried is the cause, not swallowed between attempts.
    assert isinstance(raised.value.__cause__, httpx.ConnectError)
    assert retry_counts()["llm.anthropic"] >= 1
    assert cap_hit_counts() == {"llm.gateway_outage_window_seconds": 1}


@respx.mock
async def test_stream_invalid_json_skipped() -> None:
    """Invalid JSON lines in SSE stream are skipped without crashing."""
    adapter = AnthropicAdapter(api_key="k", base_url="https://api.anthropic.com")

    lines = ["data: {invalid json}"] + _text_block_sse("ok")
    sse_body = "\n".join(lines) + "\n"

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=sse_body)
    )

    events = []
    async for ev in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
        events.append(ev)

    text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
    assert len(text_events) >= 1


@respx.mock
async def test_stream_invalid_tool_json_handled() -> None:
    """Invalid JSON in tool input is handled gracefully (empty dict)."""
    adapter = AnthropicAdapter(api_key="k", base_url="https://api.anthropic.com")

    lines = _tool_block_sse("x", "tool", "{invalid")
    sse_body = "\n".join(lines) + "\n"

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=sse_body)
    )

    events = []
    async for ev in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
        events.append(ev)

    tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL]
    assert len(tool_events) == 1
    assert tool_events[0].tool_call.input == {}


@respx.mock
async def test_stream_input_json_delta_without_prior_block_start() -> None:
    """input_json_delta for unknown index is handled gracefully."""
    adapter = AnthropicAdapter(api_key="k", base_url="https://api.anthropic.com")

    # Send input_json_delta for index 99 without a prior content_block_start.
    delta = {"type": "input_json_delta", "partial_json": '{"k":"v"}'}
    usage = {"output_tokens": 1, "input_tokens": 1}
    lines = [
        _sse({"type": "content_block_delta", "index": 99, "delta": delta}),
        _sse({"type": "message_delta", "delta": {}, "usage": usage}),
        _sse({"type": "message_stop"}),
    ]
    sse_body = "\n".join(lines) + "\n"

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=sse_body)
    )

    events = []
    async for ev in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
        events.append(ev)

    # Should complete without crashing — just emits MESSAGE_DONE.
    done = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
    assert len(done) == 1


@respx.mock
async def test_stream_retry_exhausted_raises_llm_error() -> None:
    """Stream raises LLMError when all retries are exhausted (covers last-attempt close)."""
    adapter = AnthropicAdapter(
        api_key="k",
        base_url="https://api.anthropic.com",
        gateway_outage_window_seconds=0.05,
        retry_base_delay=0.01,
    )

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(503, text="unavailable")
    )

    with pytest.raises(LLMError):
        async for _ in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
            pass


@respx.mock
async def test_stream_retry_on_server_error() -> None:
    """Stream retries on 500 errors."""
    adapter = AnthropicAdapter(
        api_key="k",
        base_url="https://api.anthropic.com",
        gateway_outage_window_seconds=5.0,
        retry_base_delay=0.001,
    )

    sse_body = "\n".join(_text_block_sse("ok")) + "\n"

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=side_effect)

    events = []
    async for ev in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
        events.append(ev)

    assert call_count == 2
