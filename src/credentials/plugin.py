"""CredentialsPlugin — registers credentials routes as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin


class CredentialsPlugin(ServicePlugin):
    """Plugin for credential and secret surfaces."""

    @property
    def name(self) -> str:
        return "credentials"

    @property
    def description(self) -> str:
        return "Credential, secret, and MCP metadata routes"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="credentials",
            description="Credential, secret, and MCP metadata service",
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8085,
        )

    def create_api_app(self) -> Any:
        from credentials.app import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="credentials-api",
                prefixes=("/api/v1/credentials",),
                description="Canonical credential and secret-type routes.",
            ),
        )
