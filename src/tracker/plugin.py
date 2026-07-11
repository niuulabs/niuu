"""TrackerPlugin — registers tracker routes as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin


class TrackerPlugin(ServicePlugin):
    """Plugin for tracker issue, status, and mapping routes."""

    @property
    def name(self) -> str:
        return "tracker"

    @property
    def description(self) -> str:
        return "Tracker issue, status, and repo mapping routes"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="tracker",
            description="Tracker issue, status, and repo mapping service",
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8087,
        )

    def create_api_app(self) -> Any:
        from tracker.app import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="tracker-api",
                prefixes=(
                    "/api/v1/tracker/status",
                    "/api/v1/tracker/issues",
                    "/api/v1/tracker/repo-mappings",
                ),
                description="Canonical tracker issue, status, and repo mapping routes.",
            ),
        )
