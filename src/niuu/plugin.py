"""NiuuPlugin — registers the remaining shared niuu services as a CLI plugin."""

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
    """Plugin for shared platform services that remain on niuu-shared."""

    @property
    def name(self) -> str:
        return "niuu"

    @property
    def description(self) -> str:
        return "Shared platform services — repos, PATs, identity, features, personas"

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
                name="niuu-repos-api",
                prefixes=("/api/v1/niuu/repos",),
                description="Shared repository catalog routes.",
            ),
            APIRouteDomain(
                name="niuu-shared-api",
                prefixes=(
                    "/api/v1/tokens",
                    "/api/v1/identity",
                    "/api/v1/features",
                    "/api/v1/personas",
                    "/api/v1/ravn/personas",
                ),
                description="Shared identity, PAT, feature, and persona routes.",
            ),
        )

    def create_api_client(self) -> Any:
        from niuu.cli_api_client import CLIAPIClient

        return CLIAPIClient(base_url="http://localhost:8080", service_name="Niuu")
