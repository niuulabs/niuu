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
            depends_on=["postgres"],
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self, *, base_url: str | None = None) -> Any:
        from niuu.adapters.outbound.http_auth import NoAuthHeaderAdapter
        from niuu.service_settings import Settings
        from niuu.utils import import_class, resolve_secret_kwargs
        from observatory.app import create_app
        from observatory.discovery import ObservatoryDiscoveryService

        settings = Settings()
        guild_cfg = settings.observatory.guild
        auth_cls = import_class(guild_cfg.auth.adapter)
        auth_kwargs = resolve_secret_kwargs(
            guild_cfg.auth.kwargs,
            guild_cfg.auth.secret_kwargs_env,
        )
        auth = auth_cls(**auth_kwargs) if guild_cfg.auth.adapter else NoAuthHeaderAdapter()
        discovery_service = ObservatoryDiscoveryService(
            guild_url=guild_cfg.url,
            auth=auth,
            timeout_seconds=guild_cfg.timeout_seconds,
        )
        return create_app(discovery_service=discovery_service)

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
