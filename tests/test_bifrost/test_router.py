"""Tests for ModelRouter: alias expansion, provider selection, and all routing strategies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bifrost.config import BifrostConfig, ProviderConfig, RoutingStrategy
from bifrost.ports.provider import ProviderError, ProviderPort
from bifrost.router import ModelRouter, RouterError
from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    Message,
    TextBlock,
    UsageInfo,
)


def _make_response(text: str = "OK") -> AnthropicResponse:
    return AnthropicResponse(
        id="msg_test",
        content=[TextBlock(text=text)],
        model="some-model",
        stop_reason="end_turn",
        usage=UsageInfo(input_tokens=5, output_tokens=3),
    )


def _make_request(model: str = "gpt-4o") -> AnthropicRequest:
    return AnthropicRequest(
        model=model,
        max_tokens=100,
        messages=[Message(role="user", content="Hello")],
    )


class FakeProvider(ProviderPort):
    """Test double for a provider adapter."""

    def __init__(self, response: AnthropicResponse | None = None, raises: Exception | None = None):
        self._response = response or _make_response()
        self._raises = raises
        self.complete_calls: list = []
        self.stream_calls: list = []
        self.closed = False

    async def complete(self, request: AnthropicRequest, model: str) -> AnthropicResponse:
        self.complete_calls.append((request, model))
        if self._raises:
            raise self._raises
        return self._response

    async def stream(self, request: AnthropicRequest, model: str) -> AsyncIterator[str]:
        self.stream_calls.append((request, model))
        if self._raises:
            raise self._raises
        yield "data: test\n\n"

    async def close(self) -> None:
        self.closed = True


def _http_error(status: int) -> httpx.HTTPStatusError:
    mock_resp = MagicMock()
    mock_resp.status_code = status
    return httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp)


class TestAliasResolution:
    @pytest.mark.asyncio
    async def test_alias_expanded_before_routing(self):
        cfg = BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o"])},
            aliases={"smart": "gpt-4o"},
        )
        router = ModelRouter(cfg)
        fake = FakeProvider()
        router._adapters["openai"] = fake

        req = _make_request(model="smart")
        await router.complete(req)

        assert fake.complete_calls[0][1] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_unknown_model_raises_router_error(self):
        cfg = BifrostConfig(providers={"openai": ProviderConfig(models=["gpt-4o"])})
        router = ModelRouter(cfg)

        req = _make_request(model="unknown-model-xyz")
        with pytest.raises(RouterError, match="No provider configured"):
            await router.complete(req)

    @pytest.mark.asyncio
    async def test_alias_to_unknown_model_raises(self):
        cfg = BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o"])},
            aliases={"mystery": "non-existent-model"},
        )
        router = ModelRouter(cfg)
        req = _make_request(model="mystery")
        with pytest.raises(RouterError):
            await router.complete(req)


class TestProviderRouting:
    @pytest.mark.asyncio
    async def test_routes_to_correct_provider(self):
        cfg = BifrostConfig(
            providers={
                "anthropic": ProviderConfig(models=["claude-sonnet-4-20250514"]),
                "openai": ProviderConfig(models=["gpt-4o"]),
            }
        )
        router = ModelRouter(cfg)
        fake_anthropic = FakeProvider()
        fake_openai = FakeProvider()
        router._adapters["anthropic"] = fake_anthropic
        router._adapters["openai"] = fake_openai

        req_openai = _make_request(model="gpt-4o")
        await router.complete(req_openai)
        assert len(fake_openai.complete_calls) == 1
        assert len(fake_anthropic.complete_calls) == 0

        req_anthropic = _make_request(model="claude-sonnet-4-20250514")
        await router.complete(req_anthropic)
        assert len(fake_anthropic.complete_calls) == 1

    @pytest.mark.asyncio
    async def test_correct_model_forwarded_to_adapter(self):
        cfg = BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o-mini"])},
        )
        router = ModelRouter(cfg)
        fake = FakeProvider()
        router._adapters["openai"] = fake

        req = _make_request(model="gpt-4o-mini")
        await router.complete(req)
        assert fake.complete_calls[0][1] == "gpt-4o-mini"


class TestFailoverStrategy:
    @pytest.mark.asyncio
    async def test_failover_on_http_503(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
        )
        router = ModelRouter(cfg)
        primary = FakeProvider(raises=_http_error(503))
        backup = FakeProvider()
        router._adapters["openai"] = primary
        router._adapters["backup"] = backup

        req = _make_request(model="gpt-4o")
        result = await router.complete(req)
        assert result.content[0].text == "OK"
        assert len(backup.complete_calls) == 1

    @pytest.mark.asyncio
    async def test_failover_not_triggered_on_non_retryable_http_error(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
        )
        router = ModelRouter(cfg)
        primary = FakeProvider(raises=_http_error(401))
        backup = FakeProvider()
        router._adapters["openai"] = primary
        router._adapters["backup"] = backup

        req = _make_request(model="gpt-4o")
        with pytest.raises(httpx.HTTPStatusError):
            await router.complete(req)
        assert len(backup.complete_calls) == 0

    @pytest.mark.asyncio
    async def test_direct_strategy_no_failover(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.DIRECT,
        )
        router = ModelRouter(cfg)
        primary = FakeProvider(raises=_http_error(503))
        backup = FakeProvider()
        router._adapters["openai"] = primary
        router._adapters["backup"] = backup

        req = _make_request(model="gpt-4o")
        with pytest.raises(RouterError):
            await router.complete(req)
        assert len(backup.complete_calls) == 0

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises_router_error(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
        )
        router = ModelRouter(cfg)
        exc = _http_error(503)
        router._adapters["openai"] = FakeProvider(raises=exc)
        router._adapters["backup"] = FakeProvider(raises=exc)

        req = _make_request(model="gpt-4o")
        with pytest.raises(RouterError, match="All providers failed"):
            await router.complete(req)

    @pytest.mark.asyncio
    async def test_failover_on_provider_error(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
        )
        router = ModelRouter(cfg)
        router._adapters["openai"] = FakeProvider(raises=ProviderError("down"))
        router._adapters["backup"] = FakeProvider()

        req = _make_request(model="gpt-4o")
        result = await router.complete(req)
        assert result.content[0].text == "OK"


class TestCostOptimisedStrategy:
    @pytest.mark.asyncio
    async def test_cheapest_provider_tried_first(self):
        cfg = BifrostConfig(
            providers={
                "expensive": ProviderConfig(models=["gpt-4o"], cost_per_token=10.0),
                "cheap": ProviderConfig(models=["gpt-4o"], cost_per_token=1.0),
            },
            routing_strategy=RoutingStrategy.COST_OPTIMISED,
        )
        router = ModelRouter(cfg)
        cheap = FakeProvider()
        expensive = FakeProvider()
        router._adapters["cheap"] = cheap
        router._adapters["expensive"] = expensive

        req = _make_request(model="gpt-4o")
        await router.complete(req)

        assert len(cheap.complete_calls) == 1
        assert len(expensive.complete_calls) == 0

    @pytest.mark.asyncio
    async def test_falls_back_to_next_cheapest_on_failure(self):
        cfg = BifrostConfig(
            providers={
                "expensive": ProviderConfig(models=["gpt-4o"], cost_per_token=10.0),
                "cheap": ProviderConfig(models=["gpt-4o"], cost_per_token=1.0),
            },
            routing_strategy=RoutingStrategy.COST_OPTIMISED,
        )
        router = ModelRouter(cfg)
        router._adapters["cheap"] = FakeProvider(raises=_http_error(503))
        router._adapters["expensive"] = FakeProvider()

        req = _make_request(model="gpt-4o")
        result = await router.complete(req)
        assert result.content[0].text == "OK"

    @pytest.mark.asyncio
    async def test_equal_cost_preserves_config_order(self):
        cfg = BifrostConfig(
            providers={
                "first": ProviderConfig(models=["gpt-4o"], cost_per_token=5.0),
                "second": ProviderConfig(models=["gpt-4o"], cost_per_token=5.0),
            },
            routing_strategy=RoutingStrategy.COST_OPTIMISED,
        )
        router = ModelRouter(cfg)
        first = FakeProvider()
        second = FakeProvider()
        router._adapters["first"] = first
        router._adapters["second"] = second

        req = _make_request(model="gpt-4o")
        await router.complete(req)
        # Python's sort is stable; first should be tried first since costs are equal.
        assert len(first.complete_calls) == 1
        assert len(second.complete_calls) == 0


class TestRoundRobinStrategy:
    @pytest.mark.asyncio
    async def test_cycles_through_providers(self):
        cfg = BifrostConfig(
            providers={
                "a": ProviderConfig(models=["gpt-4o"]),
                "b": ProviderConfig(models=["gpt-4o"]),
                "c": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.ROUND_ROBIN,
        )
        router = ModelRouter(cfg)
        providers = {"a": FakeProvider(), "b": FakeProvider(), "c": FakeProvider()}
        router._adapters.update(providers)

        req = _make_request(model="gpt-4o")
        await router.complete(req)
        await router.complete(req)
        await router.complete(req)

        assert len(providers["a"].complete_calls) == 1
        assert len(providers["b"].complete_calls) == 1
        assert len(providers["c"].complete_calls) == 1

    @pytest.mark.asyncio
    async def test_wraps_around_after_all_providers(self):
        cfg = BifrostConfig(
            providers={
                "a": ProviderConfig(models=["gpt-4o"]),
                "b": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.ROUND_ROBIN,
        )
        router = ModelRouter(cfg)
        a = FakeProvider()
        b = FakeProvider()
        router._adapters["a"] = a
        router._adapters["b"] = b

        req = _make_request(model="gpt-4o")
        # 4 requests should hit: a, b, a, b
        for _ in range(4):
            await router.complete(req)

        assert len(a.complete_calls) == 2
        assert len(b.complete_calls) == 2

    @pytest.mark.asyncio
    async def test_falls_back_to_next_on_failure(self):
        cfg = BifrostConfig(
            providers={
                "a": ProviderConfig(models=["gpt-4o"]),
                "b": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.ROUND_ROBIN,
        )
        router = ModelRouter(cfg)
        router._adapters["a"] = FakeProvider(raises=_http_error(503))
        router._adapters["b"] = FakeProvider()

        req = _make_request(model="gpt-4o")
        # First request starts at 'a', fails, falls back to 'b'.
        result = await router.complete(req)
        assert result.content[0].text == "OK"


class TestLatencyOptimisedStrategy:
    @pytest.mark.asyncio
    async def test_fastest_provider_tried_first(self):
        cfg = BifrostConfig(
            providers={
                "slow": ProviderConfig(models=["gpt-4o"]),
                "fast": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.LATENCY_OPTIMISED,
        )
        router = ModelRouter(cfg)
        # Seed latency data: slow = 2.0s, fast = 0.1s.
        router._latency_ewma["slow"] = 2.0
        router._latency_ewma["fast"] = 0.1

        slow = FakeProvider()
        fast = FakeProvider()
        router._adapters["slow"] = slow
        router._adapters["fast"] = fast

        req = _make_request(model="gpt-4o")
        await router.complete(req)

        assert len(fast.complete_calls) == 1
        assert len(slow.complete_calls) == 0

    @pytest.mark.asyncio
    async def test_unknown_latency_providers_placed_after_known(self):
        cfg = BifrostConfig(
            providers={
                "unknown": ProviderConfig(models=["gpt-4o"]),
                "known": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.LATENCY_OPTIMISED,
        )
        router = ModelRouter(cfg)
        router._latency_ewma["known"] = 0.5
        # "unknown" has no EWMA data — should be tried after "known".

        unknown = FakeProvider()
        known = FakeProvider()
        router._adapters["unknown"] = unknown
        router._adapters["known"] = known

        req = _make_request(model="gpt-4o")
        await router.complete(req)

        assert len(known.complete_calls) == 1
        assert len(unknown.complete_calls) == 0

    @pytest.mark.asyncio
    async def test_latency_recorded_after_successful_request(self):
        cfg = BifrostConfig(
            providers={"a": ProviderConfig(models=["gpt-4o"])},
            routing_strategy=RoutingStrategy.LATENCY_OPTIMISED,
        )
        router = ModelRouter(cfg)
        router._adapters["a"] = FakeProvider()

        req = _make_request(model="gpt-4o")
        await router.complete(req)

        assert "a" in router._latency_ewma
        assert router._latency_ewma["a"] >= 0.0

    @pytest.mark.asyncio
    async def test_falls_back_to_next_on_failure(self):
        cfg = BifrostConfig(
            providers={
                "fast": ProviderConfig(models=["gpt-4o"]),
                "slow": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.LATENCY_OPTIMISED,
        )
        router = ModelRouter(cfg)
        router._latency_ewma["fast"] = 0.1
        router._latency_ewma["slow"] = 2.0
        router._adapters["fast"] = FakeProvider(raises=_http_error(503))
        router._adapters["slow"] = FakeProvider()

        req = _make_request(model="gpt-4o")
        result = await router.complete(req)
        assert result.content[0].text == "OK"

    @pytest.mark.asyncio
    async def test_ewma_update_on_successive_calls(self):
        cfg = BifrostConfig(
            providers={"a": ProviderConfig(models=["gpt-4o"])},
            routing_strategy=RoutingStrategy.LATENCY_OPTIMISED,
        )
        router = ModelRouter(cfg)
        router._adapters["a"] = FakeProvider()
        router._latency_ewma["a"] = 1.0

        req = _make_request(model="gpt-4o")
        await router.complete(req)

        # After a fast call the EWMA should move toward the new (small) value.
        assert router._latency_ewma["a"] < 1.0


class TestBuildCandidatesDefaultCase:
    def test_unknown_strategy_raises_value_error(self):
        """The match default case must raise ValueError, not silently return None."""
        cfg = BifrostConfig(providers={"a": ProviderConfig(models=["gpt-4o"])})
        router = ModelRouter(cfg)

        # Inject an unrecognised strategy value, bypassing Pydantic field validation.
        object.__setattr__(cfg, "routing_strategy", "__invalid__")

        with pytest.raises(ValueError, match="Unknown routing strategy"):
            router._build_candidates("gpt-4o")


class TestStreamingRouter:
    @pytest.mark.asyncio
    async def test_stream_routes_correctly(self):
        cfg = BifrostConfig(providers={"openai": ProviderConfig(models=["gpt-4o"])})
        router = ModelRouter(cfg)
        fake = FakeProvider()
        router._adapters["openai"] = fake

        req = _make_request(model="gpt-4o")
        req_stream = req.model_copy(update={"stream": True})
        chunks = []
        async for chunk in router.stream(req_stream):
            chunks.append(chunk)
        assert chunks == ["data: test\n\n"]

    @pytest.mark.asyncio
    async def test_stream_failover_on_provider_error(self):
        cfg = BifrostConfig(
            providers={
                "openai": ProviderConfig(models=["gpt-4o"]),
                "backup": ProviderConfig(models=["gpt-4o"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
        )
        router = ModelRouter(cfg)
        router._adapters["openai"] = FakeProvider(raises=ProviderError("down"))
        router._adapters["backup"] = FakeProvider()

        req = _make_request(model="gpt-4o")
        chunks = []
        async for chunk in router.stream(req):
            chunks.append(chunk)
        assert chunks == ["data: test\n\n"]


class TestClose:
    @pytest.mark.asyncio
    async def test_close_closes_all_adapters(self):
        cfg = BifrostConfig(providers={"openai": ProviderConfig(models=["gpt-4o"])})
        router = ModelRouter(cfg)
        fake = FakeProvider()
        router._adapters["openai"] = fake
        await router.close()
        assert fake.closed is True
        assert router._adapters == {}


class TestAdapterLoading:
    @pytest.mark.asyncio
    async def test_adapter_loaded_lazily(self):
        cfg = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-20250514"])}
        )
        router = ModelRouter(cfg)
        assert router._adapters == {}

        with patch(
            "bifrost.adapters.anthropic.AnthropicAdapter.complete",
            new_callable=AsyncMock,
            return_value=_make_response(),
        ):
            req = _make_request(model="claude-sonnet-4-20250514")
            await router.complete(req)

        assert "anthropic" in router._adapters

    @pytest.mark.asyncio
    async def test_adapter_cached_on_second_call(self):
        cfg = BifrostConfig(providers={"openai": ProviderConfig(models=["gpt-4o"])})
        router = ModelRouter(cfg)
        fake = FakeProvider()
        router._adapters["openai"] = fake

        req = _make_request(model="gpt-4o")
        await router.complete(req)
        await router.complete(req)

        assert router._adapters["openai"] is fake


class TestModelThinkingFlag:
    """thinking/reasoning_effort follow the catalog's supports_thinking per model."""

    def _router(self, *, supports_thinking: bool) -> tuple[ModelRouter, FakeProvider]:
        from bifrost.config import ManagedModelConfig

        config = BifrostConfig(
            providers={"local": ProviderConfig(models=["llama3.1:8b"])},
            models=[
                ManagedModelConfig(
                    id="llama3.1:8b",
                    name="Llama",
                    vendor="local",
                    supports_thinking=supports_thinking,
                )
            ],
        )
        provider = FakeProvider(_make_response())
        router = ModelRouter(config)
        router._adapters["local"] = provider
        return router, provider

    async def test_controls_dropped_for_a_model_without_thinking(self):
        router, provider = self._router(supports_thinking=False)
        request = _make_request("llama3.1:8b")
        request.reasoning_effort = "high"
        request.thinking = {"type": "enabled"}
        await router.complete(request)
        sent, _ = provider.complete_calls[-1]
        assert sent.reasoning_effort is None
        assert sent.thinking is None
        assert sent.model == "llama3.1:8b"

    async def test_controls_kept_for_a_thinking_model_and_unknown_models(self):
        router, provider = self._router(supports_thinking=True)
        request = _make_request("llama3.1:8b")
        request.reasoning_effort = "high"
        await router.complete(request)
        assert provider.complete_calls[-1][0].reasoning_effort == "high"

        unknown = ModelRouter(BifrostConfig(providers={"local": ProviderConfig(models=["m"])}))
        unknown._adapters["local"] = provider
        request = _make_request("m")
        request.reasoning_effort = "max"
        await unknown.complete(request)
        assert provider.complete_calls[-1][0].reasoning_effort == "max"
