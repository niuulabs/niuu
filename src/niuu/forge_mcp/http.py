"""The Forge MCP over Streamable HTTP, JSON response mode.

One endpoint accepts ``POST`` with a single JSON-RPC 2.0 message and answers
with ``application/json``. The server is stateless: it issues no
``Mcp-Session-Id`` and offers no server-to-client SSE stream, so ``GET`` and
``DELETE`` are answered 405 by the route. Implemented from the MCP transport
specification (revisions 2025-03-26 through 2025-11-25):

* ``Origin`` is validated on every request. A browser request from an origin that
  is not explicitly allowed gets 403 (DNS-rebinding and CSRF protection).
  Requests without an ``Origin`` header (agents, CLIs) are not browsers.
* ``Accept`` must allow ``application/json`` (406 otherwise), ``Content-Type``
  must be ``application/json`` (415), and the body is bounded (413).
* An unsupported ``MCP-Protocol-Version`` header is a 400; without the header
  the 2025-03-26 revision is assumed, as the specification requires.
* A request gets 200 with its JSON-RPC response; a notification or a response
  gets 202 with no body. Batches are refused (removed in 2025-06-18).

Version negotiation in ``initialize`` echoes a supported requested version and
otherwise offers the latest; the client decides whether to continue.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from niuu.forge_mcp.tools import ForgeMcpToolbox, UnknownToolError

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
#: The revision a client that sends no MCP-Protocol-Version header is assumed to speak.
ASSUMED_PROTOCOL_VERSION = "2025-03-26"
PROTOCOL_VERSION_HEADER = "mcp-protocol-version"
JSONRPC_VERSION = "2.0"
JSON_CONTENT_TYPE = "application/json"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

SERVER_NAME = "forge"
SERVER_TITLE = "Forge"
SERVER_VERSION = "1.0.0"


def negotiate_protocol_version(requested: object) -> str:
    """Echo a revision we implement, otherwise offer our latest."""
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


@dataclass(frozen=True)
class McpHttpResponse:
    """What the route should send: status, optional JSON body and headers."""

    status: int
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _media_types(value: str) -> list[str]:
    return [part.split(";", 1)[0].strip().lower() for part in value.split(",") if part.strip()]


class _JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


ToolboxFactory = Callable[[], Awaitable[ForgeMcpToolbox]]


class StreamableHttpMcpServer:
    """Protocol handling for one stateless Streamable HTTP MCP endpoint."""

    def __init__(
        self,
        *,
        allowed_origins: Iterable[str],
        max_body_bytes: int,
        instructions: str,
    ) -> None:
        self._allowed_origins = frozenset(origin.rstrip("/") for origin in allowed_origins)
        self._max_body_bytes = max_body_bytes
        self._instructions = instructions

    @property
    def max_body_bytes(self) -> int:
        return self._max_body_bytes

    def reject_transport(self, headers: Mapping[str, str]) -> McpHttpResponse | None:
        """Check Origin, Accept, Content-Type and protocol version before the body."""
        origin = headers.get("origin")
        if origin is not None and origin.rstrip("/") not in self._allowed_origins:
            return McpHttpResponse(
                403, _error(None, INVALID_REQUEST, f"Origin not allowed: {origin}")
            )
        accept = headers.get("accept")
        if accept is not None:
            types = _media_types(accept)
            if not any(t in (JSON_CONTENT_TYPE, "application/*", "*/*") for t in types):
                return McpHttpResponse(
                    406,
                    _error(None, INVALID_REQUEST, "Accept must allow application/json"),
                )
        content_types = _media_types(headers.get("content-type", ""))
        if content_types != [JSON_CONTENT_TYPE]:
            return McpHttpResponse(
                415, _error(None, INVALID_REQUEST, "Content-Type must be application/json")
            )
        version = headers.get(PROTOCOL_VERSION_HEADER)
        if version is not None and version not in SUPPORTED_PROTOCOL_VERSIONS:
            supported = ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
            return McpHttpResponse(
                400,
                _error(
                    None,
                    INVALID_REQUEST,
                    f"Unsupported MCP-Protocol-Version {version!r}; supported: {supported}",
                ),
            )
        return None

    def body_too_large(self) -> McpHttpResponse:
        return McpHttpResponse(
            413,
            _error(None, INVALID_REQUEST, f"Request body exceeds {self._max_body_bytes} bytes"),
        )

    async def handle(self, body: bytes, toolbox: ToolboxFactory) -> McpHttpResponse:
        """Answer one JSON-RPC message. ``toolbox`` is built only when a tool is used."""
        try:
            message = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return McpHttpResponse(400, _error(None, PARSE_ERROR, "Parse error: invalid JSON"))
        if isinstance(message, list):
            return McpHttpResponse(
                400, _error(None, INVALID_REQUEST, "JSON-RPC batches are not supported")
            )
        if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC_VERSION:
            return McpHttpResponse(400, _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message"))
        if "method" not in message:
            if "result" in message or "error" in message:
                return McpHttpResponse(202)  # a response; this server sends no requests
            return McpHttpResponse(
                400, _error(message.get("id"), INVALID_REQUEST, "missing method")
            )
        if "id" not in message:
            return McpHttpResponse(202)  # notifications/initialized, cancelled, …
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(method, str):
            return McpHttpResponse(
                400, _error(request_id, INVALID_REQUEST, "method must be a string")
            )
        if not isinstance(params, dict):
            return McpHttpResponse(
                200, _error(request_id, INVALID_PARAMS, "params must be an object")
            )
        try:
            result = await self._dispatch(method, params, toolbox)
        except _JsonRpcError as exc:
            return McpHttpResponse(200, _error(request_id, exc.code, exc.message))
        return McpHttpResponse(200, _result(request_id, result))

    async def _dispatch(
        self, method: str, params: dict[str, Any], toolbox: ToolboxFactory
    ) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": negotiate_protocol_version(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "title": SERVER_TITLE,
                    "version": SERVER_VERSION,
                },
                "instructions": self._instructions,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": (await toolbox()).list_tools()}
        if method == "tools/call":
            return await self._call_tool(params, toolbox)
        raise _JsonRpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    async def _call_tool(self, params: dict[str, Any], toolbox: ToolboxFactory) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            raise _JsonRpcError(INVALID_PARAMS, "tools/call needs a tool name")
        if not isinstance(arguments, dict):
            raise _JsonRpcError(INVALID_PARAMS, "tool arguments must be an object")
        try:
            result = await (await toolbox()).call(name, arguments)
        except UnknownToolError:
            raise _JsonRpcError(INVALID_PARAMS, f"unknown tool: {name}") from None
        return result.to_mcp()
