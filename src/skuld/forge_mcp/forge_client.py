"""``ForgeClient`` over the broker's Forge HTTP client.

The broker already holds an authenticated ``httpx.AsyncClient`` for Volundr
(``EventLogMixin._get_http_client``). MCP-proxied calls reuse it, with one
difference: when a scoped session token is configured (``forge_mcp.token``,
``SKULD__FORGE_MCP__TOKEN``) it replaces the broker's own ``Authorization`` for
these calls only, so the model's reach is bounded by that token's scopes.

The REST mapping itself is shared with Forge's HTTP MCP endpoint
(:class:`niuu.forge_mcp.rest_client.RestForgeClient`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from niuu.forge_mcp.rest_client import FORGE_API_PREFIX, RestForgeClient

__all__ = ["FORGE_API_PREFIX", "HttpForgeClient"]


class HttpForgeClient(RestForgeClient):
    """Forge REST through the broker's HTTP client, as the broker's own session."""

    def __init__(
        self,
        *,
        http_client: Callable[[], Awaitable[httpx.AsyncClient]],
        token: str,
        timeout_s: float,
    ) -> None:
        super().__init__(
            http_client=http_client,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout_s=timeout_s,
        )
