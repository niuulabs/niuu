"""Observatory plugin for the niuu host."""

from __future__ import annotations

from typing import Any

from niuu.cli_api_client import CLIAPIClient
from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _ObservatoryStub(Service):
    """Stub lifecycle service for the host-mounted Observatory API."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class ObservatoryPlugin(ServicePlugin):
    """Plugin for the Observatory registry and live topology/event surfaces."""

    @property
    def name(self) -> str:
        return "observatory"

    @property
    def description(self) -> str:
        return "Topology and registry service for the Niuu observability surface"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="observatory",
            description="Observatory registry and live streams",
            factory=_ObservatoryStub,
            default_enabled=True,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from observatory.app import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
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
