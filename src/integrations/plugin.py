"""IntegrationsPlugin — registers integration routes as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _IntegrationsStub(Service):
    """Stub service while integrations remains co-hosted in the niuu API."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class IntegrationsPlugin(ServicePlugin):
    """Plugin for integration connection and OAuth routes."""

    @property
    def name(self) -> str:
        return "integrations"

    @property
    def description(self) -> str:
        return "Integration connection and OAuth routes"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="integrations",
            description="Integration connection and OAuth service",
            factory=lambda: _IntegrationsStub(),
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8086,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from integrations.app import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="integrations-api",
                prefixes=("/api/v1/integrations",),
                description="Canonical integrations and OAuth routes.",
            ),
        )
