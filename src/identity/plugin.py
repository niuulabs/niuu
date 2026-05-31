"""IdentityPlugin — registers identity routes as a niuu host plugin."""

from __future__ import annotations

from typing import Any

from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin


class _IdentityStub(Service):
    """Stub service while identity remains co-hosted in the niuu API."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class IdentityPlugin(ServicePlugin):
    """Plugin for identity, tenancy, and token routes."""

    @property
    def name(self) -> str:
        return "identity"

    @property
    def description(self) -> str:
        return "Identity, tenancy, and token routes"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="identity",
            description="Identity, tenancy, and token service",
            factory=lambda: _IdentityStub(),
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8083,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from identity.app import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="identity-api",
                prefixes=(
                    "/api/v1/identity/me",
                    "/api/v1/identity/settings",
                    "/api/v1/identity/auth/config",
                    "/api/v1/identity/users",
                ),
                description="Canonical identity, profile, user, and auth discovery routes.",
            ),
            APIRouteDomain(
                name="tenancy-api",
                prefixes=("/api/v1/identity/tenants",),
                description="Tenant hierarchy, membership, and tenant reprovisioning routes.",
            ),
            APIRouteDomain(
                name="tokens-api",
                prefixes=("/api/v1/tokens",),
                description="Canonical personal access token routes.",
            ),
        )
