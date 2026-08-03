"""Integration tests for provider adapters against mock HTTP endpoints."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from bifrost.adapters.anthropic import AnthropicAdapter
from bifrost.adapters.ollama import OllamaAdapter
from bifrost.adapters.openai_compat import OpenAICompatAdapter
from bifrost.translation.models import (
    AnthropicRequest,
    Message,
    TextBlock,
    ToolUseBlock,
)


def _oai_choice(content: str = "ok") -> dict:
    """Build a minimal OpenAI chat completion choice dict."""
    return {
        "index": 0,
        "message": {"role": "assistant", "content": content},
        "finish_reason": "stop",
    }


def _oai_usage(inp: int = 1, out: int = 1) -> dict:
    return {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}


def _simple_request(**kwargs) -> AnthropicRequest:
    defaults = {
        "model": "test-model",
        "max_tokens": 256,
        "messages": [Message(role="user", content="Hello")],
    }
    defaults.update(kwargs)
    return AnthropicRequest.model_validate(defaults)


# ---------------------------------------------------------------------------
# AnthropicAdapter
# ---------------------------------------------------------------------------


class TestAnthropicAdapter:
    def test_provider_specific_chat_template_kwargs_are_not_sent(self):
        adapter = AnthropicAdapter(api_key="test-key")
        request = _simple_request().model_copy(
            update={"chat_template_kwargs": {"force_nonempty_content": True}}
        )

        payload = adapter._build_payload(request, "claude-sonnet-4-20250514")

        assert "chat_template_kwargs" not in payload

    @pytest.mark.asyncio
    @respx.mock
    async def test_complete_success(self):
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=Response(
                200,
                json={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hi there!"}],
                    "model": "claude-sonnet-4-20250514",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 4},
                },
            )
        )
        adapter = AnthropicAdapter(api_key="test-key")
        req = _simple_request()
        resp = await adapter.complete(req, "claude-sonnet-4-20250514")

        assert resp.id == "msg_123"
        assert len(resp.content) == 1
        assert isinstance(resp.content[0], TextBlock)
        assert resp.content[0].text == "Hi there!"
        assert resp.usage.input_tokens == 5
        assert resp.usage.output_tokens == 4
        assert resp.stop_reason == "end_turn"

    @pytest.mark.asyncio
    @respx.mock
    async def test_complete_sends_api_key_header(self):
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=Response(
                200,
                json={
                    "id": "msg_x",
                    "content": [{"type": "text", "text": "ok"}],
                    "model": "test",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        )
        adapter = AnthropicAdapter(api_key="my-secret-key")
        await adapter.complete(_simple_request(), "test")
        assert route.calls[0].request.headers["x-api-key"] == "my-secret-key"

    @pytest.mark.asyncio
    @respx.mock
    async def test_complete_sends_anthropic_version_header(self):
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=Response(
                200,
                json={
                    "id": "msg_x",
                    "content": [{"type": "text", "text": "ok"}],
                    "model": "test",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        )
        adapter = AnthropicAdapter(api_key="key")
        await adapter.complete(_simple_request(), "test")
        assert "anthropic-version" in route.calls[0].request.headers

    @pytest.mark.asyncio
    @respx.mock
    async def test_complete_tool_use_response(self):
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=Response(
                200,
                json={
                    "id": "msg_tool",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_1",
                            "name": "search",
                            "input": {"query": "oslo"},
                        }
                    ],
                    "model": "claude",
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            )
        )
        adapter = AnthropicAdapter()
        resp = await adapter.complete(_simple_request(), "claude")
        assert isinstance(resp.content[0], ToolUseBlock)
        assert resp.content[0].name == "search"

    @pytest.mark.asyncio
    @respx.mock
    async def test_complete_http_error_propagates(self):
        import httpx

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=Response(401, json={"error": "Unauthorised"})
        )
        adapter = AnthropicAdapter(api_key="bad-key")
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.complete(_simple_request(), "claude")

    @pytest.mark.asyncio
    async def test_close(self):
        adapter = AnthropicAdapter()
        await adapter.close()  # Should not raise.

    @pytest.mark.asyncio
    @respx.mock
    async def test_custom_base_url(self):
        route = respx.post("http://localhost:8080/v1/messages").mock(
            return_value=Response(
                200,
                json={
                    "id": "msg_x",
                    "content": [{"type": "text", "text": "ok"}],
                    "model": "test",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        )
        adapter = AnthropicAdapter(base_url="http://localhost:8080")
        await adapter.complete(_simple_request(), "test")
        assert route.called


# ---------------------------------------------------------------------------
# OpenAICompatAdapter
# ---------------------------------------------------------------------------


class TestOpenAICompatAdapter:
    @pytest.mark.asyncio
    @respx.mock
    async def test_complete_success(self):
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "chatcmpl-abc",
                    "object": "chat.completion",
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello!"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
                },
            )
        )
        adapter = OpenAICompatAdapter(api_key="sk-test")
        resp = await adapter.complete(_simple_request(), "gpt-4o")

        assert isinstance(resp.content[0], TextBlock)
        assert resp.content[0].text == "Hello!"
        assert resp.stop_reason == "end_turn"
        assert resp.usage.input_tokens == 5

    @pytest.mark.asyncio
    @respx.mock
    async def test_bearer_auth_header(self):
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "c1",
                    "choices": [_oai_choice("x")],
                    "usage": _oai_usage(),
                },
            )
        )
        adapter = OpenAICompatAdapter(api_key="my-oai-key")
        await adapter.complete(_simple_request(), "gpt-4o")
        assert route.calls[0].request.headers["authorization"] == "Bearer my-oai-key"

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_auth_header_when_no_key(self):
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "c1",
                    "choices": [_oai_choice("x")],
                    "usage": _oai_usage(),
                },
            )
        )
        adapter = OpenAICompatAdapter()
        await adapter.complete(_simple_request(), "gpt-4o")
        assert "authorization" not in route.calls[0].request.headers

    @pytest.mark.asyncio
    @respx.mock
    async def test_custom_base_url(self):
        route = respx.post("http://vllm.local/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "c1",
                    "choices": [_oai_choice("ok")],
                    "usage": _oai_usage(),
                },
            )
        )
        adapter = OpenAICompatAdapter(base_url="http://vllm.local")
        await adapter.complete(_simple_request(), "mistral-7b")
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_system_prompt_injected(self):
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "c1",
                    "choices": [_oai_choice("ok")],
                    "usage": _oai_usage(),
                },
            )
        )
        adapter = OpenAICompatAdapter(api_key="key")
        req = _simple_request(system="You are a pirate.")
        await adapter.complete(req, "gpt-4o")
        body = json.loads(route.calls[0].request.content)
        assert body["messages"][0] == {"role": "system", "content": "You are a pirate."}

    @pytest.mark.asyncio
    async def test_close(self):
        adapter = OpenAICompatAdapter()
        await adapter.close()


# ---------------------------------------------------------------------------
# OllamaAdapter
# ---------------------------------------------------------------------------


class TestOllamaAdapter:
    @pytest.mark.asyncio
    @respx.mock
    async def test_complete_success(self):
        respx.post("http://localhost:11434/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "ollama-1",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Bonjour!"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": _oai_usage(3, 2),
                },
            )
        )
        adapter = OllamaAdapter()
        resp = await adapter.complete(_simple_request(), "llama3.1:8b")
        assert isinstance(resp.content[0], TextBlock)
        assert resp.content[0].text == "Bonjour!"

    @pytest.mark.asyncio
    @respx.mock
    async def test_top_p_stripped(self):
        route = respx.post("http://localhost:11434/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "x",
                    "choices": [_oai_choice("ok")],
                    "usage": _oai_usage(),
                },
            )
        )
        adapter = OllamaAdapter()
        req = _simple_request(top_p=0.9)
        await adapter.complete(req, "llama3.1:8b")
        body = json.loads(route.calls[0].request.content)
        assert "top_p" not in body

    @pytest.mark.asyncio
    @respx.mock
    async def test_custom_base_url(self):
        route = respx.post("http://remote-ollama:11434/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={
                    "id": "x",
                    "choices": [_oai_choice("ok")],
                    "usage": _oai_usage(),
                },
            )
        )
        adapter = OllamaAdapter(base_url="http://remote-ollama:11434")
        await adapter.complete(_simple_request(), "llama3.1:8b")
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_top_p_stripped_in_streaming(self):
        sse_body = (
            'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"hi"},'
            '"finish_reason":null}]}\n\n'
            "data: [DONE]\n\n"
        )
        route = respx.post("http://localhost:11434/v1/chat/completions").mock(
            return_value=Response(200, text=sse_body)
        )
        adapter = OllamaAdapter()
        req = _simple_request(top_p=0.9)
        chunks = []
        async for chunk in adapter.stream(req, "llama3.1:8b"):
            chunks.append(chunk)
        body = json.loads(route.calls[0].request.content)
        assert "top_p" not in body


# ---------------------------------------------------------------------------
# Streaming adapters
# ---------------------------------------------------------------------------


class TestAnthropicAdapterStreaming:
    @pytest.mark.asyncio
    @respx.mock
    async def test_stream_yields_lines(self):
        sse_body = (
            "event: message_start\n"
            'data: {"type":"message_start","message":{"id":"m1"}}\n\n'
            "event: message_stop\n"
            'data: {"type":"message_stop"}\n\n'
        )
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=Response(200, text=sse_body)
        )
        adapter = AnthropicAdapter(api_key="key")
        chunks = []
        async for chunk in adapter.stream(_simple_request(), "claude"):
            chunks.append(chunk)
        assert any("message_start" in c for c in chunks)

    @pytest.mark.asyncio
    @respx.mock
    async def test_stream_http_error_propagates(self):
        import httpx

        respx.post("https://api.anthropic.com/v1/messages").mock(return_value=Response(401))
        adapter = AnthropicAdapter(api_key="bad")
        with pytest.raises(httpx.HTTPStatusError):
            async for _ in adapter.stream(_simple_request(), "claude"):
                pass


class TestOpenAICompatAdapterStreaming:
    @pytest.mark.asyncio
    @respx.mock
    async def test_stream_yields_anthropic_events(self):
        sse_body = (
            'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"hi"},'
            '"finish_reason":null}]}\n\n'
            "data: [DONE]\n\n"
        )
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=Response(200, text=sse_body)
        )
        adapter = OpenAICompatAdapter(api_key="key")
        chunks = []
        async for chunk in adapter.stream(_simple_request(), "gpt-4o"):
            chunks.append(chunk)
        payload = json.loads(respx.calls[0].request.content)
        assert payload["stream_options"] == {"include_usage": True}
        # Should contain at least a message_start event.
        all_text = "".join(chunks)
        assert "message_start" in all_text

    @pytest.mark.asyncio
    @respx.mock
    async def test_ollama_stream_omits_unsupported_stream_options(self):
        route = respx.post("http://localhost:11434/v1/chat/completions").mock(
            return_value=Response(200, text="data: [DONE]\n\n")
        )
        adapter = OllamaAdapter()

        async for _ in adapter.stream(_simple_request(), "llama3.1:8b"):
            pass

        assert "stream_options" not in json.loads(route.calls[0].request.content)
