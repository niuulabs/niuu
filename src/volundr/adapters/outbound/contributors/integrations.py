"""Integration contributor — resolves integrations into MCP servers and secret manifest."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from volundr.domain.models import Session
from volundr.domain.ports import (
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
        **_extra: object,
    ):
        self._registry = integration_registry

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

            # MCP server integration
            if defn.mcp_server is not None:
                spec = defn.mcp_server
                # MCP env mappings go into the manifest too
                for env_var, cred_key in spec.env_from_credentials.items():
                    manifest["env"][env_var] = {
                        "file": conn.credential_name,
                        "key": cred_key,
                    }
                # MCP server config with empty env — sourced by entrypoint
                mcp_servers.append(
                    {
                        "name": spec.name,
                        "type": "stdio",
                        "command": spec.command,
                        "args": list(spec.args),
                        "env": {},
                    }
                )

            # File mounts (e.g., Claude OAuth credentials)
            for target_path in defn.file_mounts:
                manifest["files"][target_path] = {
                    "file": conn.credential_name,
                }

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
