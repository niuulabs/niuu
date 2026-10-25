"""Native passthrough: a provider served in its own protocol, side by side with
the canonical Anthropic translation, selected by configuration.

The fake provider below answers both paths from the same recorded Responses
stream — the canonical path runs it through Bifröst's real Responses → Anthropic
translation — so the tests can hold the two paths to identical metering.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from bifrost.adapters.audit.null import NullAuditAdapter
from bifrost.adapters.auth.open import OpenAuthAdapter
from bifrost.adapters.memory_store import MemoryUsageStore
from bifrost.config import (
    AuditDetailLevel,
    BifrostConfig,
    CacheConfig,
    CacheMode,
    ProviderConfig,
)
from bifrost.domain.models import TokenUsage
from bifrost.inbound.native import (
    ANTHROPIC_STREAM_USAGE,
    ChatCompletionsStreamUsage,
    ResponsesStreamUsage,
    native_response_usage,
    responses_native_only_features,
    responses_requests_compaction,
    responses_stream_errors_reported,
    strip_native_images,
)
from bifrost.inbound.routes import create_router
from bifrost.ports.audit import AuditEvent
from bifrost.ports.events import (
    BudgetDegradedEvent,
    BudgetWarningEvent,
    CostEventEmitter,
    RequestCompletedEvent,
)
from bifrost.ports.native import NativePassthroughPort, NativeProtocol
from bifrost.ports.provider import ProviderPort
from bifrost.ports.rules import RuleAction, RuleEnginePort, RuleMatch
from bifrost.pricing import ModelPricing
from bifrost.router import ModelRouter
from bifrost.translation.models import AnthropicRequest, AnthropicResponse
from bifrost.translation.responses_stream import (
    responses_stream_to_anthropic,
    responses_stream_to_message,
)

CHATGPT = "bifrost.adapters.chatgpt_subscription.ChatGPTSubscriptionAdapter"
OPENAI_COMPAT = "bifrost.adapters.openai_compat.OpenAICompatAdapter"
ANTHROPIC = "bifrost.adapters.anthropic.AnthropicAdapter"
MODEL = "gpt-6-sol"
PRICING = {MODEL: ModelPricing(input_per_million=2.0, output_per_million=8.0)}

COMPACTION = {"type": "compaction", "id": "cmp_1", "encrypted_content": "ENC" * 50}


def _responses_lines(
    *, text: str = "PELICAN-42", items: list[dict] | None = None, usage: dict | None = None
) -> list[str]:
    """A recorded ChatGPT backend stream: created, the output items, completed."""
    usage = usage or {
        "input_tokens": 1200,
        "input_tokens_details": {"cached_tokens": 800},
        "output_tokens": 40,
        "output_tokens_details": {"reasoning_tokens": 12},
        "total_tokens": 1240,
    }
    message = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    output = [*(items or []), message]
    events: list[dict] = [
        {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
    ]
    for index, item in enumerate(output):
        if item["type"] == "message":
            events.append(
                {
                    "type": "response.output_item.added",
                    "output_index": index,
                    "item": {**item, "content": []},
                }
            )
            events.append(
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_1",
                    "output_index": index,
                    "content_index": 0,
                    "delta": text,
                }
            )
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append(
        {
            "type": "response.completed",
            "response": {"id": "resp_1", "status": "completed", "output": [], "usage": usage},
        }
    )
    lines: list[str] = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]
    return lines


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


class FakeResponsesProvider(ProviderPort, NativePassthroughPort):
    """Answers both paths from one recorded Responses stream."""

    native_protocol = NativeProtocol.OPENAI_RESPONSES
    supports_native_compaction = True

    def __init__(self, lines: list[str] | None = None) -> None:
        self.lines = lines or _responses_lines()
        self.native_payloads: list[tuple[dict, str]] = []
        self.canonical_requests: list[AnthropicRequest] = []

    async def complete(self, request: AnthropicRequest, model: str) -> AnthropicResponse:
        self.canonical_requests.append(request)
        return await responses_stream_to_message(
            _aiter(self.lines), message_id="m", model=model, include_thinking=False
        )

    async def stream(self, request: AnthropicRequest, model: str) -> AsyncIterator[str]:
        self.canonical_requests.append(request)
        async for event in responses_stream_to_anthropic(
            _aiter(self.lines), message_id="m", model=model, include_thinking=False
        ):
            yield event

    async def native_stream(self, payload: dict[str, Any], model: str) -> AsyncIterator[str]:
        self.native_payloads.append((payload, model))
        for line in self.lines:
            yield f"{line}\n"

    async def native_complete(self, payload: dict[str, Any], model: str) -> dict[str, Any]:
        self.native_payloads.append((payload, model))
        return {
            "id": "resp_1",
            "object": "response",
            "status": "completed",
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 40,
                "output_tokens_details": {"reasoning_tokens": 12},
            },
            "output": [COMPACTION],
        }


class RecordingEmitter(CostEventEmitter):
    def __init__(self) -> None:
        self.completed: list[RequestCompletedEvent] = []

    async def emit_request_completed(self, event: RequestCompletedEvent) -> None:
        self.completed.append(event)

    async def emit_budget_warning(self, event: BudgetWarningEvent) -> None:
        return None

    async def emit_budget_degraded(self, event: BudgetDegradedEvent) -> None:
        return None


class RecordingAudit(NullAuditAdapter):
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def log(self, event: AuditEvent) -> None:
        self.events.append(event)


class Gateway:
    """A Bifröst API with one injected provider and inspectable bookkeeping."""

    def __init__(
        self,
        *,
        passthrough: bool,
        provider: ProviderPort | None = None,
        adapter: str = CHATGPT,
        rule_engine: RuleEnginePort | None = None,
    ) -> None:
        config = BifrostConfig(
            providers={
                "chatgpt": ProviderConfig(
                    adapter=adapter,
                    models=[MODEL],
                    passthrough=passthrough,
                    kwargs={"broker_base_url": "http://v", "auth_adapter": "x"},
                )
            },
        )
        config.audit.level = AuditDetailLevel.FULL
        self.provider = provider or FakeResponsesProvider()
        self.router = ModelRouter(config, rule_engine=rule_engine)
        self.router._adapters["chatgpt"] = self.provider
        self.store = MemoryUsageStore()
        self.emitter = RecordingEmitter()
        self.audit = RecordingAudit()
        app = FastAPI()

        @app.middleware("http")
        async def correlation(request: Request, call_next):  # noqa: ANN001
            request.state.correlation_id = str(uuid.uuid4())
            return await call_next(request)

        app.include_router(
            create_router(
                config,
                self.router,
                self.store,
                PRICING,
                OpenAuthAdapter(),
                self.emitter,
                audit=self.audit,
            )
        )
        self.client = TestClient(app)

    def post(self, path: str, body: dict) -> Any:
        return self.client.post(path, json=body)

    @property
    def records(self) -> list:
        return self.store._records


def _responses_body(**extra: Any) -> dict:
    return {
        "model": MODEL,
        "instructions": "terse",
        "input": [{"type": "message", "role": "user", "content": "codeword?"}],
        "stream": True,
        "store": False,
        **extra,
    }


def _events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


class TestPassthroughConfiguration:
    def test_passthrough_on_an_adapter_without_a_native_protocol_is_fatal(self):
        config = BifrostConfig(
            providers={"claude": ProviderConfig(adapter=ANTHROPIC, models=["c"], passthrough=True)}
        )
        with pytest.raises(ValueError, match="cannot serve requests in a native protocol"):
            ModelRouter(config)

    def test_one_model_on_two_paths_is_fatal(self):
        config = BifrostConfig(
            providers={
                "a": ProviderConfig(base_url="http://a", models=["m"], passthrough=True),
                "b": ProviderConfig(base_url="http://b", models=["m"]),
            }
        )
        with pytest.raises(ValueError, match="different paths"):
            ModelRouter(config)

    def test_the_response_cache_and_passthrough_are_exclusive(self):
        config = BifrostConfig(
            providers={"a": ProviderConfig(base_url="http://a", models=["m"], passthrough=True)},
            cache=CacheConfig(mode=CacheMode.MEMORY),
        )
        with pytest.raises(ValueError, match="cannot be cached"):
            ModelRouter(config)

    def test_default_keeps_every_provider_on_the_canonical_path(self):
        router = ModelRouter(
            BifrostConfig(providers={"a": ProviderConfig(base_url="http://a", models=["m"])})
        )
        assert router._native_protocols == {}

    async def test_route_names_the_native_protocol(self):
        router = ModelRouter(
            BifrostConfig(
                providers={"a": ProviderConfig(base_url="http://a", models=["m"], passthrough=True)}
            )
        )
        routed = await router.route(AnthropicRequest(model="m", messages=[]))
        assert routed.native_protocol == NativeProtocol.OPENAI_CHAT_COMPLETIONS
        assert routed.candidates == [("a", "m")]
        # Routing is idempotent: the routed request keeps its route.
        again = await router.route(routed.request)
        assert again.candidates == routed.candidates


# ---------------------------------------------------------------------------
# Responses: both paths
# ---------------------------------------------------------------------------


class TestResponsesPassthrough:
    def test_the_body_reaches_the_provider_verbatim(self):
        gateway = Gateway(passthrough=True)
        body = _responses_body(
            input=[
                COMPACTION,
                {"type": "message", "role": "user", "content": "codeword?"},
            ],
            context_management=[{"type": "compaction", "compact_threshold": 106666}],
            include=["reasoning.encrypted_content"],
            tools=[{"type": "web_search"}, {"type": "function", "name": "f", "parameters": {}}],
        )
        resp = gateway.post("/v1/responses", body)
        assert resp.status_code == 200, resp.text
        [(payload, model)] = gateway.provider.native_payloads
        assert payload == body
        assert model == MODEL
        assert gateway.provider.canonical_requests == []

    def test_the_stream_reaches_the_caller_unchanged(self):
        gateway = Gateway(passthrough=True)
        resp = gateway.post("/v1/responses", _responses_body())
        assert resp.status_code == 200
        assert resp.text == "".join(f"{line}\n" for line in gateway.provider.lines)
        done = [e for e in _events(resp.text) if e["type"] == "response.output_item.done"]
        assert done[-1]["item"]["phase"] == "final_answer"

    def test_compaction_items_come_back_to_the_caller(self):
        lines = _responses_lines(items=[COMPACTION])
        gateway = Gateway(passthrough=True, provider=FakeResponsesProvider(lines))
        resp = gateway.post("/v1/responses", _responses_body())
        items = [e["item"] for e in _events(resp.text) if e["type"] == "response.output_item.done"]
        assert items[0] == COMPACTION

    def test_canonical_path_refuses_what_only_the_provider_can_honour(self):
        gateway = Gateway(passthrough=False)
        for extra in (
            {"context_management": [{"type": "compaction", "compact_threshold": 1}]},
            {"input": [COMPACTION, {"type": "message", "role": "user", "content": "x"}]},
            {"previous_response_id": "resp_0"},
            {"tools": [{"type": "web_search"}]},
        ):
            resp = gateway.post("/v1/responses", _responses_body(**extra))
            assert resp.status_code == 400, extra
            assert "passthrough: true" in resp.json()["error"]["message"]
        assert gateway.provider.canonical_requests == []

    def test_canonical_path_still_serves_ordinary_requests(self):
        gateway = Gateway(passthrough=False)
        resp = gateway.post("/v1/responses", _responses_body())
        assert resp.status_code == 200
        assert gateway.provider.native_payloads == []
        completed = _events(resp.text)[-1]
        assert completed["type"] == "response.completed"
        assert completed["response"]["output"][0]["content"][0]["text"] == "PELICAN-42"

    def test_compaction_on_a_provider_without_it_is_refused(self):
        class NoCompaction(FakeResponsesProvider):
            supports_native_compaction = False

        gateway = Gateway(passthrough=True, provider=NoCompaction())
        gateway.router.supports_native_compaction = lambda routed: False
        resp = gateway.post(
            "/v1/responses",
            _responses_body(context_management=[{"type": "compaction", "compact_threshold": 1}]),
        )
        assert resp.status_code == 400
        assert "cannot compact context natively" in resp.json()["error"]["message"]
        assert gateway.provider.native_payloads == []

    def test_non_streaming_returns_the_native_body(self):
        gateway = Gateway(passthrough=True)
        resp = gateway.post("/v1/responses", _responses_body(stream=False))
        assert resp.status_code == 200
        assert resp.json()["output"] == [COMPACTION]
        [record] = gateway.records
        assert (record.input_tokens, record.output_tokens, record.reasoning_tokens) == (
            1200,
            40,
            12,
        )
        assert record.streaming is False
        [audit] = gateway.audit.events
        assert audit.tokens_input == 1200
        assert json.loads(audit.response_content)["output"] == [COMPACTION]

    def test_strip_images_rule_applies_to_the_native_payload(self):
        class StripImages(RuleEnginePort):
            def evaluate(self, request, context):  # noqa: ANN001
                return RuleMatch(rule_name="no-images", action=RuleAction.STRIP_IMAGES)

        gateway = Gateway(passthrough=True, rule_engine=StripImages())
        body = _responses_body(
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": "https://x/y.png"},
                        {"type": "input_text", "text": "what is it?"},
                    ],
                }
            ]
        )
        assert gateway.post("/v1/responses", body).status_code == 200
        [(payload, _)] = gateway.provider.native_payloads
        assert payload["input"][0]["content"] == [{"type": "input_text", "text": "what is it?"}]


class TestBothPathsAccountIdentically:
    """Usage, cost, metrics inputs and cost events match across the two paths."""

    @staticmethod
    def _run(passthrough: bool, stream: bool) -> Gateway:
        gateway = Gateway(passthrough=passthrough)
        resp = gateway.post("/v1/responses", _responses_body(stream=stream))
        assert resp.status_code == 200, resp.text
        return gateway

    @pytest.mark.parametrize("stream", [True, False])
    def test_usage_and_cost(self, stream: bool):
        native, canonical = self._run(True, stream), self._run(False, stream)
        [n], [c] = native.records, canonical.records
        assert (n.model, n.provider, n.input_tokens, n.output_tokens) == (
            c.model,
            c.provider,
            c.input_tokens,
            c.output_tokens,
        )
        assert n.cost_usd == pytest.approx(c.cost_usd)
        assert n.cost_usd == pytest.approx((1200 * 2.0 + 40 * 8.0) / 1_000_000)
        assert n.streaming is c.streaming is stream

    def test_streaming_reasoning_tokens(self):
        native, canonical = self._run(True, True), self._run(False, True)
        assert native.records[0].reasoning_tokens == canonical.records[0].reasoning_tokens == 12

    @pytest.mark.parametrize("stream", [True, False])
    def test_cost_events(self, stream: bool):
        native, canonical = self._run(True, stream), self._run(False, stream)
        [n], [c] = native.emitter.completed, canonical.emitter.completed
        assert (n.tokens_used, n.model) == (c.tokens_used, c.model) == (1240, MODEL)
        assert n.cost_usd == pytest.approx(c.cost_usd)


# ---------------------------------------------------------------------------
# Chat Completions: native passthrough for OpenAI-compatible providers
# ---------------------------------------------------------------------------


class FakeChatProvider(ProviderPort, NativePassthroughPort):
    native_protocol = NativeProtocol.OPENAI_CHAT_COMPLETIONS

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def complete(self, request, model):  # noqa: ANN001
        raise AssertionError("canonical path must not run")

    async def stream(self, request, model):  # noqa: ANN001
        raise AssertionError("canonical path must not run")
        yield ""

    async def native_stream(self, payload, model):  # noqa: ANN001
        self.payloads.append(payload)
        chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]}
        usage = {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 3}}
        for line in (
            f"data: {json.dumps(chunk)}",
            "",
            f"data: {json.dumps(usage)}",
            "",
            "data: [DONE]",
        ):
            yield f"{line}\n"

    async def native_complete(self, payload, model):  # noqa: ANN001
        self.payloads.append(payload)
        return {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 3},
        }


class TestChatCompletionsPassthrough:
    def _gateway(self) -> Gateway:
        return Gateway(passthrough=True, provider=FakeChatProvider(), adapter=OPENAI_COMPAT)

    def test_streaming(self):
        gateway = self._gateway()
        body = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
            "logit_bias": {"1": 2},
        }
        resp = gateway.post("/v1/chat/completions", body)
        assert resp.status_code == 200
        assert "data: [DONE]" in resp.text
        assert gateway.provider.payloads == [body]
        [record] = gateway.records
        assert (record.input_tokens, record.output_tokens) == (9, 3)

    def test_non_streaming(self):
        gateway = self._gateway()
        resp = gateway.post(
            "/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": "x"}]}
        )
        assert resp.json()["choices"][0]["message"]["content"] == "hi"
        assert gateway.records[0].output_tokens == 3


# ---------------------------------------------------------------------------
# Anthropic context management on the canonical path
# ---------------------------------------------------------------------------


class TestAnthropicContextManagement:
    @pytest.mark.parametrize("stream", [False, True])
    def test_a_translating_provider_refuses_compaction(self, stream: bool):
        gateway = Gateway(passthrough=False)
        resp = gateway.post(
            "/v1/messages",
            {
                "model": MODEL,
                "max_tokens": 10,
                "stream": stream,
                "messages": [
                    {"role": "assistant", "content": [{"type": "compaction", "content": "s"}]},
                    {"role": "user", "content": "go on"},
                ],
            },
        )
        assert resp.status_code == 400
        assert "cannot carry Anthropic context_management" in resp.text
        assert gateway.provider.canonical_requests == []


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------


class TestUsageReaders:
    def test_responses_reader_reads_the_terminal_event(self):
        usage = TokenUsage()
        reader = ResponsesStreamUsage()
        for line in _responses_lines():
            reader.feed(line, usage)
        assert (usage.input_tokens, usage.output_tokens, usage.reasoning_tokens) == (1200, 40, 12)
        assert reader.is_terminal('data: {"type": "response.completed"}')
        assert not reader.is_terminal('data: {"type": "response.output_item.done"}')

    def test_chat_reader_reads_the_usage_chunk(self):
        usage = TokenUsage()
        reader = ChatCompletionsStreamUsage()
        reader.feed('data: {"usage": {"prompt_tokens": 4, "completion_tokens": 2}}', usage)
        assert (usage.input_tokens, usage.output_tokens) == (4, 2)
        assert reader.is_terminal("data: [DONE]\n")

    def test_anthropic_reader_adds_compaction_iterations(self):
        usage = TokenUsage()
        delta = {
            "type": "message_delta",
            "usage": {
                "input_tokens": 23000,
                "output_tokens": 1000,
                "iterations": [
                    {"type": "compaction", "input_tokens": 180000, "output_tokens": 3500},
                    {"type": "message", "input_tokens": 23000, "output_tokens": 1000},
                ],
            },
        }
        ANTHROPIC_STREAM_USAGE.feed(f"data: {json.dumps(delta)}", usage)
        assert (usage.input_tokens, usage.output_tokens) == (203000, 4500)

    def test_native_response_usage(self):
        assert (
            native_response_usage(
                NativeProtocol.OPENAI_CHAT_COMPLETIONS, {"usage": {"prompt_tokens": 1}}
            ).input_tokens
            == 1
        )


class TestNativeOnlyFeatures:
    def test_detects_what_the_translation_cannot_carry(self):
        body = {
            "previous_response_id": "r",
            "context_management": [{"type": "compaction"}],
            "input": [COMPACTION, {"type": "item_reference"}, {"role": "user", "content": "x"}],
            "tools": [{"type": "function"}, {"type": "web_search"}],
        }
        assert responses_native_only_features(body) == [
            "previous_response_id",
            "tool type 'web_search'",
            "context_management",
            "input item type 'compaction'",
            "input item type 'item_reference'",
        ]
        assert responses_native_only_features({"input": "plain"}) == []

    def test_compaction_requests(self):
        assert responses_requests_compaction({"context_management": [{}]})
        assert responses_requests_compaction({"input": [{"type": "compaction_trigger"}]})
        assert not responses_requests_compaction({"input": [COMPACTION]})


class TestStripNativeImages:
    def test_chat_completions(self):
        payload = {
            "messages": [
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "u"}}]},
                {"role": "user", "content": "text"},
            ]
        }
        stripped = strip_native_images(NativeProtocol.OPENAI_CHAT_COMPLETIONS, payload)
        assert stripped["messages"][0]["content"] == [{"type": "text", "text": "[image removed]"}]
        assert stripped["messages"][1] == payload["messages"][1]

    def test_responses_without_a_list_input(self):
        payload = {"input": "plain"}
        assert strip_native_images(NativeProtocol.OPENAI_RESPONSES, payload) is payload


async def test_mid_stream_failure_becomes_a_response_failed_event():
    async def broken() -> AsyncIterator[str]:
        yield "data: {}\n"
        raise RuntimeError("upstream dropped")

    chunks = [
        c async for c in responses_stream_errors_reported(broken(), response_id="r", model=MODEL)
    ]
    failed = json.loads(chunks[-1].split("data: ", 1)[1])
    assert failed["type"] == "response.failed"
    assert failed["response"]["error"]["message"] == "upstream dropped"
