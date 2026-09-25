"""No-fallbacks and multi-component coverage for niuu.observability.

``configure_observability`` and the instrumentation helpers must raise, with
the remedy in the message, when observability is enabled but the required
package is missing — never warn-and-continue uninstrumented. See
.claude/rules/no-fallbacks.md.

Also covers the one-process-one-pipeline join/opt-out/conflict rules that
back several composition roots sharing a process in mini mode. See
ravn-niuu-boundary.md's Observability section and the module docstring in
niuu/observability.py.

State reset between tests comes from the autouse fixture in
tests/test_niuu/conftest.py.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from niuu import observability as obs_module
from niuu.domain.observability import ObservabilityConfig

_ENABLED_KWARGS = {
    "enabled": True,
    "trace_endpoint": "http://localhost:4317",
    "metric_endpoint": "http://localhost:4318/v1/metrics",
}


def test_configure_observability_raises_when_sdk_is_missing():
    """enabled: true with no opentelemetry-sdk installed must raise, not warn."""
    cfg = ObservabilityConfig(**_ENABLED_KWARGS)
    # Simulate the SDK not being installed without uninstalling it: None in
    # sys.modules makes the next `import` of that name raise ImportError.
    with patch.dict(sys.modules, {"opentelemetry.sdk.trace": None}):
        with pytest.raises(RuntimeError, match="otel extra is not installed"):
            obs_module.configure_observability(cfg)
    # And it must not have silently left a half-enabled, uninstrumented state.
    assert obs_module.get_observability().enabled is False


def test_instrument_fastapi_app_raises_when_instrumentation_package_is_missing():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    instance = obs_module.Observability(
        tracer_provider=TracerProvider(), meter_provider=MeterProvider()
    )
    with patch.dict(sys.modules, {"opentelemetry.instrumentation.fastapi": None}):
        with pytest.raises(RuntimeError, match="opentelemetry-instrumentation-fastapi"):
            obs_module.instrument_fastapi_app(object(), instance)


def test_instrument_httpx_client_raises_when_instrumentation_package_is_missing():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    instance = obs_module.Observability(
        tracer_provider=TracerProvider(), meter_provider=MeterProvider()
    )
    with patch.dict(sys.modules, {"opentelemetry.instrumentation.httpx": None}):
        with pytest.raises(RuntimeError, match="opentelemetry-instrumentation-httpx"):
            obs_module.instrument_httpx_client(instance)


def test_configure_observability_is_a_decision_not_a_fallback_when_disabled():
    """enabled: false (the default) must not raise even with no SDK installed."""
    cfg = ObservabilityConfig(enabled=False)
    with patch.dict(sys.modules, {"opentelemetry.sdk.trace": None}):
        result = obs_module.configure_observability(cfg)
    assert result.enabled is False


class TestMultiComponentProcess:
    """Several composition roots calling configure_observability in one process."""

    def test_second_component_with_matching_config_joins_the_same_pipeline(self):
        pytest.importorskip("opentelemetry.sdk")
        pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.metric_exporter")
        first = obs_module.configure_observability(
            ObservabilityConfig(**_ENABLED_KWARGS, service_name="volundr"),
            component="volundr",
        )
        second = obs_module.configure_observability(
            ObservabilityConfig(**_ENABLED_KWARGS, service_name="ting"),
            component="ting",
        )
        assert second is first
        assert "volundr" in obs_module._seen_components
        assert "ting" in obs_module._seen_components

    def test_second_component_with_different_endpoint_raises(self):
        pytest.importorskip("opentelemetry.sdk")
        pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.metric_exporter")
        obs_module.configure_observability(
            ObservabilityConfig(**_ENABLED_KWARGS, service_name="volundr"),
            component="volundr",
        )
        conflicting = ObservabilityConfig(
            enabled=True,
            trace_endpoint="http://somewhere-else:4317",
            metric_endpoint="http://localhost:4318/v1/metrics",
            service_name="ting",
        )
        with pytest.raises(RuntimeError, match="Conflicting observability configuration"):
            obs_module.configure_observability(conflicting, component="ting")

    def test_component_with_explicit_enabled_false_opts_out_cleanly(self):
        """A service that explicitly says enabled: false is never instrumented,
        even though a sibling already enabled observability for the process."""
        pytest.importorskip("opentelemetry.sdk")
        pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.metric_exporter")
        obs_module.configure_observability(
            ObservabilityConfig(**_ENABLED_KWARGS, service_name="volundr"),
            component="volundr",
        )
        opted_out = ObservabilityConfig(**{**_ENABLED_KWARGS, "enabled": False})
        assert "enabled" in opted_out.model_fields_set

        result = obs_module.configure_observability(opted_out, component="skuld")

        assert result.enabled is False
        assert result is not obs_module.get_observability()

    def test_component_that_never_set_observability_inherits_the_ambient_pipeline(self):
        """A service whose own config never mentions observability (the class
        default, enabled=false) inherits the process pipeline instead of being
        treated as an explicit opt-out."""
        pytest.importorskip("opentelemetry.sdk")
        pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.metric_exporter")
        shared = obs_module.configure_observability(
            ObservabilityConfig(**_ENABLED_KWARGS, service_name="volundr"),
            component="volundr",
        )
        never_configured = ObservabilityConfig()
        assert "enabled" not in never_configured.model_fields_set

        result = obs_module.configure_observability(never_configured, component="mimir")

        assert result is shared


class TestDefaultServiceName:
    def test_explicit_service_name_wins_over_the_composition_roots_default(self):
        cfg = ObservabilityConfig(service_name="observatory-prod")
        assert obs_module._resolve_service_name(cfg, "observatory") == "observatory-prod"

    def test_unset_service_name_falls_back_to_the_composition_roots_default(self):
        """Observatory and the shared host both reuse volundr.config.Settings,
        so ObservabilityConfig's class default ("volundr") is never the
        operator's decision unless they wrote service_name: explicitly."""
        cfg = ObservabilityConfig()
        assert "service_name" not in cfg.model_fields_set
        assert obs_module._resolve_service_name(cfg, "observatory") == "observatory"

    def test_unset_service_name_without_a_default_keeps_the_class_default(self):
        cfg = ObservabilityConfig()
        assert obs_module._resolve_service_name(cfg, "") == "ravn"
