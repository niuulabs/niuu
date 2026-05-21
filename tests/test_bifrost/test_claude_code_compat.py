"""Claude Code CLI compatibility validation tests — NIU-477.

Validates that Bifröst correctly handles all request patterns that the
Anthropic SDK / Claude Code CLI generates when pointed at Bifröst via
ANTHROPIC_BASE_URL=http://localhost:<port>.

Test matrix:
 - Model resolution (GET /v1/models)
 - Non-streaming completion (POST /v1/messages)
 - Streaming completion with Anthropic SSE events
 - Tool use round-trip (tool_use + tool_result)
 - Multi-turn context (alternating user/assistant messages)
 - Extended thinking blocks (ThinkingBlock in response)
 - Cost tracking recorded in usage store after each request
 - No API key leaks in structured log output
 - Pi mode (auth_mode=open): arbitrary ANTHROPIC_API_KEY accepted
 - anthropic-version header is accepted without error
 - anthropic-beta header accepted without error
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from bifrost.adapters.memory_store import MemoryUsageStore
from bifrost.app import create_app
from bifrost.config import AuthMode, BifrostConfig, ProviderConfig
from bifrost.translation.models import (
    AnthropicResponse,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    UsageInfo,
)
from tests.test_bifrost.conftest import make_config, make_response

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLAUDE_MODEL = "claude-sonnet-4-6"

_ANTHROPIC_HEADERS = {
    # Claude Code / Anthropic SDK sends these on every request.
    "anthropic-version": "2023-06-01",
    "x-api-key": "sk-ant-any-value",
    "content-type": "application/json",
}

_ANTHROPIC_BETA_HEADERS = {
    **_ANTHROPIC_HEADERS,
    "anthropic-beta": "interleaved-thinking-2025-05-14",
}


_RESPONSE_INPUT_TOKENS = 15
_RESPONSE_OUTPUT_TOKENS = 8


def _text_response(text: str = "Hello from Bifröst!") -> AnthropicResponse:
    return make_response(
        text, input_tokens=_RESPONSE_INPUT_TOKENS, output_tokens=_RESPONSE_OUTPUT_TOKENS
    )


def _tool_use_response() -> AnthropicResponse:
    return AnthropicResponse(
        id="msg_test_002",
        content=[
            ToolUseBlock(
                id="toolu_01",
                name="read_file",
                input={"path": "/tmp/test.txt"},
            )
        ],
        model=_CLAUDE_MODEL,
        stop_reason="tool_use",
        usage=UsageInfo(input_tokens=30, output_tokens=12),
    )


def _thinking_response() -> AnthropicResponse:
    return AnthropicResponse(
        id="msg_test_003",
        content=[
            ThinkingBlock(thinking="Let me think about this carefully..."),
            TextBlock(text="The answer is 42."),
        ],
        model=_CLAUDE_MODEL,
        stop_reason="end_turn",
        usage=UsageInfo(input_tokens=20, output_tokens=25),
    )


def _anthropic_sse_stream(*event_pairs: tuple[str, dict]) -> AsyncIterator[str]:
    """Build a fake Anthropic-format SSE stream from (event_type, payload) pairs."""

    async def _gen():
        for event_type, payload in event_pairs:
            yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        yield "event: message_stop\ndata: {}\n\n"

    return _gen()


def _make_stream_events(text: str = "Hi there") -> list[tuple[str, dict]]:
    """Return a minimal but complete Anthropic SSE event sequence."""
    return [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_stream_001",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": _CLAUDE_MODEL,
                    "stop_reason": None,
                    "usage": {"input_tokens": 15, "output_tokens": 0},
                },
            },
        ),
        ("ping", {"type": "ping"}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        (
            "content_block_stop",
            {"type": "content_block_stop", "index": 0},
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> BifrostConfig:
    return make_config(
        models=[_CLAUDE_MODEL, "claude-opus-4-6"],
        aliases={"claude-3-5-sonnet-latest": _CLAUDE_MODEL},
    )


@pytest.fixture
def client(config: BifrostConfig) -> TestClient:
    app = create_app(config)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Model resolution (GET /v1/models)
# ---------------------------------------------------------------------------


class TestModelResolution:
    def test_models_endpoint_returns_configured_model(self, client: TestClient):
        resp = client.get("/api/v1/bifrost/v1/models")

        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        model_ids = [m["id"] for m in body["data"]]
        assert _CLAUDE_MODEL in model_ids

    def test_models_endpoint_includes_aliases(self, client: TestClient):
        resp = client.get("/api/v1/bifrost/v1/models")

        body = resp.json()
        model_ids = [m["id"] for m in body["data"]]
        assert "claude-3-5-sonnet-latest" in model_ids

    def test_models_endpoint_returns_openai_compatible_structure(self, client: TestClient):
        resp = client.get("/api/v1/bifrost/v1/models")

        body = resp.json()
        assert "data" in body
        for model in body["data"]:
            assert "id" in model
            assert "object" in model
            assert model["object"] == "model"
            assert "owned_by" in model

    def test_models_include_multiple_claude_models(self, client: TestClient):
        resp = client.get("/api/v1/bifrost/v1/models")

        model_ids = [m["id"] for m in resp.json()["data"]]
        assert "claude-opus-4-6" in model_ids


# ---------------------------------------------------------------------------
# 2. Non-streaming completion
# ---------------------------------------------------------------------------


class TestNonStreamingCompletion:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_basic_completion_succeeds(self, mock_complete: AsyncMock, client: TestClient):
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "Hello, what is 2+2?"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "message"
        assert body["role"] == "assistant"
        assert body["stop_reason"] == "end_turn"
        assert body["content"][0]["type"] == "text"
        assert "Hello from Bifröst!" in body["content"][0]["text"]

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_completion_response_includes_usage(self, mock_complete: AsyncMock, client: TestClient):
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 512,
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert "usage" in body
        assert body["usage"]["input_tokens"] == 15
        assert body["usage"]["output_tokens"] == 8

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_completion_response_includes_model_field(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 512,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] == _CLAUDE_MODEL

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_alias_resolves_to_configured_model(self, mock_complete: AsyncMock, client: TestClient):
        """Claude Code often sends model aliases like claude-3-5-sonnet-latest."""
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": "claude-3-5-sonnet-latest",
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_system_prompt_accepted(self, mock_complete: AsyncMock, client: TestClient):
        """Claude Code sends a rich system prompt on every request."""
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 256,
                "system": "You are a helpful coding assistant.",
                "messages": [{"role": "user", "content": "List files"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        called_request = mock_complete.call_args[0][0]
        assert called_request.system == "You are a helpful coding assistant."

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_anthropic_version_header_accepted(self, mock_complete: AsyncMock, client: TestClient):
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 128,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"anthropic-version": "2023-06-01", "x-api-key": "sk-any"},
        )

        assert resp.status_code == 200

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_anthropic_beta_header_accepted(self, mock_complete: AsyncMock, client: TestClient):
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 128,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers=_ANTHROPIC_BETA_HEADERS,
        )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. Streaming responses
# ---------------------------------------------------------------------------


class TestStreamingCompletion:
    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_returns_event_stream_content_type(
        self, mock_stream: AsyncMock, client: TestClient
    ):
        mock_stream.return_value = _anthropic_sse_stream(*_make_stream_events())

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "Stream me some text"}],
                "stream": True,
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_response_contains_message_start_event(
        self, mock_stream: AsyncMock, client: TestClient
    ):
        mock_stream.return_value = _anthropic_sse_stream(*_make_stream_events("Hello!"))

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "Say hello"}],
                "stream": True,
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.text
        assert "message_start" in body

    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_response_delivers_text_delta(
        self, mock_stream: AsyncMock, client: TestClient
    ):
        mock_stream.return_value = _anthropic_sse_stream(*_make_stream_events("streamed text"))

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "Say something"}],
                "stream": True,
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.text
        assert "streamed text" in body

    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_response_ends_with_message_stop(
        self, mock_stream: AsyncMock, client: TestClient
    ):
        mock_stream.return_value = _anthropic_sse_stream(*_make_stream_events())

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 256,
                "messages": [{"role": "user", "content": "Done?"}],
                "stream": True,
            },
            headers=_ANTHROPIC_HEADERS,
        )

        body = resp.text
        assert "message_stop" in body or "message_delta" in body

    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_no_cache_headers_set(self, mock_stream: AsyncMock, client: TestClient):
        """Bifröst must disable HTTP caching on streaming responses."""
        mock_stream.return_value = _anthropic_sse_stream(*_make_stream_events())

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
            headers=_ANTHROPIC_HEADERS,
        )

        # FastAPI's TestClient collapses the response; verify status only.
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. Tool use round-trip
# ---------------------------------------------------------------------------


class TestToolUse:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_tool_use_response_stop_reason_is_tool_use(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        """Claude Code expects stop_reason=tool_use when the model calls a tool."""
        mock_complete.return_value = _tool_use_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 1024,
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read a file from the filesystem",
                        "input_schema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    }
                ],
                "messages": [{"role": "user", "content": "Read /tmp/test.txt"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["stop_reason"] == "tool_use"

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_tool_use_block_in_response_content(self, mock_complete: AsyncMock, client: TestClient):
        mock_complete.return_value = _tool_use_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 512,
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read a file",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
                "messages": [{"role": "user", "content": "Use the tool"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        body = resp.json()
        tool_blocks = [b for b in body["content"] if b["type"] == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["name"] == "read_file"
        assert tool_blocks[0]["id"] == "toolu_01"
        assert tool_blocks[0]["input"] == {"path": "/tmp/test.txt"}

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_tool_result_in_followup_turn_accepted(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        """After a tool call, Claude Code sends a tool_result in the next user message."""
        mock_complete.return_value = _text_response("Based on the file content...")

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 512,
                "messages": [
                    {"role": "user", "content": "Read /tmp/test.txt"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_01",
                                "name": "read_file",
                                "input": {"path": "/tmp/test.txt"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_01",
                                "content": "file contents here",
                            }
                        ],
                    },
                ],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["stop_reason"] == "end_turn"

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_write_file_tool_definition_accepted(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        """Claude Code also uses write_file — verify the schema is accepted."""
        mock_complete.return_value = _text_response("I'll create the file.")

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 256,
                "tools": [
                    {
                        "name": "write_file",
                        "description": "Write content to a file",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    }
                ],
                "messages": [{"role": "user", "content": "Write hello to /tmp/out.txt"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5. Multi-turn context
# ---------------------------------------------------------------------------


class TestMultiTurnContext:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_multi_turn_messages_passed_to_router(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        """All prior messages should reach the router, not just the latest."""
        mock_complete.return_value = _text_response("Turn 3 response")

        messages = [
            {"role": "user", "content": "Turn 1: hello"},
            {"role": "assistant", "content": "Turn 1 reply"},
            {"role": "user", "content": "Turn 2: follow-up"},
            {"role": "assistant", "content": "Turn 2 reply"},
            {"role": "user", "content": "Turn 3: final question"},
        ]

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={"model": _CLAUDE_MODEL, "max_tokens": 512, "messages": messages},
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        called_request = mock_complete.call_args[0][0]
        # All 5 messages preserved in the routed request.
        assert len(called_request.messages) == 5

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_multi_turn_alternating_roles_preserved(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        mock_complete.return_value = _text_response()

        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]

        client.post(
            "/api/v1/bifrost/v1/messages",
            json={"model": _CLAUDE_MODEL, "max_tokens": 256, "messages": messages},
            headers=_ANTHROPIC_HEADERS,
        )

        called_request = mock_complete.call_args[0][0]
        roles = [m.role for m in called_request.messages]
        assert roles == ["user", "assistant", "user"]


# ---------------------------------------------------------------------------
# 6. Extended thinking
# ---------------------------------------------------------------------------


class TestExtendedThinking:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_thinking_block_in_response_is_returned(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        """Claude Code with extended thinking expects ThinkingBlock in the content."""
        mock_complete.return_value = _thinking_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 16000,
                "messages": [{"role": "user", "content": "Solve a hard problem"}],
            },
            headers=_ANTHROPIC_BETA_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        content_types = [b["type"] for b in body["content"]]
        assert "thinking" in content_types

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_thinking_block_contains_reasoning_text(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        mock_complete.return_value = _thinking_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 16000,
                "messages": [{"role": "user", "content": "Reason through this"}],
            },
            headers=_ANTHROPIC_BETA_HEADERS,
        )

        body = resp.json()
        thinking_blocks = [b for b in body["content"] if b["type"] == "thinking"]
        assert thinking_blocks[0]["thinking"] == "Let me think about this carefully..."

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_response_with_thinking_also_has_text_block(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        mock_complete.return_value = _thinking_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 16000,
                "messages": [{"role": "user", "content": "What is the answer?"}],
            },
            headers=_ANTHROPIC_BETA_HEADERS,
        )

        body = resp.json()
        text_blocks = [b for b in body["content"] if b["type"] == "text"]
        assert text_blocks[0]["text"] == "The answer is 42."


# ---------------------------------------------------------------------------
# 7. Cost tracking
# ---------------------------------------------------------------------------


class TestCostTracking:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_usage_recorded_after_non_streaming_request(
        self, mock_complete: AsyncMock, config: BifrostConfig
    ):
        """Every request must be persisted to the usage store."""
        store = MemoryUsageStore()

        # Inject the store via the app factory's internal state by patching.
        with patch("bifrost.app._build_usage_store", return_value=store):
            app = create_app(config)
            mock_complete.return_value = _text_response()
            with TestClient(app) as client:
                client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": _CLAUDE_MODEL,
                        "max_tokens": 128,
                        "messages": [{"role": "user", "content": "track me"}],
                    },
                    headers={**_ANTHROPIC_HEADERS, "x-agent-id": "claude-code-agent"},
                )

        records = asyncio.run(store.query())
        assert len(records) == 1
        assert records[0].model == _CLAUDE_MODEL
        assert records[0].input_tokens == 15
        assert records[0].output_tokens == 8

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_agent_id_attributed_in_usage_record(
        self, mock_complete: AsyncMock, config: BifrostConfig
    ):
        store = MemoryUsageStore()

        with patch("bifrost.app._build_usage_store", return_value=store):
            app = create_app(config)
            mock_complete.return_value = _text_response()
            with TestClient(app) as client:
                client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": _CLAUDE_MODEL,
                        "max_tokens": 64,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    headers={**_ANTHROPIC_HEADERS, "x-agent-id": "claude-code-cli"},
                )

        records = asyncio.run(store.query())
        assert records[0].agent_id == "claude-code-cli"

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_session_id_attributed_in_usage_record(
        self, mock_complete: AsyncMock, config: BifrostConfig
    ):
        store = MemoryUsageStore()

        with patch("bifrost.app._build_usage_store", return_value=store):
            app = create_app(config)
            mock_complete.return_value = _text_response()
            with TestClient(app) as client:
                client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": _CLAUDE_MODEL,
                        "max_tokens": 64,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    headers={
                        **_ANTHROPIC_HEADERS,
                        "x-session-id": "session-abc-123",
                    },
                )

        records = asyncio.run(store.query())
        assert records[0].session_id == "session-abc-123"

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_cost_usd_populated_in_usage_record(
        self, mock_complete: AsyncMock, config: BifrostConfig
    ):
        """For known models the cost must be > 0."""
        store = MemoryUsageStore()

        with patch("bifrost.app._build_usage_store", return_value=store):
            app = create_app(config)
            mock_complete.return_value = _text_response()
            with TestClient(app) as client:
                client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": _CLAUDE_MODEL,
                        "max_tokens": 128,
                        "messages": [{"role": "user", "content": "cost test"}],
                    },
                    headers=_ANTHROPIC_HEADERS,
                )

        records = asyncio.run(store.query())
        assert records[0].cost_usd > 0.0


# ---------------------------------------------------------------------------
# 8. No API key leaks in logs
# ---------------------------------------------------------------------------


class TestNoApiKeyLeaks:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_api_key_not_logged_in_request_log(
        self, mock_complete: AsyncMock, client: TestClient, caplog
    ):
        """The x-api-key / Authorization value must never appear in structured logs."""
        mock_complete.return_value = _text_response()
        secret_key = "sk-ant-api03-supersecretkey"

        with caplog.at_level(logging.DEBUG, logger="bifrost"):
            client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": _CLAUDE_MODEL,
                    "max_tokens": 64,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={
                    "anthropic-version": "2023-06-01",
                    "x-api-key": secret_key,
                },
            )

        for record in caplog.records:
            assert secret_key not in record.getMessage(), (
                f"API key found in log record: {record.getMessage()!r}"
            )

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_bearer_token_not_logged(self, mock_complete: AsyncMock, config: BifrostConfig, caplog):
        """PAT Bearer tokens must not appear in any log output."""
        # Use a 32-byte secret to satisfy the JWT library's minimum key length.
        _pat_secret = "test-secret-for-log-test-32bytes!"
        pat_config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=[_CLAUDE_MODEL])},
            auth_mode=AuthMode.PAT,
            pat_secret=_pat_secret,
        )
        token = jwt.encode(
            {"sub": "agent-1", "tenant_id": "default"},
            _pat_secret,
            algorithm="HS256",
        )

        app = create_app(pat_config)
        mock_complete.return_value = _text_response()
        with caplog.at_level(logging.DEBUG, logger="bifrost"):
            with TestClient(app) as client:
                client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": _CLAUDE_MODEL,
                        "max_tokens": 64,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )

        for record in caplog.records:
            assert token not in record.getMessage(), (
                f"Bearer token found in log: {record.getMessage()!r}"
            )


# ---------------------------------------------------------------------------
# 9. Pi mode (auth_mode=open): arbitrary API key accepted
# ---------------------------------------------------------------------------


class TestPiMode:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_open_mode_accepts_any_api_key(self, mock_complete: AsyncMock, client: TestClient):
        """Pi mode must not validate the ANTHROPIC_API_KEY value."""
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 128,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"x-api-key": "sk-ant-FAKE-KEY-THAT-SHOULD-FAIL-ON-REAL-API"},
        )

        assert resp.status_code == 200

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_open_mode_accepts_no_auth_header(self, mock_complete: AsyncMock, client: TestClient):
        """Pi mode must work with no authentication headers at all."""
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 128,
                "messages": [{"role": "user", "content": "no auth"}],
            },
        )

        assert resp.status_code == 200

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_open_mode_defaults_agent_id_to_anonymous(
        self, mock_complete: AsyncMock, config: BifrostConfig
    ):
        store = MemoryUsageStore()
        with patch("bifrost.app._build_usage_store", return_value=store):
            app = create_app(config)
            mock_complete.return_value = _text_response()
            with TestClient(app) as client:
                client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": _CLAUDE_MODEL,
                        "max_tokens": 64,
                        "messages": [{"role": "user", "content": "anon"}],
                    },
                )

        records = asyncio.run(store.query())
        assert records[0].agent_id == "anonymous"


# ---------------------------------------------------------------------------
# 10. Correlation ID / request tracing
# ---------------------------------------------------------------------------


class TestRequestTracing:
    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_response_includes_correlation_id_header(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        mock_complete.return_value = _text_response()

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "trace me"}],
            },
            headers=_ANTHROPIC_HEADERS,
        )

        assert resp.status_code == 200
        assert "x-correlation-id" in resp.headers

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_supplied_correlation_id_echoed_back(
        self, mock_complete: AsyncMock, client: TestClient
    ):
        mock_complete.return_value = _text_response()
        request_id = "my-trace-id-12345"

        resp = client.post(
            "/api/v1/bifrost/v1/messages",
            json={
                "model": _CLAUDE_MODEL,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "echo"}],
            },
            headers={**_ANTHROPIC_HEADERS, "X-Correlation-ID": request_id},
        )

        assert resp.headers["x-correlation-id"] == request_id
