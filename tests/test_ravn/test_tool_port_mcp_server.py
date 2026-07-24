from __future__ import annotations

import pytest

from ravn.adapters.mcp.tool_port_server import ToolPortMcpServer
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort


class EchoTool(ToolPort):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "Echo input text."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

    @property
    def required_permission(self) -> str:
        return "test:echo"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content=str(input["text"]))


async def test_tool_port_mcp_server_lists_tool_defs() -> None:
    server = ToolPortMcpServer([EchoTool()])

    response = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response["result"]["tools"] == [
        {
            "name": "echo_tool",
            "description": "Echo input text.",
            "inputSchema": EchoTool().input_schema,
        }
    ]


async def test_tool_port_mcp_server_calls_tool() -> None:
    server = ToolPortMcpServer([EchoTool()])

    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo_tool", "arguments": {"text": "hello"}},
        }
    )

    assert response["result"] == {
        "content": [{"type": "text", "text": "hello"}],
        "isError": False,
    }


async def test_tool_port_mcp_server_registers_tool_after_startup() -> None:
    catalog = [EchoTool()]
    server = ToolPortMcpServer(catalog)

    replacement = EchoTool()
    server.register_tool(replacement, replace=True)

    response = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert [tool["name"] for tool in response["result"]["tools"]] == ["echo_tool"]
    assert catalog == [replacement]


async def test_tool_port_mcp_server_rejects_duplicate_registration() -> None:
    server = ToolPortMcpServer([EchoTool()])

    with pytest.raises(ValueError, match="already registered"):
        server.register_tool(EchoTool())


async def test_tool_port_mcp_server_emits_tool_metrics_and_propagates_trace(
    monkeypatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import niuu.observability as observability_module
    from niuu.observability import Observability

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    metric_reader = InMemoryMetricReader()
    telemetry = Observability(
        tracer_provider=tracer_provider,
        meter_provider=MeterProvider(metric_readers=[metric_reader]),
        capture_content=True,
    )
    monkeypatch.setattr(observability_module, "_active", telemetry)

    with telemetry.span("resident-turn"):
        carrier = telemetry.inject()
    server = ToolPortMcpServer(
        [EchoTool()],
        agent_name="ivaldi",
        conversation_id="conversation-1",
        task_id="task-1",
    )
    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "echo_tool",
                "arguments": {"text": "hello"},
                "_meta": {
                    "toolCallId": "call-1",
                    "traceContext": carrier,
                },
            },
        }
    )

    assert response["result"]["isError"] is False
    parent, tool_span = exporter.get_finished_spans()
    assert tool_span.name == "execute_tool echo_tool"
    assert tool_span.context.trace_id == parent.context.trace_id
    assert tool_span.attributes["ravn.task.id"] == "task-1"
    assert [event.name for event in tool_span.events] == [
        "gen_ai.tool.request",
        "gen_ai.tool.response",
    ]
    assert tool_span.events[0].attributes["gen_ai.tool.call.id"] == "call-1"

    metrics = {
        metric.name: metric
        for resource in metric_reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {"ravn.agent.tool.calls", "ravn.agent.tool.duration"} <= metrics.keys()
    call_point = metrics["ravn.agent.tool.calls"].data.data_points[0]
    assert call_point.attributes["gen_ai.agent.name"] == "ivaldi"
    assert call_point.attributes["gen_ai.tool.name"] == "echo_tool"
    assert call_point.attributes["ravn.tool.outcome"] == "success"
    telemetry.shutdown()
