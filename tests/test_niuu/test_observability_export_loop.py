"""Telemetry export must not export the logs the export itself produces.

Live on Ting at debug level, urllib3 logged every OTLP log post and those
records were exported in turn: ~145 lines a second, later ~280 once the SDK's
``force_flush`` thread (not an ``Otel…`` thread) was doing the export.
"""

from __future__ import annotations

import logging
import threading

import pytest

from niuu import observability as obs_module
from niuu.domain.observability import ObservabilityConfig

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.context import (  # noqa: E402
    _SUPPRESS_INSTRUMENTATION_KEY,
    attach,
    detach,
    set_value,
)


def _record(name: str = "urllib3.connectionpool") -> logging.LogRecord:
    return logging.LogRecord(name, logging.DEBUG, __file__, 1, "POST /otlp/v1/logs", (), None)


def _during_export(check):
    """Run *check* in the context the SDK sets while it exports a batch."""
    token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
    try:
        return check()
    finally:
        detach(token)


def test_records_logged_during_an_export_are_kept_off_otlp() -> None:
    assert _during_export(lambda: obs_module._not_from_telemetry_export(_record())) is False


def test_an_export_on_any_thread_is_recognised() -> None:
    """force_flush exports on an unnamed thread; the context mark still applies."""
    results: list[bool] = []

    def flush_thread() -> None:
        results.append(_during_export(lambda: obs_module._not_from_telemetry_export(_record())))

    thread = threading.Thread(target=flush_thread)  # named "Thread-N", as the SDK's is
    thread.start()
    thread.join()

    assert results == [False]


def test_application_records_are_exported_whatever_their_logger() -> None:
    """urllib3 traffic the application makes is still exported; only the export's own is not."""
    assert obs_module._not_from_telemetry_export(_record()) is True
    assert obs_module._not_from_telemetry_export(_record("ting.main")) is True


def test_the_configured_otlp_handler_applies_the_filter() -> None:
    for module in (
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
        "opentelemetry.exporter.otlp.proto.http.metric_exporter",
        "opentelemetry.exporter.otlp.proto.http._log_exporter",
    ):
        pytest.importorskip(module, exc_type=ImportError)
    telemetry = obs_module.configure_observability(
        ObservabilityConfig(
            enabled=True,
            trace_endpoint="http://127.0.0.1:4317",
            metric_endpoint="http://127.0.0.1:4318/v1/metrics",
            log_endpoint="http://127.0.0.1:4318/v1/logs",
            insecure=True,
        ),
        component="test",
    )
    handler = telemetry._log_handler

    assert _during_export(lambda: handler.filter(_record())) is False
    assert handler.filter(_record()) is not False
