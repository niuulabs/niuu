"""CredentialsPlugin — registers credentials routes as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _CredentialsStub(Service):
    """Stub service while credentials remains co-hosted in the niuu API."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class CredentialsPlugin(ServicePlugin):
    """Plugin for credential and secret surfaces."""

    @property
    def name(self) -> str:
        return "credentials"

    @property
    def description(self) -> str:
        return "Credential, secret, and MCP metadata routes"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="credentials",
            description="Credential, secret, and MCP metadata service",
            factory=lambda: _CredentialsStub(),
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8085,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

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
