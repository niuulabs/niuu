"""TrackerPlugin — registers tracker routes as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _TrackerStub(Service):
    """Stub service while tracker remains co-hosted in the niuu API."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class TrackerPlugin(ServicePlugin):
    """Plugin for tracker issue, status, and mapping routes."""

    @property
    def name(self) -> str:
        return "tracker"

    @property
    def description(self) -> str:
        return "Tracker issue, status, and repo mapping routes"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="tracker",
            description="Tracker issue, status, and repo mapping service",
            factory=lambda: _TrackerStub(),
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8087,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

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
