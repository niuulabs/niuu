"""Official MCP stdio server backed by existing Ravn ToolPort instances."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import types
from mcp.server import Server
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
    read_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)
    loop = asyncio.get_running_loop()
    stopped = threading.Event()

    async def deliver(line: bytes) -> None:
        try:
            message = types.JSONRPCMessage.model_validate_json(line)
        except Exception as exc:
            await read_writer.send(exc)
            return
        await read_writer.send(SessionMessage(message))

    def read_stdin() -> None:
        fd = sys.stdin.fileno()
        pending = bytearray()
        while not stopped.is_set():
            try:
                chunk = os.read(fd, 64 * 1024)
            except InterruptedError:
                continue
            except BlockingIOError:
                stopped.wait(0.01)
                continue
            if not chunk:
                break
            pending.extend(chunk)
            while (newline := pending.find(b"\n")) >= 0:
                line = bytes(pending[: newline + 1])
                del pending[: newline + 1]
                future = asyncio.run_coroutine_threadsafe(deliver(line), loop)
                try:
                    future.result()
                except Exception:
                    return
        if pending:
            asyncio.run_coroutine_threadsafe(deliver(bytes(pending)), loop)
        asyncio.run_coroutine_threadsafe(read_writer.aclose(), loop)

    async def write_stdout() -> None:
        async with write_reader:
            async for session_message in write_reader:
                payload = session_message.message.model_dump_json(
                    by_alias=True,
                    exclude_none=True,
                )
                sys.stdout.write(payload + "\n")
                sys.stdout.flush()

    reader_thread = threading.Thread(target=read_stdin, name="ravn-mcp-stdin", daemon=True)
    reader_thread.start()
    writer_task = asyncio.create_task(write_stdout())
    try:
        yield read_stream, write_stream
    finally:
        stopped.set()
        writer_task.cancel()
        await asyncio.gather(writer_task, return_exceptions=True)
