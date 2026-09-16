"""Integration contributor — resolves integrations into MCP servers and secret manifest."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from niuu.domain.oauth_credentials import OAUTH_ENGINE, mcp_token_env, mcp_token_path
from volundr.domain.models import Session
from volundr.domain.ports import (
    CredentialStorePort,
    SessionContext,
    SessionContribution,
    SessionContributor,
)

if TYPE_CHECKING:
    from volundr.domain.services.integration_registry import IntegrationRegistry

logger = logging.getLogger(__name__)


class IntegrationContributor(SessionContributor):
    """Resolves user-selected integration connections into MCP server configs
    and a secret mapping manifest.

    The manifest tells the entrypoint which credential files to read and
    which env vars / file symlinks to create. Volundr never sees secret
    values in production — the CSI driver mounts credential files and
    the entrypoint sources them.

    MCP integrations produce entries in ``mcpServers`` with empty ``env``
    dicts (MCP processes inherit env vars sourced by the entrypoint).
    """

    def __init__(
        self,
        *,
        integration_registry: IntegrationRegistry | None = None,
        credential_store: CredentialStorePort | None = None,
        **_extra: object,
    ):
        self._registry = integration_registry
        self._credential_store = credential_store

    @property
    def name(self) -> str:
        return "integrations"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if not context.integration_connections:
            return SessionContribution()

        if self._registry is None:
            return SessionContribution()

        active = context.integration_connections
        manifest: dict[str, Any] = {"env": {}, "files": {}}
        mcp_servers: list[dict[str, Any]] = []
        env_vars: list[dict[str, str]] = []

        for conn in active:
            defn = self._registry.get_definition(conn.slug)
            if defn is None:
                continue

            if defn.credential_enrollment and defn.credential_enrollment.method == "claude_setup":
                env_vars.append({"name": "SKULD__CLAUDE_AUTH", "value": "subscription"})

            # Build manifest entries from definition's env_from_credentials
            for env_var, cred_key in defn.env_from_credentials.items():
                manifest["env"][env_var] = {
                    "file": conn.credential_name,
                    "key": cred_key,
                }

            # Non-secret settings the session needs, straight from the connection
            # config (the Model server's gateway URL). A connection without them
            # cannot run the session, so say so instead of launching without.
            for env_var, config_key in defn.env_from_config.items():
                value = conn.config.get(config_key) if isinstance(conn.config, dict) else None
                if value is None or value == "":
                    raise ValueError(
                        f"Connection '{conn.credential_name}' ({conn.slug}) has no "
                        f"'{config_key}' in its config, which {env_var} needs. Reconnect the "
                        "provider from Settings → Integrations; a Model server is registered "
                        "from Settings → Runtime."
                    )
                env_vars.append({"name": env_var, "value": str(value)})

            # MCP server integration
            if defn.mcp_server is not None:
                spec = defn.mcp_server
                # MCP env mappings go into the manifest too
                for env_var, cred_key in spec.env_from_credentials.items():
                    manifest["env"][env_var] = {
                        "file": conn.credential_name,
                        "key": cred_key,
                    }
                if spec.transport == "stdio":
                    mcp_servers.append(
                        {
                            "name": spec.name,
                            "type": "stdio",
                            "command": spec.command,
                            "args": list(spec.args),
                            "env": {},
                            "env_vars": list(spec.env_from_credentials),
                        }
                    )
                else:
                    server = {"name": spec.name, "type": spec.transport, "url": spec.url}
                    if spec.token_field:
                        if context.runtime_backend == "openshell":
                            server["credential_env"] = mcp_token_env(conn.id)
                        else:
                            server["credential_file"] = mcp_token_path(conn.id)
                        server["auth_header"] = spec.auth_header
                        server["auth_prefix"] = spec.auth_prefix
                        if self._credential_store and context.runtime_backend != "openshell":
                            stored = await self._credential_store.get(
                                "user", session.owner_id, conn.credential_name
                            )
                            if stored and stored.metadata.get("renewal_owner") == OAUTH_ENGINE:
                                server["credential_format"] = "oauth"
                    mcp_servers.append(server)

            # File mounts (e.g., Claude OAuth credentials)
            for target_path in defn.file_mounts:
                manifest["files"][target_path] = {
                    "file": conn.credential_name,
                }

        # The Claude transports default to the subscription login and strip
        # API-key variables from the spawn environment; a session whose only
        # Claude credential is an API key must say so or it starts with none.
        subscription = any(var["name"] == "SKULD__CLAUDE_AUTH" for var in env_vars)
        if not subscription and "ANTHROPIC_API_KEY" in manifest["env"]:
            env_vars.append({"name": "SKULD__CLAUDE_AUTH", "value": "api_key"})

        values: dict[str, Any] = {}
        if env_vars:
            values["envVars"] = env_vars
        if mcp_servers:
            values["mcpServers"] = mcp_servers

        has_manifest = manifest["env"] or manifest["files"]
        if has_manifest:
            values["secretManifest"] = manifest

        if not values:
            return SessionContribution()

        return SessionContribution(values=values)
