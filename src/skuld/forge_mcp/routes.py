"""Broker loopback routes the ``skuld.forge_mcp`` stdio server forwards to.

``GET /api/forge-mcp/tools`` lists the tools; ``POST /api/forge-mcp/tools/{name}``
runs one with the JSON body as its arguments and answers the MCP
``CallToolResult`` shape ``{content, structuredContent?, isError}``. Both require
the broker's loopback secret (see :mod:`skuld.session_runtime`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.params import Depends

from niuu.forge_mcp.tools import UnknownToolError

logger = logging.getLogger("skuld.broker")

FORGE_MCP_TOOLS_PATH = "/api/forge-mcp/tools"


def register_forge_mcp_routes(
    app: FastAPI, broker_getter: Callable[[], Any], *, dependencies: Sequence[Depends]
) -> None:
    """Mount the Forge MCP routes on the broker app."""

    def _toolbox() -> Any:
        broker = broker_getter()
        if not broker._settings.forge_mcp.enabled:
            raise HTTPException(404, "the forge MCP is disabled for this session")
        return broker._forge_mcp_toolbox()

    @app.get(FORGE_MCP_TOOLS_PATH, dependencies=list(dependencies))
    async def list_forge_mcp_tools() -> dict[str, Any]:
        return {"tools": _toolbox().list_tools()}

    @app.post(FORGE_MCP_TOOLS_PATH + "/{name}", dependencies=list(dependencies))
    async def call_forge_mcp_tool(name: str, request: Request) -> dict[str, Any]:
        toolbox = _toolbox()
        body = await request.body()
        arguments: Any = {}
        if body.strip():
            try:
                arguments = await request.json()
            except ValueError as exc:
                raise HTTPException(400, "tool arguments must be a JSON object") from exc
        if not isinstance(arguments, dict):
            raise HTTPException(400, "tool arguments must be a JSON object")
        try:
            result = await toolbox.call(name, arguments)
        except UnknownToolError:
            raise HTTPException(404, f"unknown tool: {name}") from None
        logger.info(
            "forge-mcp: %s -> %s", name.replace("\n", " "), "error" if result.is_error else "ok"
        )
        return result.to_mcp()
