"""End-to-end proof that one W3C trace id flows: Ravn LLM call -> Bifröst.

Uses an in-memory span exporter instead of a real OTLP collector or a live
Bifröst server. The goal (per the end-to-end-tracing task) is that a single
trace follows a piece of work across the whole system; this test proves the
propagation contract at the two seams that make that possible:

1. A Ravn LLM adapter (``BifrostAdapter``, which calls through Bifröst)
   injects the active span's W3C ``traceparent`` onto its outbound request
   headers (``AnthropicAdapter._headers``, inherited by ``BifrostAdapter``).
2. Bifröst's router attaches GenAI attributes to whatever span is active when
   a completion is served — in production that's the FastAPI server span
   that ``niuu.observability.instrument_fastapi_app`` opens from the same
   inbound ``traceparent`` header; here we extract it directly with
   ``Observability.span(carrier=...)`` to isolate the propagation contract
   from FastAPI/ASGI plumbing, which is exercised separately in
   ``tests/test_bifrost`` and ``tests/test_volundr``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def telemetry(monkeypatch):
    """An enabled Observability backed by an in-memory exporter, installed as active."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from niuu import observability as obs_module
    from niuu.observability import Observability

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    instance = Observability(tracer_provider=tracer_provider, meter_provider=MeterProvider())
    monkeypatch.setattr(obs_module, "_active", instance)
    return instance, exporter


def test_ravn_llm_call_trace_id_reaches_the_bifrost_span(telemetry) -> None:
    from bifrost.router import record_genai_span_attributes
    from bifrost.translation.models import UsageInfo
    from ravn.adapters.llm.bifrost import BifrostAdapter

    instance, exporter = telemetry
    adapter = BifrostAdapter(agent_id="valkyrie-1", session_id="session-1")

    # 1. Ravn opens a span around its model call (what ravn's judgment loop /
    #    tool-call path does today via `get_observability().span(...)`).
    with instance.span("ravn.llm_request") as ravn_span:
        ravn_trace_id = f"{ravn_span.get_span_context().trace_id:032x}"

        # The adapter builds headers for the outbound HTTP call to Bifröst.
        headers = adapter._headers()

    assert "traceparent" in headers
    carrier = {"traceparent": headers["traceparent"]}
    if "tracestate" in headers:
        carrier["tracestate"] = headers["tracestate"]

    # 2. Bifröst receives the request; its own server span (here, extracted
    #    directly from the propagated carrier) becomes a child of Ravn's span.
    with instance.span("bifrost.messages", carrier=carrier) as bifrost_span:
        bifrost_trace_id = f"{bifrost_span.get_span_context().trace_id:032x}"
        record_genai_span_attributes(
            requested_model="claude-sonnet-4-6",
            provider="anthropic",
            failover_attempts=0,
            cache_hit=False,
            usage=UsageInfo(input_tokens=120, output_tokens=45),
        )

    # Same trace id on both ends: the GOAL this task exists to deliver.
    assert bifrost_trace_id == ravn_trace_id

    finished = {span.name: span for span in exporter.get_finished_spans()}
    assert set(finished) == {"ravn.llm_request", "bifrost.messages"}
    bifrost_recorded = finished["bifrost.messages"]
    attrs = dict(bifrost_recorded.attributes or {})
    assert attrs["gen_ai.request.model"] == "claude-sonnet-4-6"
    assert attrs["gen_ai.provider.name"] == "anthropic"
    assert attrs["bifrost.failover_attempts"] == 0
    assert attrs["bifrost.cache_hit"] is False
    assert attrs["gen_ai.usage.input_tokens"] == 120
    assert attrs["gen_ai.usage.output_tokens"] == 45
    # And the child span really is parented under the Ravn span, not merely
    # sharing a trace id by coincidence.
    assert bifrost_recorded.parent is not None
    assert f"{bifrost_recorded.parent.trace_id:032x}" == ravn_trace_id


def test_no_traceparent_header_when_observability_disabled() -> None:
    from ravn.adapters.llm.bifrost import BifrostAdapter

    adapter = BifrostAdapter(agent_id="valkyrie-1", session_id="session-1")
    headers = adapter._headers()

    assert "traceparent" not in headers


def test_skuld_closes_the_chain_by_attaching_its_inherited_traceparent(telemetry) -> None:
    """Skuld's per-session broker process inherits TRACEPARENT/TRACESTATE in
    its own env (set by Volundr's CoreSessionContributor when it spawned the
    process) and attaches it as ambient context at startup — this is what
    lets Ravn -> Bifrost -> Volundr -> Skuld -> Claude Code share one trace
    id instead of the chain breaking at Skuld.
    """
    instance, exporter = telemetry

    # The "creating request" (Volundr handling the session-create call).
    with instance.span("volundr.create_session") as creating_span:
        creating_trace_id = f"{creating_span.get_span_context().trace_id:032x}"
        inherited_env_carrier = instance.inject()

    # Skuld's own process startup: same call broker_api.py's lifespan makes.
    instance.attach_ambient_context(inherited_env_carrier)
    with instance.span("skuld.broker_startup") as skuld_span:
        skuld_trace_id = f"{skuld_span.get_span_context().trace_id:032x}"

    assert skuld_trace_id == creating_trace_id
