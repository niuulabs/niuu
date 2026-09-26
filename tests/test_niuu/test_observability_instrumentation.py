"""Real-app coverage for niuu.observability's FastAPI/httpx instrumentation.

Proves the two things a mocked-tracer unit test cannot: that instrumenting
inside a lifespan handler has no effect (Starlette caches its middleware
stack on the first ASGI ``__call__``, and the lifespan event arrives through
that same call), and that the redaction hooks actually reach the attributes
OTel's own instrumentation sets on real spans.

State reset between tests comes from the autouse fixture in
tests/test_niuu/conftest.py.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk")

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from niuu.observability import (
    Observability,
    _build_component_attribute_span_processor,
    instrument_fastapi_app,
    instrument_httpx_client,
)


def _telemetry() -> tuple[Observability, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer_provider.add_span_processor(_build_component_attribute_span_processor())
    return (
        Observability(tracer_provider=tracer_provider, meter_provider=MeterProvider()),
        exporter,
    )


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    return app


class TestInstrumentingInLifespanHasNoEffect:
    """Regression test for the bug a reviewer caught with a script:
    0 spans when instrumenting from inside a lifespan handler, vs spans with
    the propagated trace id when instrumenting before the app's first call."""

    def test_instrumenting_inside_lifespan_produces_no_spans(self):
        telemetry, exporter = _telemetry()
        app = FastAPI()

        @app.get("/ping")
        async def ping() -> dict:
            return {"ok": True}

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # The bug: this used to be where composition roots called it.
            instrument_fastapi_app(app, telemetry)
            yield

        app.router.lifespan_context = lifespan

        with TestClient(app) as client:
            response = client.get("/ping")
        assert response.status_code == 200
        assert exporter.get_finished_spans() == ()

    def test_instrumenting_before_first_call_produces_the_server_span(self):
        telemetry, exporter = _telemetry()
        app = _make_app()
        # The fix: instrument during create_app, before the app is ever
        # handed to a server/TestClient.
        instrument_fastapi_app(app, telemetry)

        with TestClient(app) as client:
            response = client.get("/ping")
        assert response.status_code == 200
        spans = exporter.get_finished_spans()
        assert len(spans) >= 1
        assert any(span.name == "GET /ping" for span in spans)


class TestTraceparentContinuesTheCallersTrace:
    """The goal-defining proof: an inbound traceparent must continue the
    caller's trace, not start a new root span."""

    def test_inbound_traceparent_parents_the_server_span(self):
        telemetry, exporter = _telemetry()
        app = _make_app()
        instrument_fastapi_app(app, telemetry)

        tracer = trace.get_tracer("caller", tracer_provider=telemetry.tracer_provider)
        with tracer.start_as_current_span("caller.request") as caller_span:
            caller_trace_id = caller_span.get_span_context().trace_id
            carrier = telemetry.inject()

        with TestClient(app) as client:
            response = client.get("/ping", headers=carrier)
        assert response.status_code == 200

        spans = {span.name: span for span in exporter.get_finished_spans()}
        server_span = spans["GET /ping"]
        assert server_span.context.trace_id == caller_trace_id
        assert server_span.parent is not None
        assert server_span.parent.trace_id == caller_trace_id


class TestComponentAttributeAcrossMountedApps:
    """Several composition roots sharing one pipeline (mini mode) — spans
    from each app's requests carry the right niuu.component, not a shared
    false service.name."""

    def test_component_scope_middleware_stamps_the_right_app(self):
        telemetry, exporter = _telemetry()

        volundr_app = _make_app()
        instrument_fastapi_app(volundr_app, telemetry, component="volundr")

        ting_app = _make_app()
        instrument_fastapi_app(ting_app, telemetry, component="ting")

        with TestClient(volundr_app) as client:
            client.get("/ping")
        with TestClient(ting_app) as client:
            client.get("/ping")

        spans = exporter.get_finished_spans()
        components = {dict(span.attributes or {}).get("niuu.component") for span in spans}
        assert components == {"volundr", "ting"}


class TestServerUrlRedaction:
    def test_inbound_query_token_is_redacted_on_the_server_span(self):
        telemetry, exporter = _telemetry()
        app = _make_app()
        instrument_fastapi_app(app, telemetry)

        with TestClient(app) as client:
            client.get("/ping?token=super-secret-jwt&keep=me")

        (span,) = [s for s in exporter.get_finished_spans() if s.name == "GET /ping"]
        attrs = dict(span.attributes or {})
        assert "super-secret-jwt" not in str(attrs)
        assert attrs.get("url.query") == "token=%5BREDACTED%5D&keep=me"
        assert "keep=me" in attrs.get("url.query", "")


class TestClientUrlRedaction:
    def test_outbound_bot_token_path_is_redacted_on_the_client_span(self, respx_mock):
        telemetry, exporter = _telemetry()
        instrument_httpx_client(telemetry)
        respx_mock.post("https://api.telegram.org/bot123456:AAExampleToken/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        async def _call() -> None:
            async with httpx.AsyncClient() as client:
                await client.post("https://api.telegram.org/bot123456:AAExampleToken/sendMessage")

        import asyncio

        asyncio.run(_call())

        (span,) = [s for s in exporter.get_finished_spans() if s.name.upper().startswith("POST")]
        attrs = dict(span.attributes or {})
        assert "AAExampleToken" not in str(attrs)
        assert attrs.get("http.url", "").endswith("/bot[REDACTED]/sendMessage")
