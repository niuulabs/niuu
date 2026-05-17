"""Tests for InMemoryStorageAdapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from volundr.adapters.outbound.k8s_storage import (
    InMemoryStorageAdapter,
)
from volundr.domain.models import StorageQuota


@pytest.fixture
def storage() -> InMemoryStorageAdapter:
    """Create a fresh in-memory storage adapter."""
    return InMemoryStorageAdapter()


class TestProvisionUserStorage:
    """Tests for provision_user_storage."""

    async def test_creates_pvc_with_correct_name(
        self,
        storage: InMemoryStorageAdapter,
    ):
        quota = StorageQuota(home_gb=10)
        pvc = await storage.provision_user_storage(
            "user-1",
            quota,
        )
        assert pvc.name == "volundr-user-user-1-home"

    async def test_idempotent_returns_same_pvc(
        self,
        storage: InMemoryStorageAdapter,
    ):
        quota = StorageQuota(home_gb=10)
        pvc1 = await storage.provision_user_storage(
            "user-1",
            quota,
        )
        pvc2 = await storage.provision_user_storage(
            "user-1",
            quota,
        )
        assert pvc1.name == pvc2.name

    async def test_different_users_get_different_pvcs(
        self,
        storage: InMemoryStorageAdapter,
    ):
        quota = StorageQuota(home_gb=10)
        pvc_a = await storage.provision_user_storage(
            "a",
            quota,
        )
        pvc_b = await storage.provision_user_storage(
            "b",
            quota,
        )
        assert pvc_a.name != pvc_b.name


class TestCreateSessionWorkspace:
    """Tests for create_session_workspace."""

    async def test_creates_pvc(
        self,
        storage: InMemoryStorageAdapter,
    ):
        pvc = await storage.create_session_workspace("s1")
        assert pvc.name == "volundr-session-s1-workspace"

    async def test_stored_in_internal_dict(
        self,
        storage: InMemoryStorageAdapter,
    ):
        await storage.create_session_workspace("s1", user_id="u1", tenant_id="t1")
        assert "s1" in storage._session_workspaces

    async def test_stores_user_and_tenant(
        self,
        storage: InMemoryStorageAdapter,
    ):
        await storage.create_session_workspace(
            "s1",
            user_id="u1",
            tenant_id="t1",
            workspace_gb=100,
        )
        entry = storage._session_workspaces["s1"]
        assert entry.user_id == "u1"
        assert entry.tenant_id == "t1"
        assert entry.size_gb == 100


class TestArchiveSessionWorkspace:
    """Tests for archive_session_workspace."""

    async def test_archive_is_noop(
        self,
        storage: InMemoryStorageAdapter,
    ):
        await storage.create_session_workspace("s1")
        await storage.archive_session_workspace("s1")
        # Workspace still exists (archive is soft delete)
        assert "s1" in storage._session_workspaces

    async def test_archive_nonexistent_is_noop(
        self,
        storage: InMemoryStorageAdapter,
    ):
        await storage.archive_session_workspace("ghost")


class TestDeleteWorkspace:
    """Tests for delete_workspace."""

    async def test_removes_workspace(
        self,
        storage: InMemoryStorageAdapter,
    ):
        await storage.create_session_workspace("s1")
        await storage.delete_workspace("s1")
        assert "s1" not in storage._session_workspaces

    async def test_delete_nonexistent_is_noop(
        self,
        storage: InMemoryStorageAdapter,
    ):
        await storage.delete_workspace("ghost")


class TestGetUserStorageUsage:
    """Tests for get_user_storage_usage."""

    async def test_returns_zero_for_no_workspaces(
        self,
        storage: InMemoryStorageAdapter,
    ):
        usage = await storage.get_user_storage_usage("u1")
        assert usage == 0

    async def test_sums_workspace_sizes(
        self,
        storage: InMemoryStorageAdapter,
    ):
        await storage.create_session_workspace(
            "s1",
            user_id="u1",
            workspace_gb=50,
        )
        await storage.create_session_workspace(
            "s2",
            user_id="u1",
            workspace_gb=30,
        )
        await storage.create_session_workspace(
            "s3",
            user_id="u2",
            workspace_gb=100,
        )
        usage = await storage.get_user_storage_usage("u1")
        assert usage == 80


class TestDeprovisionUserStorage:
    """Tests for deprovision_user_storage."""

    async def test_removes_pvc(
        self,
        storage: InMemoryStorageAdapter,
    ):
        quota = StorageQuota(home_gb=10)
        await storage.provision_user_storage("u1", quota)
        await storage.deprovision_user_storage("u1")
        assert "u1" not in storage._user_pvcs

    async def test_deprovision_nonexistent_is_noop(
        self,
        storage: InMemoryStorageAdapter,
    ):
        await storage.deprovision_user_storage("ghost")


class TestResolveSessionWorkspacePath:
    """Tests for resolve_session_workspace_path."""

    def test_prefers_nested_workspace_path_when_present(self, tmp_path: Path) -> None:
        storage = InMemoryStorageAdapter(workspace_mount_path=str(tmp_path))
        nested = tmp_path / "sess-1" / "workspace"
        nested.mkdir(parents=True)

        assert storage.resolve_session_workspace_path("sess-1") == str(nested)

    def test_falls_back_to_direct_workspace_path_when_present(self, tmp_path: Path) -> None:
        storage = InMemoryStorageAdapter(workspace_mount_path=str(tmp_path))
        direct = tmp_path / "sess-2"
        direct.mkdir(parents=True)

        assert storage.resolve_session_workspace_path("sess-2") == str(direct)
