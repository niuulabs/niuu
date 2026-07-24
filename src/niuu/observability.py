"""Shared OpenTelemetry runtime for Niuu processes and Ravn.

Consumers only see this small facade. OpenTelemetry stays optional when
disabled, while an enabled configuration fails loudly if its SDK/exporters are
not installed.
"""

from __future__ import annotations

import atexit
import json
import logging
import re
from contextlib import nullcontext
from threading import Lock
from typing import Any

from niuu.domain.observability import ObservabilityConfig

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "cookie",
    "nkey",
    "password",
    "private_key",
    "secret",
    "seed",
    "token",
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


class _NullSpan:
    def set_attribute(self, _name: str, _value: Any) -> None:
        return None

    def add_event(self, _name: str, attributes: dict[str, Any] | None = None) -> None:
        return None

    def set_status(self, _status: Any) -> None:
        return None


class Observability:
    """Trace and metric facade with explicit provider ownership."""

    def __init__(
        self,
        *,
        tracer_provider: Any | None = None,
        meter_provider: Any | None = None,
        capture_content: bool = False,
        content_max_chars: int = 8_192,
    ) -> None:
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._gauges: dict[str, Any] = {}
        self._lock = Lock()
        self._shutdown = False
        self._capture_content = capture_content
        self._content_max_chars = content_max_chars
        if tracer_provider is None or meter_provider is None:
            self._tracer = None
            self._meter = None
            return
        from opentelemetry import metrics, trace

        # Retain the established instrumentation scope while moving ownership
        # of the facade; existing Tempo/Grafana queries depend on these names.
        self._tracer = trace.get_tracer("ravn.runtime", tracer_provider=tracer_provider)
        self._meter = metrics.get_meter("ravn.runtime", meter_provider=meter_provider)

    @property
    def enabled(self) -> bool:
        return self._tracer is not None and self._meter is not None

    def span(
        self,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
        carrier: dict[str, str] | None = None,
        link_carrier: dict[str, str] | None = None,
    ) -> Any:
        if self._tracer is None:
            return nullcontext(_NullSpan())
        if carrier and link_carrier:
            raise ValueError("span cannot use both a parent carrier and a link carrier")
        context = None
        links = None
        if carrier:
            from opentelemetry.propagate import extract

            context = extract(carrier)
        if link_carrier:
            from opentelemetry import trace
            from opentelemetry.context import Context
            from opentelemetry.propagate import extract

            linked_context = trace.get_current_span(extract(link_carrier)).get_span_context()
            context = Context()
            if linked_context.is_valid:
                links = [trace.Link(linked_context)]
        return self._tracer.start_as_current_span(
            name,
            context=context,
            attributes=_clean_attributes(attributes or {}),
            links=links,
        )

    def inject(self) -> dict[str, str]:
        if self._tracer is None:
            return {}
        from opentelemetry.propagate import inject

        carrier: dict[str, str] = {}
        inject(carrier)
        return carrier

    def trace_id(self) -> str:
        """Return the active trace id as lowercase hex, or an empty string."""
        if self._tracer is None:
            return ""
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if not context.is_valid:
            return ""
        return f"{context.trace_id:032x}"

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        """Attach searchable fields to the active span."""
        if self._tracer is None:
            return
        from opentelemetry import trace

        span = trace.get_current_span()
        for key, value in _clean_attributes(attributes).items():
            span.set_attribute(key, value)

    def event(
        self,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
        content: Any | None = None,
    ) -> None:
        """Add a structured event to the active span.

        Content is attached only when explicitly enabled. It is recursively
        redacted and bounded before entering the exporter.
        """
        if self._tracer is None:
            return
        from opentelemetry import trace

        event_attributes = _clean_attributes(attributes or {})
        if self._capture_content and content is not None:
            event_attributes["ravn.content"] = _serialized_content(
                content,
                max_chars=self._content_max_chars,
            )
        trace.get_current_span().add_event(name, attributes=event_attributes)

    def count(
        self,
        name: str,
        value: int = 1,
        *,
        attributes: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        if self._meter is None:
            return
        with self._lock:
            instrument = self._counters.get(name)
            if instrument is None:
                instrument = self._meter.create_counter(name, description=description)
                self._counters[name] = instrument
        instrument.add(value, attributes=_clean_attributes(attributes or {}))

    def duration(
        self,
        name: str,
        seconds: float,
        *,
        attributes: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        self.record(
            name,
            seconds,
            unit="s",
            attributes=attributes,
            description=description,
        )

    def record(
        self,
        name: str,
        value: int | float,
        *,
        unit: str = "1",
        attributes: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        if self._meter is None:
            return
        key = f"{name}\0{unit}"
        with self._lock:
            instrument = self._histograms.get(key)
            if instrument is None:
                instrument = self._meter.create_histogram(
                    name,
                    unit=unit,
                    description=description,
                )
                self._histograms[key] = instrument
        instrument.record(value, attributes=_clean_attributes(attributes or {}))

    def gauge(
        self,
        name: str,
        value: int | float,
        *,
        unit: str = "1",
        attributes: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        if self._meter is None:
            return
        key = f"{name}\0{unit}"
        with self._lock:
            instrument = self._gauges.get(key)
            if instrument is None:
                instrument = self._meter.create_gauge(
                    name,
                    unit=unit,
                    description=description,
                )
                self._gauges[key] = instrument
        instrument.set(value, attributes=_clean_attributes(attributes or {}))

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        for provider in (self._meter_provider, self._tracer_provider):
            if provider is not None:
                provider.shutdown()

    def mark_error(
        self,
        span: Any,
        error_type: str,
        description: str = "",
    ) -> None:
        if self._tracer is None:
            return
        from opentelemetry.trace import Status, StatusCode

        safe_error_type = _redact_string(str(error_type))[: self._content_max_chars]
        safe_description = _redact_string(str(description))[: self._content_max_chars]
        span.set_attribute("error.type", safe_error_type)
        if safe_description:
            span.set_attribute("error.message", safe_description)
        span.set_status(Status(StatusCode.ERROR, safe_description or safe_error_type))


_active = Observability()


def configure_observability(
    config: ObservabilityConfig,
    *,
    resource_attributes: dict[str, Any] | None = None,
) -> Observability:
    """Replace the active runtime from validated application configuration."""
    global _active
    _active.shutdown()
    if not config.enabled:
        _active = Observability()
        return _active
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Niuu observability is enabled but the otel extra is not installed"
        ) from exc

    resource = Resource.create(
        {
            "service.name": config.service_name,
            **_clean_attributes(resource_attributes or {}),
        }
    )
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=config.trace_endpoint,
                insecure=config.insecure,
                headers=config.headers,
            )
        )
    )
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=config.metric_endpoint, headers=config.headers),
        export_interval_millis=config.metric_export_interval_milliseconds,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    _active = Observability(
        tracer_provider=trace_provider,
        meter_provider=meter_provider,
        capture_content=config.capture_content,
        content_max_chars=config.content_max_chars,
    )
    atexit.register(_active.shutdown)
    logger.info(
        "Niuu OpenTelemetry enabled: traces=%s metrics=%s service=%s",
        config.trace_endpoint,
        config.metric_endpoint,
        config.service_name,
    )
    return _active


def get_observability() -> Observability:
    return _active


def shutdown_observability() -> None:
    global _active
    _active.shutdown()
    _active = Observability()


def _clean_attributes(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, bool | int | float | str)
        or (
            isinstance(value, list | tuple)
            and all(isinstance(item, bool | int | float | str) for item in value)
        )
    }


def _serialized_content(value: Any, *, max_chars: int) -> str:
    redacted = _redact_content(value)
    try:
        rendered = json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = str(redacted)
    rendered = _redact_string(rendered)
    if len(rendered) <= max_chars:
        return rendered
    omitted = len(rendered) - max_chars
    return f"{rendered[:max_chars]}\n…[truncated {omitted} characters]"


def _redact_content(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).casefold() for part in _SENSITIVE_KEY_PARTS)
                else _redact_content(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_content(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_string(value: str) -> str:
    return _JWT_RE.sub("[REDACTED_JWT]", _BEARER_RE.sub("Bearer [REDACTED]", value))


__all__ = [
    "Observability",
    "configure_observability",
    "get_observability",
    "shutdown_observability",
]
