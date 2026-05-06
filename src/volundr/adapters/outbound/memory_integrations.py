"""In-memory IntegrationRepository adapter.

Used for development and testing when no database is available.
"""

from __future__ import annotations

import logging

from volundr.domain.models import IntegrationConnection, IntegrationType
from volundr.domain.ports import IntegrationRepository

logger = logging.getLogger(__name__)


class InMemoryIntegrationRepository(IntegrationRepository):
    """In-memory implementation of IntegrationRepository."""

    def __init__(self) -> None:
        self._store: dict[str, IntegrationConnection] = {}

    async def list_connections(
        self,
        owner_id: str,
        integration_type: IntegrationType | None = None,
    ) -> list[IntegrationConnection]:
        """List connections for a user, optionally filtered by type."""
        results = [c for c in self._store.values() if c.owner_id == owner_id]
        if integration_type is not None:
            results = [c for c in results if c.integration_type == integration_type]
        return sorted(results, key=lambda c: c.created_at, reverse=True)

    async def list_connections_global(
        self,
        integration_type: IntegrationType | None = None,
        *,
        slug: str | None = None,
        enabled_only: bool = False,
    ) -> list[IntegrationConnection]:
        """List connections across all owners with optional filters."""
        results = list(self._store.values())
        if integration_type is not None:
            results = [c for c in results if c.integration_type == integration_type]
        if slug is not None:
            results = [c for c in results if c.slug == slug]
        if enabled_only:
            results = [c for c in results if c.enabled]
        return sorted(results, key=lambda c: (c.owner_id, c.created_at), reverse=True)

    async def get_connection(self, connection_id: str) -> IntegrationConnection | None:
        """Get a single connection by ID."""
        return self._store.get(connection_id)

    async def save_connection(
        self,
        connection: IntegrationConnection,
    ) -> IntegrationConnection:
        """Create or update a connection."""
        self._store[connection.id] = connection
        return connection

    async def delete_connection(self, connection_id: str) -> None:
        """Delete a connection by ID."""
        self._store.pop(connection_id, None)
