"""Tests for semantic response caching (NIU-486).

Covers:
- DisabledCache: always misses, never stores
- MemoryCache: LRU eviction, TTL expiry, per-key isolation
- RedisCache: mocked redis client interactions
- CacheConfig / BifrostConfig: cache field parsing
- _compute_cache_key: per-tenant isolation, key consistency
- Route integration: cache hit returns cached response with cost=0,
  cache miss stores response, streaming skips cache
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

from bifrost.adapters.cache.disabled import DisabledCache
from bifrost.adapters.cache.memory_cache import MemoryCache
from bifrost.config import BifrostConfig, CacheConfig, CacheMode, ProviderConfig
from bifrost.inbound.routes import _compute_cache_key
from bifrost.translation.models import (
    AnthropicRequest,
    AnthropicResponse,
    Message,
    TextBlock,
    UsageInfo,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL = "claude-sonnet-4-6"
_BODY = {"model": _MODEL, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 100}


def _response(text: str = "Hello!", model: str = _MODEL) -> AnthropicResponse:
    return AnthropicResponse(
        id="msg_test",
        content=[TextBlock(text=text)],
        model=model,
        stop_reason="end_turn",
        usage=UsageInfo(input_tokens=10, output_tokens=5),
    )


def _request(
    model: str = _MODEL,
    content: str = "Hello",
    system: str | None = None,
) -> AnthropicRequest:
    return AnthropicRequest(
        model=model,
        messages=[Message(role="user", content=content)],
        system=system,
    )


def _make_config(mode: str = "memory") -> BifrostConfig:
    return BifrostConfig(
        providers={"anthropic": ProviderConfig(models=[_MODEL])},
        cache=CacheConfig(mode=mode, default_ttl=300),
    )


# ---------------------------------------------------------------------------
# DisabledCache
# ---------------------------------------------------------------------------


class TestDisabledCache:
    async def test_get_always_returns_none(self):
        cache = DisabledCache()
        assert await cache.get("any-key") is None

    async def test_set_is_a_no_op(self):
        cache = DisabledCache()
        await cache.set("key", _response(), ttl=300)
        assert await cache.get("key") is None

    async def test_close_is_a_no_op(self):
        await DisabledCache().close()


# ---------------------------------------------------------------------------
# MemoryCache
# ---------------------------------------------------------------------------


class TestMemoryCache:
    async def test_miss_on_empty_cache(self):
        assert await MemoryCache().get("nonexistent") is None

    async def test_store_and_retrieve(self):
        cache = MemoryCache()
        r = _response("stored text")
        await cache.set("key1", r, ttl=300)
        retrieved = await cache.get("key1")
        assert retrieved is not None
        assert retrieved.content[0].text == "stored text"  # type: ignore[attr-defined]

    async def test_different_keys_are_isolated(self):
        cache = MemoryCache()
        await cache.set("key1", _response("r1"), ttl=300)
        await cache.set("key2", _response("r2"), ttl=300)
        got1 = await cache.get("key1")
        got2 = await cache.get("key2")
        assert got1 is not None and got1.content[0].text == "r1"  # type: ignore[attr-defined]
        assert got2 is not None and got2.content[0].text == "r2"  # type: ignore[attr-defined]

    async def test_expired_entry_returns_none(self):
        cache = MemoryCache()
        await cache.set("key", _response(), ttl=1)
        # Wind back the expiry to simulate elapsed TTL.
        key, (resp, _expiry) = next(iter(cache._store.items()))
        cache._store[key] = (resp, time.monotonic() - 1)
        assert await cache.get("key") is None

    async def test_lru_eviction_on_max_entries(self):
        cache = MemoryCache(max_entries=2)
        await cache.set("a", _response("a"), ttl=300)
        await cache.set("b", _response("b"), ttl=300)
        # Access "a" to make it most-recently-used.
        await cache.get("a")
        # Adding "c" evicts "b" (least-recently-used).
        await cache.set("c", _response("c"), ttl=300)
        assert await cache.get("a") is not None
        assert await cache.get("b") is None
        assert await cache.get("c") is not None

    async def test_update_existing_key_does_not_grow_cache(self):
        cache = MemoryCache(max_entries=2)
        r = _response()
        await cache.set("key", r, ttl=300)
        await cache.set("key", r, ttl=300)
        assert len(cache._store) == 1

    async def test_close_is_a_no_op(self):
        await MemoryCache().close()


# ---------------------------------------------------------------------------
# RedisCache (mocked)
# ---------------------------------------------------------------------------


class TestRedisCache:
    def _make_redis_cache(self, get_return=None, setex_raises=False):
        """Build a RedisCache with a fully mocked aioredis client."""
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=get_return)
        if setex_raises:
            mock_client.setex = AsyncMock(side_effect=Exception("connection error"))
        else:
            mock_client.setex = AsyncMock(return_value=True)
        mock_client.aclose = AsyncMock()

        mock_aioredis = MagicMock()
        mock_aioredis.from_url.return_value = mock_client

        with patch.dict("sys.modules", {"redis": MagicMock(), "redis.asyncio": mock_aioredis}):
            from bifrost.adapters.cache.redis_cache import RedisCache

            cache = RedisCache(redis_url="redis://localhost:6379")
            cache._client = mock_client
        return cache, mock_client

    async def test_get_miss_returns_none(self):
        cache, _ = self._make_redis_cache(get_return=None)
        assert await cache.get("key") is None

    async def test_get_hit_deserializes_response(self):
        r = _response("from redis")
        cache, _ = self._make_redis_cache(get_return=r.model_dump_json())
        result = await cache.get("key")
        assert result is not None
        assert result.content[0].text == "from redis"  # type: ignore[attr-defined]

    async def test_set_calls_setex_with_ttl(self):
        cache, mock_client = self._make_redis_cache()
        r = _response()
        await cache.set("my-key", r, ttl=300)
        mock_client.setex.assert_called_once_with("my-key", 300, r.model_dump_json())

    async def test_get_redis_error_returns_none(self):
        cache, mock_client = self._make_redis_cache()
        mock_client.get = AsyncMock(side_effect=Exception("redis down"))
        assert await cache.get("key") is None  # Graceful degradation

    async def test_set_redis_error_does_not_raise(self):
        cache, _ = self._make_redis_cache(setex_raises=True)
        await cache.set("key", _response(), ttl=300)  # Must not propagate

    async def test_close_calls_aclose(self):
        cache, mock_client = self._make_redis_cache()
        await cache.close()
        mock_client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# _compute_cache_key
# ---------------------------------------------------------------------------


class TestComputeCacheKey:
    def test_same_request_produces_same_key(self):
        req = _request(content="Hello")
        assert _compute_cache_key("t", req) == _compute_cache_key("t", req)

    def test_different_tenants_produce_different_keys(self):
        req = _request(content="Hello")
        assert _compute_cache_key("tenant-a", req) != _compute_cache_key("tenant-b", req)

    def test_different_models_produce_different_keys(self):
        r1 = _request(model=_MODEL, content="Hello")
        r2 = _request(model="claude-opus-4-6", content="Hello")
        assert _compute_cache_key("t", r1) != _compute_cache_key("t", r2)

    def test_different_content_produces_different_keys(self):
        r1 = _request(content="Hello")
        r2 = _request(content="Goodbye")
        assert _compute_cache_key("t", r1) != _compute_cache_key("t", r2)

    def test_different_system_prompts_produce_different_keys(self):
        r1 = _request(content="Hello", system="Be concise.")
        r2 = _request(content="Hello", system="Be verbose.")
        assert _compute_cache_key("t", r1) != _compute_cache_key("t", r2)

    def test_key_is_sha256_hex_64_chars(self):
        key = _compute_cache_key("t", _request())
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_stream_flag_does_not_affect_key(self):
        """Streaming flag must not affect the cache key (content is identical)."""
        r1 = AnthropicRequest(
            model=_MODEL, messages=[Message(role="user", content="Hi")], stream=False
        )
        r2 = AnthropicRequest(
            model=_MODEL, messages=[Message(role="user", content="Hi")], stream=True
        )
        assert _compute_cache_key("t", r1) == _compute_cache_key("t", r2)

    def test_system_none_and_empty_string_produce_different_keys(self):
        """None system is excluded from the key; empty-string system is included."""
        r_none = _request(content="Hi", system=None)
        r_empty = _request(content="Hi", system="")
        assert _compute_cache_key("t", r_none) != _compute_cache_key("t", r_empty)

    def test_different_max_tokens_produce_different_keys(self):
        """max_tokens affects the response, so must affect the cache key."""
        r1 = AnthropicRequest(
            model=_MODEL, messages=[Message(role="user", content="Hi")], max_tokens=100
        )
        r2 = AnthropicRequest(
            model=_MODEL, messages=[Message(role="user", content="Hi")], max_tokens=500
        )
        assert _compute_cache_key("t", r1) != _compute_cache_key("t", r2)

    def test_different_temperature_produces_different_keys(self):
        """temperature affects the response, so must affect the cache key."""
        r1 = AnthropicRequest(
            model=_MODEL, messages=[Message(role="user", content="Hi")], temperature=0.0
        )
        r2 = AnthropicRequest(
            model=_MODEL, messages=[Message(role="user", content="Hi")], temperature=1.0
        )
        assert _compute_cache_key("t", r1) != _compute_cache_key("t", r2)


# ---------------------------------------------------------------------------
# CacheConfig / BifrostConfig
# ---------------------------------------------------------------------------


class TestCacheConfig:
    def test_defaults_to_disabled_mode(self):
        assert CacheConfig().mode == CacheMode.DISABLED

    def test_parses_memory_mode(self):
        cfg = CacheConfig(mode="memory", max_memory_entries=500)
        assert cfg.mode == CacheMode.MEMORY
        assert cfg.max_memory_entries == 500

    def test_parses_redis_mode(self):
        cfg = CacheConfig(mode="redis", redis_url="redis://myhost:6379", default_ttl=600)
        assert cfg.mode == CacheMode.REDIS
        assert cfg.redis_url == "redis://myhost:6379"
        assert cfg.default_ttl == 600

    def test_bifrost_config_has_cache_field(self):
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=[_MODEL])},
            cache=CacheConfig(mode="memory"),
        )
        assert config.cache.mode == CacheMode.MEMORY


# ---------------------------------------------------------------------------
# Route integration
# ---------------------------------------------------------------------------


class TestRoutesCacheIntegration:
    """Integration tests for cache behaviour in the /v1/messages endpoint."""

    async def test_disabled_cache_calls_provider_every_time(self):
        from fastapi.testclient import TestClient

        from bifrost.app import create_app

        config = _make_config("disabled")
        app = create_app(config)
        mock_resp = _response()

        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as mc:
            mc.return_value = mock_resp
            with TestClient(app) as client:
                r1 = client.post("/api/v1/bifrost/v1/messages", json=_BODY)
                r2 = client.post("/api/v1/bifrost/v1/messages", json=_BODY)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert mc.call_count == 2  # Both requests hit the provider

    async def test_memory_cache_second_request_served_from_cache(self):
        from fastapi.testclient import TestClient

        from bifrost.app import create_app

        config = _make_config("memory")
        cache = MemoryCache(max_entries=100)
        mock_resp = _response()

        with patch("bifrost.app._build_cache", return_value=cache):
            app = create_app(config)
            with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as mc:
                mc.return_value = mock_resp
                with TestClient(app) as client:
                    r1 = client.post("/api/v1/bifrost/v1/messages", json=_BODY)
                    r2 = client.post("/api/v1/bifrost/v1/messages", json=_BODY)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert mc.call_count == 1  # Second request served from cache

    async def test_different_content_produces_cache_miss(self):
        from fastapi.testclient import TestClient

        from bifrost.app import create_app

        config = _make_config("memory")
        cache = MemoryCache(max_entries=100)
        mock_resp = _response()
        body_hi = {**_BODY, "messages": [{"role": "user", "content": "Hi"}]}
        body_bye = {**_BODY, "messages": [{"role": "user", "content": "Bye"}]}

        with patch("bifrost.app._build_cache", return_value=cache):
            app = create_app(config)
            with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as mc:
                mc.return_value = mock_resp
                with TestClient(app) as client:
                    r1 = client.post("/api/v1/bifrost/v1/messages", json=body_hi)
                    r2 = client.post("/api/v1/bifrost/v1/messages", json=body_bye)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert mc.call_count == 2  # Different prompts → both hit the provider

    async def test_streaming_request_skips_cache(self):
        from fastapi.testclient import TestClient

        from bifrost.app import create_app

        config = _make_config("memory")
        cache = MemoryCache(max_entries=100)

        async def _fake_stream(_request):
            yield "data: {}\n\n"

        with patch("bifrost.app._build_cache", return_value=cache):
            app = create_app(config)
            with patch("bifrost.router.ModelRouter.stream", return_value=_fake_stream(None)):
                with TestClient(app) as client:
                    client.post(
                        "/api/v1/bifrost/v1/messages",
                        json={**_BODY, "stream": True},
                    )
        # Streaming must not populate the cache.
        assert len(cache._store) == 0

    async def test_cache_hit_appears_in_usage_records(self):
        """On a cache hit, a zero-cost usage record with cache_hit=True is stored."""
        from fastapi.testclient import TestClient

        from bifrost.adapters.memory_store import MemoryUsageStore
        from bifrost.app import create_app

        config = _make_config("memory")
        cache = MemoryCache(max_entries=100)
        store = MemoryUsageStore()
        mock_resp = _response()

        with patch("bifrost.app._build_cache", return_value=cache):
            with patch("bifrost.app._build_usage_store", return_value=store):
                app = create_app(config)
                with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as mc:
                    mc.return_value = mock_resp
                    with TestClient(app) as client:
                        client.post(
                            "/api/v1/bifrost/v1/messages",
                            json=_BODY,
                        )  # miss — populates cache
                        client.post("/api/v1/bifrost/v1/messages", json=_BODY)  # hit

        records = store._records
        assert len(records) == 2
        hit_records = [r for r in records if r.cache_hit]
        assert len(hit_records) == 1
        assert hit_records[0].cost_usd == 0.0
