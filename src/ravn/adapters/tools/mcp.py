"""mcp_auth tool — initiate authentication for a named MCP server.

Supports three auth patterns (see ``MCPAuthType``):

* ``api_key``             — read from an env var and attach as a header.
* ``client_credentials``  — OAuth 2.0 client-credentials grant (automated).
* ``device_flow``         — OAuth 2.0 device-authorization grant (user opens
                            URL + enters code in their browser).

Token storage
~~~~~~~~~~~~~
Tokens are persisted via an ``MCPAuthSession`` whose backing store is
configured in ``Settings.mcp_token_store``:

* ``local`` (Pi mode) — encrypted file at ``~/.ravn/mcp_tokens.json``.
* ``openbao`` (infra mode) — OpenBao KV v2 secret.

After a successful auth call, all subsequent calls to the corresponding MCP
server automatically include the ``Authorization`` header (for HTTP/SSE
transports).  Tokens are refreshed transparently when they expire.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort
from ravn.adapters.mcp.auth import MCPAuthSession, MCPAuthType
from ravn.config import MCPServerConfig

if TYPE_CHECKING:
    from ravn.adapters.mcp.manager import MCPManager

logger = logging.getLogger(__name__)

_PERMISSION_MCP_AUTH = "mcp:auth"


class MCPAuthTool(ToolPort):
    """Initiate an authentication flow for a named MCP server.

    On success the acquired token is:

    1. Cached in ``auth_session`` for the lifetime of the agent session.
    2. Persisted to the configured token store (OpenBao or encrypted file).
    3. Injected into the live transport so subsequent MCP tool calls include
       the ``Authorization`` header automatically.

    Args:
        auth_session:  Shared auth session (one per agent lifetime).
        server_configs: Map of server name → ``MCPServerConfig`` built from
                        ``Settings.mcp_servers``.
        manager:       Live ``MCPManager`` — used to inject auth headers into
                       transport after successful auth.  May be None (headers
                       are still cached for future calls).
    """

    def __init__(
        self,
        auth_session: MCPAuthSession,
        server_configs: dict[str, MCPServerConfig],
        manager: MCPManager | None = None,
    ) -> None:
        self._auth_session = auth_session
        self._server_configs = server_configs
        self._manager = manager

    # ------------------------------------------------------------------
    # ToolPort interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "mcp_auth"

    @property
    def description(self) -> str:
        return (
            "Authenticate with a named MCP server so that subsequent calls "
            "include the required credentials automatically. "
            "Supported auth types: 'api_key', 'client_credentials', 'device_flow'. "
            "If auth_type is omitted, the type configured for the server is used. "
            "Tokens are cached for the session and refreshed on expiry."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "Name of the MCP server to authenticate with.",
                },
                "auth_type": {
                    "type": "string",
                    "enum": ["api_key", "client_credentials", "device_flow"],
                    "description": (
                        "Authentication type to use.  Omit to use the server's configured default."
                    ),
                },
            },
            "required": ["server_name"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_MCP_AUTH

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        server_name = input.get("server_name", "").strip()
        if not server_name:
            return ToolResult(
                tool_call_id="",
                content="mcp_auth requires a non-empty 'server_name'.",
                is_error=True,
            )

        cfg = self._server_configs.get(server_name)
        if cfg is None:
            known = ", ".join(sorted(self._server_configs)) or "(none configured)"
            return ToolResult(
                tool_call_id="",
                content=(f"Unknown MCP server {server_name!r}. Configured servers: {known}."),
                is_error=True,
            )

        raw_auth_type = input.get("auth_type") or cfg.auth.auth_type
        if not raw_auth_type:
            return ToolResult(
                tool_call_id="",
                content=(
                    f"No auth type specified for server {server_name!r} and none "
                    "configured in ravn.yaml. Provide 'auth_type' in the call or "
                    "set auth.auth_type in mcp_servers config."
                ),
                is_error=True,
            )

        try:
            auth_type = MCPAuthType(raw_auth_type)
        except ValueError:
            return ToolResult(
                tool_call_id="",
                content=(
                    f"Unknown auth_type {raw_auth_type!r}. "
                    "Valid values: 'api_key', 'client_credentials', 'device_flow'."
                ),
                is_error=True,
            )

        auth_cfg = cfg.auth
        client_secret = self._resolve_secret(auth_cfg.client_secret_env)

        try:
            token, message = await self._auth_session.authenticate(
                server_name=server_name,
                auth_type=auth_type,
                # api_key
                api_key_env=auth_cfg.api_key_env,
                api_key_header=auth_cfg.api_key_header,
                api_key_prefix=auth_cfg.api_key_prefix,
                # oauth
                token_url=auth_cfg.token_url,
                client_id=auth_cfg.client_id,
                client_secret=client_secret,
                scope=auth_cfg.scope,
                audience=auth_cfg.audience,
            )
        except (ValueError, RuntimeError) as exc:
            return ToolResult(
                tool_call_id="",
                content=f"Authentication failed for {server_name!r}: {exc}",
                is_error=True,
            )

        self._inject_headers(server_name, token.as_auth_headers())
        return ToolResult(tool_call_id="", content=message, is_error=False)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_secret(self, env_var: str) -> str:
        """Read a secret value from an environment variable."""
        if not env_var:
            return ""
        return os.environ.get(env_var, "")

    def _inject_headers(self, server_name: str, headers: dict[str, str]) -> None:
        """Push auth headers into the live transport if manager is available."""
        if self._manager is None:
            return
        client = self._manager.get_client(server_name)
        if client is None:
            logger.debug("mcp_auth: no live client for %r — headers cached only", server_name)
            return
        client.set_auth_headers(headers)
        logger.debug("mcp_auth: injected auth headers into transport for %r", server_name)


def build_mcp_auth_tool(
    auth_session: MCPAuthSession,
    server_configs: list[MCPServerConfig],
    manager: MCPManager | None = None,
) -> MCPAuthTool:
    """Construct an ``MCPAuthTool`` from a list of ``MCPServerConfig`` objects.

    Args:
        auth_session:   Shared per-session auth cache.
        server_configs: The ``Settings.mcp_servers`` list.
        manager:        Live ``MCPManager`` for transport injection (optional).

    Returns:
        A configured ``MCPAuthTool`` ready to be registered in the tool
        registry.
    """
    config_map = {cfg.name: cfg for cfg in server_configs}
    return MCPAuthTool(
        auth_session=auth_session,
        server_configs=config_map,
        manager=manager,
    )


def build_token_store(store_cfg: object) -> object:
    """Construct the appropriate token store from ``MCPTokenStoreConfig``.

    Uses dynamic dispatch on ``store_cfg.backend`` to avoid import overhead
    for the unused backend.

    Args:
        store_cfg: An ``MCPTokenStoreConfig`` instance from Settings.

    Returns:
        A concrete token store (``LocalEncryptedTokenStore`` or
        ``OpenBaoTokenStore``).
    """
    from ravn.config import MCPTokenStoreConfig

    cfg: MCPTokenStoreConfig = store_cfg  # type: ignore[assignment]

    if cfg.backend == "openbao":
        from ravn.adapters.mcp.auth import OpenBaoTokenStore

        return OpenBaoTokenStore(
            url=cfg.openbao_url,
            token_env=cfg.openbao_token_env,
            mount=cfg.openbao_mount,
            path_prefix=cfg.openbao_path_prefix,
        )

    from ravn.adapters.mcp.auth import LocalEncryptedTokenStore

    return LocalEncryptedTokenStore(path=cfg.local_path)
