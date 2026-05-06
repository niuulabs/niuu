"""Shared fixtures and stubs for Tyr tests."""

from __future__ import annotations

from niuu.domain.models import IntegrationConnection, IntegrationType
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.integrations import IntegrationRepository


class StubIntegrationRepo(IntegrationRepository):
    """In-memory integration repository for testing."""

    def __init__(self, connections: list[IntegrationConnection] | None = None) -> None:
        self._connections = connections or []

    async def list_connections(
        self,
        user_id: str,
        integration_type: IntegrationType | None = None,
    ) -> list[IntegrationConnection]:
        return [
            c
            for c in self._connections
            if c.owner_id == user_id
            and (integration_type is None or c.integration_type == integration_type)
        ]

    async def get_connection(self, connection_id: str) -> IntegrationConnection | None:
        return next((c for c in self._connections if c.id == connection_id), None)

    async def list_connections_global(
        self,
        integration_type: IntegrationType | None = None,
        *,
        slug: str | None = None,
        enabled_only: bool = False,
    ) -> list[IntegrationConnection]:
        connections = list(self._connections)
        if integration_type is not None:
            connections = [c for c in connections if c.integration_type == integration_type]
        if slug is not None:
            connections = [c for c in connections if c.slug == slug]
        if enabled_only:
            connections = [c for c in connections if c.enabled]
        return connections

    async def save_connection(self, connection: IntegrationConnection) -> IntegrationConnection:
        return connection

    async def delete_connection(self, connection_id: str) -> None:
        pass


class StubCredentialStore(CredentialStorePort):
    """In-memory credential store for testing."""

    def __init__(self, values: dict[str, dict[str, str]] | None = None) -> None:
        self._values = values or {}

    async def get_value(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        return self._values.get(f"{owner_type}:{owner_id}:{name}")

    async def store(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError

    async def get(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError

    async def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError

    async def list(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True
