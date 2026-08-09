"""Tests for BifrostAdapter — Niuu's centralised LLM proxy adapter."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ravn.adapters.llm.bifrost import (
    ANTHROPIC_API_VERSION,
    HEADER_AGENT_ID,
    HEADER_SESSION_ID,
    BifrostAdapter,
)
from ravn.domain.exceptions import LLMError
from ravn.domain.models import StopReason, StreamEventType

_BIFROST_URL = "http://bifrost:8080/v1/messages"
_BASE_URL = "http://bifrost:8080"


# ---------------------------------------------------------------------------
# Construction / configuration
# ---------------------------------------------------------------------------


class TestBifrostAdapterInit:
    def test_defaults(self) -> None:
        adapter = BifrostAdapter()
        assert "bifrost" in adapter._base_url
        assert adapter._default_max_tokens > 0
        assert adapter._max_retries >= 0
        assert adapter._agent_id == ""
        assert adapter._session_id == ""

    def test_custom_base_url_trailing_slash_stripped(self) -> None:
        adapter = BifrostAdapter(base_url="http://proxy:9090/")
        assert not adapter._base_url.endswith("/")

    def test_agent_and_session_ids_stored(self) -> None:
        adapter = BifrostAdapter(agent_id="agent-1", session_id="sess-abc")
        assert adapter._agent_id == "agent-1"
        assert adapter._session_id == "sess-abc"

    def test_api_key_not_used(self) -> None:
        """BifrostAdapter stores no meaningful api_key — header is absent."""
        adapter = BifrostAdapter()
        # _api_key exists (inherited) but is empty and excluded from headers
        assert adapter._api_key == ""
        assert "x-api-key" not in adapter._headers()


# ---------------------------------------------------------------------------
# Header generation
# ---------------------------------------------------------------------------


class TestBifrostAdapterHeaders:
    def test_no_api_key_in_headers(self) -> None:
        adapter = BifrostAdapter()
        headers = adapter._headers()
        assert "x-api-key" not in headers

    def test_anthropic_version_present(self) -> None:
        adapter = BifrostAdapter()
        headers = adapter._headers()
        assert headers["anthropic-version"] == ANTHROPIC_API_VERSION

    def test_beta_header_for_caching(self) -> None:
        adapter = BifrostAdapter()
        headers = adapter._headers()
        assert "anthropic-beta" in headers

    def test_agent_id_header_injected(self) -> None:
        adapter = BifrostAdapter(agent_id="ravn-42")
        headers = adapter._headers()
        assert headers[HEADER_AGENT_ID] == "ravn-42"

    def test_session_id_header_injected(self) -> None:
        adapter = BifrostAdapter(session_id="sess-99")
        headers = adapter._headers()
        assert headers[HEADER_SESSION_ID] == "sess-99"

    def test_identity_headers_absent_when_empty(self) -> None:
        adapter = BifrostAdapter()
        headers = adapter._headers()
        assert HEADER_AGENT_ID not in headers
        assert HEADER_SESSION_ID not in headers


# ---------------------------------------------------------------------------
# System prompt building
# ---------------------------------------------------------------------------


class TestBifrostAdapterBuildSystem:
    def test_empty_string_returns_empty_list(self) -> None:
        adapter = BifrostAdapter()
        assert adapter._build_system("") == []

    def test_string_wrapped_with_cache_control(self) -> None:
        adapter = BifrostAdapter()
        blocks = adapter._build_system("You are helpful.")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "You are helpful."
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_block_list_passed_through(self) -> None:
        adapter = BifrostAdapter()
        blocks = [{"type": "text", "text": "custom", "cache_control": {"type": "ephemeral"}}]
        result = adapter._build_system(blocks)
        assert result is blocks


# ---------------------------------------------------------------------------
# Request body building
# ---------------------------------------------------------------------------


class TestBifrostAdapterBuildRequest:
    def test_includes_tools_when_provided(self) -> None:
        adapter = BifrostAdapter()
        tools = [{"name": "echo", "description": "...", "input_schema": {}}]
        body = adapter._build_request(
            [{"role": "user", "content": "hi"}],
            tools=tools,
            system="sys",
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            stream=False,
        )
        assert "tools" in body
        assert body["tools"] == tools

    def test_omits_tools_when_empty(self) -> None:
        adapter = BifrostAdapter()
        body = adapter._build_request(
            [],
            tools=[],
            system="",
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            stream=False,
        )
        assert "tools" not in body

    def test_stream_flag_set(self) -> None:
        adapter = BifrostAdapter()
        body = adapter._build_request(
            [], tools=[], system="", model="m", max_tokens=100, stream=True
        )
        assert body["stream"] is True

    def test_omits_system_when_empty(self) -> None:
        adapter = BifrostAdapter()
        body = adapter._build_request(
            [], tools=[], system="", model="m", max_tokens=100, stream=False
        )
        assert "system" not in body


# ---------------------------------------------------------------------------
# generate() — non-streaming
# ---------------------------------------------------------------------------


@respx.mock
async def test_generate_success() -> None:
    adapter = BifrostAdapter(base_url=_BASE_URL)

    response_body = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello from Bifrost!"}],
        "model": "claude-sonnet-4-20250514",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    respx.post(_BIFROST_URL).mock(return_value=httpx.Response(200, json=response_body))

    result = await adapter.generate(
        [{"role": "user", "content": "Hi"}],
        tools=[],
        system="",
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
    )

    assert result.content == "Hello from Bifrost!"
    assert result.stop_reason == StopReason.END_TURN
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.tool_calls == []


@respx.mock
async def test_generate_with_tool_use() -> None:
    adapter = BifrostAdapter(base_url=_BASE_URL)

    response_body = {
        "id": "msg_456",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "tu1",
                "name": "bash",
                "input": {"command": "ls"},
            }
        ],
        "model": "claude-sonnet-4-20250514",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 15, "output_tokens": 8},
    }

    respx.post(_BIFROST_URL).mock(return_value=httpx.Response(200, json=response_body))

    result = await adapter.generate(
        [{"role": "user", "content": "list files"}],
        tools=[],
        system="",
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
    )

    assert result.stop_reason == StopReason.TOOL_USE
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "bash"
    assert result.tool_calls[0].input == {"command": "ls"}


@respx.mock
async def test_generate_cache_tokens_parsed() -> None:
    adapter = BifrostAdapter(base_url=_BASE_URL)

    response_body = {
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 5,
        },
    }

    respx.post(_BIFROST_URL).mock(return_value=httpx.Response(200, json=response_body))

    result = await adapter.generate([], tools=[], system="", model="m", max_tokens=100)

    assert result.usage.cache_read_tokens == 80
    assert result.usage.cache_write_tokens == 5


@respx.mock
async def test_generate_error_response() -> None:
    adapter = BifrostAdapter(base_url=_BASE_URL, max_retries=0)

    respx.post(_BIFROST_URL).mock(return_value=httpx.Response(400, text="Bad request"))

    with pytest.raises(LLMError) as exc_info:
        await adapter.generate(
            [{"role": "user", "content": "hi"}],
            tools=[],
            system="",
            model="m",
            max_tokens=1024,
        )

    assert exc_info.value.status_code == 400


@respx.mock
async def test_generate_unknown_stop_reason_defaults_to_end_turn() -> None:
    adapter = BifrostAdapter(base_url=_BASE_URL)

    response_body = {
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "future_unknown_reason",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    respx.post(_BIFROST_URL).mock(return_value=httpx.Response(200, json=response_body))

    result = await adapter.generate([], tools=[], system="", model="m", max_tokens=100)
    assert result.stop_reason == StopReason.END_TURN


@respx.mock
async def test_generate_sends_identity_headers() -> None:
    adapter = BifrostAdapter(base_url=_BASE_URL, agent_id="ravn-1", session_id="sess-42")

    response_body = {
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    route = respx.post(_BIFROST_URL).mock(return_value=httpx.Response(200, json=response_body))

    await adapter.generate([], tools=[], system="", model="m", max_tokens=100)

    assert route.called
    sent_headers = route.calls[0].request.headers
    assert sent_headers[HEADER_AGENT_ID] == "ravn-1"
    assert sent_headers[HEADER_SESSION_ID] == "sess-42"


# ---------------------------------------------------------------------------
# stream() — SSE parsing
# ---------------------------------------------------------------------------


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event)


def _text_block_sse(text: str, usage: dict | None = None) -> list[str]:
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
        _sse(
            {
                "type": "message_delta",
                "delta": {},
                "usage": usage or {"output_tokens": 5, "input_tokens": 3},
            }
        ),
    ]


def _tool_block_sse(
    tool_id: str, name: str, chunks: list[str], usage: dict | None = None
) -> list[str]:
    lines = [
        _sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": tool_id, "name": name},
            }
        ),
    ]
    for chunk in chunks:
        lines.append(
            _sse(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": chunk},
                }
            )
        )
    lines.append(_sse({"type": "content_block_stop", "index": 0}))
    lines.append(
        _sse(
            {
                "type": "message_delta",
                "delta": {},
                "usage": usage or {"output_tokens": 10, "input_tokens": 5},
            }
        )
    )
    return lines


class TestBifrostStreamParsing:
    @respx.mock
    async def test_stream_text_delta(self) -> None:
        adapter = BifrostAdapter(base_url=_BASE_URL)

        lines = _text_block_sse("Hello", {"output_tokens": 5, "input_tokens": 3})
        lines.append("data: [DONE]")
        sse_body = "\n".join(lines) + "\n"

        respx.post(_BIFROST_URL).mock(
            return_value=httpx.Response(
                200, text=sse_body, headers={"content-type": "text/event-stream"}
            )
        )

        events = []
        async for event in adapter.stream(
            [{"role": "user", "content": "hi"}],
            tools=[],
            system="",
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
        ):
            events.append(event)

        text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        assert len(text_events) >= 1
        assert text_events[0].text == "Hello"

        done_events = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
        assert len(done_events) == 1
        assert done_events[0].usage is not None
        assert done_events[0].usage.output_tokens == 5

    @respx.mock
    async def test_stream_tool_call(self) -> None:
        adapter = BifrostAdapter(base_url=_BASE_URL)

        lines = _tool_block_sse("tu1", "bash", ['{"command":', '"ls"}'])
        lines.append("data: [DONE]")
        sse_body = "\n".join(lines) + "\n"

        respx.post(_BIFROST_URL).mock(
            return_value=httpx.Response(
                200, text=sse_body, headers={"content-type": "text/event-stream"}
            )
        )

        events = []
        async for event in adapter.stream(
            [{"role": "user", "content": "list"}],
            tools=[],
            system="",
            model="m",
            max_tokens=1024,
        ):
            events.append(event)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.name == "bash"
        assert tool_events[0].tool_call.input == {"command": "ls"}

    @respx.mock
    async def test_stream_error_response(self) -> None:
        adapter = BifrostAdapter(base_url=_BASE_URL, max_retries=0)

        respx.post(_BIFROST_URL).mock(return_value=httpx.Response(500, text="server error"))

        with pytest.raises(LLMError):
            async for _ in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
                pass

    @respx.mock
    async def test_stream_skips_non_data_lines(self) -> None:
        adapter = BifrostAdapter(base_url=_BASE_URL)

        lines = ["event: message_start", ""] + _text_block_sse("Hi")
        sse_body = "\n".join(lines) + "\n"

        respx.post(_BIFROST_URL).mock(return_value=httpx.Response(200, text=sse_body))

        events = []
        async for ev in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
            events.append(ev)

        assert len(events) > 0

    @respx.mock
    async def test_stream_with_cache_usage(self) -> None:
        adapter = BifrostAdapter(base_url=_BASE_URL)

        lines = _text_block_sse(
            "ok",
            {
                "output_tokens": 3,
                "input_tokens": 50,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 2,
            },
        )
        sse_body = "\n".join(lines) + "\n"

        respx.post(_BIFROST_URL).mock(return_value=httpx.Response(200, text=sse_body))

        events = []
        async for ev in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
            events.append(ev)

        done_events = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
        assert len(done_events) == 1
        assert done_events[0].usage is not None
        assert done_events[0].usage.cache_read_tokens == 40
        assert done_events[0].usage.cache_write_tokens == 2

    @respx.mock
    async def test_stream_sends_identity_headers(self) -> None:
        adapter = BifrostAdapter(base_url=_BASE_URL, agent_id="ravn-7", session_id="sess-1")

        lines = _text_block_sse("hi")
        lines.append("data: [DONE]")
        sse_body = "\n".join(lines) + "\n"

        route = respx.post(_BIFROST_URL).mock(
            return_value=httpx.Response(
                200, text=sse_body, headers={"content-type": "text/event-stream"}
            )
        )

        async for _ in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
            pass

        assert route.called
        sent_headers = route.calls[0].request.headers
        assert sent_headers[HEADER_AGENT_ID] == "ravn-7"
        assert sent_headers[HEADER_SESSION_ID] == "sess-1"


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class TestBifrostRetry:
    @respx.mock
    async def test_retries_on_503(self) -> None:
        adapter = BifrostAdapter(base_url=_BASE_URL, max_retries=1, retry_base_delay=0.0)

        response_body = {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

        respx.post(_BIFROST_URL).mock(
            side_effect=[
                httpx.Response(503, text="unavailable"),
                httpx.Response(200, json=response_body),
            ]
        )

        result = await adapter.generate([], tools=[], system="", model="m", max_tokens=100)
        assert result.content == "ok"

    @respx.mock
    async def test_raises_after_exhausted_retries(self) -> None:
        adapter = BifrostAdapter(base_url=_BASE_URL, max_retries=1, retry_base_delay=0.0)

        respx.post(_BIFROST_URL).mock(
            side_effect=[
                httpx.Response(503, text="unavailable"),
                httpx.Response(503, text="unavailable"),
            ]
        )

        with pytest.raises(LLMError):
            await adapter.generate([], tools=[], system="", model="m", max_tokens=100)


# ---------------------------------------------------------------------------
# Fallback chain integration
# ---------------------------------------------------------------------------
