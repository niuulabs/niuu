"""NiuuPlugin — registers the shared niuu services as a CLI plugin.

Provides the shared ``/api/v1/niuu`` API (repos, PATs, integrations)
as an independent FastAPI sub-application, decoupled from Volundr.
"""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _NiuuStub(Service):
    """Stub — the niuu API is hosted by the root server."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class NiuuPlugin(ServicePlugin):
    """Plugin for the Niuu shared services (repos, PATs, integrations)."""

    @property
    def name(self) -> str:
        return "niuu"

    @property
    def description(self) -> str:
        return "Shared platform services — repos, PATs, integrations"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="niuu",
            description="Shared platform services",
            factory=lambda: _NiuuStub(),
            default_enabled=True,
            depends_on=["postgres"],
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from niuu.main import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="niuu-api",
                prefixes=("/api/v1/niuu",),
                description="Shared Niuu API routes.",
            ),
        )

    def create_api_client(self) -> Any:
        from niuu.cli_api_client import CLIAPIClient

        return CLIAPIClient(base_url="http://localhost:8080", service_name="Niuu")
