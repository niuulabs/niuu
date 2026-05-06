"""BifrostPlugin — registers Bifrost as a niuu CLI plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _BifrostService(Service):
    """Bifrost lifecycle service."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class BifrostPlugin(ServicePlugin):
    """Plugin for the Bifrost LLM proxy service."""

    @property
    def name(self) -> str:
        return "bifrost"

    @property
    def description(self) -> str:
        return "Anthropic-compatible LLM proxy — streaming passthrough, token tracking"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="bifrost",
            description="Anthropic-compatible LLM proxy",
            factory=_BifrostService,
            default_enabled=True,
            depends_on=[],
            default_port=8082,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from bifrost.app import create_app
        from bifrost.config import BifrostConfig

        return create_app(BifrostConfig())

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="llm-api",
                prefixes=(
                    "/api/v1/bifrost/health",
                    "/api/v1/bifrost/admin",
                    "/api/v1/bifrost/v1",
                    "/api/v1/bifrost/api",
                ),
                description="Bifrost gateway, model, cache, and admin routes.",
            ),
            APIRouteDomain(
                name="bifrost-observability-api",
                prefixes=(
                    "/api/v1/bifrost/metrics",
                    "/api/v1/bifrost/healthz",
                    "/api/v1/bifrost/readyz",
                ),
                description="Bifrost metrics and readiness/liveness routes.",
            ),
            APIRouteDomain(
                name="bifrost-api",
                prefixes=("/api/v1/bifrost",),
                description="All currently mounted Bifrost routes.",
            ),
        )
