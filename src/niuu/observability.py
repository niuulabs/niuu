"""Shared OpenTelemetry runtime for Niuu processes and Ravn.

Consumers only see this small facade. OpenTelemetry stays optional when
disabled, while an enabled configuration fails loudly if its SDK/exporters are
not installed.

One process, one OTLP pipeline
-------------------------------
A process gets exactly one trace/metric export pipeline (one ``TracerProvider``
/ ``MeterProvider`` pair), even when several composition roots share it (the
mini-mode host runs Volundr, Ting, Bifrost, Mimir, Observatory, and the shared
host as sub-apps of one root FastAPI app, in one process — see
``niuu/app.py::build_root_app``). Distinct services are told apart on that one
pipeline two ways:

* ``service.name`` on the process ``Resource`` — the host-level name (e.g.
  ``niuu-mini``) when the CLI's own ``CLISettings.observability`` configures
  first, or a single service's own name when that service is the only
  component in the process (the common, standalone-deployment case).
* ``niuu.component`` on every span — stamped by ``_ComponentAttributeSpanProcessor``
  from a ``ContextVar`` that ``instrument_fastapi_app`` scopes to each
  mounted app's requests, so spans from Bifrost's sub-app are still
  attributable to "bifrost" even while ``service.name`` says "niuu-mini".

``configure_observability`` is safe to call once per composition root: the
first call in a process builds the real pipeline from its config; later calls
either join it (same settings, or their own ``observability`` block was never
explicitly set — they inherit the ambient decision), opt out cleanly
(``enabled: false`` explicitly set), or raise (two components each explicitly
want a real, but different, export pipeline — one process cannot honour both).
"""

from __future__ import annotations

import atexit
import json
import logging
import re
from contextlib import nullcontext
from contextvars import ContextVar
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
# Telegram's Bot API embeds the bot token directly in the URL path:
# https://api.telegram.org/bot<numeric-id>:<token>/sendMessage
_BOT_TOKEN_PATH_RE = re.compile(r"(?i)/bot\d+:[^/]+")


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

    @property
    def tracer_provider(self) -> Any | None:
        """The concrete ``TracerProvider`` backing this facade, or ``None``.

        Exposed so third-party auto-instrumentation (FastAPI, httpx) can be
        pointed at the exact provider this process configured, instead of
        the OTel global API — this facade deliberately never calls
        ``trace.set_tracer_provider``, so relying on the global default
        would silently no-op the instrumentation.
        """
        return self._tracer_provider

    @property
    def meter_provider(self) -> Any | None:
        """The concrete ``MeterProvider`` backing this facade, or ``None``."""
        return self._meter_provider

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

    def attach_ambient_context(self, carrier: dict[str, str]) -> None:
        """Make *carrier*'s trace context the ambient context for this process.

        Unlike ``span(carrier=...)``, which parents one span, this affects
        every span created afterward with no explicit carrier — appropriate
        only for a process whose entire lifetime belongs to one inbound
        trace. Skuld's per-session broker process reads its own inherited
        ``TRACEPARENT``/``TRACESTATE`` env vars this way at startup (see
        ``skuld/broker_api.py``), so every span it opens for that session
        nests under the request that created it.

        Trade-off, intentionally accepted rather than built around here: a
        long-lived session nests potentially days of work under the single
        trace that was active at creation time. A span *link* to the
        creating trace, instead of a parent, would avoid that at the cost of
        losing the direct parent/child relationship most trace UIs render —
        not implemented; revisit if long sessions make single-trace nesting
        unusable in practice.
        """
        if self._tracer is None:
            return
        from opentelemetry import context as otel_context
        from opentelemetry.propagate import extract

        otel_context.attach(extract(carrier))

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


def _build_component_attribute_span_processor() -> Any:
    """Stamps ``niuu.component`` on every span from the active ContextVar.

    Added once, to the single process-wide ``TracerProvider``. The value
    comes from ``_current_component``, which ``instrument_fastapi_app``
    scopes to each mounted app's requests via ``_ComponentScopeMiddleware`` —
    this is what lets several services share one pipeline (mini mode)
    without every span claiming the same identity.

    Subclasses the real SDK ``SpanProcessor`` (only available once the otel
    extra is installed, so this is a factory rather than a module-level
    class) purely to inherit its no-op defaults for every hook the SDK
    defines — including ones added after this facade was written — rather
    than reimplementing and risking drifting out of sync with them.
    """
    from opentelemetry.sdk.trace import SpanProcessor

    class _ComponentAttributeSpanProcessor(SpanProcessor):
        def on_start(self, span: Any, parent_context: Any = None) -> None:
            component = _current_component.get()
            if component:
                span.set_attribute("niuu.component", component)

    return _ComponentAttributeSpanProcessor()


_active = Observability()
_active_fingerprint: tuple[Any, ...] | None = None
_active_component = ""
_seen_components: set[str] = set()
_current_component: ContextVar[str] = ContextVar("niuu_observability_component", default="")


def _pipeline_fingerprint(config: ObservabilityConfig) -> tuple[Any, ...]:
    """Everything about *config* that determines where/how telemetry exports.

    Deliberately excludes ``service_name`` — that is the one thing allowed to
    differ between components sharing a pipeline (it becomes ``niuu.component``
    on their spans instead of the process ``service.name``).
    """
    return (
        config.enabled,
        config.trace_endpoint,
        config.metric_endpoint,
        config.insecure,
        tuple(sorted(config.headers.items())),
        config.capture_content,
        config.content_max_chars,
        config.metric_export_interval_milliseconds,
    )


def _resolve_service_name(config: ObservabilityConfig, default_service_name: str) -> str:
    """The Resource's ``service.name`` — the operator's explicit choice wins.

    A composition root whose Settings class is *reused* by more than one
    service (Observatory and the mini-mode shared host both embed
    ``volundr.config.Settings``) would otherwise always report
    ``service.name=volundr``, even when nobody asked for that — the pydantic
    class default, not an operator decision. ``model_fields_set`` tells the
    two apart: if the operator actually wrote ``service_name:`` in this
    service's own config, honour it; otherwise fall back to the composition
    root's own default label instead of the shared class's default.
    """
    if "service_name" in config.model_fields_set or not default_service_name:
        return config.service_name
    return default_service_name


def configure_observability(
    config: ObservabilityConfig,
    *,
    resource_attributes: dict[str, Any] | None = None,
    component: str = "",
    default_service_name: str = "",
) -> Observability:
    """Configure or join this process's one OTLP pipeline.

    Call once per composition root, as early as possible (before the app's
    first ASGI call — see the module docstring on why that matters for
    ``instrument_fastapi_app``). See the module docstring for the join/raise
    rules when more than one composition root shares a process.
    """
    global _active, _active_fingerprint, _active_component

    service_name = _resolve_service_name(config, default_service_name)
    component_name = component or service_name
    fingerprint = _pipeline_fingerprint(config)
    explicit_enabled = "enabled" in config.model_fields_set

    if _active_fingerprint is not None:
        if not config.enabled and explicit_enabled:
            # This component explicitly opted out. It must not light up just
            # because a sibling in the same process enabled observability —
            # return a private, disabled instance; the shared pipeline for
            # everyone else is untouched.
            logger.info(
                "niuu observability: component '%s' has observability.enabled: "
                "false explicitly set; not instrumenting it, though this "
                "process already has an active pipeline for '%s'.",
                component_name,
                _active_component,
            )
            return Observability()
        if not config.enabled:
            # Never mentioned observability at all — inherit the ambient
            # decision rather than treating the unset default as an opt-out.
            _seen_components.add(component_name)
            return _active
        if fingerprint != _active_fingerprint:
            raise RuntimeError(
                f"Conflicting observability configuration: component "
                f"'{component_name}' requests a trace_endpoint/metric_endpoint/"
                f"headers/capture_content different from the pipeline component "
                f"'{_active_component}' already configured for this process. "
                "One process exports through one OTLP pipeline — align both "
                "services' observability: blocks (CLISettings.observability, "
                "the mini-mode host config, does this automatically when "
                "services don't set their own), or run them as separate "
                "processes if they need genuinely different pipelines."
            )
        _seen_components.add(component_name)
        logger.info(
            "niuu observability: component '%s' joins the pipeline '%s' "
            "already active for this process.",
            component_name,
            _active_component,
        )
        return _active

    # First configure_observability call in this process.
    if not config.enabled:
        _active = Observability()
        _active_fingerprint = fingerprint
        _active_component = component_name
        _seen_components.add(component_name)
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
            "service.name": service_name,
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
    trace_provider.add_span_processor(_build_component_attribute_span_processor())
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
    _active_fingerprint = fingerprint
    _active_component = component_name
    _seen_components.add(component_name)
    atexit.register(_active.shutdown)
    logger.info(
        "Niuu OpenTelemetry enabled: traces=%s metrics=%s service=%s component=%s",
        config.trace_endpoint,
        config.metric_endpoint,
        service_name,
        component_name,
    )
    return _active


def get_observability() -> Observability:
    return _active


def shutdown_observability() -> None:
    global _active, _active_fingerprint, _active_component
    _active.shutdown()
    _active = Observability()
    _active_fingerprint = None
    _active_component = ""
    _seen_components.clear()


def instrument_fastapi_app(
    app: Any,
    observability: Observability | None = None,
    *,
    component: str = "",
) -> None:
    """Instrument a FastAPI app with OTel server spans, when enabled.

    Call from the composition root's ``create_app()``, before the app is
    ever handed to an ASGI server or ``TestClient`` — Starlette builds and
    caches its middleware stack on the first ``__call__`` (which is also how
    the lifespan startup event arrives), so instrumenting from inside a
    lifespan handler has no effect: the middleware stack was already frozen
    by the time that code runs.

    A no-op while *observability* (or, if omitted, ``get_observability()``)
    is disabled. When enabled, the ``opentelemetry-instrumentation-fastapi``
    package is required — the same no-fallbacks contract as
    :func:`configure_observability`: configured but impossible raises, it
    does not run uninstrumented.

    *component*, when given, scopes every ``get_observability()`` call made
    while handling this app's requests (including by nested business logic,
    not just the server span itself) to *component* — see the module
    docstring. Set from ``server_request_hook``, not an ASGI middleware:
    ``FastAPIInstrumentor.instrument_app`` overrides ``build_middleware_stack``
    so its own ASGI middleware is always outermost regardless of
    ``add_middleware`` call order, which would put a middleware-based scope
    outside the server span it needs to cover. The hook runs synchronously
    before the request is handed to the route, in the same task/context, so
    setting the ContextVar there reaches everything downstream.
    """
    instance = observability if observability is not None else get_observability()
    if not instance.enabled:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Niuu observability is enabled but "
            "opentelemetry-instrumentation-fastapi is not installed; "
            "install the 'otel' extra or set observability.enabled: false"
        ) from exc
    # Pass the concrete providers explicitly: this facade never calls
    # trace.set_tracer_provider, so the OTel global default would otherwise
    # hand the instrumentation a no-op tracer/meter.
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=instance.tracer_provider,
        meter_provider=instance.meter_provider,
        server_request_hook=_make_server_request_hook(component),
    )


def instrument_httpx_client(observability: Observability | None = None) -> None:
    """Instrument the process-wide httpx transport, when enabled.

    Propagates W3C trace context onto every outbound ``httpx`` request made
    by this process (sync and async clients) and opens a client span per
    call, with credential-bearing URL paths/query strings redacted. A no-op
    while disabled; raises when enabled but the instrumentation package is
    missing, per the no-fallbacks rule.

    ``opentelemetry-instrumentation-httpx`` monkeypatches the httpx transport
    classes once for the whole process — there is no per-app equivalent of
    ``tracer_provider=`` here the way there is for FastAPI. A second call
    (another component's own instrumentation setup) is a safe no-op: outbound
    call *export* follows whichever provider instrumented first. In practice
    this only matters when components in one process use genuinely different
    pipelines, which :func:`configure_observability` already refuses to allow
    when they're both enabled (see its conflict check).
    """
    instance = observability if observability is not None else get_observability()
    if not instance.enabled:
        return
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Niuu observability is enabled but "
            "opentelemetry-instrumentation-httpx is not installed; "
            "install the 'otel' extra or set observability.enabled: false"
        ) from exc
    instrumentor = HTTPXClientInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        return
    instrumentor.instrument(
        tracer_provider=instance.tracer_provider,
        meter_provider=instance.meter_provider,
        request_hook=_httpx_request_hook,
        async_request_hook=_httpx_async_request_hook,
    )


def uninstrument_httpx_client() -> None:
    """Undo :func:`instrument_httpx_client`. For test teardown."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError:  # pragma: no cover - depends on optional install
        return
    instrumentor = HTTPXClientInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()


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


def _is_sensitive_query_key(key: str) -> bool:
    return any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS)


def _redact_query(query: str) -> str:
    """Redact sensitive query parameter *values*, keeping keys and order."""
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    redacted = [
        (key, "[REDACTED]" if _is_sensitive_query_key(key) else value) for key, value in pairs
    ]
    return urlencode(redacted)


def _redact_path(path: str) -> str:
    """Redact credential-bearing path segments (e.g. Telegram's /bot<token>/)."""
    return _BOT_TOKEN_PATH_RE.sub("/bot[REDACTED]", path)


def _redact_url(url: str) -> str:
    """Redact a full URL's userinfo, sensitive path segments, and query values.

    Broader than ``opentelemetry.util.http.redact_url``, which only strips
    userinfo and a short hardcoded list of AWS/GCS signature query params
    (``AWSAccessKeyId``, ``Signature``, ``sig``, ``X-Goog-Signature``) — it
    never touches ``token``/``access_token``-style query params or credential
    path segments like Telegram's bot token.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    netloc = parts.netloc.rsplit("@", 1)[-1] if "@" in parts.netloc else parts.netloc
    path = _redact_path(parts.path)
    query = _redact_query(parts.query)
    return urlunsplit((parts.scheme, netloc, path, query, parts.fragment))


def _asgi_host(scope: dict) -> str:
    for key, value in scope.get("headers") or []:
        if key == b"host":
            return value.decode("latin-1")
    server = scope.get("server")
    if server:
        host, port = server
        return f"{host}:{port}" if port else str(host)
    return ""


def _make_server_request_hook(component: str) -> Any:
    """Build the ``server_request_hook`` for one instrumented app.

    Two jobs, both because this is the earliest point that runs inside the
    request's own task/context, before the route (or the SpanProcessor's
    ``on_start`` for the server span itself, which already fired by now) sees
    it:

    * Sets ``_current_component`` for *component* (when given), and stamps
      ``niuu.component`` directly on the server span — ``on_start`` fires
      before this hook, so the SpanProcessor alone would miss the server
      span even though it correctly tags every span created later in the
      same request.
    * Overwrites the inbound URL attributes with a fully redacted version —
      the ASGI instrumentation already set ``http.url``/``http.target`` via
      its own, narrower ``redact_url`` before this hook runs, so this
      reconstructs and overwrites them, covering inbound ``?token=``/
      ``?access_token=`` query params (including on WebSocket upgrade
      requests) that the built-in redaction never touches.
    """

    def hook(span: Any, scope: dict) -> None:
        is_recording = getattr(span, "is_recording", lambda: False)()
        if component:
            _current_component.set(component)
            if is_recording:
                span.set_attribute("niuu.component", component)
        if not is_recording:
            return
        path = _redact_path(str(scope.get("path", "") or ""))
        query = _redact_query((scope.get("query_string") or b"").decode("latin-1"))
        target = f"{path}?{query}" if query else path
        host = _asgi_host(scope)
        scheme = scope.get("scheme", "http")
        url = f"{scheme}://{host}{target}" if host else target
        for key in ("http.url", "url.full"):
            span.set_attribute(key, url)
        span.set_attribute("http.target", target)
        span.set_attribute("url.path", path)
        if query:
            span.set_attribute("url.query", query)

    return hook


def _httpx_request_hook(span: Any, request_info: Any) -> None:
    """Overwrite the outbound URL attribute with a fully redacted version.

    Covers ``/bot<token>/...``-style credential path segments (Ting's
    Telegram client) that the built-in ``redact_url`` never touches, and
    query-parameter values beyond its hardcoded AWS/GCS signature list.
    """
    if not getattr(span, "is_recording", lambda: False)():
        return
    url = _redact_url(str(request_info.url))
    span.set_attribute("http.url", url)
    span.set_attribute("url.full", url)


async def _httpx_async_request_hook(span: Any, request_info: Any) -> None:
    _httpx_request_hook(span, request_info)


__all__ = [
    "Observability",
    "configure_observability",
    "get_observability",
    "instrument_fastapi_app",
    "instrument_httpx_client",
    "shutdown_observability",
    "uninstrument_httpx_client",
]
