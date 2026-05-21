"""Tests for Bifröst Prometheus metrics registry and observability endpoints."""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.metrics import (
    Counter,
    Histogram,
    LabelKey,
    Registry,
    _escape,
    _label_str,
    record_cache_hit,
    record_cache_miss,
    record_quota_rejection,
    record_request,
    record_rule_hit,
)
from tests.test_bifrost.conftest import make_config

# ---------------------------------------------------------------------------
# Unit tests for the metrics primitives
# ---------------------------------------------------------------------------


class TestCounter:
    def test_starts_at_zero(self) -> None:
        c = Counter(name="test_c", help="help", label_names=("a",))
        assert c.collect() == {}

    def test_inc_creates_entry(self) -> None:
        c = Counter(name="test_c2", help="help", label_names=("a",))
        c.inc(("x",))
        assert c.collect()[LabelKey(("x",))] == 1.0

    def test_inc_accumulates(self) -> None:
        c = Counter(name="test_c3", help="help", label_names=("a",))
        c.inc(("x",), 3.0)
        c.inc(("x",), 2.0)
        assert c.collect()[LabelKey(("x",))] == 5.0

    def test_multiple_labels(self) -> None:
        c = Counter(name="test_c4", help="help", label_names=("a", "b"))
        c.inc(("x", "y"))
        c.inc(("x", "z"))
        data = c.collect()
        assert data[LabelKey(("x", "y"))] == 1.0
        assert data[LabelKey(("x", "z"))] == 1.0

    def test_thread_safety(self) -> None:
        c = Counter(name="test_c5", help="help", label_names=("a",))
        errors: list[Exception] = []

        def _inc() -> None:
            try:
                for _ in range(100):
                    c.inc(("key",))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_inc) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert c.collect()[LabelKey(("key",))] == 1000.0


class TestHistogram:
    def test_observe_increments_buckets(self) -> None:
        h = Histogram(name="test_h", help="help", label_names=("a",), buckets=(0.1, 1.0, 10.0))
        h.observe(("x",), 0.5)
        data = h.collect()
        key = LabelKey(("x",))
        buckets, total, count = data[key]
        # 0.5 > 0.1, so bucket[0]=0 (0.5 not <= 0.1)
        assert buckets[0] == 0.0  # le=0.1: not counted
        assert buckets[1] == 1.0  # le=1.0: counted
        assert buckets[2] == 1.0  # le=10.0: counted
        assert buckets[3] == 1.0  # le=+Inf: always counted
        assert total == 0.5
        assert count == 1.0

    def test_observe_multiple(self) -> None:
        h = Histogram(name="test_h2", help="help", label_names=("a",), buckets=(1.0, 5.0))
        h.observe(("x",), 0.5)
        h.observe(("x",), 2.0)
        h.observe(("x",), 10.0)
        data = h.collect()
        key = LabelKey(("x",))
        buckets, total, count = data[key]
        assert buckets[0] == 1.0  # le=1.0: only 0.5 counts
        assert buckets[1] == 2.0  # le=5.0: 0.5 and 2.0 count
        assert buckets[2] == 3.0  # +Inf: all three
        assert total == pytest.approx(12.5)
        assert count == 3.0


class TestRegistry:
    def test_generate_text_counter(self) -> None:
        reg = Registry()
        c = reg.register(Counter(name="my_counter", help="A counter.", label_names=("env",)))
        c.inc(("prod",), 7.0)
        text = reg.generate_text()
        assert "# HELP my_counter A counter." in text
        assert "# TYPE my_counter counter" in text
        assert 'my_counter{env="prod"} 7.0' in text

    def test_generate_text_histogram(self) -> None:
        reg = Registry()
        h = reg.register(
            Histogram(name="my_hist", help="A histogram.", label_names=("env",), buckets=(1.0,))
        )
        h.observe(("prod",), 0.5)
        text = reg.generate_text()
        assert "# HELP my_hist A histogram." in text
        assert "# TYPE my_hist histogram" in text
        assert 'my_hist_bucket{env="prod",le="1"} 1.0' in text
        assert 'my_hist_sum{env="prod"}' in text
        assert 'my_hist_count{env="prod"}' in text

    def test_generate_text_no_labels(self) -> None:
        reg = Registry()
        c = reg.register(Counter(name="nolabel_c", help="No labels.", label_names=()))
        c.inc(())
        text = reg.generate_text()
        assert "nolabel_c 1.0" in text

    def test_empty_registry(self) -> None:
        reg = Registry()
        assert reg.generate_text() == ""


class TestLabelHelpers:
    def test_escape_backslash(self) -> None:
        assert _escape("a\\b") == "a\\\\b"

    def test_escape_quote(self) -> None:
        assert _escape('a"b') == 'a\\"b'

    def test_escape_newline(self) -> None:
        assert _escape("a\nb") == "a\\nb"

    def test_label_str_empty(self) -> None:
        assert _label_str((), ()) == ""

    def test_label_str_single(self) -> None:
        result = _label_str(("env",), ("prod",))
        assert result == '{env="prod"}'

    def test_label_str_multiple(self) -> None:
        result = _label_str(("a", "b"), ("x", "y"))
        assert result == '{a="x",b="y"}'


# ---------------------------------------------------------------------------
# Convenience helper tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def setup_method(self) -> None:
        # Create a fresh private registry so each test is isolated from the
        # global module-level singletons.
        import bifrost.metrics as m

        self._original_registry = m.REGISTRY
        m.REGISTRY = Registry()
        # Re-register metrics against the private registry
        m.requests_total = m.REGISTRY.register(
            Counter("bifrost_requests_total", "help", ("provider", "model", "status"))
        )
        m.tokens_total = m.REGISTRY.register(
            Counter("bifrost_tokens_total", "help", ("provider", "model", "type"))
        )
        m.cost_usd_total = m.REGISTRY.register(
            Counter("bifrost_cost_usd_total", "help", ("provider", "model"))
        )
        m.cache_hits_total = m.REGISTRY.register(
            Counter("bifrost_cache_hits_total", "help", ("provider", "model"))
        )
        m.cache_misses_total = m.REGISTRY.register(
            Counter("bifrost_cache_misses_total", "help", ("provider", "model"))
        )
        m.quota_rejections_total = m.REGISTRY.register(
            Counter("bifrost_quota_rejections_total", "help", ("agent_id",))
        )
        m.rule_hits_total = m.REGISTRY.register(
            Counter("bifrost_rule_hits_total", "help", ("rule_name", "action"))
        )
        m.request_duration_seconds = m.REGISTRY.register(
            Histogram("bifrost_request_duration_seconds", "help", ("provider", "model"))
        )

    def teardown_method(self) -> None:
        import bifrost.metrics as m

        m.REGISTRY = self._original_registry

    def test_record_request_increments_counter(self) -> None:
        import bifrost.metrics as m

        record_request(
            provider="anthropic",
            model="claude-sonnet-4-6",
            status="200",
            duration_seconds=1.0,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
        )
        data = m.requests_total.collect()
        assert data[LabelKey(("anthropic", "claude-sonnet-4-6", "200"))] == 1.0

    def test_record_request_increments_tokens(self) -> None:
        import bifrost.metrics as m

        record_request(
            provider="anthropic",
            model="claude-sonnet-4-6",
            status="200",
            duration_seconds=1.0,
            input_tokens=100,
            output_tokens=50,
        )
        data = m.tokens_total.collect()
        assert data[LabelKey(("anthropic", "claude-sonnet-4-6", "input"))] == 100.0
        assert data[LabelKey(("anthropic", "claude-sonnet-4-6", "output"))] == 50.0

    def test_record_request_records_duration(self) -> None:
        import bifrost.metrics as m

        record_request(
            provider="openai",
            model="gpt-4o",
            status="200",
            duration_seconds=2.5,
        )
        data = m.request_duration_seconds.collect()
        key = LabelKey(("openai", "gpt-4o"))
        assert key in data
        _, total, count = data[key]
        assert total == pytest.approx(2.5)
        assert count == 1.0

    def test_record_cache_hit(self) -> None:
        import bifrost.metrics as m

        record_cache_hit(provider="anthropic", model="claude-haiku-4-5-20251001")
        data = m.cache_hits_total.collect()
        assert data[LabelKey(("anthropic", "claude-haiku-4-5-20251001"))] == 1.0

    def test_record_cache_miss(self) -> None:
        import bifrost.metrics as m

        record_cache_miss(provider="anthropic", model="claude-sonnet-4-6")
        data = m.cache_misses_total.collect()
        assert data[LabelKey(("anthropic", "claude-sonnet-4-6"))] == 1.0

    def test_record_quota_rejection(self) -> None:
        import bifrost.metrics as m

        record_quota_rejection(agent_id="ting")
        data = m.quota_rejections_total.collect()
        assert data[LabelKey(("ting",))] == 1.0

    def test_record_rule_hit(self) -> None:
        import bifrost.metrics as m

        record_rule_hit(rule_name="no-images", action="strip_images")
        data = m.rule_hits_total.collect()
        assert data[LabelKey(("no-images", "strip_images"))] == 1.0


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def _make_app_client(config: BifrostConfig | None = None) -> TestClient:
    cfg = config or make_config()
    app = create_app(cfg)
    return TestClient(app)


class TestHealthzEndpoint:
    def test_healthz_returns_200(self) -> None:
        with _make_app_client() as client:
            resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.text == "ok"

    def test_healthz_always_ok(self) -> None:
        """Liveness probe must always return 200 if the process is alive."""
        with _make_app_client() as client:
            for _ in range(3):
                resp = client.get("/healthz")
                assert resp.status_code == 200


class TestReadyzEndpoint:
    def test_readyz_ok_when_store_and_providers_up(self) -> None:
        """readyz returns 200 when the store summarise() succeeds and provider responds."""
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])}
        )
        _store_patch = "bifrost.adapters.memory_store.MemoryUsageStore.summarise"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_http = AsyncMock()
        mock_http.head = AsyncMock(return_value=mock_resp)
        mock_http.aclose = AsyncMock()
        with (
            patch("bifrost.inbound.observability.httpx.AsyncClient", return_value=mock_http),
            patch(_store_patch, new_callable=AsyncMock) as mock_summarise,
        ):
            mock_summarise.return_value = MagicMock()
            app = create_app(config)
            with TestClient(app) as client:
                resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_readyz_503_when_store_fails(self) -> None:
        """readyz returns 503 when the store raises an exception."""
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])}
        )
        _store_patch = "bifrost.adapters.memory_store.MemoryUsageStore.summarise"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_http = AsyncMock()
        mock_http.head = AsyncMock(return_value=mock_resp)
        mock_http.aclose = AsyncMock()
        with (
            patch("bifrost.inbound.observability.httpx.AsyncClient", return_value=mock_http),
            patch(_store_patch, new_callable=AsyncMock) as mock_summarise,
        ):
            mock_summarise.side_effect = RuntimeError("db down")
            app = create_app(config)
            with TestClient(app) as client:
                resp = client.get("/readyz")
        assert resp.status_code == 503
        assert "usage_store" in resp.json()["failures"][0]

    def test_readyz_503_when_providers_unreachable(self) -> None:
        """readyz returns 503 when no provider base URL can be reached."""
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])}
        )
        _store_patch = "bifrost.adapters.memory_store.MemoryUsageStore.summarise"
        mock_http = AsyncMock()
        mock_http.head = AsyncMock(side_effect=Exception("connection refused"))
        mock_http.aclose = AsyncMock()
        with (
            patch("bifrost.inbound.observability.httpx.AsyncClient", return_value=mock_http),
            patch(_store_patch, new_callable=AsyncMock) as mock_summarise,
        ):
            mock_summarise.return_value = MagicMock()
            app = create_app(config)
            with TestClient(app) as client:
                resp = client.get("/readyz")
        assert resp.status_code == 503
        assert any("providers" in f for f in resp.json()["failures"])

    def test_readyz_ok_when_no_providers_configured(self) -> None:
        """readyz returns 200 when there are no providers (nothing to check)."""
        config = BifrostConfig(providers={})
        app = create_app(config)
        _store_patch = "bifrost.adapters.memory_store.MemoryUsageStore.summarise"
        with patch(_store_patch, new_callable=AsyncMock) as mock_summarise:
            mock_summarise.return_value = MagicMock()
            with TestClient(app) as client:
                resp = client.get("/readyz")
        assert resp.status_code == 200


class TestMetricsEndpoint:
    def test_metrics_returns_200(self) -> None:
        with _make_app_client() as client:
            resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_content_type(self) -> None:
        with _make_app_client() as client:
            resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]

    def test_metrics_contains_bifrost_metric_names(self) -> None:
        with _make_app_client() as client:
            resp = client.get("/metrics")
        text = resp.text
        assert "bifrost_requests_total" in text
        assert "bifrost_request_duration_seconds" in text
        assert "bifrost_tokens_total" in text
        assert "bifrost_cost_usd_total" in text
        assert "bifrost_cache_hits_total" in text
        assert "bifrost_cache_misses_total" in text
        assert "bifrost_quota_rejections_total" in text
        assert "bifrost_rule_hits_total" in text

    def test_metrics_has_help_and_type_lines(self) -> None:
        with _make_app_client() as client:
            resp = client.get("/metrics")
        text = resp.text
        assert "# HELP bifrost_requests_total" in text
        assert "# TYPE bifrost_requests_total counter" in text
        assert "# HELP bifrost_request_duration_seconds" in text
        assert "# TYPE bifrost_request_duration_seconds histogram" in text
