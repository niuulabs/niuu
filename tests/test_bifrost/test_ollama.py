"""Tests for the Ollama inbound interface (inbound/ollama.py and routes)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.inbound.ollama import (
    OllamaChatRequest,
    OllamaGenerateRequest,
    _done_reason,
    _ndjson,
    anthropic_response_to_ollama_chat,
    anthropic_response_to_ollama_generate,
    anthropic_stream_to_ollama_chat,
    anthropic_stream_to_ollama_generate,
    ollama_chat_to_anthropic,
    ollama_error_response,
    ollama_generate_to_anthropic,
)
from bifrost.translation.models import (
    AnthropicResponse,
    TextBlock,
    ThinkingBlock,
    UsageInfo,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(models: list[str] | None = None) -> BifrostConfig:
    return BifrostConfig(
        providers={"anthropic": ProviderConfig(models=models or ["claude-sonnet-4-6"])},
    )


def _make_response(
    text: str = "Hello!",
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
    model: str = "claude-sonnet-4-6",
) -> AnthropicResponse:
    return AnthropicResponse(
        id="msg_test",
        content=[TextBlock(text=text)],
        model=model,
        stop_reason=stop_reason,
        usage=UsageInfo(input_tokens=input_tokens, output_tokens=output_tokens),
    )


async def _async_iter(items: list[str]):
    for item in items:
        yield item


def _parse_ndjson_lines(body: str) -> list[dict]:
    """Parse an NDJSON response body into a list of dicts."""
    lines = []
    for line in body.splitlines():
        line = line.strip()
        if line:
            lines.append(json.loads(line))
    return lines


# ---------------------------------------------------------------------------
# Unit: _done_reason
# ---------------------------------------------------------------------------


class TestDoneReason:
    def test_end_turn_maps_to_stop(self):
        assert _done_reason("end_turn") == "stop"

    def test_max_tokens_maps_to_length(self):
        assert _done_reason("max_tokens") == "length"

    def test_tool_use_maps_to_stop(self):
        assert _done_reason("tool_use") == "stop"

    def test_stop_sequence_maps_to_stop(self):
        assert _done_reason("stop_sequence") == "stop"

    def test_none_defaults_to_stop(self):
        assert _done_reason(None) == "stop"

    def test_unknown_value_defaults_to_stop(self):
        assert _done_reason("unknown_reason") == "stop"


# ---------------------------------------------------------------------------
# Unit: _ndjson
# ---------------------------------------------------------------------------


class TestNdjson:
    def test_produces_newline_terminated_json(self):
        result = _ndjson({"model": "llama3", "done": False})
        assert result.endswith("\n")
        parsed = json.loads(result.strip())
        assert parsed == {"model": "llama3", "done": False}


# ---------------------------------------------------------------------------
# Unit: ollama_generate_to_anthropic
# ---------------------------------------------------------------------------


class TestOllamaGenerateToAnthropic:
    def _req(self, **kwargs) -> OllamaGenerateRequest:
        defaults = {"model": "claude-sonnet-4-6", "prompt": "Why is the sky blue?"}
        defaults.update(kwargs)
        return OllamaGenerateRequest.model_validate(defaults)

    def test_basic_prompt(self):
        result = ollama_generate_to_anthropic(self._req())
        assert result.model == "claude-sonnet-4-6"
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"
        assert result.messages[0].content == "Why is the sky blue?"

    def test_no_num_predict_is_no_cap(self):
        result = ollama_generate_to_anthropic(self._req())
        assert result.max_tokens is None

    def test_unlimited_num_predict_is_no_cap(self):
        for unlimited in (-1, -2):
            result = ollama_generate_to_anthropic(self._req(options={"num_predict": unlimited}))
            assert result.max_tokens is None

    def test_num_predict_sets_max_tokens(self):
        result = ollama_generate_to_anthropic(self._req(options={"num_predict": 512}))
        assert result.max_tokens == 512

    def test_system_forwarded(self):
        result = ollama_generate_to_anthropic(self._req(system="You are a helpful assistant."))
        assert result.system == "You are a helpful assistant."

    def test_temperature_forwarded(self):
        result = ollama_generate_to_anthropic(self._req(options={"temperature": 0.7}))
        assert result.temperature == 0.7

    def test_top_p_forwarded(self):
        result = ollama_generate_to_anthropic(self._req(options={"top_p": 0.9}))
        assert result.top_p == 0.9

    def test_top_k_forwarded(self):
        result = ollama_generate_to_anthropic(self._req(options={"top_k": 40}))
        assert result.top_k == 40

    def test_stop_forwarded(self):
        result = ollama_generate_to_anthropic(self._req(options={"stop": ["END", "STOP"]}))
        assert result.stop_sequences == ["END", "STOP"]

    def test_stream_forwarded(self):
        result = ollama_generate_to_anthropic(self._req(stream=False))
        assert result.stream is False

    def test_empty_prompt_gives_empty_messages(self):
        result = ollama_generate_to_anthropic(self._req(prompt=""))
        assert result.messages == []


# ---------------------------------------------------------------------------
# Unit: ollama_chat_to_anthropic
# ---------------------------------------------------------------------------


class TestOllamaChatToAnthropic:
    def _req(self, **kwargs) -> OllamaChatRequest:
        defaults = {
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        defaults.update(kwargs)
        return OllamaChatRequest.model_validate(defaults)

    def test_basic_user_message(self):
        result = ollama_chat_to_anthropic(self._req())
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"
        assert result.messages[0].content == "Hello"

    def test_system_message_extracted(self):
        result = ollama_chat_to_anthropic(
            self._req(
                messages=[
                    {"role": "system", "content": "Be concise."},
                    {"role": "user", "content": "Hi"},
                ]
            )
        )
        assert result.system == "Be concise."
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"

    def test_multiple_system_messages_joined(self):
        result = ollama_chat_to_anthropic(
            self._req(
                messages=[
                    {"role": "system", "content": "Rule one."},
                    {"role": "system", "content": "Rule two."},
                    {"role": "user", "content": "Hi"},
                ]
            )
        )
        assert result.system == "Rule one.\n\nRule two."

    def test_assistant_message_preserved(self):
        result = ollama_chat_to_anthropic(
            self._req(
                messages=[
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ]
            )
        )
        assert len(result.messages) == 2
        assert result.messages[1].role == "assistant"
        assert result.messages[1].content == "Hi there!"

    def test_no_system_when_absent(self):
        result = ollama_chat_to_anthropic(self._req())
        assert result.system is None

    def test_options_forwarded(self):
        result = ollama_chat_to_anthropic(
            self._req(options={"temperature": 0.5, "num_predict": 256})
        )
        assert result.temperature == 0.5
        assert result.max_tokens == 256

    def test_stream_forwarded(self):
        result = ollama_chat_to_anthropic(self._req(stream=False))
        assert result.stream is False


# ---------------------------------------------------------------------------
# Unit: anthropic_response_to_ollama_generate
# ---------------------------------------------------------------------------


class TestAnthropicResponseToOllamaGenerate:
    def test_basic_response(self):
        resp = _make_response(text="42")
        result = anthropic_response_to_ollama_generate(
            resp, created_at="2024-01-01T00:00:00Z", total_duration_ns=1_000_000
        )
        assert result["response"] == "42"
        assert result["done"] is True
        assert result["model"] == "claude-sonnet-4-6"
        assert result["done_reason"] == "stop"
        assert result["prompt_eval_count"] == 10
        assert result["eval_count"] == 5
        assert result["total_duration"] == 1_000_000

    def test_thinking_block_wrapped(self):
        resp = AnthropicResponse(
            id="msg_1",
            content=[ThinkingBlock(thinking="Let me think...")],
            model="claude-sonnet-4-6",
            stop_reason="end_turn",
            usage=UsageInfo(input_tokens=5, output_tokens=3),
        )
        result = anthropic_response_to_ollama_generate(
            resp, created_at="2024-01-01T00:00:00Z", total_duration_ns=0
        )
        assert "<thinking>Let me think...</thinking>" in result["response"]

    def test_max_tokens_reason(self):
        resp = _make_response(stop_reason="max_tokens")
        result = anthropic_response_to_ollama_generate(
            resp, created_at="2024-01-01T00:00:00Z", total_duration_ns=0
        )
        assert result["done_reason"] == "length"


# ---------------------------------------------------------------------------
# Unit: anthropic_response_to_ollama_chat
# ---------------------------------------------------------------------------


class TestAnthropicResponseToOllamaChat:
    def test_basic_response(self):
        resp = _make_response(text="Hello!")
        result = anthropic_response_to_ollama_chat(
            resp, created_at="2024-01-01T00:00:00Z", total_duration_ns=0
        )
        assert result["message"] == {"role": "assistant", "content": "Hello!"}
        assert result["done"] is True
        assert result["done_reason"] == "stop"

    def test_model_preserved(self):
        resp = _make_response(model="claude-opus-4-6")
        result = anthropic_response_to_ollama_chat(
            resp, created_at="2024-01-01T00:00:00Z", total_duration_ns=0
        )
        assert result["model"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# Unit: ollama_error_response
# ---------------------------------------------------------------------------


class TestOllamaErrorResponse:
    def test_error_shape(self):
        resp = ollama_error_response(422, "bad input")
        assert resp.status_code == 422
        body = json.loads(resp.body)
        assert body == {"error": "bad input"}


# ---------------------------------------------------------------------------
# Unit: streaming translators
# ---------------------------------------------------------------------------


class TestAnthropicStreamToOllamaGenerate:
    @pytest.mark.asyncio
    async def test_yields_text_chunks_and_final_done(self):
        sse_lines = [
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {"id": "m1", "usage": {"input_tokens": 8}},
                }
            )
            + "\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Hello"},
                }
            )
            + "\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": " world"},
                }
            )
            + "\n",
            "event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 3},
                }
            )
            + "\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n",
        ]

        chunks = []
        async for chunk in anthropic_stream_to_ollama_generate(
            _async_iter(sse_lines), model="claude-sonnet-4-6", start=0.0
        ):
            chunks.append(json.loads(chunk.strip()))

        # Intermediate text chunks (not done)
        text_chunks = [c for c in chunks if not c["done"]]
        assert len(text_chunks) == 2
        assert text_chunks[0]["response"] == "Hello"
        assert text_chunks[1]["response"] == " world"

        # Final done chunk
        done_chunks = [c for c in chunks if c["done"]]
        assert len(done_chunks) == 1
        assert done_chunks[0]["done_reason"] == "stop"
        assert done_chunks[0]["prompt_eval_count"] == 8
        assert done_chunks[0]["eval_count"] == 3
        assert done_chunks[0]["response"] == ""

    @pytest.mark.asyncio
    async def test_empty_stream_emits_done(self):
        chunks = []
        async for chunk in anthropic_stream_to_ollama_generate(
            _async_iter([]), model="claude-sonnet-4-6", start=0.0
        ):
            chunks.append(json.loads(chunk.strip()))
        assert len(chunks) == 1
        assert chunks[0]["done"] is True

    @pytest.mark.asyncio
    async def test_non_text_deltas_ignored(self):
        sse_lines = [
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '{"k":'},
                }
            )
            + "\n",
        ]
        chunks = []
        async for chunk in anthropic_stream_to_ollama_generate(
            _async_iter(sse_lines), model="claude-sonnet-4-6", start=0.0
        ):
            chunks.append(json.loads(chunk.strip()))
        # Only the final done chunk, no intermediate chunks for non-text deltas
        assert all(c["done"] for c in chunks)


class TestAnthropicStreamToOllamaChat:
    @pytest.mark.asyncio
    async def test_yields_message_chunks_and_final_done(self):
        sse_lines = [
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {"id": "m1", "usage": {"input_tokens": 5}},
                }
            )
            + "\n",
            "event: content_block_delta\ndata: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Hi"},
                }
            )
            + "\n",
            "event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 2},
                }
            )
            + "\n",
            "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n",
        ]

        chunks = []
        async for chunk in anthropic_stream_to_ollama_chat(
            _async_iter(sse_lines), model="claude-sonnet-4-6", start=0.0
        ):
            chunks.append(json.loads(chunk.strip()))

        text_chunks = [c for c in chunks if not c["done"]]
        assert len(text_chunks) == 1
        assert text_chunks[0]["message"] == {"role": "assistant", "content": "Hi"}

        done_chunks = [c for c in chunks if c["done"]]
        assert len(done_chunks) == 1
        assert done_chunks[0]["message"] == {"role": "assistant", "content": ""}
        assert done_chunks[0]["prompt_eval_count"] == 5
        assert done_chunks[0]["eval_count"] == 2

    @pytest.mark.asyncio
    async def test_max_tokens_stop_reason(self):
        sse_lines = [
            "event: message_delta\ndata: "
            + json.dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "max_tokens"},
                    "usage": {"output_tokens": 100},
                }
            )
            + "\n",
        ]
        chunks = []
        async for chunk in anthropic_stream_to_ollama_chat(
            _async_iter(sse_lines), model="claude-sonnet-4-6", start=0.0
        ):
            chunks.append(json.loads(chunk.strip()))
        done = [c for c in chunks if c["done"]]
        assert done[0]["done_reason"] == "length"


# ---------------------------------------------------------------------------
# Integration: HTTP endpoints via TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    config = _make_config()
    app = create_app(config)
    with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as mock:
        mock.return_value = _make_response()
        with TestClient(app) as c:
            yield c


class TestOllamaTagsEndpoint:
    def test_returns_models_list(self, client: TestClient):
        resp = client.get("/api/tags")
        assert resp.status_code == 200
        body = resp.json()
        assert "models" in body
        names = [m["name"] for m in body["models"]]
        assert "claude-sonnet-4-6" in names

    def test_model_shape(self, client: TestClient):
        resp = client.get("/api/tags")
        model = resp.json()["models"][0]
        assert "name" in model
        assert "model" in model
        assert "details" in model
        assert "family" in model["details"]

    def test_aliases_included(self):
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            aliases={"sonnet": "claude-sonnet-4-6"},
        )
        app = create_app(config)
        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
            m.return_value = _make_response()
            with TestClient(app) as c:
                resp = c.get("/api/tags")
        names = [m["name"] for m in resp.json()["models"]]
        assert "sonnet" in names


class TestOllamaGenerateEndpoint:
    def test_non_streaming_response(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={"model": "claude-sonnet-4-6", "prompt": "Hello", "stream": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["done"] is True
        assert "response" in body
        assert body["model"] == "claude-sonnet-4-6"

    def test_non_streaming_response_shape(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={"model": "claude-sonnet-4-6", "prompt": "Hi", "stream": False},
        )
        body = resp.json()
        assert "created_at" in body
        assert "total_duration" in body
        assert "prompt_eval_count" in body
        assert "eval_count" in body

    def test_invalid_body_returns_422(self, client: TestClient):
        resp = client.post("/api/generate", json={"bad": "body"})
        assert resp.status_code == 422
        assert "error" in resp.json()

    def test_streaming_response(self):
        config = _make_config()
        app = create_app(config)

        async def _fake_stream(_request):
            sse = [
                "event: message_start\ndata: "
                + json.dumps(
                    {
                        "type": "message_start",
                        "message": {"id": "m1", "usage": {"input_tokens": 3}},
                    }
                )
                + "\n",
                "event: content_block_delta\ndata: "
                + json.dumps(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Hi"},
                    }
                )
                + "\n",
                "event: message_delta\ndata: "
                + json.dumps(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 1},
                    }
                )
                + "\n",
                "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n",
            ]
            for s in sse:
                yield s

        with patch("bifrost.router.ModelRouter.stream", return_value=_fake_stream(None)):
            with TestClient(app) as c:
                resp = c.post(
                    "/api/generate",
                    json={"model": "claude-sonnet-4-6", "prompt": "Hi", "stream": True},
                )
        assert resp.status_code == 200
        lines = _parse_ndjson_lines(resp.text)
        assert len(lines) >= 2
        assert any(ln["done"] for ln in lines)
        assert any(not ln["done"] and ln["response"] == "Hi" for ln in lines)


class TestOllamaChatEndpoint:
    def test_non_streaming_response(self, client: TestClient):
        resp = client.post(
            "/api/chat",
            json={
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["done"] is True
        assert "message" in body
        assert body["message"]["role"] == "assistant"

    def test_non_streaming_response_shape(self, client: TestClient):
        resp = client.post(
            "/api/chat",
            json={
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        body = resp.json()
        assert "created_at" in body
        assert "total_duration" in body
        assert "prompt_eval_count" in body
        assert "eval_count" in body

    def test_invalid_body_returns_422(self, client: TestClient):
        resp = client.post("/api/chat", json={"bad": "input"})
        assert resp.status_code == 422
        assert "error" in resp.json()

    def test_streaming_response(self):
        config = _make_config()
        app = create_app(config)

        async def _fake_stream(_request):
            sse = [
                "event: message_start\ndata: "
                + json.dumps(
                    {
                        "type": "message_start",
                        "message": {"id": "m1", "usage": {"input_tokens": 4}},
                    }
                )
                + "\n",
                "event: content_block_delta\ndata: "
                + json.dumps(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Sure"},
                    }
                )
                + "\n",
                "event: message_delta\ndata: "
                + json.dumps(
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 1},
                    }
                )
                + "\n",
                "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}) + "\n",
            ]
            for s in sse:
                yield s

        with patch("bifrost.router.ModelRouter.stream", return_value=_fake_stream(None)):
            with TestClient(app) as c:
                resp = c.post(
                    "/api/chat",
                    json={
                        "model": "claude-sonnet-4-6",
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": True,
                    },
                )
        assert resp.status_code == 200
        lines = _parse_ndjson_lines(resp.text)
        assert any(ln["done"] for ln in lines)
        text_chunks = [ln for ln in lines if not ln["done"]]
        assert any(ln["message"]["content"] == "Sure" for ln in text_chunks)

    def test_system_message_in_chat(self, client: TestClient):
        """System messages in chat requests should be accepted without error."""
        resp = client.post(
            "/api/chat",
            json={
                "model": "claude-sonnet-4-6",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Hi"},
                ],
                "stream": False,
            },
        )
        assert resp.status_code == 200
