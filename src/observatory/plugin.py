"""Observatory plugin for the niuu host."""

from __future__ import annotations

from typing import Any

from niuu.cli_api_client import CLIAPIClient
from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin


class ObservatoryPlugin(ServicePlugin):
    """Plugin for the Observatory registry and live topology/event surfaces."""

    @property
    def name(self) -> str:
        return "observatory"

    @property
    def description(self) -> str:
        return "Topology and registry service for the Niuu observability surface"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="observatory",
            description="Observatory registry and live streams",
            default_enabled=True,
            depends_on=["postgres"],
        )

    def create_api_app(self, *, base_url: str | None = None) -> Any:
        from observatory.app import create_app
        from observatory.discovery import ObservatoryDiscoveryService
        from observatory.entity_discovery import build_discovery_adapter
        from volundr.config import Settings

        settings = Settings()
        guild_cfg = settings.observatory.guild
        discovery_service = ObservatoryDiscoveryService(
            guild_url=guild_cfg.url,
            discovery_adapter=build_discovery_adapter(settings.observatory.discovery),
        )
        return create_app(discovery_service=discovery_service)

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="observatory-agents-api",
                prefixes=("/api/v1/observatory/agents",),
                description="Principal-aware A2A Agent Directory routes.",
            ),
            APIRouteDomain(
                name="observatory-registry-api",
                prefixes=("/api/v1/observatory/registry",),
                description="Observatory entity-type registry routes.",
            ),
            APIRouteDomain(
                name="observatory-topology-api",
                prefixes=(
                    "/api/v1/observatory/topology/stream",
                    "/api/v1/observatory/topology",
                ),
                description="Observatory live topology snapshot stream routes.",
            ),
            APIRouteDomain(
                name="observatory-events-api",
                prefixes=(
                    "/api/v1/observatory/events/stream",
                    "/api/v1/observatory/events",
                ),
                description="Observatory live event stream routes.",
            ),
            APIRouteDomain(
                name="observatory-api",
                prefixes=("/api/v1/observatory",),
                description="All currently mounted Observatory routes.",
            ),
        )

    def create_api_client(self) -> Any:
        return CLIAPIClient(base_url="http://localhost:8080", service_name="Observatory")
