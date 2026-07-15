"""FeaturesPlugin — registers feature routes as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin


class FeaturesPlugin(ServicePlugin):
    """Plugin for feature catalog and preference routes."""

    @property
    def name(self) -> str:
        return "features"

    @property
    def description(self) -> str:
        return "Feature catalog and preference routes"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="features",
            description="Feature catalog and preference service",
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8084,
        )

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
