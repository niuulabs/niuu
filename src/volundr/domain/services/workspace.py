"""Domain service for workspace management."""

from __future__ import annotations

import logging

from volundr.domain.models import WorkspaceStatus
from volundr.domain.ports import StoragePort

logger = logging.getLogger(__name__)


class WorkspaceService:
    """Service for managing persistent workspaces.

    Uses StoragePort directly — PVCs are the source of truth.
    """

    def __init__(self, storage: StoragePort) -> None:
        self._storage = storage

    async def list_workspaces(
        self,
        user_id: str,
        status: WorkspaceStatus | None = None,
    ):
        """List workspaces for a user, optionally filtered by status."""
        return await self._storage.list_workspaces(user_id, status)

    async def list_all_workspaces(
        self,
        status: WorkspaceStatus | None = None,
    ):
        """List all workspaces (admin), optionally filtered by status."""
        return await self._storage.list_all_workspaces(status)

    async def delete_workspace_by_session(self, session_id: str) -> bool:
        """Delete a workspace by its session ID."""
        ws = await self._storage.get_workspace_by_session(session_id)
        if ws is None:
            return False
        await self._storage.delete_workspace(session_id)
        logger.info("Deleted workspace for session %s", repr(session_id))
        return True
