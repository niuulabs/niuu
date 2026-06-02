"""Guild plugin for the local Niuu host."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _GuildStub(Service):
    """Stub lifecycle service for the host-mounted Guild API."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class GuildPlugin(ServicePlugin):
    """Plugin for the Guild registry and aggregate APIs."""

    @property
    def name(self) -> str:
        return "guild"

    @property
    def description(self) -> str:
        return "Shared instance registry, discovery, and aggregation APIs"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="guild",
            description="Shared instance registry and aggregate service",
            factory=_GuildStub,
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8084,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self, *, embedded_forge_app: Any | None = None) -> Any:
        from guild.app import create_app

        return create_app(embedded_forge_app=embedded_forge_app)

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        route_domains = [
            APIRouteDomain(
                name="guild-instances-api",
                prefixes=(
                    "/api/v1/niuu/instances",
                    "/api/v1/niuu/targets",
                    "/api/v1/niuu/observatory",
                ),
                description="Guild registry, target selection, and discovery routes.",
            ),
        ]
        route_domains.extend(
            [
                APIRouteDomain(
                    name="forge-api",
                    prefixes=("/api/v1/forge",),
                    description="Guild-backed aggregate Forge runtime routes.",
                ),
                APIRouteDomain(
                    name="session-api",
                    prefixes=(
                        "/api/v1/forge/sessions",
                        "/api/v1/forge/chronicles",
                    ),
                    description="Guild-backed session, logs, messages, and chronicle routes.",
                ),
            ]
        )
        return tuple(route_domains)

    def create_api_client(self) -> Any:
        from niuu.cli_api_client import CLIAPIClient

        return CLIAPIClient(base_url="http://localhost:8080", service_name="Guild")
