"""Tests for the AnthropicAdapter."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ravn.adapters.llm.anthropic import AnthropicAdapter
from ravn.domain.exceptions import LLMError
from ravn.domain.models import StopReason, StreamEventType


class TestAnthropicAdapterInit:
    def test_defaults(self) -> None:
        adapter = AnthropicAdapter(api_key="test")
        assert adapter._api_key == "test"
        assert adapter._base_url == "https://api.anthropic.com"
        assert adapter._default_max_tokens > 0
        assert adapter._max_retries >= 0

    def test_custom_base_url_trailing_slash_stripped(self) -> None:
        adapter = AnthropicAdapter(api_key="k", base_url="http://proxy/")
        assert not adapter._base_url.endswith("/")


class TestAnthropicAdapterHeaders:
    def test_headers_include_api_key(self) -> None:
        adapter = AnthropicAdapter(api_key="my-key")
        headers = adapter._headers()
        assert headers["x-api-key"] == "my-key"
        assert "anthropic-version" in headers

    def test_headers_include_beta_for_caching(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        headers = adapter._headers()
        assert "anthropic-beta" in headers


class TestAnthropicAdapterBuildSystem:
    def test_empty_system(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        assert adapter._build_system("") == []

    def test_system_with_cache_control(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        blocks = adapter._build_system("You are helpful.")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "You are helpful."
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}


class TestAnthropicAdapterBuildRequest:
    def test_includes_tools_when_provided(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        tools = [{"name": "echo", "description": "...", "input_schema": {}}]
        body = adapter._build_request(
            [{"role": "user", "content": "hi"}],
            tools=tools,
            system="sys",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            stream=False,
        )
        assert "tools" in body
        assert body["tools"] == tools

    def test_omits_tools_when_empty(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        body = adapter._build_request(
            [], tools=[], system="", model="claude-sonnet-4-6", max_tokens=1024, stream=False
        )
        assert "tools" not in body

    def test_stream_flag(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        body = adapter._build_request(
            [], tools=[], system="", model="m", max_tokens=100, stream=True
        )
        assert body["stream"] is True

    def test_tool_result_keeps_only_schema_keys(self) -> None:
        """A tool_result carrying extra keys is a 400 — prune before sending."""
        adapter = AnthropicAdapter(api_key="k")
        body = adapter._build_request(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "name": "read_file",
                            "content": "ok",
                            "is_error": False,
                        }
                    ],
                }
            ],
            tools=[],
            system="",
            model="m",
            max_tokens=100,
            stream=False,
        )
        block = body["messages"][0]["content"][0]
        assert "name" not in block
        assert block == {
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": "ok",
            "is_error": False,
        }

    def test_other_blocks_pass_through(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        body = adapter._build_request(
            [
                {
                    "role": "assistant",
                    "reasoning": "internal",
                    "content": [
                        {"type": "text", "text": "hi"},
                        {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {}},
                    ],
                }
            ],
            tools=[],
            system="",
            model="m",
            max_tokens=100,
            stream=False,
        )
        message = body["messages"][0]
        assert "reasoning" not in message
        assert message["content"][0] == {"type": "text", "text": "hi"}
        assert message["content"][1]["name"] == "read_file"

    def test_thinking_budget_becomes_adaptive_on_current_models(self) -> None:
        """budget_tokens is a 400 from Opus 4.7 onward."""
        adapter = AnthropicAdapter(api_key="k")
        for model in ("claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-fable-5"):
            body = adapter._build_request(
                [],
                tools=[],
                system="",
                model=model,
                max_tokens=100,
                stream=False,
                thinking={"type": "enabled", "budget_tokens": 8000},
            )
            assert body["thinking"] == {"type": "adaptive"}, model

    def test_thinking_budget_kept_on_legacy_models(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        for model in ("claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5"):
            body = adapter._build_request(
                [],
                tools=[],
                system="",
                model=model,
                max_tokens=100,
                stream=False,
                thinking={"type": "enabled", "budget_tokens": 8000},
            )
            assert body["thinking"] == {"type": "enabled", "budget_tokens": 8000}, model

    def test_unknown_model_defaults_to_adaptive(self) -> None:
        """A model released after this code was written must not get budget_tokens."""
        adapter = AnthropicAdapter(api_key="k")
        body = adapter._build_request(
            [],
            tools=[],
            system="",
            model="claude-something-7",
            max_tokens=100,
            stream=False,
            thinking={"type": "enabled", "budget_tokens": 8000},
        )
        assert body["thinking"] == {"type": "adaptive"}

    def test_adaptive_passed_through_unchanged(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        body = adapter._build_request(
            [],
            tools=[],
            system="",
            model="claude-opus-5",
            max_tokens=100,
            stream=False,
            thinking={"type": "adaptive"},
        )
        assert body["thinking"] == {"type": "adaptive"}

    def test_interleaved_beta_only_for_legacy_thinking(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        current = adapter._headers(thinking_enabled=True, model="claude-opus-5")
        legacy = adapter._headers(thinking_enabled=True, model="claude-sonnet-4-5")
        assert "interleaved-thinking" not in current["anthropic-beta"]
        assert "interleaved-thinking" in legacy["anthropic-beta"]

    def test_string_content_untouched(self) -> None:
        adapter = AnthropicAdapter(api_key="k")
        body = adapter._build_request(
            [{"role": "user", "content": "plain"}],
            tools=[],
            system="",
            model="m",
            max_tokens=100,
            stream=False,
        )
        assert body["messages"][0]["content"] == "plain"


@respx.mock
async def test_generate_success() -> None:
    adapter = AnthropicAdapter(api_key="test-key", base_url="https://api.anthropic.com")

    response_body = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello!"}],
        "model": "claude-sonnet-4-6",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=response_body)
    )

    result = await adapter.generate(
        [{"role": "user", "content": "Hi"}],
        tools=[],
        system="",
        model="claude-sonnet-4-6",
        max_tokens=1024,
    )

    assert result.content == "Hello!"
    assert result.stop_reason == StopReason.END_TURN
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.tool_calls == []


@respx.mock
async def test_generate_with_tool_use() -> None:
    adapter = AnthropicAdapter(api_key="test-key", base_url="https://api.anthropic.com")

    response_body = {
        "id": "msg_456",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "tu1", "name": "echo", "input": {"message": "hi"}},
        ],
        "model": "claude-sonnet-4-6",
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 15, "output_tokens": 8},
    }

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=response_body)
    )

    result = await adapter.generate(
        [{"role": "user", "content": "echo hi"}],
        tools=[],
        system="",
        model="claude-sonnet-4-6",
        max_tokens=1024,
    )

    assert result.stop_reason == StopReason.TOOL_USE
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "echo"
    assert result.tool_calls[0].input == {"message": "hi"}


@respx.mock
async def test_generate_error_response() -> None:
    adapter = AnthropicAdapter(
        api_key="test-key", base_url="https://api.anthropic.com", max_retries=0
    )

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(400, text="Bad request")
    )

    with pytest.raises(LLMError) as exc_info:
        await adapter.generate(
            [{"role": "user", "content": "hi"}],
            tools=[],
            system="",
            model="claude-sonnet-4-6",
            max_tokens=1024,
        )

    assert exc_info.value.status_code == 400


@respx.mock
async def test_generate_unknown_stop_reason() -> None:
    adapter = AnthropicAdapter(api_key="test-key", base_url="https://api.anthropic.com")

    response_body = {
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "future_unknown_reason",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=response_body)
    )

    result = await adapter.generate([], tools=[], system="", model="m", max_tokens=100)
    assert result.stop_reason == StopReason.END_TURN


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event)


def _text_block_sse(text: str, usage: dict | None = None) -> list[str]:
    lines = [
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
    return lines


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


class TestStreamParsing:
    """Tests for SSE stream parsing logic."""

    @respx.mock
    async def test_stream_text_delta(self) -> None:
        adapter = AnthropicAdapter(api_key="k", base_url="https://api.anthropic.com")

        lines = _text_block_sse("Hello", {"output_tokens": 5, "input_tokens": 3})
        lines.append("data: [DONE]")
        sse_body = "\n".join(lines) + "\n"

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200, text=sse_body, headers={"content-type": "text/event-stream"}
            )
        )

        events = []
        async for event in adapter.stream(
            [{"role": "user", "content": "hi"}],
            tools=[],
            system="",
            model="claude-sonnet-4-6",
            max_tokens=1024,
        ):
            events.append(event)

        text_events = [e for e in events if e.type == StreamEventType.TEXT_DELTA]
        assert len(text_events) >= 1
        assert text_events[0].text == "Hello"

        done_events = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
        assert len(done_events) == 1
        assert done_events[0].usage is not None

    @respx.mock
    async def test_stream_tool_call(self) -> None:
        adapter = AnthropicAdapter(api_key="k", base_url="https://api.anthropic.com")

        lines = _tool_block_sse("tu1", "echo", ['{"message":', '"hi"}'])
        lines.append("data: [DONE]")
        sse_body = "\n".join(lines) + "\n"

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200, text=sse_body, headers={"content-type": "text/event-stream"}
            )
        )

        events = []
        async for event in adapter.stream(
            [{"role": "user", "content": "echo hi"}],
            tools=[],
            system="",
            model="claude-sonnet-4-6",
            max_tokens=1024,
        ):
            events.append(event)

        tool_events = [e for e in events if e.type == StreamEventType.TOOL_CALL]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.name == "echo"
        assert tool_events[0].tool_call.input == {"message": "hi"}

    @respx.mock
    async def test_stream_error_response(self) -> None:
        adapter = AnthropicAdapter(api_key="k", base_url="https://api.anthropic.com", max_retries=0)

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(500, text="server error")
        )

        with pytest.raises(LLMError):
            async for _ in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
                pass

    @respx.mock
    async def test_stream_skips_non_data_lines(self) -> None:
        adapter = AnthropicAdapter(api_key="k", base_url="https://api.anthropic.com")

        lines = ["event: message_start", ""] + _text_block_sse("Hi")
        sse_body = "\n".join(lines) + "\n"

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, text=sse_body)
        )

        events = []
        async for ev in adapter.stream([], tools=[], system="", model="m", max_tokens=100):
            events.append(ev)

        assert len(events) > 0
