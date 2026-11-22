"""Person and project connections, each from the store that holds them."""

from __future__ import annotations

from niuu.domain.models import (
    IntegrationConnection,
    IntegrationType,
    is_project_owner_id,
)
from niuu.ports.integrations import IntegrationRepository


class RoutedIntegrationRepository(IntegrationRepository):
    """A host whose people's connections are another service's, with its own team store.

    The shared integrations API answers for the caller alone, so it cannot hold a
    connection a project owns. Those live in *teams*, beside the project; everything
    else is *personal*'s.
    """

    def __init__(self, personal: IntegrationRepository, teams: IntegrationRepository) -> None:
        self._personal = personal
        self._teams = teams

    async def close(self) -> None:
        close = getattr(self._personal, "close", None)
        if close is not None:
            await close()

    async def list_connections(
        self, owner_id: str, integration_type: IntegrationType | None = None
    ) -> list[IntegrationConnection]:
        store = self._teams if is_project_owner_id(owner_id) else self._personal
        return await store.list_connections(owner_id, integration_type)

    async def list_connections_global(
        self,
        integration_type: IntegrationType | None = None,
        *,
        slug: str | None = None,
        enabled_only: bool = False,
    ) -> list[IntegrationConnection]:
        return await self._personal.list_connections_global(
            integration_type, slug=slug, enabled_only=enabled_only
        )

    async def get_connection(self, connection_id: str) -> IntegrationConnection | None:
        team = await self._team_connection(connection_id)
        if team is not None:
            return team
        return await self._personal.get_connection(connection_id)

    async def save_connection(self, connection: IntegrationConnection) -> IntegrationConnection:
        store = self._teams if connection.project_id else self._personal
        return await store.save_connection(connection)

    async def delete_connection(self, connection_id: str) -> None:
        if await self._team_connection(connection_id) is not None:
            await self._teams.delete_connection(connection_id)
            return
        await self._personal.delete_connection(connection_id)

    async def _team_connection(self, connection_id: str) -> IntegrationConnection | None:
        """The connection, when a project owns it: this store may also hold people's rows
        from before their connections moved to the shared API."""
        found = await self._teams.get_connection(connection_id)
        return found if found is not None and found.project_id else None
