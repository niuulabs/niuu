from __future__ import annotations

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
