"""MimirPlugin — registers Mimir as a niuu CLI plugin.

Provides the Mimir knowledge API as a mountable niuu host plugin without
changing the underlying standalone service logic.
"""

from __future__ import annotations

from typing import Any

from mimir.config import MimirServiceConfig
from niuu.cli_api_client import CLIAPIClient
from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _MimirStub(Service):
    """Stub — the Mimir API is hosted by the root server."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class MimirPlugin(ServicePlugin):
    """Plugin for the Mimir knowledge service."""

    @property
    def name(self) -> str:
        return "mimir"

    @property
    def description(self) -> str:
        return "Knowledge service — search, pages, ingest, lint"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="mimir",
            description="Knowledge service",
            factory=lambda: _MimirStub(),
            default_enabled=True,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from mimir.app import create_app

        return create_app(MimirServiceConfig())

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="mimir-api",
                prefixes=(
                    "/api/v1/mimir/mcp",
                    "/api/v1/mimir",
                    "/mcp",
                    "/mimir",
                ),
                description="Mimir knowledge, ingest, lint, graph, and MCP routes.",
            ),
        )

    def create_api_client(self) -> Any:
        return CLIAPIClient(base_url="http://localhost:8080", service_name="Mimir")
