"""The per-model compaction setting: Ravn's own, the provider's (native), or off.

Native and Ravn's compactor never run together; native on an adapter that
cannot do it is fatal at startup; the provider's opaque compaction state
round-trips through Ravn's history verbatim and supersedes what came before.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from ravn.adapters.llm.anthropic import AnthropicAdapter
from ravn.adapters.llm.bifrost import BifrostAdapter
from ravn.adapters.llm.openai import OpenAICompatibleAdapter
from ravn.adapters.llm.openai_responses import OpenAIResponsesAdapter
from ravn.adapters.llm.provider_items import ANTHROPIC_MESSAGES, OPENAI_RESPONSES
from ravn.budget import TokenEstimator
from ravn.cli.tool_builders import _build_compaction
from ravn.compression import ContextCompressor
from ravn.config import CompactionMode, ContextManagementConfig, LLMConfig, Settings
from ravn.domain.exceptions import ConfigurationError, LLMError
from ravn.domain.models import (
    Message,
    StreamEvent,
    StreamEventType,
    TokenUsage,
)
from ravn.ports.llm import LLMPort
from ravn.ports.native_compaction import NativeCompactionPort
from tests.test_ravn.test_agent import make_agent

COMPACTION = {"type": "compaction", "id": "cmp_9", "encrypted_content": "ENC"}


def _settings(**context_management) -> Settings:
    settings = Settings()
    settings.llm = LLMConfig(model="gpt-6-sol", max_tokens=32000)
    settings.context_management = ContextManagementConfig(**context_management)
    return settings


# ---------------------------------------------------------------------------
# Configuration and the builder
# ---------------------------------------------------------------------------


class TestCompactionSetting:
    def test_default_is_ravn_for_every_model(self):
        cm = ContextManagementConfig()
        assert cm.compaction_for("gpt-6-sol") is CompactionMode.RAVN

    def test_per_model_override(self):
        cm = ContextManagementConfig(
            compaction="off", compaction_models={"gpt-6-sol": "native", "Qwen/Q": "ravn"}
        )
        assert cm.compaction_for("gpt-6-sol") is CompactionMode.NATIVE
        assert cm.compaction_for("Qwen/Q") is CompactionMode.RAVN
        assert cm.compaction_for("other") is CompactionMode.OFF

    def test_ravn_builds_the_compressor_only(self):
        compressor, native = _build_compaction(_settings(), MagicMock(), "gpt-6-sol")
        assert isinstance(compressor, ContextCompressor)
        assert native is None

    def test_off_builds_neither(self):
        assert _build_compaction(_settings(compaction="off"), MagicMock(), "m") == (None, None)

    def test_native_enables_the_provider_and_never_the_compressor(self):
        llm = OpenAIResponsesAdapter()
        settings = _settings(
            compaction_models={"gpt-6-sol": "native"},
            max_prompt_tokens=200000,
            compression_threshold=0.8,
            token_estimate_safety_factor=1.5,
        )
        compressor, native = _build_compaction(settings, llm, "gpt-6-sol")
        assert compressor is None
        assert native is llm
        # Ravn's own trigger point (80% of its conservative budget) in real tokens.
        assert llm._compaction_thresholds == {"gpt-6-sol": 106666}

    def test_native_threshold_from_the_context_window(self):
        llm = OpenAIResponsesAdapter()
        settings = _settings(
            compaction="native", context_window_tokens=272000, token_estimate_safety_factor=1.0
        )
        _build_compaction(settings, llm, "gpt-6-sol")
        assert llm._compaction_thresholds["gpt-6-sol"] == int((272000 - 32000) * 0.8)

    def test_an_uncapped_answer_leaves_the_window_alone(self):
        # With no answer cap the window holds prompt and answer together, so it
        # cannot be the prompt budget on its own.
        settings = _settings(compaction="native", context_window_tokens=272000)
        settings.llm = LLMConfig(model="gpt-6-sol", max_tokens=None)
        with pytest.raises(ConfigurationError, match="no prompt budget"):
            _build_compaction(settings, OpenAIResponsesAdapter(), "gpt-6-sol")

    def test_an_uncapped_answer_uses_the_explicit_prompt_budget(self):
        llm = OpenAIResponsesAdapter()
        settings = _settings(
            compaction="native",
            context_window_tokens=272000,
            max_prompt_tokens=200000,
            token_estimate_safety_factor=1.0,
        )
        settings.llm = LLMConfig(model="gpt-6-sol", max_tokens=None)
        _build_compaction(settings, llm, "gpt-6-sol")
        assert llm._compaction_thresholds["gpt-6-sol"] == int(200000 * 0.8)

    def test_native_on_an_adapter_without_it_is_fatal(self):
        settings = _settings(compaction="native", max_prompt_tokens=200000)
        with pytest.raises(ConfigurationError, match="cannot ask its provider to compact"):
            _build_compaction(settings, OpenAICompatibleAdapter(), "gpt-6-sol")

    def test_native_without_a_budget_is_fatal(self):
        with pytest.raises(ConfigurationError, match="no prompt budget"):
            _build_compaction(_settings(compaction="native"), OpenAIResponsesAdapter(), "gpt-6-sol")

    def test_anthropic_refuses_a_trigger_below_its_minimum(self):
        settings = _settings(compaction="native", max_prompt_tokens=64000)
        with pytest.raises(ConfigurationError, match="no trigger below 50000"):
            _build_compaction(settings, AnthropicAdapter(), "claude-opus-5-5")

    def test_the_agent_refuses_both(self):
        with pytest.raises(ValueError, match="never both"):
            make_agent(
                MagicMock(spec=LLMPort),
                compressor=MagicMock(spec=ContextCompressor),
                native_compaction=MagicMock(spec=NativeCompactionPort),
            )


# ---------------------------------------------------------------------------
# The agent: verbatim items, superseded history
# ---------------------------------------------------------------------------


class _NativeLLM(LLMPort, NativeCompactionPort):
    """Answers with a compaction item on the second call."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def enable_native_compaction(self, *, model: str, threshold_tokens: int) -> None:
        return None

    def compaction_window_start(self, messages: list[dict]) -> int:
        return OpenAIResponsesAdapter().compaction_window_start(messages)

    async def stream(self, messages, **kwargs) -> AsyncIterator[StreamEvent]:  # noqa: ANN001
        self.calls.append(messages)
        items = [COMPACTION] if len(self.calls) == 2 else []
        message = {"type": "message", "role": "assistant", "content": []}
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=f"answer {len(self.calls)}")
        yield StreamEvent(
            type=StreamEventType.PROVIDER_ITEMS,
            provider_items=[*items, message],
            provider_protocol=OPENAI_RESPONSES,
        )
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE, usage=TokenUsage(input_tokens=1, output_tokens=1)
        )

    async def generate(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("not used")


class TestAgentWithNativeCompaction:
    async def test_items_land_on_the_message_and_supersede_history(self):
        llm = _NativeLLM()
        agent, _ = make_agent(llm, native_compaction=llm)
        await agent.run_turn("first")
        await agent.run_turn("second")
        assert agent.session.messages[-1].provider_items[0] == COMPACTION
        assert agent.session.messages[-1].provider_protocol == OPENAI_RESPONSES
        assert len(agent.session.messages) == 4

        await agent.run_turn("third")
        # Everything before the compacted turn is inside its compaction item.
        assert [m.role for m in agent.session.messages] == ["assistant", "user", "assistant"]
        assert agent.session.messages[0].provider_items[0] == COMPACTION
        assert llm.calls[-1][0]["provider_items"][0] == COMPACTION
        assert agent.last_compression_result.removed_message_count == 3

    async def test_ravns_compressor_does_not_run_with_native(self):
        llm = _NativeLLM()
        agent, _ = make_agent(llm, native_compaction=llm, max_prompt_tokens=10)
        assert agent._compressor is None


class TestMessageCarriesProviderItems:
    def test_round_trip_and_estimate(self):
        message = Message(
            role="assistant",
            content="ok",
            provider_items=[COMPACTION],
            provider_protocol=OPENAI_RESPONSES,
        )
        assert Message.from_api_dict(message.to_api_dict()) == message
        assert "provider_items" not in Message(role="user", content="x").to_api_dict()
        plain = Message(role="assistant", content="ok")
        assert TokenEstimator.rough_messages([message]) > TokenEstimator.rough_messages([plain])
        assert TokenEstimator.rough_api_messages([message.to_api_dict()]) > 0


# ---------------------------------------------------------------------------
# Other adapters and foreign compaction state
# ---------------------------------------------------------------------------


def _foreign(protocol: str, item: dict) -> dict:
    return Message(
        role="assistant", content="ok", provider_items=[item], provider_protocol=protocol
    ).to_api_dict()


class TestOtherAdapters:
    def test_chat_completions_strips_items_and_refuses_compaction(self):
        adapter = OpenAICompatibleAdapter()
        reasoning_only = _foreign(OPENAI_RESPONSES, {"type": "reasoning"})
        built = adapter._build_messages([reasoning_only], "")
        assert built == [{"role": "assistant", "content": "ok"}]
        with pytest.raises(LLMError, match="compacted natively"):
            adapter._build_messages([_foreign(OPENAI_RESPONSES, COMPACTION)], "")

    def test_anthropic_refuses_responses_compaction(self):
        with pytest.raises(LLMError, match="compacted natively by a openai_responses"):
            AnthropicAdapter()._build_request(
                [_foreign(OPENAI_RESPONSES, COMPACTION)],
                tools=[],
                system="",
                model="claude-opus-5-5",
                max_tokens=10,
                stream=False,
            )


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _anthropic_sse(*events: dict) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


class TestAnthropicNativeCompaction:
    @staticmethod
    def _adapter() -> AnthropicAdapter:
        adapter = AnthropicAdapter(api_key="k", retry_base_delay=0)
        adapter.enable_native_compaction(model="claude-opus-5-5", threshold_tokens=106666)
        return adapter

    @respx.mock
    async def test_stream_asks_for_compaction_and_keeps_the_turn_verbatim(self):
        route = respx.post(ANTHROPIC_URL).mock(
            return_value=httpx.Response(
                200,
                content=_anthropic_sse(
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "compaction", "content": None},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "compaction_delta", "content": "summary"},
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {"type": "thinking", "thinking": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "thinking_delta", "thinking": "t"},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {"type": "signature_delta", "signature": "sig"},
                    },
                    {"type": "content_block_stop", "index": 1},
                    {
                        "type": "content_block_start",
                        "index": 2,
                        "content_block": {"type": "text", "text": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 2,
                        "delta": {"type": "text_delta", "text": "hi"},
                    },
                    {"type": "content_block_stop", "index": 2},
                    {
                        "type": "content_block_start",
                        "index": 3,
                        "content_block": {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 3,
                        "delta": {"type": "input_json_delta", "partial_json": '{"a": 1}'},
                    },
                    {"type": "content_block_stop", "index": 3},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 10,
                            "iterations": [
                                {"type": "compaction", "input_tokens": 900, "output_tokens": 90}
                            ],
                        },
                    },
                    {"type": "message_stop"},
                ),
            )
        )
        events = [
            e
            async for e in self._adapter().stream(
                [{"role": "user", "content": "x"}],
                tools=[],
                system="",
                model="claude-opus-5-5",
                max_tokens=100,
            )
        ]
        sent = route.calls.last.request
        assert "compact-2026-01-12" in sent.headers["anthropic-beta"]
        assert json.loads(sent.content)["context_management"] == {
            "edits": [
                {"type": "compact_20260112", "trigger": {"type": "input_tokens", "value": 106666}}
            ]
        }
        [items] = [e for e in events if e.type == StreamEventType.PROVIDER_ITEMS]
        assert items.provider_protocol == ANTHROPIC_MESSAGES
        assert items.provider_items == [
            {"type": "compaction", "content": "summary"},
            {"type": "thinking", "thinking": "t", "signature": "sig"},
            {"type": "text", "text": "hi"},
            {"type": "tool_use", "id": "t1", "name": "x", "input": {"a": 1}},
        ]
        [done] = [e for e in events if e.type == StreamEventType.MESSAGE_DONE]
        assert (done.usage.input_tokens, done.usage.output_tokens) == (1000, 100)

    def test_replays_the_turn_verbatim_and_finds_the_window(self):
        adapter = self._adapter()
        turn = Message(
            role="assistant",
            content=[{"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
            provider_items=[
                {"type": "compaction", "content": "summary"},
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
            ],
            provider_protocol=ANTHROPIC_MESSAGES,
        ).to_api_dict()
        body = adapter._build_request(
            [{"role": "user", "content": "a"}, turn],
            tools=[],
            system="",
            model="claude-opus-5-5",
            max_tokens=10,
            stream=True,
        )
        assert body["messages"][1] == {"role": "assistant", "content": turn["provider_items"]}
        assert adapter.compaction_window_start([{"role": "user", "content": "a"}, turn]) == 1
        edited = {**turn, "content": [{"type": "tool_use", "id": "other"}]}
        with pytest.raises(LLMError, match="cannot be replayed"):
            adapter._build_request(
                [edited], tools=[], system="", model="", max_tokens=1, stream=True
            )

    def test_models_without_native_compaction_are_unchanged(self):
        adapter = self._adapter()
        body = adapter._build_request(
            [{"role": "user", "content": "a"}],
            tools=[],
            system="",
            model="claude-sonnet-5-5",
            max_tokens=10,
            stream=True,
        )
        assert "context_management" not in body
        assert "compact" not in adapter._headers(model="claude-sonnet-5-5")["anthropic-beta"]

    @respx.mock
    async def test_generate_keeps_the_content_and_counts_compaction(self):
        respx.post("http://bifrost:8080/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [
                        {"type": "compaction", "content": "s"},
                        {"type": "text", "text": "ok"},
                    ],
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 1,
                        "iterations": [
                            {"type": "compaction", "input_tokens": 50, "output_tokens": 5}
                        ],
                    },
                },
            )
        )
        adapter = BifrostAdapter(retry_base_delay=0)
        adapter.enable_native_compaction(model="claude-opus-5-5", threshold_tokens=60000)
        response = await adapter.generate(
            [{"role": "user", "content": "x"}],
            tools=[],
            system="",
            model="claude-opus-5-5",
            max_tokens=5,
        )
        assert response.content == "ok"
        assert response.provider_items[0] == {"type": "compaction", "content": "s"}
        assert (response.usage.input_tokens, response.usage.output_tokens) == (55, 6)
