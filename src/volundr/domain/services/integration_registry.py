"""Integration registry — catalog of known integration definitions.

The registry holds ``IntegrationDefinition`` objects loaded from
configuration YAML.  It supports lookup by slug or by type and
provides a factory method to build MCP server env-var mappings for
session injection.
"""

from __future__ import annotations

import logging

from volundr.domain.models import (
    CredentialEnrollmentSpec,
    IntegrationConnection,
    IntegrationDefinition,
    IntegrationType,
    MCPServerSpec,
    OAuthSpec,
)

logger = logging.getLogger(__name__)


class IntegrationRegistry:
    """In-memory catalog of known integration definitions.

    Definitions are loaded once at startup from configuration. The
    registry is read-only after construction.
    """

    def __init__(self, definitions: list[IntegrationDefinition] | None = None) -> None:
        self._by_slug: dict[str, IntegrationDefinition] = {}
        for defn in definitions or []:
            self._by_slug[defn.slug] = defn

    # --- read API --------------------------------------------------

    def get_definition(self, slug: str) -> IntegrationDefinition | None:
        """Return a definition by slug, or ``None``."""
        return self._by_slug.get(slug)

    def list_definitions(self) -> list[IntegrationDefinition]:
        """Return all definitions sorted by name."""
        return sorted(self._by_slug.values(), key=lambda d: d.name)

    def get_definitions_by_type(
        self,
        integration_type: IntegrationType,
    ) -> list[IntegrationDefinition]:
        """Return definitions filtered by integration type."""
        return [d for d in self._by_slug.values() if d.integration_type == integration_type]

    # --- MCP helpers -----------------------------------------------

    def mcp_spec(self, connection: IntegrationConnection) -> MCPServerSpec | None:
        """Resolve user-connected MCP endpoints without modifying the shared catalog."""
        if connection.slug == "mcp":
            return MCPServerSpec(
                name=f"mcp-{connection.id}",
                transport="http",
                url=str(connection.config.get("mcp_url", "")),
                token_field="access_token",
                auth_header=str(connection.config.get("auth_header", "Authorization")),
                auth_prefix=str(connection.config.get("auth_prefix", "Bearer ")),
            )
        definition = self.get_definition(connection.slug)
        return definition.mcp_server if definition else None

    def build_mcp_env(
        self,
        connection: IntegrationConnection,
        credentials: dict[str, str],
    ) -> dict[str, str] | None:
        """Build environment variables for the MCP server of a connection.

        Returns ``None`` if the connection's definition has no MCP
        server spec.
        """
        spec = self.mcp_spec(connection)
        if spec is None:
            return None

        env: dict[str, str] = {}
        for env_var, cred_field in spec.env_from_credentials.items():
            value = credentials.get(cred_field, "")
            if not value:
                value = connection.config.get(cred_field, "")
            env[env_var] = value
        return env

    def build_mcp_server_config(
        self,
        connection: IntegrationConnection,
        credentials: dict[str, str],
    ) -> dict | None:
        """Build a complete MCP server config dict for session task_args.

        Returns ``None`` if the connection's definition has no MCP
        server spec.
        """
        spec = self.mcp_spec(connection)
        if spec is None:
            return None

        env = self.build_mcp_env(connection, credentials)
        if spec.transport != "stdio":
            config = {"name": spec.name, "type": spec.transport, "url": spec.url}
            if spec.token_field:
                token = credentials.get(spec.token_field)
                if not token:
                    raise ValueError("MCP credential missing; reconnect the integration")
                config["headers"] = {spec.auth_header: spec.auth_prefix + token}
            return config
        return {
            "name": spec.name,
            "type": "stdio",
            "command": spec.command,
            "args": list(spec.args),
            "env": env or {},
        }


def definitions_from_config(
    raw_definitions: list[dict],
) -> list[IntegrationDefinition]:
    """Parse raw config dicts into ``IntegrationDefinition`` objects."""
    result: list[IntegrationDefinition] = []
    for item in raw_definitions:
        mcp_raw = item.get("mcp_server")
        mcp_spec: MCPServerSpec | None = None
        if mcp_raw and isinstance(mcp_raw, dict):
            mcp_spec = MCPServerSpec(
                name=mcp_raw["name"],
                command=mcp_raw.get("command", ""),
                transport=mcp_raw.get("transport", "stdio"),
                url=mcp_raw.get("url", ""),
                token_field=mcp_raw.get("token_field", ""),
                auth_header=mcp_raw.get("auth_header", "Authorization"),
                auth_prefix=mcp_raw.get("auth_prefix", "Bearer "),
                args=tuple(mcp_raw.get("args", [])),
                env_from_credentials=mcp_raw.get("env_from_credentials", {}),
            )

        oauth_raw = item.get("oauth")
        oauth_spec: OAuthSpec | None = None
        if oauth_raw and isinstance(oauth_raw, dict):
            oauth_spec = OAuthSpec(
                authorize_url=oauth_raw["authorize_url"],
                token_url=oauth_raw["token_url"],
                revoke_url=oauth_raw.get("revoke_url", ""),
                scopes=tuple(oauth_raw.get("scopes", [])),
                token_field_mapping=oauth_raw.get("token_field_mapping", {}),
                extra_authorize_params=oauth_raw.get("extra_authorize_params", {}),
                extra_token_params=oauth_raw.get("extra_token_params", {}),
                device_authorization_url=oauth_raw.get("device_authorization_url", ""),
                token_request_format=oauth_raw.get("token_request_format", "form"),
                client_secret_required=oauth_raw.get("client_secret_required", False),
            )

        enrollment_raw = item.get("credential_enrollment")
        enrollment_spec: CredentialEnrollmentSpec | None = None
        if enrollment_raw and isinstance(enrollment_raw, dict):
            enrollment_spec = CredentialEnrollmentSpec(
                method=enrollment_raw["method"],
                credential_field=enrollment_raw["credential_field"],
                default_credential_name=enrollment_raw["default_credential_name"],
            )

        defn = IntegrationDefinition(
            slug=item["slug"],
            name=item["name"],
            description=item.get("description", ""),
            integration_type=IntegrationType(item["integration_type"]),
            adapter=item.get("adapter", ""),
            icon=item.get("icon", ""),
            credential_schema=item.get("credential_schema", {}),
            config_schema=item.get("config_schema", {}),
            mcp_server=mcp_spec,
            env_from_credentials=item.get("env_from_credentials", {}),
            env_from_config=item.get("env_from_config", {}),
            auth_type=item.get("auth_type", "api_key"),
            oauth=oauth_spec,
            file_mounts=item.get("file_mounts", {}),
            credential_enrollment=enrollment_spec,
            model_vendor=item.get("model_vendor", ""),
            key_probe=item.get("key_probe") or {},
        )
        result.append(defn)
        logger.debug("Loaded integration definition: %s", defn.slug)

    return result
