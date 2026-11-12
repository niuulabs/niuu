"""Broker loopback routes the memory MCP stdio server forwards to.

``GET /api/memory-mcp/tools`` answers the gateway's ``{instructions, tools}`` and
``POST /api/memory-mcp/tools/{name}`` runs one tool with the JSON body as its
arguments, answering the MCP ``CallToolResult`` shape. Both require the broker's
loopback secret (see :mod:`skuld.session_runtime`). The broker calls the gateway with
the session's Forge credential; a gateway failure is a 502 on the listing and an
``isError`` tool result on a call, always naming what failed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.params import Depends

from skuld.memory_mcp.gateway import (
    MemoryCredentialRefusedError,
    MemoryGatewayClient,
    MemoryGatewayError,
    UnknownMemoryToolError,
)

logger = logging.getLogger("skuld.broker")

MEMORY_MCP_TOOLS_PATH = "/api/memory-mcp/tools"
BAD_GATEWAY = 502


def register_memory_mcp_routes(
    app: FastAPI, broker_getter: Callable[[], Any], *, dependencies: Sequence[Depends]
) -> None:
    """Mount the memory MCP routes on the broker app."""

    def _gateway() -> MemoryGatewayClient:
        broker = broker_getter()
        if not broker._settings.memory.enabled:
            raise HTTPException(404, "memory is disabled for this session")
        return broker._memory_gateway()

    @app.get(MEMORY_MCP_TOOLS_PATH, dependencies=list(dependencies))
    async def list_memory_mcp_tools() -> dict[str, Any]:
        try:
            return await _gateway().list_tools()
        except MemoryGatewayError as exc:
            logger.error("memory-mcp: listing tools failed: %s", exc)
            raise HTTPException(BAD_GATEWAY, str(exc)) from exc

    @app.post(MEMORY_MCP_TOOLS_PATH + "/{name}", dependencies=list(dependencies))
    async def call_memory_mcp_tool(name: str, request: Request) -> dict[str, Any]:
        gateway = _gateway()
        body = await request.body()
        arguments: Any = {}
        if body.strip():
            try:
                arguments = await request.json()
            except ValueError as exc:
                raise HTTPException(400, "tool arguments must be a JSON object") from exc
        if not isinstance(arguments, dict):
            raise HTTPException(400, "tool arguments must be a JSON object")
        safe_name = name.replace("\n", " ")
        try:
            result = await gateway.call_tool(name, arguments)
        except UnknownMemoryToolError:
            raise HTTPException(404, f"unknown tool: {name}") from None
        except MemoryGatewayError as exc:
            logger.error("memory-mcp: %s failed: %s", safe_name, exc)
            prefix = (
                "Memory credential refused"
                if isinstance(exc, MemoryCredentialRefusedError)
                else "Memory is unavailable"
            )
            return {"content": [{"type": "text", "text": f"{prefix}: {exc}"}], "isError": True}
        logger.info("memory-mcp: %s -> %s", safe_name, "error" if result.get("isError") else "ok")
        return result
