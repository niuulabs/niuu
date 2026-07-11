"""AuditPlugin — registers the audit domain as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin


class AuditPlugin(ServicePlugin):
    """Plugin for audit query routes."""

    @property
    def name(self) -> str:
        return "audit"

    @property
    def description(self) -> str:
        return "Audit log query surfaces"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="audit",
            description="Audit log query service",
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8082,
        )

    def create_api_app(self) -> Any:
        from audit.app import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="audit-api",
                prefixes=("/api/v1/audit", "/audit"),
                description="Canonical audit log query routes.",
            ),
        )
