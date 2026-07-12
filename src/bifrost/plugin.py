"""BifrostPlugin — registers Bifrost as a niuu CLI plugin."""

from __future__ import annotations

import os
from typing import Any

from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin


class BifrostPlugin(ServicePlugin):
    """Plugin for the Bifrost LLM proxy service."""

    @property
    def name(self) -> str:
        return "bifrost"

    @property
    def description(self) -> str:
        return "Anthropic-compatible LLM proxy — streaming passthrough, token tracking"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="bifrost",
            description="Anthropic-compatible LLM proxy",
            default_enabled=True,
            depends_on=[],
            default_port=8082,
        )

    def create_api_app(self) -> Any:
        from bifrost.app import create_app
        from bifrost.config import BifrostConfig

        raw_config = os.environ.get("BIFROST_CONFIG", "").strip()
        config = BifrostConfig.model_validate_json(raw_config) if raw_config else BifrostConfig()
        return create_app(config)

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
