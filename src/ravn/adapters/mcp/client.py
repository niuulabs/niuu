"""MCPServerClient — manages the full lifecycle for a single MCP server."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from niuu.domain.tool import ToolResult
from ravn.adapters.mcp.lifecycle import MCPLifecyclePhase, MCPServerState
from ravn.adapters.mcp.protocol import (
    MCPProtocolError,
    extract_result,
    make_initialize_request,
    make_initialized_notification,
    make_resources_list_request,
    make_tool_call_request,
    make_tools_list_request,
    parse_tool_call_result,
    parse_tool_definitions,
)
from ravn.adapters.mcp.transport import MCPTransport, MCPTransportError

logger = logging.getLogger(__name__)

# Characters that are illegal in normalised server names.
_INVALID_NAME_RE = re.compile(r"[^a-zA-Z0-9_]")


def normalise_server_name(name: str) -> str:
    """Convert a server name to a safe identifier component.

    Spaces become underscores; all other non-alphanumeric characters are
    stripped.  The result is lowercased.

    >>> normalise_server_name("My Server!")
    'my_server'
    """
    return _INVALID_NAME_RE.sub("", name.replace(" ", "_")).lower()


def make_tool_prefix(server_name: str) -> str:
    """Return the tool name prefix for *server_name*."""
    return f"mcp__{normalise_server_name(server_name)}__"


@dataclass
class MCPServerHealth:
    """Tracks the health and lifecycle phase of a single server."""

    state: MCPServerState = MCPServerState.DISCONNECTED
    phase: MCPLifecyclePhase = MCPLifecyclePhase.CONFIG_LOAD
    error: str = ""
    server_info: dict[str, Any] = field(default_factory=dict)


class MCPServerClient:
    """Manages the complete lifecycle of a single MCP server connection.

    Usage::

        client = MCPServerClient(name="linear", transport=StdioTransport(...))
        tools = await client.connect()   # list[ToolDefinition]
        result = await client.call_tool("search_issues", {...})
        await client.shutdown()

    The caller is responsible for constructing the appropriate transport.
    """

    def __init__(self, name: str, transport: MCPTransport) -> None:
        self._name = name
        self._transport = transport
        self._health = MCPServerHealth()
        self._tool_defs: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def health(self) -> MCPServerHealth:
        return self._health

    @property
    def tool_defs(self) -> list[dict[str, Any]]:
        """Raw tool definitions returned by the server during discovery."""
        return list(self._tool_defs)

    @property
    def is_healthy(self) -> bool:
        return self._health.state == MCPServerState.CONNECTED

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def set_auth_headers(self, headers: dict[str, str]) -> None:
        """Inject authentication headers into the transport.

        Called by ``MCPAuthTool`` after a successful auth flow.  Delegates to
        the transport's ``set_auth_headers`` (no-op for stdio).
        """
        self._transport.set_auth_headers(headers)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> list[dict[str, Any]]:
        """Run the full connection + discovery lifecycle.

        Returns the list of raw tool definitions on success.  On failure the
        health state is set to ERROR and an empty list is returned — the
        caller should check ``is_healthy`` before using returned tools.
        """
        await self._spawn_connect()
        if not self.is_healthy:
            return []

        await self._initialize_handshake()
        if not self.is_healthy:
            return []

        await self._discover_tools()
        await self._discover_resources()

        if self.is_healthy:
            self._health.phase = MCPLifecyclePhase.READY
            logger.info(
                "MCP server %r ready — %d tool(s) available",
                self._name,
                len(self._tool_defs),
            )

        return list(self._tool_defs)

    async def shutdown(self) -> None:
        """Gracefully close the transport."""
        self._health.phase = MCPLifecyclePhase.SHUTDOWN
        try:
            await self._transport.close()
        except Exception as exc:
            logger.debug("Error shutting down MCP server %r: %s", self._name, exc)
        finally:
            self._health.state = MCPServerState.DISCONNECTED

    # ------------------------------------------------------------------
    # Tool invocation
    # ------------------------------------------------------------------

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Call a tool on the MCP server and return a ToolResult."""
        if not self.is_healthy:
            return ToolResult(
                tool_call_id="",
                content=f"MCP server {self._name!r} is not connected",
                is_error=True,
            )

        self._health.phase = MCPLifecyclePhase.READY
        request = make_tool_call_request(tool_name, arguments)
        try:
            await self._transport.send(request)
            response = await self._transport.receive()
            result = extract_result(response)
            content, is_error = parse_tool_call_result(result)
            return ToolResult(tool_call_id="", content=content, is_error=is_error)
        except MCPProtocolError as exc:
            return ToolResult(
                tool_call_id="",
                content=f"MCP tool error: {exc}",
                is_error=True,
            )
        except MCPTransportError as exc:
            self._set_error(str(exc))
            return ToolResult(
                tool_call_id="",
                content=f"MCP transport error: {exc}",
                is_error=True,
            )

    # ------------------------------------------------------------------
    # Internal lifecycle steps
    # ------------------------------------------------------------------

    async def _spawn_connect(self) -> None:
        self._health.state = MCPServerState.CONNECTING
        self._health.phase = MCPLifecyclePhase.SPAWN_CONNECT
        try:
            await self._transport.start()
            self._health.state = MCPServerState.CONNECTED
        except MCPTransportError as exc:
            self._set_error(str(exc))

    async def _initialize_handshake(self) -> None:
        self._health.phase = MCPLifecyclePhase.INITIALIZE_HANDSHAKE
        try:
            await self._transport.send(make_initialize_request())
            response = await self._transport.receive()
            result = extract_result(response)
            self._health.server_info = result or {}
            await self._transport.send(make_initialized_notification())
        except (MCPTransportError, MCPProtocolError) as exc:
            self._set_error(str(exc))

    async def _discover_tools(self) -> None:
        self._health.phase = MCPLifecyclePhase.TOOL_DISCOVERY
        try:
            await self._transport.send(make_tools_list_request())
            response = await self._transport.receive()
            result = extract_result(response)
            self._tool_defs = parse_tool_definitions(result)
        except (MCPTransportError, MCPProtocolError) as exc:
            logger.warning("Tool discovery failed for %r: %s", self._name, exc)
            # Tool discovery failure is non-fatal — server stays connected.
            self._tool_defs = []

    async def _discover_resources(self) -> None:
        self._health.phase = MCPLifecyclePhase.RESOURCE_DISCOVERY
        try:
            await self._transport.send(make_resources_list_request())
            response = await self._transport.receive()
            extract_result(response)  # Ignore content; just validate no error.
        except (MCPTransportError, MCPProtocolError) as exc:
            logger.debug("Resource discovery for %r: %s", self._name, exc)
            # Resource discovery failure is non-fatal.

    def _set_error(self, message: str) -> None:
        self._health.state = MCPServerState.ERROR
        self._health.error = message
        logger.warning("MCP server %r error: %s", self._name, message)
