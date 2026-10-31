"""An uncapped request stays uncapped through Bifröst.

Each inbound surface used to fill a missing cap with its own guess — 1024 for
chat completions and Ollama, 4096 for Responses — so a resident that sent no
cap to a reasoning model got its answer cut at 1024 tokens. Now ``None`` is
carried through: OpenAI-compatible and Responses upstreams receive no cap, and
the Anthropic upstream (whose protocol requires one) sends the model's maximum
output from the Models API.
"""

from __future__ import annotations

import json

import httpx
import pytest

from bifrost.adapters.anthropic import AnthropicAdapter
from bifrost.adapters.rules.yaml_engine import YamlRuleEngine
from bifrost.config import BifrostConfig, ProviderConfig, RuleCondition, RuleConfig
from bifrost.ports.provider import ProviderError
from bifrost.ports.rules import RoutingContext
from bifrost.translation.models import AnthropicRequest, Message
from bifrost.translation.to_openai import anthropic_to_openai
from bifrost.translation.to_responses import anthropic_to_responses

_MODELS_URL = "https://api.anthropic.com/v1/models/claude-opus-5-5"
_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _uncapped(model: str = "claude-opus-5-5") -> AnthropicRequest:
    return AnthropicRequest(model=model, messages=[Message(role="user", content="hi")])


def _reply() -> dict:
    return {
        "id": "msg_1",
        "content": [{"type": "text", "text": "ok"}],
        "model": "claude-opus-5-5",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class TestTranslation:
    def test_canonical_request_defaults_to_no_cap(self) -> None:
        assert _uncapped().max_tokens is None

    def test_openai_payload_carries_no_cap(self) -> None:
        assert "max_tokens" not in anthropic_to_openai(_uncapped("Qwen/Qwen3.8-27B"), "q")

    def test_openai_payload_carries_an_explicit_cap(self) -> None:
        request = _uncapped("Qwen/Qwen3.8-27B").model_copy(update={"max_tokens": 300})
        assert anthropic_to_openai(request, "q")["max_tokens"] == 300

    def test_responses_payload_carries_no_cap(self) -> None:
        assert "max_output_tokens" not in anthropic_to_responses(_uncapped("gpt-6-sol"), "gpt")

    def test_responses_payload_carries_an_explicit_cap(self) -> None:
        request = _uncapped("gpt-6-sol").model_copy(update={"max_tokens": 300})
        assert anthropic_to_responses(request, "gpt")["max_output_tokens"] == 300


class TestAnthropicUpstream:
    async def test_no_cap_sends_the_models_own_maximum(self, respx_mock) -> None:
        models = respx_mock.get(_MODELS_URL).mock(
            return_value=httpx.Response(200, json={"id": "claude-opus-5-5", "max_tokens": 128000})
        )
        sent = respx_mock.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json=_reply()))
        adapter = AnthropicAdapter(api_key="k")

        await adapter.complete(_uncapped(), "claude-opus-5-5")
        await adapter.complete(_uncapped(), "claude-opus-5-5")
        await adapter.close()

        assert models.call_count == 1
        assert json.loads(sent.calls.last.request.content)["max_tokens"] == 128000

    async def test_an_explicit_cap_is_sent_without_a_lookup(self, respx_mock) -> None:
        models = respx_mock.get(_MODELS_URL)
        sent = respx_mock.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json=_reply()))
        adapter = AnthropicAdapter(api_key="k")

        await adapter.complete(
            _uncapped().model_copy(update={"max_tokens": 900}), "claude-opus-5-5"
        )
        await adapter.close()

        assert not models.called
        assert json.loads(sent.calls.last.request.content)["max_tokens"] == 900

    async def test_stream_derives_the_cap_too(self, respx_mock) -> None:
        respx_mock.get(_MODELS_URL).mock(
            return_value=httpx.Response(200, json={"id": "claude-opus-5-5", "max_tokens": 64000})
        )
        sent = respx_mock.post(_MESSAGES_URL).mock(
            return_value=httpx.Response(200, content=b'data: {"type": "message_stop"}\n')
        )
        adapter = AnthropicAdapter(api_key="k")

        _ = [line async for line in adapter.stream(_uncapped(), "claude-opus-5-5")]
        await adapter.close()

        assert json.loads(sent.calls.last.request.content)["max_tokens"] == 64000

    async def test_an_unreadable_limit_raises(self, respx_mock) -> None:
        respx_mock.get(_MODELS_URL).mock(return_value=httpx.Response(404, text="unknown"))
        adapter = AnthropicAdapter(api_key="k")

        with pytest.raises(ProviderError, match="send max_tokens explicitly"):
            await adapter.complete(_uncapped(), "claude-opus-5-5")
        await adapter.close()

    async def test_a_catalog_without_a_limit_raises(self, respx_mock) -> None:
        respx_mock.get(_MODELS_URL).mock(
            return_value=httpx.Response(200, json={"id": "claude-opus-5-5"})
        )
        adapter = AnthropicAdapter(api_key="k")

        with pytest.raises(ProviderError, match="no output limit"):
            await adapter.complete(_uncapped(), "claude-opus-5-5")
        await adapter.close()


class TestRules:
    def _engine(self, expr: str) -> YamlRuleEngine:
        config = BifrostConfig(providers={"anthropic": ProviderConfig(models=["claude-opus-5-5"])})
        rule = RuleConfig(name="r", when=RuleCondition(max_tokens=expr), action="log")
        return YamlRuleEngine(rules=[rule], config=config)

    def test_an_uncapped_request_is_not_a_small_one(self) -> None:
        assert self._engine("<= 512").evaluate(_uncapped(), RoutingContext()) is None

    def test_an_uncapped_request_counts_as_a_large_one(self) -> None:
        assert self._engine(">= 100000").evaluate(_uncapped(), RoutingContext()) is not None
