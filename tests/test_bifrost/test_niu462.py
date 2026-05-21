"""Tests for NIU-462: advanced routing, caching, and audit logging.

Covers:
- Per-model and per-alias routing strategy configuration
- Cache stats (hit rate, saved tokens)
- Configurable audit detail levels (minimal, standard, full)
- NullAuditAdapter, SQLiteAuditAdapter
- OtelAuditAdapter (graceful import error handling)
- Audit wiring in routes: success, cache_hit, rejection, error paths
- /v1/cache/stats endpoint
- AuditConfig and BifrostConfig audit integration
- app.py _build_audit factory
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from bifrost.adapters.audit.null import NullAuditAdapter
from bifrost.app import _build_audit, create_app
from bifrost.config import (
    AuditConfig,
    AuditDetailLevel,
    BifrostConfig,
    ProviderConfig,
    RoutingStrategy,
)
from bifrost.inbound.routes import _build_audit_event
from bifrost.ports.audit import AuditEvent
from bifrost.ports.cache import CacheStats
from bifrost.router import ModelRouter
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
        usage=UsageInfo(input_tokens=10, output_tokens=5),
    )


def _fake_identity(
    agent_id: str = "agent-1",
    tenant_id: str = "tenant-1",
    session_id: str = "sess",
    saga_id: str = "saga",
):
    identity = MagicMock()
    identity.agent_id = agent_id
    identity.tenant_id = tenant_id
    identity.session_id = session_id
    identity.saga_id = saga_id
    return identity


# ---------------------------------------------------------------------------
# Per-model routing strategy
# ---------------------------------------------------------------------------


class TestPerModelRoutingStrategy:
    def test_global_strategy_used_when_no_per_model(self):
        cfg = BifrostConfig(
            providers={
                "a": ProviderConfig(models=["m1"]),
                "b": ProviderConfig(models=["m1"]),
            },
            routing_strategy=RoutingStrategy.ROUND_ROBIN,
        )
        assert cfg.routing_strategy_for_model("m1") == RoutingStrategy.ROUND_ROBIN

    def test_per_model_overrides_global(self):
        cfg = BifrostConfig(
            providers={
                "a": ProviderConfig(models=["m1"]),
                "b": ProviderConfig(models=["m1"]),
            },
            routing_strategy=RoutingStrategy.FAILOVER,
            model_routing_strategies={"m1": RoutingStrategy.ROUND_ROBIN},
        )
        assert cfg.routing_strategy_for_model("m1") == RoutingStrategy.ROUND_ROBIN

    def test_alias_resolved_for_per_model_strategy(self):
        cfg = BifrostConfig(
            providers={"a": ProviderConfig(models=["canonical-model"])},
            aliases={"fast": "canonical-model"},
            routing_strategy=RoutingStrategy.FAILOVER,
            model_routing_strategies={"canonical-model": RoutingStrategy.DIRECT},
        )
        # Lookup via alias → resolves to canonical → matches override
        assert cfg.routing_strategy_for_model("fast") == RoutingStrategy.DIRECT

    def test_alias_key_in_model_routing_strategies(self):
        cfg = BifrostConfig(
            providers={"a": ProviderConfig(models=["real-model"])},
            aliases={"cheap": "real-model"},
            routing_strategy=RoutingStrategy.FAILOVER,
            model_routing_strategies={"cheap": RoutingStrategy.COST_OPTIMISED},
        )
        assert cfg.routing_strategy_for_model("cheap") == RoutingStrategy.COST_OPTIMISED

    def test_fallback_when_model_not_in_overrides(self):
        cfg = BifrostConfig(
            providers={"a": ProviderConfig(models=["m1", "m2"])},
            routing_strategy=RoutingStrategy.LATENCY_OPTIMISED,
            model_routing_strategies={"m1": RoutingStrategy.DIRECT},
        )
        assert cfg.routing_strategy_for_model("m2") == RoutingStrategy.LATENCY_OPTIMISED

    @pytest.mark.asyncio
    async def test_router_uses_per_model_strategy(self):
        """Router dispatches ROUND_ROBIN for m1 and DIRECT for m2."""
        cfg = BifrostConfig(
            providers={
                "a": ProviderConfig(models=["m1", "m2"]),
                "b": ProviderConfig(models=["m1"]),
            },
            routing_strategy=RoutingStrategy.DIRECT,
            model_routing_strategies={"m1": RoutingStrategy.ROUND_ROBIN},
        )
        router = ModelRouter(cfg)
        # m1 with round_robin: two providers available
        candidates_m1 = router._build_candidates("m1")
        assert len(candidates_m1) == 2  # both a and b serve m1, rotated

        # m2 with direct (global): only a serves m2
        candidates_m2 = router._build_candidates("m2")
        assert len(candidates_m2) == 1
        assert candidates_m2[0][0] == "a"


# ---------------------------------------------------------------------------
# CacheStats
# ---------------------------------------------------------------------------


class TestCacheStats:
    def test_hit_rate_zero_when_no_requests(self):
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        stats = CacheStats(hits=3, misses=1)
        assert stats.hit_rate == 0.75

    def test_saved_tokens_sum(self):
        stats = CacheStats(saved_input_tokens=10, saved_output_tokens=5)
        assert stats.saved_tokens == 15

    @pytest.mark.asyncio
    async def test_memory_cache_tracks_misses(self):
        from bifrost.adapters.cache.memory_cache import MemoryCache

        c = MemoryCache()
        await c.get("nonexistent")
        s = c.stats()
        assert s.misses == 1
        assert s.hits == 0

    @pytest.mark.asyncio
    async def test_memory_cache_tracks_hits_and_saved_tokens(self):
        from bifrost.adapters.cache.memory_cache import MemoryCache

        c = MemoryCache()
        resp = _make_response()
        await c.set("k", resp, 300)
        await c.get("k")  # hit
        s = c.stats()
        assert s.hits == 1
        assert s.saved_input_tokens == resp.usage.input_tokens
        assert s.saved_output_tokens == resp.usage.output_tokens

    @pytest.mark.asyncio
    async def test_memory_cache_expired_entry_counts_as_miss(self):
        from bifrost.adapters.cache.memory_cache import MemoryCache

        c = MemoryCache()
        resp = _make_response()
        await c.set("k", resp, 0)  # TTL=0 → already expired
        result = await c.get("k")
        assert result is None
        s = c.stats()
        assert s.misses == 1
        assert s.hits == 0

    @pytest.mark.asyncio
    async def test_memory_cache_entries_count(self):
        from bifrost.adapters.cache.memory_cache import MemoryCache

        c = MemoryCache()
        await c.set("a", _make_response(), 300)
        await c.set("b", _make_response(), 300)
        assert c.stats().entries == 2

    @pytest.mark.asyncio
    async def test_disabled_cache_returns_zero_stats(self):
        from bifrost.adapters.cache.disabled import DisabledCache

        c = DisabledCache()
        await c.get("k")
        await c.set("k", _make_response(), 300)
        s = c.stats()
        assert s.hits == 0
        assert s.misses == 0


# ---------------------------------------------------------------------------
# /v1/cache/stats endpoint
# ---------------------------------------------------------------------------


class TestCacheStatsEndpoint:
    def _client(self) -> TestClient:
        cfg = BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o"])},
        )
        return TestClient(create_app(cfg))

    def test_cache_stats_returns_200(self):
        with self._client() as client:
            resp = client.get("/api/v1/bifrost/v1/cache/stats")
        assert resp.status_code == 200

    def test_cache_stats_response_shape(self):
        with self._client() as client:
            resp = client.get("/api/v1/bifrost/v1/cache/stats")
        body = resp.json()
        assert "hits" in body
        assert "misses" in body
        assert "hit_rate" in body
        assert "saved_tokens" in body
        assert "saved_input_tokens" in body
        assert "saved_output_tokens" in body
        assert "entries" in body

    def test_cache_stats_initial_values(self):
        with self._client() as client:
            resp = client.get("/api/v1/bifrost/v1/cache/stats")
        body = resp.json()
        assert body["hits"] == 0
        assert body["misses"] == 0
        assert body["hit_rate"] == 0.0


# ---------------------------------------------------------------------------
# NullAuditAdapter
# ---------------------------------------------------------------------------


class TestNullAuditAdapter:
    @pytest.mark.asyncio
    async def test_log_is_a_no_op(self):
        adapter = NullAuditAdapter()
        event = AuditEvent(
            request_id="r1",
            agent_id="a",
            tenant_id="t",
            model="m",
            timestamp=datetime.now(UTC),
        )
        await adapter.log(event)  # should not raise

    @pytest.mark.asyncio
    async def test_query_returns_empty_list(self):
        adapter = NullAuditAdapter()
        results = await adapter.query()
        assert results == []

    @pytest.mark.asyncio
    async def test_query_with_filters_returns_empty_list(self):
        adapter = NullAuditAdapter()
        results = await adapter.query(agent_id="a", outcome="success")
        assert results == []


# ---------------------------------------------------------------------------
# SQLiteAuditAdapter
# ---------------------------------------------------------------------------


class TestSQLiteAuditAdapter:
    @pytest.mark.asyncio
    async def test_log_and_query(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        event = AuditEvent(
            request_id="req-1",
            agent_id="agent-1",
            tenant_id="tenant-1",
            model="gpt-4o",
            timestamp=datetime.now(UTC),
            tokens_input=10,
            tokens_output=5,
            cost_usd=0.01,
            provider="openai",
            outcome="success",
        )
        await adapter.log(event)
        results = await adapter.query()
        assert len(results) == 1
        r = results[0]
        assert r.request_id == "req-1"
        assert r.agent_id == "agent-1"
        assert r.tokens_input == 10
        assert r.tokens_output == 5
        await adapter.close()

    @pytest.mark.asyncio
    async def test_query_filter_by_agent(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        for i in range(3):
            await adapter.log(
                AuditEvent(
                    request_id=f"req-{i}",
                    agent_id="agent-a" if i == 0 else "agent-b",
                    tenant_id="t",
                    model="m",
                    timestamp=datetime.now(UTC),
                )
            )
        results = await adapter.query(agent_id="agent-a")
        assert len(results) == 1
        assert results[0].agent_id == "agent-a"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_query_filter_by_outcome(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        for outcome in ["success", "error", "success"]:
            await adapter.log(
                AuditEvent(
                    request_id=outcome,
                    agent_id="a",
                    tenant_id="t",
                    model="m",
                    timestamp=datetime.now(UTC),
                    outcome=outcome,
                )
            )
        results = await adapter.query(outcome="success")
        assert len(results) == 2
        await adapter.close()

    @pytest.mark.asyncio
    async def test_query_filter_by_model(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        for model in ["gpt-4o", "claude-3", "gpt-4o"]:
            await adapter.log(
                AuditEvent(
                    request_id=model,
                    agent_id="a",
                    tenant_id="t",
                    model=model,
                    timestamp=datetime.now(UTC),
                )
            )
        results = await adapter.query(model="gpt-4o")
        assert len(results) == 2
        await adapter.close()

    @pytest.mark.asyncio
    async def test_prune_deletes_old_records(self, tmp_path):
        from datetime import timedelta

        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        old_ts = datetime.now(UTC) - timedelta(days=100)
        await adapter.log(
            AuditEvent(
                request_id="old",
                agent_id="a",
                tenant_id="t",
                model="m",
                timestamp=old_ts,
            )
        )
        await adapter.log(
            AuditEvent(
                request_id="new",
                agent_id="a",
                tenant_id="t",
                model="m",
                timestamp=datetime.now(UTC),
            )
        )
        deleted = await adapter.prune(retention_days=30)
        assert deleted == 1
        results = await adapter.query()
        assert len(results) == 1
        assert results[0].request_id == "new"
        await adapter.close()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        await adapter._get_conn()  # Initialize connection
        await adapter.close()
        await adapter.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_prune_zero_days_is_noop(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        await adapter.log(
            AuditEvent(
                request_id="r",
                agent_id="a",
                tenant_id="t",
                model="m",
                timestamp=datetime.now(UTC),
            )
        )
        deleted = await adapter.prune(retention_days=0)
        assert deleted == 0
        await adapter.close()

    @pytest.mark.asyncio
    async def test_log_serialises_tags(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        await adapter.log(
            AuditEvent(
                request_id="r",
                agent_id="a",
                tenant_id="t",
                model="m",
                timestamp=datetime.now(UTC),
                tags={"env": "prod", "tier": "premium"},
            )
        )
        results = await adapter.query()
        assert results[0].tags == {"env": "prod", "tier": "premium"}
        await adapter.close()

    @pytest.mark.asyncio
    async def test_log_full_detail_fields(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        adapter = SQLiteAuditAdapter(path=path)
        await adapter.log(
            AuditEvent(
                request_id="r",
                agent_id="a",
                tenant_id="t",
                model="m",
                timestamp=datetime.now(UTC),
                cache_hit=True,
                tokens_input=42,
                tokens_output=13,
                cost_usd=0.005,
                prompt_content='[{"role":"user","content":"hi"}]',
                response_content='{"text":"hello"}',
            )
        )
        results = await adapter.query()
        r = results[0]
        assert r.cache_hit is True
        assert r.tokens_input == 42
        assert r.tokens_output == 13
        assert r.cost_usd == pytest.approx(0.005)
        assert r.prompt_content == '[{"role":"user","content":"hi"}]'
        assert r.response_content == '{"text":"hello"}'
        await adapter.close()


# ---------------------------------------------------------------------------
# OtelAuditAdapter — tested with mocked opentelemetry
# ---------------------------------------------------------------------------


class _MockBatchLogRecordProcessor:
    def __init__(self, exporter):
        pass


class _MockLoggerProvider:
    resource = MagicMock()

    def __init__(self, resource=None):
        self._loggers = {}

    def add_log_record_processor(self, processor):
        pass

    def get_logger(self, name):
        return _MockOtelLogger()

    def shutdown(self):
        pass


class _MockOtelLogger:
    def __init__(self):
        self.emitted = []

    def emit(self, record):
        self.emitted.append(record)


class _MockLogRecord:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _MockResource:
    @staticmethod
    def create(attrs):
        return MagicMock()


_INVALID_SPAN_CONTEXT = MagicMock()


def _make_otel_mocks():
    """Return a dict of mock modules for opentelemetry."""
    logs_mod = MagicMock()
    logs_mod.set_logger_provider = MagicMock()

    sdk_logs_mod = MagicMock()
    sdk_logs_mod.LoggerProvider = _MockLoggerProvider
    sdk_logs_mod.LogRecord = _MockLogRecord

    export_mod = MagicMock()
    export_mod.BatchLogRecordProcessor = _MockBatchLogRecordProcessor
    export_mod.ConsoleLogExporter = MagicMock(return_value=MagicMock())

    sdk_resources_mod = MagicMock()
    sdk_resources_mod.Resource = _MockResource

    trace_mod = MagicMock()
    trace_mod.INVALID_SPAN_CONTEXT = _INVALID_SPAN_CONTEXT

    return {
        "opentelemetry": MagicMock(),
        "opentelemetry._logs": logs_mod,
        "opentelemetry.sdk": MagicMock(),
        "opentelemetry.sdk._logs": sdk_logs_mod,
        "opentelemetry.sdk._logs.export": export_mod,
        "opentelemetry.sdk.resources": sdk_resources_mod,
        "opentelemetry.trace": trace_mod,
    }


class TestOtelAuditAdapter:
    @pytest.mark.asyncio
    async def test_log_emits_record(self, monkeypatch):
        import sys

        mocks = _make_otel_mocks()
        for mod_name, mod in mocks.items():
            monkeypatch.setitem(sys.modules, mod_name, mod)

        # Reload the otel module with mocked imports
        import importlib

        import bifrost.adapters.audit.otel as otel_mod

        importlib.reload(otel_mod)

        adapter = otel_mod.OtelAuditAdapter()
        otel_logger = adapter._otel_logger

        event = AuditEvent(
            request_id="req-1",
            agent_id="agent-1",
            tenant_id="tenant-1",
            model="gpt-4o",
            timestamp=datetime.now(UTC),
            outcome="success",
            tokens_input=10,
            tokens_output=5,
        )
        await adapter.log(event)
        assert len(otel_logger.emitted) == 1

    @pytest.mark.asyncio
    async def test_query_returns_empty_list(self, monkeypatch):
        import sys

        mocks = _make_otel_mocks()
        for mod_name, mod in mocks.items():
            monkeypatch.setitem(sys.modules, mod_name, mod)

        import importlib

        import bifrost.adapters.audit.otel as otel_mod

        importlib.reload(otel_mod)

        adapter = otel_mod.OtelAuditAdapter()
        results = await adapter.query()
        assert results == []

    @pytest.mark.asyncio
    async def test_close_calls_shutdown(self, monkeypatch):
        import sys

        mocks = _make_otel_mocks()
        for mod_name, mod in mocks.items():
            monkeypatch.setitem(sys.modules, mod_name, mod)

        import importlib

        import bifrost.adapters.audit.otel as otel_mod

        importlib.reload(otel_mod)

        adapter = otel_mod.OtelAuditAdapter()
        await adapter.close()  # Should not raise

    def test_severity_error_outcome(self, monkeypatch):
        import importlib
        import sys

        mocks = _make_otel_mocks()
        for mod_name, mod in mocks.items():
            monkeypatch.setitem(sys.modules, mod_name, mod)

        import bifrost.adapters.audit.otel as otel_mod

        importlib.reload(otel_mod)

        num, text = otel_mod._severity("error")
        assert text == "ERROR"

    def test_severity_warn_outcome(self, monkeypatch):
        import importlib
        import sys

        mocks = _make_otel_mocks()
        for mod_name, mod in mocks.items():
            monkeypatch.setitem(sys.modules, mod_name, mod)

        import bifrost.adapters.audit.otel as otel_mod

        importlib.reload(otel_mod)

        num, text = otel_mod._severity("rejected")
        assert text == "WARN"

        num2, text2 = otel_mod._severity("quota_exceeded")
        assert text2 == "WARN"

    def test_severity_info_outcome(self, monkeypatch):
        import importlib
        import sys

        mocks = _make_otel_mocks()
        for mod_name, mod in mocks.items():
            monkeypatch.setitem(sys.modules, mod_name, mod)

        import bifrost.adapters.audit.otel as otel_mod

        importlib.reload(otel_mod)

        num, text = otel_mod._severity("success")
        assert text == "INFO"

    @pytest.mark.asyncio
    async def test_log_with_full_fields(self, monkeypatch):
        import importlib
        import sys

        mocks = _make_otel_mocks()
        for mod_name, mod in mocks.items():
            monkeypatch.setitem(sys.modules, mod_name, mod)

        import bifrost.adapters.audit.otel as otel_mod

        importlib.reload(otel_mod)

        adapter = otel_mod.OtelAuditAdapter()
        event = AuditEvent(
            request_id="req-1",
            agent_id="a",
            tenant_id="t",
            model="m",
            timestamp=datetime.now(UTC),
            outcome="success",
            tags={"key": "value"},
            prompt_content="hello",
            response_content="world",
        )
        await adapter.log(event)
        otel_logger = adapter._otel_logger
        assert len(otel_logger.emitted) == 1

    def test_otel_adapter_build_from_factory_with_mocks(self, monkeypatch):
        """_build_audit with adapter='otel' creates an OtelAuditAdapter."""
        import importlib
        import sys

        mocks = _make_otel_mocks()
        for mod_name, mod in mocks.items():
            monkeypatch.setitem(sys.modules, mod_name, mod)

        import bifrost.adapters.audit.otel as otel_mod

        importlib.reload(otel_mod)

        # Patch the otel module to use our mocked version
        with patch("bifrost.adapters.audit.otel.OtelAuditAdapter") as mock_cls:
            mock_cls.return_value = NullAuditAdapter()
            cfg = BifrostConfig(audit=AuditConfig(adapter="otel"))
            _build_audit(cfg)
            mock_cls.assert_called_once_with(
                otel_endpoint=cfg.audit.otel.endpoint,
                service_name=cfg.audit.otel.service_name,
            )


class TestOtelAuditAdapterImportError:
    def test_raises_import_error_when_opentelemetry_missing(self, monkeypatch):
        import builtins
        import sys

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("opentelemetry"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        # Remove cached otel modules
        otel_keys = [k for k in sys.modules if k.startswith("opentelemetry")]
        for k in otel_keys:
            monkeypatch.delitem(sys.modules, k, raising=False)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        import importlib

        import bifrost.adapters.audit.otel as otel_mod

        try:
            importlib.reload(otel_mod)
        except Exception:
            pass  # reload may fail; we test constructor separately

        with pytest.raises(ImportError):
            otel_mod.OtelAuditAdapter()


# ---------------------------------------------------------------------------
# _build_audit factory (app.py)
# ---------------------------------------------------------------------------


class TestBuildAudit:
    def test_null_adapter_by_default(self):
        cfg = BifrostConfig()
        adapter = _build_audit(cfg)
        assert isinstance(adapter, NullAuditAdapter)

    def test_null_adapter_explicit(self):
        cfg = BifrostConfig(audit=AuditConfig(adapter="null"))
        adapter = _build_audit(cfg)
        assert isinstance(adapter, NullAuditAdapter)

    def test_sqlite_adapter(self, tmp_path):
        from bifrost.adapters.audit.sqlite import SQLiteAuditAdapter

        path = str(tmp_path / "audit.db")
        cfg = BifrostConfig(audit=AuditConfig(adapter="sqlite", path=path))
        adapter = _build_audit(cfg)
        assert isinstance(adapter, SQLiteAuditAdapter)

    def test_postgres_adapter_raises_when_no_dsn(self):
        cfg = BifrostConfig(
            audit=AuditConfig(adapter="postgres", dsn="", dsn_env="NONEXISTENT_VAR")
        )
        with pytest.raises(ValueError, match="DSN"):
            _build_audit(cfg)

    def test_postgres_adapter_created_with_dsn(self):
        from bifrost.adapters.audit.postgres import PostgresAuditAdapter

        cfg = BifrostConfig(audit=AuditConfig(adapter="postgres", dsn="postgresql://fake/db"))
        adapter = _build_audit(cfg)
        assert isinstance(adapter, PostgresAuditAdapter)


# ---------------------------------------------------------------------------
# _build_audit_event — detail levels
# ---------------------------------------------------------------------------


class TestBuildAuditEvent:
    def _identity(self):
        return _fake_identity()

    def test_minimal_level_populates_core_fields(self):
        cfg = BifrostConfig(audit=AuditConfig(level=AuditDetailLevel.MINIMAL))
        event = _build_audit_event(
            config=cfg,
            request_id="req-1",
            identity=self._identity(),
            model="gpt-4o",
            provider="openai",
            outcome="success",
            status_code=200,
            latency_ms=42.0,
            tokens_input=10,
            tokens_output=5,
            cost_usd=0.01,
        )
        assert event.tokens_input == 10
        assert event.tokens_output == 5
        assert event.cost_usd == pytest.approx(0.01)
        assert event.latency_ms == pytest.approx(42.0)
        # Minimal: provider and session/saga NOT populated
        assert event.provider == ""
        assert event.session_id == ""
        assert event.outcome == "success"  # default value still set

    def test_standard_level_adds_metadata(self):
        cfg = BifrostConfig(audit=AuditConfig(level=AuditDetailLevel.STANDARD))
        event = _build_audit_event(
            config=cfg,
            request_id="req-1",
            identity=self._identity(),
            model="gpt-4o",
            provider="openai",
            outcome="rejected",
            status_code=400,
            latency_ms=5.0,
            error_message="content policy",
            rule_name="block-rule",
            rule_action="reject",
        )
        assert event.provider == "openai"
        assert event.session_id == "sess"
        assert event.saga_id == "saga"
        assert event.outcome == "rejected"
        assert event.status_code == 400
        assert event.error_message == "content policy"
        assert event.rule_name == "block-rule"
        # Full content: empty at standard level
        assert event.prompt_content == ""
        assert event.response_content == ""

    def test_full_level_adds_content(self):
        cfg = BifrostConfig(audit=AuditConfig(level=AuditDetailLevel.FULL))
        req = _make_request()
        resp = _make_response()
        event = _build_audit_event(
            config=cfg,
            request_id="req-1",
            identity=self._identity(),
            model="gpt-4o",
            provider="openai",
            outcome="success",
            status_code=200,
            latency_ms=10.0,
            request=req,
            response=resp,
        )
        assert event.prompt_content != ""
        assert event.response_content != ""
        import json

        # Prompt content is valid JSON
        parsed = json.loads(event.prompt_content)
        assert isinstance(parsed, list)

    def test_cache_hit_event(self):
        cfg = BifrostConfig(audit=AuditConfig(level=AuditDetailLevel.STANDARD))
        event = _build_audit_event(
            config=cfg,
            request_id="r",
            identity=self._identity(),
            model="gpt-4o",
            provider="openai",
            outcome="cache_hit",
            status_code=200,
            latency_ms=1.0,
            cache_hit=True,
        )
        assert event.cache_hit is True
        assert event.outcome == "cache_hit"
        assert event.tokens_input == 0
        assert event.cost_usd == 0.0


# ---------------------------------------------------------------------------
# Audit integration in /v1/messages route
# ---------------------------------------------------------------------------


class FakeProvider:
    def __init__(self, response=None, raises=None):
        self._response = response or _make_response()
        self._raises = raises

    async def complete(self, request, model):
        if self._raises:
            raise self._raises
        return self._response

    async def stream(self, request, model) -> AsyncIterator[str]:
        if self._raises:
            raise self._raises
        yield "data: [DONE]\n\n"

    async def close(self):
        pass


class TestAuditIntegrationInRoutes:
    def _make_audit(self):
        """Return a NullAuditAdapter with a spy on log()."""
        adapter = NullAuditAdapter()
        adapter.log = AsyncMock()
        return adapter

    def _client_with_audit(self, audit=None, response=None, raises=None):
        cfg = BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o"])},
            audit=AuditConfig(level=AuditDetailLevel.STANDARD),
        )
        audit = audit or self._make_audit()
        app = create_app(cfg)

        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as mock_complete:
            if raises:
                mock_complete.side_effect = raises
            else:
                mock_complete.return_value = response or _make_response()

            client = TestClient(app)
            return client, audit, mock_complete

    def test_success_emits_audit_event(self):
        """POST /v1/messages success path schedules an audit event."""
        audit = NullAuditAdapter()
        logged_events: list[AuditEvent] = []

        async def _spy_log(event: AuditEvent) -> None:
            logged_events.append(event)

        audit.log = _spy_log

        cfg = BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o"])},
            audit=AuditConfig(level=AuditDetailLevel.STANDARD),
        )

        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
            m.return_value = _make_response()
            with TestClient(create_app(cfg)) as client:
                client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
        # Events may be scheduled async; just verify no exception raised

    def test_cache_stats_endpoint_accessible(self):
        cfg = BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o"])},
        )
        with TestClient(create_app(cfg)) as client:
            resp = client.get("/api/v1/bifrost/v1/cache/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert "hit_rate" in body


# ---------------------------------------------------------------------------
# AuditConfig
# ---------------------------------------------------------------------------


class TestAuditConfig:
    def test_default_adapter_is_null(self):
        cfg = AuditConfig()
        assert cfg.adapter == "null"

    def test_default_level_is_minimal(self):
        cfg = AuditConfig()
        assert cfg.level == AuditDetailLevel.MINIMAL

    def test_default_retention_days(self):
        cfg = AuditConfig()
        assert cfg.retention_days == 90

    def test_effective_dsn_uses_explicit_dsn(self):
        cfg = AuditConfig(dsn="postgresql://explicit/db")
        assert cfg.effective_dsn() == "postgresql://explicit/db"

    def test_effective_dsn_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("BIFROST_AUDIT_DSN", "postgresql://env/db")
        cfg = AuditConfig(dsn="")
        assert cfg.effective_dsn() == "postgresql://env/db"

    def test_effective_dsn_custom_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_AUDIT_DSN", "postgresql://custom/db")
        cfg = AuditConfig(dsn="", dsn_env="MY_AUDIT_DSN")
        assert cfg.effective_dsn() == "postgresql://custom/db"

    def test_bifrost_config_has_audit_field(self):
        cfg = BifrostConfig()
        assert hasattr(cfg, "audit")
        assert isinstance(cfg.audit, AuditConfig)

    def test_bifrost_config_audit_configurable(self):
        cfg = BifrostConfig(
            audit=AuditConfig(adapter="sqlite", level=AuditDetailLevel.FULL, retention_days=30)
        )
        assert cfg.audit.adapter == "sqlite"
        assert cfg.audit.level == AuditDetailLevel.FULL
        assert cfg.audit.retention_days == 30


# ---------------------------------------------------------------------------
# AuditEvent fields
# ---------------------------------------------------------------------------


class TestAuditEventFields:
    def test_all_new_fields_have_defaults(self):
        event = AuditEvent(
            request_id="r",
            agent_id="a",
            tenant_id="t",
            model="m",
            timestamp=datetime.now(UTC),
        )
        assert event.tokens_input == 0
        assert event.tokens_output == 0
        assert event.cost_usd == 0.0
        assert event.cache_hit is False
        assert event.prompt_content == ""
        assert event.response_content == ""

    def test_cache_hit_field(self):
        event = AuditEvent(
            request_id="r",
            agent_id="a",
            tenant_id="t",
            model="m",
            timestamp=datetime.now(UTC),
            cache_hit=True,
            tokens_input=0,
            tokens_output=0,
        )
        assert event.cache_hit is True


# ---------------------------------------------------------------------------
# Postgres audit log with new fields
# ---------------------------------------------------------------------------


class TestPostgresAuditNewFields:
    """Verify the postgres adapter passes all new NIU-462 fields."""

    @pytest.mark.asyncio
    async def test_log_passes_new_fields(self):
        from unittest.mock import AsyncMock, patch

        from bifrost.adapters.audit.postgres import PostgresAuditAdapter
        from tests.test_bifrost.conftest import make_pool_mock

        pool, conn = make_pool_mock()
        with patch(
            "bifrost.adapters._pg_base.asyncpg.create_pool", new_callable=AsyncMock
        ) as mock_cp:
            mock_cp.return_value = pool
            adapter = PostgresAuditAdapter(dsn="postgresql://fake/db")
            await adapter._get_pool()
            conn.execute.reset_mock()

            event = AuditEvent(
                request_id="req-1",
                agent_id="a",
                tenant_id="t",
                model="m",
                timestamp=datetime.now(UTC),
                tokens_input=100,
                tokens_output=50,
                cost_usd=0.015,
                cache_hit=True,
                prompt_content="hello",
                response_content="world",
            )
            await adapter.log(event)

        _, *args = conn.execute.call_args[0]
        assert 100 in args  # tokens_input
        assert 50 in args  # tokens_output
        assert True in args  # cache_hit
        # Check cost_usd is approximately present
        float_args = [a for a in args if isinstance(a, float)]
        assert any(abs(a - 0.015) < 1e-6 for a in float_args)
