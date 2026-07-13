"""Official MCP stdio server backed by existing Ravn ToolPort instances."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server as threaded_stdio_server
from mcp.shared.message import SessionMessage

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
        async with _stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(),
            )


@asynccontextmanager
async def _stdio_server():
    """Run MCP stdio with a dedicated reader, independent of shared worker pools."""
    if os.name == "nt":
        async with threaded_stdio_server() as streams:
            yield streams
        return

    read_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)
    loop = asyncio.get_running_loop()
    stdin_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    pending = bytearray()

    async def deliver(line: bytes) -> None:
        try:
            message = types.JSONRPCMessage.model_validate_json(line)
        except Exception as exc:
            await read_writer.send(exc)
            return
        await read_writer.send(SessionMessage(message))

    def read_ready() -> None:
        try:
            chunk = os.read(0, 64 * 1024)
        except (InterruptedError, BlockingIOError):
            return
        if not chunk:
            loop.remove_reader(0)
            if pending:
                stdin_queue.put_nowait(bytes(pending))
                pending.clear()
            stdin_queue.put_nowait(None)
            return
        pending.extend(chunk)
        while (newline := pending.find(b"\n")) >= 0:
            stdin_queue.put_nowait(bytes(pending[: newline + 1]))
            del pending[: newline + 1]

    async def deliver_stdin() -> None:
        async with read_writer:
            while (line := await stdin_queue.get()) is not None:
                await deliver(line)

    async def write_stdout() -> None:
        async with write_reader:
            async for session_message in write_reader:
                payload = session_message.message.model_dump_json(
                    by_alias=True,
                    exclude_none=True,
                )
                sys.stdout.write(payload + "\n")
                sys.stdout.flush()

    loop.add_reader(0, read_ready)
    reader_task = asyncio.create_task(deliver_stdin())
    writer_task = asyncio.create_task(write_stdout())
    try:
        yield read_stream, write_stream
    finally:
        loop.remove_reader(0)
        reader_task.cancel()
        writer_task.cancel()
        await asyncio.gather(reader_task, writer_task, return_exceptions=True)
