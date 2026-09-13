"""Encode a real OTLP span offline in the installed runtime image."""

from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

provider = TracerProvider()
exporter = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(exporter))
with provider.get_tracer("niuu-image-check").start_as_current_span("encode"):
    pass
request = encode_spans(exporter.get_finished_spans())
scope = request.resource_spans[0].scope_spans[0]
assert scope.scope.name == "niuu-image-check"
assert scope.spans[0].name == "encode"
assert request.SerializeToString()
OTLPSpanExporter(endpoint="http://127.0.0.1:4317", insecure=True).shutdown()
provider.shutdown()
