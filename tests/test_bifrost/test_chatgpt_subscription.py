"""ChatGPT-subscription adapter: wire format, token refresh, errors, and router wiring."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from bifrost.adapters.chatgpt_subscription import (
    DEFAULT_BASE_URL,
    ChatGPTSubscriptionAdapter,
)
from bifrost.adapters.codex_token_broker import CodexSubscriptionError
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.ports.provider import ProviderError
from bifrost.router import ModelRouter, _load_adapter
from bifrost.translation.models import AnthropicRequest, TextBlock, ToolUseBlock
from bifrost.translation.to_responses import ResponsesTranslationError

ADAPTER_PATH = "bifrost.adapters.chatgpt_subscription.ChatGPTSubscriptionAdapter"
AUTH_PATH = "niuu.adapters.outbound.http_auth.StaticBearerTokenAuthAdapter"
BROKER = "http://volundr.test"
TOKEN_URL = f"{BROKER}/api/v1/internal/credentials/codex/tokens"
RESPONSES_URL = f"{DEFAULT_BASE_URL}/responses"
FIXTURES = Path(__file__).parent / "fixtures" / "chatgpt_responses"

KWARGS = {
    "broker_base_url": BROKER,
    "auth_adapter": AUTH_PATH,
    "auth_kwargs": {"token": "workload-jwt"},
}


def _sse(name: str) -> httpx.Response:
    return httpx.Response(200, content=(FIXTURES / f"{name}.sse").read_bytes())


def _tokens(access: str = "access-1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access,
            "chatgpt_account_id": "acct-1",
            "expires_in": 1300,
            "chatgpt_plan_type": "pro",
        },
    )


def _request(**kwargs) -> AnthropicRequest:
    kwargs.setdefault("model", "gpt-6-sol")
    kwargs.setdefault("messages", [{"role": "user", "content": "Say hello in three words."}])
    return AnthropicRequest(**kwargs)


@pytest.fixture
async def adapter():
    instance = ChatGPTSubscriptionAdapter(**KWARGS)
    yield instance
    await instance.close()


class TestCompletion:
    async def test_complete_returns_the_translated_answer(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=_sse("text"))

        response = await adapter.complete(_request(), "gpt-6-sol")

        assert response.content == [TextBlock(text="Hello to you.")]
        assert response.stop_reason == "end_turn"
        assert (response.usage.input_tokens, response.usage.output_tokens) == (22, 8)
        assert response.model == "gpt-6-sol"

    async def test_complete_returns_tool_calls(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=_sse("tool_call"))

        response = await adapter.complete(_request(), "gpt-6-sol")

        [block] = response.content
        assert isinstance(block, ToolUseBlock)
        assert block.input == {"city": "Paris"}
        assert response.stop_reason == "tool_use"

    async def test_response_lines_end_with_the_upstream_stream(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        route = respx_mock.post(RESPONSES_URL).mock(return_value=_sse("text"))

        lines = [line async for line in adapter._response_lines({"model": "gpt-6-sol"})]

        assert route.call_count == 1
        assert lines[0] == "event: response.created"

    async def test_stream_yields_anthropic_events_ending_in_done(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=_sse("text"))

        events = [event async for event in adapter.stream(_request(), "gpt-6-sol")]

        assert events[0].startswith("event: message_start")
        assert events[-1] == "data: [DONE]\n\n"

    async def test_thinking_is_streamed_when_the_caller_asked_for_it(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        route = respx_mock.post(RESPONSES_URL).mock(return_value=_sse("reasoning"))

        response = await adapter.complete(
            _request(thinking={"type": "enabled", "budget_tokens": 1024}), "gpt-6-sol"
        )

        assert [b.type for b in response.content] == ["thinking", "text"]
        assert json.loads(route.calls.last.request.content)["reasoning"] == {"summary": "auto"}

    async def test_reasoning_is_returned_signed_even_when_thinking_was_not_asked_for(
        self, adapter, respx_mock
    ):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=_sse("reasoning_tool_call_no_summary"))

        response = await adapter.complete(_request(), "gpt-6-sol")

        assert [b.type for b in response.content] == ["thinking", "tool_use", "tool_use"]
        assert response.content[0].thinking == ""
        assert response.content[0].signature == "REDACTED-FINAL"

    async def test_a_signed_block_sent_back_is_replayed_as_a_reasoning_item(
        self, adapter, respx_mock
    ):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        route = respx_mock.post(RESPONSES_URL).mock(
            side_effect=[_sse("reasoning_tool_call_no_summary"), _sse("text")]
        )
        first = await adapter.complete(_request(), "gpt-6-sol")
        calls = [b for b in first.content if isinstance(b, ToolUseBlock)]

        await adapter.complete(
            _request(
                messages=[
                    {"role": "user", "content": "Say hello in three words."},
                    {"role": "assistant", "content": [b.model_dump() for b in first.content]},
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": c.id, "content": "ok"}
                            for c in calls
                        ],
                    },
                ]
            ),
            "gpt-6-sol",
        )

        sent = json.loads(route.calls[1].request.content)["input"]
        assert [item["type"] for item in sent] == [
            "message",
            "reasoning",
            "function_call",
            "function_call",
            "function_call_output",
            "function_call_output",
        ]
        assert sent[1] == {
            "type": "reasoning",
            "summary": [],
            "encrypted_content": "REDACTED-FINAL",
        }

    async def test_request_sends_the_codex_wire_format(self, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        route = respx_mock.post(RESPONSES_URL).mock(return_value=_sse("text"))
        instance = ChatGPTSubscriptionAdapter(
            **KWARGS, reasoning_effort="high", session_id="sess-1", parallel_tool_calls=False
        )

        await instance.complete(_request(system="Be terse.", max_tokens=77), "gpt-6-sol")
        await instance.close()

        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer access-1"
        assert request.headers["chatgpt-account-id"] == "acct-1"
        assert request.headers["openai-beta"] == "responses=experimental"
        assert request.headers["originator"] == "codex_cli_rs"
        assert request.headers["session_id"] == "sess-1"
        assert request.headers["accept"] == "text/event-stream"
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-sol"
        assert body["instructions"] == "Be terse."
        assert body["max_output_tokens"] == 77
        assert body["stream"] is True
        assert body["store"] is False
        assert body["include"] == ["reasoning.encrypted_content"]
        assert body["reasoning"] == {"effort": "high"}
        assert body["input"][0]["content"][0]["text"] == "Say hello in three words."

    async def test_the_broker_is_called_as_the_configured_workload(self, adapter, respx_mock):
        broker = respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=_sse("text"))

        await adapter.complete(_request(), "gpt-6-sol")

        assert broker.calls.last.request.headers["authorization"] == "Bearer workload-jwt"

    async def test_token_is_fetched_once_for_many_requests(self, adapter, respx_mock):
        broker = respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(side_effect=lambda _: _sse("text"))

        await adapter.complete(_request(), "gpt-6-sol")
        await adapter.complete(_request(), "gpt-6-sol")

        assert broker.call_count == 1

    async def test_custom_base_url(self, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        route = respx_mock.post("http://codex.test/v1/responses").mock(return_value=_sse("text"))
        instance = ChatGPTSubscriptionAdapter(**KWARGS, base_url="http://codex.test/v1/")

        await instance.complete(_request(), "gpt-6-sol")
        await instance.close()

        assert route.call_count == 1


class TestUnauthorized:
    async def test_401_refreshes_the_token_once_and_retries(self, adapter, respx_mock):
        broker = respx_mock.post(TOKEN_URL).mock(side_effect=[_tokens("old"), _tokens("new")])
        upstream = respx_mock.post(RESPONSES_URL).mock(
            side_effect=[httpx.Response(401, json={"detail": "expired"}), _sse("text")]
        )

        response = await adapter.complete(_request(), "gpt-6-sol")

        assert response.content == [TextBlock(text="Hello to you.")]
        assert [c.request.headers["authorization"] for c in upstream.calls] == [
            "Bearer old",
            "Bearer new",
        ]
        refresh = json.loads(broker.calls.last.request.content)
        assert refresh["force_refresh"] is True
        assert refresh["previous_access_token_sha256"] == hashlib.sha256(b"old").hexdigest()

    async def test_a_second_401_is_an_error_not_a_loop(self, adapter, respx_mock):
        broker = respx_mock.post(TOKEN_URL).mock(side_effect=[_tokens("old"), _tokens("new")])
        upstream = respx_mock.post(RESPONSES_URL).mock(
            return_value=httpx.Response(401, json={"detail": "still no"})
        )

        with pytest.raises(CodexSubscriptionError, match="refreshed Codex token.*still no"):
            await adapter.complete(_request(), "gpt-6-sol")

        assert broker.call_count == 2
        assert upstream.call_count == 2

    async def test_refresh_hitting_a_revoked_subscription_says_to_reconnect(
        self, adapter, respx_mock
    ):
        respx_mock.post(TOKEN_URL).mock(
            side_effect=[_tokens("old"), httpx.Response(409, json={"detail": "revoked"})]
        )
        respx_mock.post(RESPONSES_URL).mock(return_value=httpx.Response(401))

        with pytest.raises(CodexSubscriptionError, match="needs reconnecting in Völundr"):
            await adapter.complete(_request(), "gpt-6-sol")


class TestErrors:
    async def test_broker_409_is_loud_before_any_upstream_call(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=httpx.Response(409, json={"detail": "x"}))
        upstream = respx_mock.post(RESPONSES_URL).mock(return_value=_sse("text"))

        with pytest.raises(CodexSubscriptionError, match="needs reconnecting in Völundr"):
            await adapter.complete(_request(), "gpt-6-sol")

        assert upstream.call_count == 0

    async def test_upstream_400_carries_the_backends_reason(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "detail": "The 'x' model is not supported",
                    "error": {"code": "model_not_found", "message": "model x is not supported"},
                },
            )
        )

        with pytest.raises(httpx.HTTPStatusError, match="400: model x is not supported") as exc:
            await adapter.complete(_request(), "gpt-6-sol")

        assert exc.value.response.status_code == 400

    async def test_upstream_detail_only_error(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(
            return_value=httpx.Response(400, json={"detail": "Unsupported parameter: temperature"})
        )

        with pytest.raises(httpx.HTTPStatusError, match="Unsupported parameter: temperature"):
            await adapter.complete(_request(), "gpt-6-sol")

    async def test_rate_limit_keeps_its_status_so_the_router_can_fail_over(
        self, adapter, respx_mock
    ):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=httpx.Response(429, text="usage limit"))

        with pytest.raises(httpx.HTTPStatusError, match="usage limit") as exc:
            await adapter.complete(_request(), "gpt-6-sol")

        assert exc.value.response.status_code == 429

    async def test_error_body_without_known_fields_is_quoted(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=httpx.Response(400, json={"x": 1}))

        with pytest.raises(httpx.HTTPStatusError, match=r'400: \{"x":1\}'):
            await adapter.complete(_request(), "gpt-6-sol")

    async def test_error_without_a_body(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=httpx.Response(502))

        with pytest.raises(httpx.HTTPStatusError, match="502: no detail"):
            await adapter.complete(_request(), "gpt-6-sol")

    async def test_unsupported_request_is_refused_before_any_http(self, adapter, respx_mock):
        broker = respx_mock.post(TOKEN_URL).mock(return_value=_tokens())

        with pytest.raises(ResponsesTranslationError, match="stop_sequences"):
            await adapter.complete(_request(stop_sequences=["x"]), "gpt-6-sol")

        assert broker.call_count == 0

    async def test_in_stream_failure_is_raised(self, adapter, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        failed = (
            'data: {"type":"response.created"}\n\n'
            'data: {"type":"response.failed","response":{"error":'
            '{"code":"server_error","message":"boom"}}}\n\n'
        )
        respx_mock.post(RESPONSES_URL).mock(return_value=httpx.Response(200, text=failed))

        with pytest.raises(ProviderError, match="server_error: boom"):
            await adapter.complete(_request(), "gpt-6-sol")


class TestConfiguration:
    def test_broker_base_url_is_required(self):
        with pytest.raises(ValueError, match="broker_base_url"):
            ChatGPTSubscriptionAdapter(broker_base_url="", auth_adapter=AUTH_PATH)

    def test_auth_adapter_is_required(self):
        with pytest.raises(ValueError, match="auth_adapter"):
            ChatGPTSubscriptionAdapter(broker_base_url=BROKER, auth_adapter="")

    def test_unknown_auth_adapter_fails_at_construction(self):
        with pytest.raises(ModuleNotFoundError):
            ChatGPTSubscriptionAdapter(broker_base_url=BROKER, auth_adapter="no.such.Adapter")


class TestRouterWiring:
    def _config(self, **overrides) -> BifrostConfig:
        provider = ProviderConfig(
            adapter=ADAPTER_PATH, models=["gpt-6-sol"], kwargs=KWARGS, **overrides
        )
        return BifrostConfig(providers={"chatgpt": provider})

    async def test_load_adapter_builds_the_named_class_from_kwargs(self):
        cfg = self._config().providers["chatgpt"]

        adapter = _load_adapter("chatgpt", cfg, "")

        assert isinstance(adapter, ChatGPTSubscriptionAdapter)
        assert adapter._responses_url == RESPONSES_URL
        await adapter.close()

    async def test_provider_base_url_and_timeout_reach_the_adapter(self):
        cfg = ProviderConfig(
            adapter=ADAPTER_PATH,
            models=["gpt-6-sol"],
            base_url="http://codex.test",
            timeout=33.0,
            kwargs=KWARGS,
        )

        adapter = _load_adapter("chatgpt", cfg, "http://codex.test")

        assert adapter._responses_url == "http://codex.test/responses"
        assert adapter._client.timeout.read == 33.0
        await adapter.close()

    def test_setting_a_value_both_on_the_provider_and_in_kwargs_is_refused(self):
        cfg = ProviderConfig(
            adapter=ADAPTER_PATH,
            models=["gpt-6-sol"],
            timeout=33.0,
            kwargs={**KWARGS, "timeout": 5.0},
        )

        with pytest.raises(ValueError, match="timeout both on the provider and in its adapter"):
            _load_adapter("chatgpt", cfg, "")

    def test_kwargs_without_an_adapter_are_refused(self):
        with pytest.raises(ValueError, match="set 'adapter'"):
            ProviderConfig(models=["gpt-6-sol"], kwargs={"a": 1})

    def test_provider_without_adapter_keeps_name_based_selection(self):
        from bifrost.adapters.openai_compat import OpenAICompatAdapter

        adapter = _load_adapter("custom", ProviderConfig(models=["m"]), "http://x.test")

        assert isinstance(adapter, OpenAICompatAdapter)

    def test_bad_adapter_kwargs_fail_loudly(self):
        cfg = ProviderConfig(adapter=ADAPTER_PATH, models=["gpt-6-sol"], kwargs={"nope": 1})

        with pytest.raises(TypeError):
            _load_adapter("chatgpt", cfg, "")

    async def test_router_serves_the_model_through_the_configured_adapter(self, respx_mock):
        respx_mock.post(TOKEN_URL).mock(return_value=_tokens())
        respx_mock.post(RESPONSES_URL).mock(return_value=_sse("text"))
        router = ModelRouter(self._config())

        response = await router.complete(_request())
        chunks = [chunk async for chunk in router.stream(_request(stream=True))]
        await router.close()

        assert response.content == [TextBlock(text="Hello to you.")]
        assert chunks[-1] == "data: [DONE]\n\n"
