"""Official MCP stdio server backed by existing Ravn ToolPort instances."""

from __future__ import annotations

import logging
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)


class ToolPortMcpServer:
    """Expose a list of Ravn ``ToolPort`` instances over MCP JSON-RPC."""

    def __init__(self, tools: list[ToolPort], name: str = "ravn-tools") -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._server = Server(name, version="1.0.0")
        self._server.list_tools()(self.list_tools)
        self._server.call_tool()(self.call_tool)

    async def list_tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.input_schema,
            )
            for tool in self._tools.values()
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> types.CallToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )
        try:
            result = await tool.execute(arguments)
        except Exception:
            logger.exception("Ravn ToolPort MCP tool failed: %s", name)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Tool execution failed")],
                isError=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.content)],
            isError=result.is_error,
        )

    async def run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(),
            )
