"""MCP stdio server backed by existing Ravn ToolPort instances."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import IO, Any

from ravn.adapters.mcp.protocol import MCP_PROTOCOL_VERSION
from ravn.ports.tool import ToolPort
from ravn.tool_observability import execute_observed_tool

logger = logging.getLogger(__name__)


class ToolPortMcpServer:
    """Expose a list of Ravn ``ToolPort`` instances over MCP JSON-RPC."""

    def __init__(
        self,
        tools: list[ToolPort],
        name: str = "ravn-tools",
        *,
        agent_name: str = "ravn",
        conversation_id: str = "",
        task_id: str = "",
        trace_carrier: dict[str, str] | None = None,
    ) -> None:
        self._catalog_tools = tools
        self._tools = {tool.name: tool for tool in tools}
        self._name = name
        self._agent_name = agent_name
        self._conversation_id = conversation_id
        self._task_id = task_id
        self._trace_carrier = dict(trace_carrier or {})

    def register_tool(self, tool: ToolPort, *, replace: bool = False) -> None:
        """Register a tool for later MCP calls and capability discovery."""
        existing = self._tools.get(tool.name)
        if existing is not None and not replace:
            raise ValueError(f"Tool {tool.name!r} is already registered")

        self._tools[tool.name] = tool
        if existing is None:
            self._catalog_tools.append(tool)
            return

        for index, registered in enumerate(self._catalog_tools):
            if registered.name == tool.name:
                self._catalog_tools[index] = tool
                return

    async def handle(self, payload: dict[str, Any] | list[dict[str, Any]]) -> Any:
        if isinstance(payload, list):
            responses = [r for item in payload if (r := await self._handle_one(item)) is not None]
            return responses or None
        return await self._handle_one(payload)

    async def run_stdio(
        self,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
    ) -> None:
        _in = stdin or sys.stdin
        _out = stdout or sys.stdout
        loop = asyncio.get_event_loop()

        while True:
            line = await loop.run_in_executor(None, _in.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                _out.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
                _out.flush()
                continue

            response = await self.handle(payload)
            if response is not None:
                _out.write(json.dumps(response) + "\n")
                _out.flush()

    async def _handle_one(self, req: dict[str, Any]) -> dict[str, Any] | None:
        req_id = req.get("id")
        if req_id is None:
            return None

        method = str(req.get("method") or "")
        try:
            result = await self._dispatch(method, req.get("params") or {})
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except KeyError as exc:
            return _error(req_id, -32602, str(exc))
        except _MethodNotFoundError:
            return _error(req_id, -32601, "Method not found")
        except Exception:
            logger.exception("Ravn ToolPort MCP handler failed for method %s", method)
            return _error(req_id, -32603, "Internal error")

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        match method:
            case "initialize":
                return {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self._name, "version": "1.0.0"},
                }
            case "tools/list":
                return {"tools": [self._tool_def(tool) for tool in self._tools.values()]}
            case "tools/call":
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise KeyError("arguments must be an object")
                return await self._call_tool(
                    name,
                    arguments,
                    call_id=_tool_call_id(params),
                    trace_carrier=_trace_carrier(params) or self._trace_carrier,
                )
            case "ping":
                return {}
            case _:
                raise _MethodNotFoundError(method)

    def _tool_def(self, tool: ToolPort) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }

    async def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str = "",
        trace_carrier: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")

        result = await execute_observed_tool(
            name=name,
            arguments=arguments,
            execute=lambda: tool.execute(arguments),
            call_id=call_id,
            agent_name=self._agent_name,
            conversation_id=self._conversation_id,
            task_id=self._task_id,
            carrier=trace_carrier,
        )
        return {
            "content": [{"type": "text", "text": result.content}],
            "isError": result.is_error,
        }


class _MethodNotFoundError(Exception):
    pass


def _trace_carrier(params: dict[str, Any]) -> dict[str, str]:
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get("traceContext") or metadata.get("trace_context")
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(raw[key])
        for key in ("traceparent", "tracestate")
        if isinstance(raw.get(key), str) and raw[key]
    }


def _tool_call_id(params: dict[str, Any]) -> str:
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("toolCallId") or "")


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
