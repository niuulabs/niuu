"""PersonasPlugin — registers the persona registry as a niuu CLI plugin."""

from __future__ import annotations

from typing import Any

from niuu.cli_api_client import CLIAPIClient
from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin


class PersonasPlugin(ServicePlugin):
    """Plugin for the canonical persona registry service."""

    @property
    def name(self) -> str:
        return "personas"

    @property
    def description(self) -> str:
        return "Canonical persona registry — CRUD, overrides, and source resolution"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="personas",
            description="Canonical persona registry service",
            default_enabled=True,
            depends_on=["postgres"],
        )

    def create_api_app(self) -> Any:
        from personas.app import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="persona-api",
                prefixes=(
                    "/api/v1/personas",
                    "/api/v1/ravn/personas",
                ),
                description=(
                    "Canonical persona registry routes plus legacy Ravn-scoped compatibility paths."
                ),
            ),
        )

    def create_api_client(self) -> Any:
        return CLIAPIClient(base_url="http://localhost:8080", service_name="Personas")
