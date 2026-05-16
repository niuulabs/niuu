"""In-memory storage adapter for development and testing."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from volundr.domain.models import PVCRef, StorageQuota, Workspace, WorkspaceStatus
from volundr.domain.ports import StoragePort

logger = logging.getLogger(__name__)


@dataclass
class _WorkspaceEntry:
    """Internal tracking for an in-memory workspace."""

    pvc_ref: PVCRef
    user_id: str
    tenant_id: str
    size_gb: int
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    uid: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    name: str | None = None
    source_url: str | None = None
    source_ref: str | None = None


class InMemoryStorageAdapter(StoragePort):
    """In-memory storage adapter for development.

    Tracks PVC references in dictionaries keyed by user/session ID.
    No actual Kubernetes resources are created.
    """

    def __init__(
        self,
        *,
        home_mount_path: str = "/volundr/home",
        workspace_mount_path: str = "/volundr/sessions",
        workspace_size_gb: int = 2,
        **_extra: object,
    ) -> None:
        self._user_pvcs: dict[str, PVCRef] = {}
        self._session_workspaces: dict[str, _WorkspaceEntry] = {}
        self._home_mount_path = home_mount_path
        self._workspace_mount_path = workspace_mount_path
        self._workspace_size_gb = workspace_size_gb

    @property
    def home_mount_path(self) -> str:
        return self._home_mount_path

    @property
    def workspace_mount_path(self) -> str:
        return self._workspace_mount_path

    async def provision_user_storage(
        self,
        user_id: str,
        quota: StorageQuota,
    ) -> PVCRef:
        """Create (or return existing) home PVC for a user."""
        existing = self._user_pvcs.get(user_id)
        if existing:
            return existing

        name = f"volundr-user-{user_id}-home"
        ref = PVCRef(name=name)
        self._user_pvcs[user_id] = ref
        return ref

    async def create_session_workspace(
        self,
        session_id: str,
        user_id: str = "",
        tenant_id: str = "",
        workspace_gb: int | None = None,
        name: str | None = None,
        source_url: str | None = None,
        source_ref: str | None = None,
    ) -> PVCRef:
        """Create a workspace PVC for a session."""
        size = workspace_gb if workspace_gb is not None else self._workspace_size_gb
        pvc_name = f"volundr-session-{session_id}-workspace"
        ref = PVCRef(name=pvc_name)
        self._session_workspaces[session_id] = _WorkspaceEntry(
            pvc_ref=ref,
            user_id=user_id,
            tenant_id=tenant_id,
            size_gb=size,
            name=name,
            source_url=source_url,
            source_ref=source_ref,
        )
        return ref

    async def archive_session_workspace(
        self,
        session_id: str,
    ) -> None:
        """Archive a session's workspace PVC."""
        entry = self._session_workspaces.get(session_id)
        if entry:
            entry.status = WorkspaceStatus.ARCHIVED
        logger.debug("Archived workspace for session %s (in-memory)", session_id)

    async def delete_workspace(
        self,
        session_id: str,
    ) -> None:
        """Permanently delete a session's workspace."""
        self._session_workspaces.pop(session_id, None)

    async def get_user_storage_usage(
        self,
        user_id: str,
    ) -> int:
        """Get total storage in GB currently in use by a user."""
        total = 0
        for entry in self._session_workspaces.values():
            if entry.user_id == user_id:
                total += entry.size_gb
        return total

    async def deprovision_user_storage(
        self,
        user_id: str,
    ) -> None:
        """Delete a user's home PVC."""
        self._user_pvcs.pop(user_id, None)

    def _entry_to_workspace(
        self,
        session_id: str,
        entry: _WorkspaceEntry,
    ) -> Workspace:
        return Workspace(
            id=entry.uid,
            session_id=UUID(session_id),
            user_id=entry.user_id,
            tenant_id=entry.tenant_id,
            pvc_name=entry.pvc_ref.name,
            status=entry.status,
            size_gb=entry.size_gb,
            created_at=entry.created_at,
            name=entry.name,
            source_url=entry.source_url,
            source_ref=entry.source_ref,
        )

    async def list_workspaces(
        self,
        user_id: str,
        status: WorkspaceStatus | None = None,
    ) -> list[Workspace]:
        """List workspace entries for a user."""
        results = []
        for sid, entry in self._session_workspaces.items():
            if entry.user_id != user_id:
                continue
            if status is not None and entry.status != status:
                continue
            results.append(self._entry_to_workspace(sid, entry))
        return sorted(results, key=lambda ws: ws.created_at or datetime.min, reverse=True)

    async def list_all_workspaces(
        self,
        status: WorkspaceStatus | None = None,
    ) -> list[Workspace]:
        """List all workspace entries."""
        results = []
        for sid, entry in self._session_workspaces.items():
            if status is not None and entry.status != status:
                continue
            results.append(self._entry_to_workspace(sid, entry))
        return sorted(results, key=lambda ws: ws.created_at or datetime.min, reverse=True)

    async def get_workspace_by_session(
        self,
        session_id: str,
    ) -> Workspace | None:
        """Get workspace entry by session ID."""
        entry = self._session_workspaces.get(session_id)
        if entry is None:
            return None
        return self._entry_to_workspace(session_id, entry)
