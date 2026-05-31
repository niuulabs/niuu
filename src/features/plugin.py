"""FeaturesPlugin — registers feature routes as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _FeaturesStub(Service):
    """Stub service while features remains co-hosted in the niuu API."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class FeaturesPlugin(ServicePlugin):
    """Plugin for feature catalog and preference routes."""

    @property
    def name(self) -> str:
        return "features"

    @property
    def description(self) -> str:
        return "Feature catalog and preference routes"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="features",
            description="Feature catalog and preference service",
            factory=_FeaturesStub,
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8084,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from features.app import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="features-api",
                prefixes=("/api/v1/features",),
                description="Canonical feature catalog and preferences routes.",
            ),
        )
