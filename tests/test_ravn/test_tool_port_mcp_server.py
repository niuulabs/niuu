from __future__ import annotations

import asyncio
import json
import sys
import textwrap

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

    tools = await server.list_tools()

    assert [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools] == [
        {
            "name": "echo_tool",
            "description": "Echo input text.",
            "inputSchema": EchoTool().input_schema,
        }
    ]


async def test_tool_port_mcp_server_calls_tool() -> None:
    server = ToolPortMcpServer([EchoTool()])

    response = await server.call_tool("echo_tool", {"text": "hello"})

    assert response.model_dump(by_alias=True, exclude_none=True) == {
        "content": [{"type": "text", "text": "hello"}],
        "isError": False,
    }


async def test_stdio_handshake_does_not_depend_on_anyio_worker_pool() -> None:
    script = textwrap.dedent(
        """
        import asyncio
        import sys
        import threading
        from concurrent.futures import ThreadPoolExecutor

        import anyio

        from ravn.adapters.mcp.tool_port_server import ToolPortMcpServer

        class LockedBuffer:
            def readline(self):
                threading.Event().wait()

        class StdinProxy:
            buffer = LockedBuffer()

            def fileno(self):
                return 0

        async def main():
            sys.stdin = StdinProxy()
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            loop.run_in_executor(None, threading.Event().wait)
            limiter = anyio.to_thread.current_default_thread_limiter()
            limiter.total_tokens = 1
            asyncio.create_task(
                anyio.to_thread.run_sync(
                    threading.Event().wait,
                    abandon_on_cancel=True,
                )
            )
            await asyncio.sleep(0.1)
            await ToolPortMcpServer([]).run_stdio()

        asyncio.run(main())
        """
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                }
            ).encode()
            + b"\n"
        )
        await process.stdin.drain()
        response = json.loads(await asyncio.wait_for(process.stdout.readline(), timeout=5))
        assert response["id"] == 1
        assert response["result"]["serverInfo"]["name"] == "ravn-tools"
    finally:
        process.terminate()
        await process.wait()
