"""Provider adapters on the native passthrough path, and Anthropic compaction.

``compaction.sse`` was recorded from the live ChatGPT Codex backend with
``context_management: [{"type": "compaction", "compact_threshold": 2000}]``: it
compacts before answering and again after (identifiers redacted).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bifrost.adapters.anthropic import AnthropicAdapter, context_management_betas
from bifrost.adapters.chatgpt_subscription import DEFAULT_BASE_URL, ChatGPTSubscriptionAdapter
from bifrost.adapters.openai_compat import OpenAICompatAdapter
from bifrost.ports.native import NativeProtocol
from bifrost.ports.provider import ProviderError
from bifrost.translation.models import AnthropicRequest, CompactionBlock

AUTH_PATH = "niuu.adapters.outbound.http_auth.StaticBearerTokenAuthAdapter"
BROKER = "http://volundr.test"
TOKEN_URL = f"{BROKER}/api/v1/internal/credentials/codex/tokens"
RESPONSES_URL = f"{DEFAULT_BASE_URL}/responses"
FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt_responses"


def _tokens() -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": "a", "chatgpt_account_id": "acct", "expires_in": 1300},
    )


@pytest.fixture
async def chatgpt():
    adapter = ChatGPTSubscriptionAdapter(
        broker_base_url=BROKER, auth_adapter=AUTH_PATH, auth_kwargs={"token": "jwt"}
    )
    yield adapter
    await adapter.close()


PAYLOAD = {
    "model": "alias",
    "input": [{"type": "message", "role": "user", "content": "codeword?"}],
    "context_management": [{"type": "compaction", "compact_threshold": 2000}],
    "stream": False,
}


class TestChatGPTNative:
    def test_declares_its_protocol(self):
        assert ChatGPTSubscriptionAdapter.native_protocol == NativeProtocol.OPENAI_RESPONSES
        assert ChatGPTSubscriptionAdapter.supports_native_compaction is True

    async def test_stream_forwards_the_payload_and_the_lines(self, chatgpt, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        recorded = (FIXTURES / "compaction.sse").read_bytes()
        route = respx_mock.post(RESPONSES_URL).mock(
            return_value=httpx.Response(200, content=recorded)
        )
        lines = [line async for line in chatgpt.native_stream(PAYLOAD, "gpt-6-sol")]
        sent = json.loads(route.calls.last.request.content)
        assert sent == {**PAYLOAD, "model": "gpt-6-sol", "stream": True}
        assert "".join(lines) == recorded.decode()

    async def test_complete_assembles_the_output(self, chatgpt, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(
            return_value=httpx.Response(200, content=(FIXTURES / "compaction.sse").read_bytes())
        )
        body = await chatgpt.native_complete(PAYLOAD, "gpt-6-sol")
        assert [item["type"] for item in body["output"]] == ["compaction", "message", "compaction"]
        assert body["output"][1]["content"][0]["text"] == "PELICAN-42"
        assert body["usage"]["input_tokens"] == 3105

    async def test_complete_raises_on_a_failed_response(self, chatgpt, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        failed = {"type": "response.failed", "response": {"error": {"message": "no tool output"}}}
        respx_mock.post(RESPONSES_URL).mock(
            return_value=httpx.Response(200, content=f"data: {json.dumps(failed)}\n\n")
        )
        with pytest.raises(ProviderError, match="no tool output"):
            await chatgpt.native_complete(PAYLOAD, "gpt-6-sol")

    async def test_complete_raises_on_a_cut_stream(self, chatgpt, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(
            return_value=httpx.Response(
                200, content='data: {"type": "response.created"}\n\nnot-sse\n'
            )
        )
        with pytest.raises(ProviderError, match="without a terminal"):
            await chatgpt.native_complete(PAYLOAD, "gpt-6-sol")

    async def test_a_refused_request_is_an_http_error_before_any_line(self, chatgpt, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(
            return_value=httpx.Response(400, json={"detail": "Unsupported parameter"})
        )
        with pytest.raises(httpx.HTTPStatusError, match="Unsupported parameter"):
            [line async for line in chatgpt.native_stream(PAYLOAD, "gpt-6-sol")]


class TestOpenAICompatNative:
    async def test_stream_asks_for_usage_and_applies_template_kwargs(self, respx_mock):
        adapter = OpenAICompatAdapter(
            base_url="http://vllm", chat_template_kwargs={"force_nonempty_content": True}
        )
        route = respx_mock.post("http://vllm/v1/chat/completions").mock(
            return_value=httpx.Response(200, content="data: {}\n\ndata: [DONE]\n\n")
        )
        lines = [
            line
            async for line in adapter.native_stream(
                {"model": "x", "messages": [], "stream_options": {"other": 1}}, "Qwen/Qwen3.8-27B"
            )
        ]
        sent = json.loads(route.calls.last.request.content)
        assert sent["model"] == "Qwen/Qwen3.8-27B"
        assert sent["stream_options"] == {"other": 1, "include_usage": True}
        assert sent["chat_template_kwargs"] == {"force_nonempty_content": True}
        assert lines[-1] == "\n"
        await adapter.close()

    async def test_complete(self, respx_mock):
        adapter = OpenAICompatAdapter(base_url="http://vllm")
        route = respx_mock.post("http://vllm/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": []})
        )
        assert await adapter.native_complete({"messages": []}, "m") == {"choices": []}
        assert json.loads(route.calls.last.request.content)["stream"] is False
        await adapter.close()


class TestAnthropicContextManagement:
    def _request(self) -> AnthropicRequest:
        return AnthropicRequest(
            model="claude-opus-5-5",
            max_tokens=1024,
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "compaction", "content": "summary"}]},
                {"role": "user", "content": "go on"},
            ],
            context_management={
                "edits": [
                    {
                        "type": "compact_20260112",
                        "trigger": {"type": "input_tokens", "value": 60000},
                    }
                ]
            },
        )

    async def test_forwards_edits_blocks_and_beta(self, respx_mock):
        adapter = AnthropicAdapter(api_key="k")
        route = respx_mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "msg",
                    "content": [
                        {"type": "compaction", "content": "newer summary"},
                        {"type": "text", "text": "ok"},
                    ],
                    "usage": {
                        "input_tokens": 23000,
                        "output_tokens": 1000,
                        "iterations": [
                            {"type": "compaction", "input_tokens": 180000, "output_tokens": 3500},
                            {"type": "message", "input_tokens": 23000, "output_tokens": 1000},
                        ],
                    },
                },
            )
        )
        response = await adapter.complete(self._request(), "claude-opus-5-5")
        sent = route.calls.last.request
        assert sent.headers["anthropic-beta"] == "compact-2026-01-12"
        body = json.loads(sent.content)
        assert body["context_management"]["edits"][0]["type"] == "compact_20260112"
        assert body["messages"][1]["content"] == [{"type": "compaction", "content": "summary"}]
        assert response.content[0] == CompactionBlock(content="newer summary")
        # Billed tokens are the sum over every iteration, compaction included.
        assert (response.usage.input_tokens, response.usage.output_tokens) == (203000, 4500)
        await adapter.close()

    async def test_stream_sends_the_beta(self, respx_mock):
        adapter = AnthropicAdapter(api_key="k")
        route = respx_mock.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, content='data: {"type": "message_stop"}\n')
        )
        chunks = [chunk async for chunk in adapter.stream(self._request(), "claude-opus-5-5")]
        assert route.calls.last.request.headers["anthropic-beta"] == "compact-2026-01-12"
        assert chunks == ['data: {"type": "message_stop"}\n']
        await adapter.close()

    def test_no_edits_no_beta(self):
        assert context_management_betas(None) == []
        assert (
            AnthropicAdapter()
            ._headers(AnthropicRequest(model="m", messages=[]))
            .get("anthropic-beta")
            is None
        )

    def test_context_editing_shares_one_beta(self):
        assert context_management_betas(
            {"edits": [{"type": "clear_tool_uses_20250919"}, {"type": "clear_thinking_20251015"}]}
        ) == ["context-management-2025-06-27"]

    def test_an_unknown_edit_is_refused(self):
        with pytest.raises(ProviderError, match="not supported"):
            context_management_betas({"edits": [{"type": "summarise_everything"}]})

    def test_request_reports_context_management(self):
        assert self._request().uses_context_management()
        assert AnthropicRequest(
            model="m", messages=[{"role": "assistant", "content": [{"type": "compaction"}]}]
        ).uses_context_management()
        assert not AnthropicRequest(
            model="m", messages=[{"role": "user", "content": "x"}]
        ).uses_context_management()
