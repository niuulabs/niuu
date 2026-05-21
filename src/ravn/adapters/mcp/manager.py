"""MCPManager — orchestrates multiple MCP servers with degraded-mode support.

Responsibilities:
- Parse ``MCPServerConfig`` entries from Settings.
- Spawn/connect all enabled servers concurrently.
- Register discovered tools with prefixed names in the Ravn ToolRegistry.
- Detect naming collisions with built-in tools.
- Operate in degraded mode: if some servers fail, healthy servers' tools
  remain available.
- Graceful shutdown: close all transports on agent exit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ravn.adapters.mcp.client import MCPServerClient, make_tool_prefix
from ravn.adapters.mcp.lifecycle import MCPServerState
from ravn.adapters.mcp.tool import MCPTool
from ravn.adapters.mcp.transport import MCPTransport
from ravn.config import MCPServerConfig
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)


def _build_transport(cfg: MCPServerConfig) -> MCPTransport:
    """Construct the appropriate transport for *cfg*."""
    match cfg.transport:
        case "stdio":
            from ravn.adapters.mcp.stdio_transport import StdioTransport

            return StdioTransport(
                command=cfg.command,
                args=list(cfg.args),
                env=dict(cfg.env),
                read_timeout=cfg.timeout,
            )
        case "http":
            from ravn.adapters.mcp.sse_transport import HTTPTransport

            return HTTPTransport(url=cfg.url, timeout=cfg.timeout)
        case "sse":
            from ravn.adapters.mcp.sse_transport import SSETransport

            return SSETransport(
                url=cfg.url,
                timeout=cfg.timeout,
                connect_timeout=cfg.connect_timeout,
            )
        case _:
            raise ValueError(f"Unknown MCP transport type: {cfg.transport!r}")


def _build_input_schema(tool_def: dict[str, Any]) -> dict[str, Any]:
    """Extract or construct a valid JSON Schema from an MCP tool definition."""
    schema = tool_def.get("inputSchema")
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}}


class MCPManager:
    """Manages the lifecycle of all configured MCP servers.

    Args:
        configs: List of MCPServerConfig from Settings.mcp_servers.
        builtin_tool_names: Set of already-registered tool names for
            collision detection.
    """

    def __init__(
        self,
        configs: list[MCPServerConfig],
        *,
        builtin_tool_names: set[str] | None = None,
        tool_result_hook: (
            Callable[[str, str, dict[str, Any], Any], Awaitable[None]] | None
        ) = None,
    ) -> None:
        self._configs = [c for c in configs if c.enabled]
        self._builtin_names: set[str] = builtin_tool_names or set()
        self._clients: list[MCPServerClient] = []
        self._tools: list[MCPTool] = []
        self._tool_result_hook = tool_result_hook

    @property
    def tools(self) -> list[ToolPort]:
        """All successfully discovered and registered MCP tools."""
        return list(self._tools)

    @property
    def server_states(self) -> dict[str, MCPServerState]:
        """Per-server health states keyed by server name."""
        return {c.name: c.health.state for c in self._clients}

    def get_client(self, server_name: str) -> MCPServerClient | None:
        """Return the live client for *server_name*, or None if not found."""
        for client in self._clients:
            if client.name == server_name:
                return client
        return None

    async def start(self) -> list[ToolPort]:
        """Connect all configured MCP servers and return discovered tools.

        Servers that fail to connect are skipped (degraded mode).  Tools from
        healthy servers are returned even if some servers are unhealthy.
        """
        if not self._configs:
            return []

        logger.debug("Starting %d MCP server(s)", len(self._configs))
        tasks = [self._start_one(cfg) for cfg in self._configs]
        await asyncio.gather(*tasks, return_exceptions=True)
        return list(self._tools)

    async def shutdown(self) -> None:
        """Gracefully close all server connections."""
        if not self._clients:
            return
        await asyncio.gather(
            *(c.shutdown() for c in self._clients),
            return_exceptions=True,
        )
        self._clients.clear()
        self._tools.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _start_one(self, cfg: MCPServerConfig) -> None:
        """Connect a single server and register its tools."""
        transport = _build_transport(cfg)

        # Apply auth headers before connecting if auth is configured.
        if cfg.auth.auth_type:
            try:
                headers = await self._resolve_auth_headers(cfg)
                transport.set_auth_headers(headers)
            except Exception as exc:
                logger.warning(
                    "MCP server %r: auth failed (%s) — connecting without auth",
                    cfg.name,
                    exc,
                )

        client = MCPServerClient(name=cfg.name, transport=transport)
        self._clients.append(client)

        tool_defs = await client.connect()

        if not client.is_healthy:
            logger.warning(
                "MCP server %r failed to connect (%s): %s — degraded mode",
                cfg.name,
                client.health.phase,
                client.health.error,
            )
            return

        self._register_tools(client, tool_defs)

    def _register_tools(
        self,
        client: MCPServerClient,
        tool_defs: list[dict[str, Any]],
    ) -> None:
        """Create MCPTool instances for each discovered tool, with collision checks."""
        prefix = make_tool_prefix(client.name)

        for tool_def in tool_defs:
            original_name = tool_def.get("name", "")
            if not original_name:
                continue

            prefixed_name = f"{prefix}{original_name}"

            if prefixed_name in self._builtin_names:
                logger.warning(
                    "MCP tool %r from server %r collides with a built-in tool — skipping",
                    prefixed_name,
                    client.name,
                )
                continue

            description = tool_def.get("description", f"MCP tool: {original_name}")
            input_schema = _build_input_schema(tool_def)

            mcp_tool = MCPTool(
                server_client=client,
                original_name=original_name,
                prefixed_name=prefixed_name,
                description=description,
                input_schema=input_schema,
                post_execute_hook=self._tool_result_hook,
            )
            self._tools.append(mcp_tool)
            logger.debug("Registered MCP tool %r (server: %r)", prefixed_name, client.name)

        logger.info(
            "MCP server %r: registered %d tool(s)",
            client.name,
            sum(1 for t in self._tools if t.name.startswith(prefix)),
        )

    @staticmethod
    async def _resolve_auth_headers(cfg: MCPServerConfig) -> dict[str, str]:
        """Resolve auth headers from *cfg.auth* config."""
        from ravn.adapters.mcp.auth import acquire_api_key

        auth = cfg.auth
        match auth.auth_type:
            case "api_key":
                token = await acquire_api_key(
                    api_key_env=auth.api_key_env,
                    api_key_header=auth.api_key_header,
                    api_key_prefix=auth.api_key_prefix,
                )
                return {auth.api_key_header: token.auth_header_value()}
            case _:
                logger.warning(
                    "MCP auth type %r not yet supported at startup — skipping",
                    auth.auth_type,
                )
                return {}
