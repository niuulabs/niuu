"""NIU-474 — Bifröst architecture audit verification tests.

Exercises the specific items from the audit checklist and fills the remaining
coverage gaps identified during the audit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from bifrost import catalog as bifrost_catalog
from bifrost.adapters.memory_store import MemoryUsageStore
from bifrost.app import create_app
from bifrost.auth import AgentIdentity
from bifrost.config import BifrostConfig, ProviderConfig, RoutingStrategy
from bifrost.domain.models import TokenUsage
from bifrost.inbound.tracking import _extract_usage_from_sse_line, _stream_with_tracking
from bifrost.ports.provider import ProviderError, ProviderPort
from bifrost.router import ModelRouter, RouterError, _load_adapter
from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    Message,
    TextBlock,
    ToolChoiceAny,
    ToolChoiceTool,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
    UsageInfo,
)
from bifrost.translation.to_openai import (
    _content_to_openai_text,
    _message_to_openai,
    anthropic_to_openai,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(model: str = "gpt-4o") -> AnthropicRequest:
    return AnthropicRequest(
        model=model,
        max_tokens=100,
        messages=[Message(role="user", content="Hello")],
    )


def _make_response(text: str = "OK") -> AnthropicResponse:
    return AnthropicResponse(
        id="msg_test",
        content=[TextBlock(text=text)],
        model="gpt-4o",
        stop_reason="end_turn",
        usage=UsageInfo(input_tokens=5, output_tokens=3),
    )


# ---------------------------------------------------------------------------
# Inbound layer — POST /v1/messages, GET /v1/models, streaming headers
# ---------------------------------------------------------------------------


class TestInboundLayer:
    """Audit: inbound HTTP layer is functional."""

    def _client(self) -> TestClient:
        config = BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o"])},
        )
        return TestClient(create_app(config))

    def test_messages_endpoint_exists(self):
        """POST /v1/messages returns 200 for a valid request."""
        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
            m.return_value = _make_response()
            with self._client() as client:
                resp = client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": "Hi"}],
                    },
                )
        assert resp.status_code == 200

    def test_models_endpoint_lists_all_models(self):
        """GET /v1/models returns the full Bifrost-owned catalog view."""
        config = BifrostConfig(
            providers={
                "anthropic": ProviderConfig(models=["claude-sonnet-4-6"]),
                "openai": ProviderConfig(models=["gpt-4o", "gpt-4o-mini"]),
            }
        )
        with TestClient(create_app(config)) as client:
            resp = client.get("/api/v1/bifrost/v1/models")
        assert resp.status_code == 200
        ids = {m["id"] for m in resp.json()["data"]}
        assert ids == {model.id for model in bifrost_catalog.list_models(config)}

    def test_streaming_response_has_non_buffering_headers(self):
        """Streaming response carries cache-control and x-accel-buffering headers."""

        async def _fake_stream(request):
            yield "data: [DONE]\n\n"

        with patch("bifrost.router.ModelRouter.stream", return_value=_fake_stream(None)):
            with self._client() as client:
                resp = client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": True,
                    },
                )
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("x-accel-buffering") == "no"

    def test_correlation_id_echoed_in_response(self):
        """X-Correlation-ID sent by the client is echoed back."""
        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
            m.return_value = _make_response()
            with self._client() as client:
                resp = client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": "Hi"}],
                    },
                    headers={"X-Correlation-ID": "test-correlation-id"},
                )
        assert resp.headers.get("x-correlation-id") == "test-correlation-id"


# ---------------------------------------------------------------------------
# SSE token tracking — _extract_usage_from_sse_line (app.py:40-49)
# ---------------------------------------------------------------------------


class TestExtractUsageFromSseLine:
    """Audit: token usage is extracted correctly from SSE events."""

    def test_message_start_populates_input_tokens(self):
        usage = TokenUsage()
        line = 'data: {"type":"message_start","message":{"usage":{"input_tokens":42}}}'
        _extract_usage_from_sse_line(line, usage)
        assert usage.input_tokens == 42

    def test_message_start_populates_cache_tokens(self):
        usage = TokenUsage()
        line = (
            'data: {"type":"message_start","message":{"usage":{'
            '"input_tokens":10,"cache_creation_input_tokens":5,'
            '"cache_read_input_tokens":3}}}'
        )
        _extract_usage_from_sse_line(line, usage)
        assert usage.input_tokens == 10
        assert usage.cache_creation_input_tokens == 5
        assert usage.cache_read_input_tokens == 3

    def test_message_delta_populates_output_tokens(self):
        usage = TokenUsage()
        line = 'data: {"type":"message_delta","usage":{"output_tokens":17}}'
        _extract_usage_from_sse_line(line, usage)
        assert usage.output_tokens == 17

    def test_message_delta_populates_translated_openai_usage(self):
        usage = TokenUsage()
        line = 'data: {"type":"message_delta","usage":{"input_tokens":19,"output_tokens":4}}'
        _extract_usage_from_sse_line(line, usage)
        assert usage.input_tokens == 19
        assert usage.output_tokens == 4

    def test_unknown_event_type_is_a_no_op(self):
        usage = TokenUsage()
        _extract_usage_from_sse_line('data: {"type":"content_block_start"}', usage)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_non_data_line_is_a_no_op(self):
        usage = TokenUsage()
        _extract_usage_from_sse_line("event: message_start", usage)
        assert usage.input_tokens == 0

    def test_invalid_json_is_ignored(self):
        usage = TokenUsage()
        _extract_usage_from_sse_line("data: not-valid-json", usage)
        assert usage.input_tokens == 0

    def test_multiline_event_chunk_populates_usage(self):
        usage = TokenUsage()
        _extract_usage_from_sse_line(
            'event: message_start\ndata: {"type":"message_start",'
            '"message":{"usage":{"input_tokens":42}}}\n',
            usage,
        )
        assert usage.input_tokens == 42


async def test_stream_usage_is_recorded_before_terminal_chunk_is_yielded():
    async def source():
        yield (
            'event: message_start\ndata: {"type":"message_start",'
            '"message":{"usage":{"input_tokens":12}}}\n\n'
            'event: message_delta\ndata: {"type":"message_delta",'
            '"usage":{"output_tokens":7}}\n\n'
            'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )

    store = MemoryUsageStore()
    emitter = AsyncMock()
    stream = _stream_with_tracking(
        source(),
        "nvidia/nemotron-3-super",
        0.0,
        AgentIdentity(agent_id="resident-1", tenant_id="tenant-1", session_id="session-1"),
        store,
        {},
        "request-1",
        emitter,
        provider="valaskjalf-nemotron",
    )

    await anext(stream)

    records = await store.query()
    assert len(records) == 1
    assert records[0].input_tokens == 12
    assert records[0].output_tokens == 7
    assert records[0].agent_id == "resident-1"


# ---------------------------------------------------------------------------
# _load_adapter — api_key and custom timeout paths (router.py:46,48)
# ---------------------------------------------------------------------------


class TestLoadAdapter:
    """Audit: adapter instantiation passes api_key and custom timeout."""

    def test_api_key_forwarded_to_adapter(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY_ENV", "sk-test-123")
        cfg = ProviderConfig(api_key_env="TEST_KEY_ENV", models=["gpt-4o"])
        adapter = _load_adapter("openai", cfg, "https://api.openai.com")
        assert adapter._api_key == "sk-test-123"  # type: ignore[attr-defined]

    def test_custom_timeout_forwarded_to_adapter(self):
        cfg = ProviderConfig(models=["gpt-4o"], timeout=60.0)
        adapter = _load_adapter("openai", cfg, "https://api.openai.com")
        # Timeout is stored in the underlying httpx client.
        client_timeout = adapter._client.timeout  # type: ignore[attr-defined]
        assert client_timeout.read == 60.0

    def test_chat_template_kwargs_forwarded_to_adapter(self):
        cfg = ProviderConfig(
            models=["nvidia/nemotron-3-super"],
            chat_template_kwargs={"force_nonempty_content": True},
        )

        adapter = _load_adapter("nemotron", cfg, "https://nemotron.example")

        assert adapter._chat_template_kwargs == {  # type: ignore[attr-defined]
            "force_nonempty_content": True
        }

    def test_unknown_provider_falls_back_to_openai_compat(self):
        from bifrost.adapters.openai_compat import OpenAICompatAdapter

        cfg = ProviderConfig(models=["my-model"], base_url="https://custom.llm/")
        adapter = _load_adapter("custom-provider", cfg, "https://custom.llm/")
        assert isinstance(adapter, OpenAICompatAdapter)


# ---------------------------------------------------------------------------
# Streaming failover on HTTP errors (router.py:145-168)
# ---------------------------------------------------------------------------


class FakeProvider(ProviderPort):
    def __init__(self, raises: Exception | None = None):
        self._raises = raises

    async def complete(self, request: AnthropicRequest, model: str) -> AnthropicResponse:
        if self._raises:
            raise self._raises
        return _make_response()

    async def stream(self, request: AnthropicRequest, model: str) -> AsyncIterator[str]:
        if self._raises:
            raise self._raises
        yield "data: ok\n\n"


class TestStreamingFailover:
    """Audit: stream() failover mirrors complete() failover behaviour."""

    @pytest.mark.asyncio
    async def test_stream_failover_on_http_503(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
        )
        router = ModelRouter(cfg)
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        router._adapters["openai"] = FakeProvider(
            raises=httpx.HTTPStatusError("fail", request=MagicMock(), response=mock_resp)
        )
        router._adapters["backup"] = FakeProvider()

        chunks = []
        async for chunk in router.stream(_make_request()):
            chunks.append(chunk)
        assert chunks == ["data: ok\n\n"]

    @pytest.mark.asyncio
    async def test_stream_non_retryable_http_error_is_raised(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
        )
        router = ModelRouter(cfg)
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        router._adapters["openai"] = FakeProvider(
            raises=httpx.HTTPStatusError("bad req", request=MagicMock(), response=mock_resp)
        )
        router._adapters["backup"] = FakeProvider()

        with pytest.raises(httpx.HTTPStatusError):
            async for _ in router.stream(_make_request()):
                pass

    @pytest.mark.asyncio
    async def test_stream_all_providers_fail_raises_router_error(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
        )
        router = ModelRouter(cfg)
        router._adapters["openai"] = FakeProvider(raises=ProviderError("down"))
        router._adapters["backup"] = FakeProvider(raises=ProviderError("also down"))

        with pytest.raises(RouterError, match="All providers failed"):
            async for _ in router.stream(_make_request()):
                pass


# ---------------------------------------------------------------------------
# to_openai.py coverage gaps — _content_to_openai_text, _message_to_openai
# ---------------------------------------------------------------------------


class TestContentToOpenaiText:
    """Audit: content flattening handles all block types."""

    def test_string_passthrough(self):
        assert _content_to_openai_text("hello") == "hello"

    def test_list_of_text_blocks(self):
        blocks = [TextBlock(text="foo"), TextBlock(text="bar")]
        assert _content_to_openai_text(blocks) == "foobar"

    def test_non_text_blocks_skipped(self):
        blocks = [
            TextBlock(text="before"),
            ToolUseBlock(id="t1", name="my_tool", input={}),
            TextBlock(text="after"),
        ]
        assert _content_to_openai_text(blocks) == "beforeafter"


class TestMessageToOpenai:
    """Audit: message translation handles all edge cases."""

    def test_empty_content_block_list_produces_empty_content(self):
        """Message with no text and no tool use → empty content string."""
        msg = Message(role="user", content=[])
        result = _message_to_openai(msg)
        assert result == [{"role": "user", "content": ""}]

    def test_tool_result_with_list_content(self):
        """ToolResultBlock with list[TextBlock] content is flattened."""
        msg = Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content=[TextBlock(text="result text")],
                )
            ],
        )
        result = _message_to_openai(msg)
        assert result[0]["role"] == "tool"
        assert result[0]["content"] == "result text"


class TestAnthropicToOpenaiEdgeCases:
    """Audit: anthropic_to_openai covers all optional field paths."""

    def test_tool_choice_any_maps_to_required(self):
        req = AnthropicRequest(
            model="gpt-4o",
            max_tokens=100,
            messages=[Message(role="user", content="hi")],
            tools=[ToolDefinition(name="my_tool", input_schema={})],
            tool_choice=ToolChoiceAny(),
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["tool_choice"] == "required"

    def test_tool_choice_specific_tool(self):
        req = AnthropicRequest(
            model="gpt-4o",
            max_tokens=100,
            messages=[Message(role="user", content="hi")],
            tools=[ToolDefinition(name="my_tool", input_schema={})],
            tool_choice=ToolChoiceTool(name="my_tool"),
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["tool_choice"] == {"type": "function", "function": {"name": "my_tool"}}

    def test_no_tool_choice_when_no_tools(self):
        req = AnthropicRequest(
            model="gpt-4o",
            max_tokens=100,
            messages=[Message(role="user", content="hi")],
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert "tool_choice" not in payload
        assert "tools" not in payload

    def test_system_blocks_concatenated(self):
        req = AnthropicRequest(
            model="gpt-4o",
            max_tokens=100,
            messages=[Message(role="user", content="hi")],
            system=[TextBlock(text="You are"), TextBlock(text=" helpful.")],
        )
        payload = anthropic_to_openai(req, "gpt-4o")
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "You are helpful."
