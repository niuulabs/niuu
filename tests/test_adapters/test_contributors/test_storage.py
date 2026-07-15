"""Tests for StorageContributor."""

from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.contributors.storage import StorageContributor
from volundr.domain.models import GitSource, PVCRef, Session, Workspace, WorkspaceStatus
from volundr.domain.ports import SessionContext


@pytest.fixture
def session():
    return Session(
        name="test",
        model="claude",
        source=GitSource(repo="", branch="main"),
        owner_id="user-1",
        tenant_id="tenant-1",
    )


class TestStorageContributor:
    async def test_name(self):
        c = StorageContributor()
        assert c.name == "storage"

    async def test_no_storage_returns_empty(self, session):
        c = StorageContributor()
        result = await c.contribute(session, SessionContext())
        assert result.values == {}

    async def test_openshell_backend_skips_k8s_storage(self, session):
        storage = AsyncMock()
        c = StorageContributor(storage=storage)
        result = await c.contribute(session, SessionContext(runtime_backend="openshell"))

        assert result.values == {}
        assert result.pod_spec is None
        storage.get_workspace_by_session.assert_not_called()
        storage.create_session_workspace.assert_not_called()
        storage.provision_user_storage.assert_not_called()

    async def test_provisions_pvc(self, session):
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.provision_user_storage.return_value = PVCRef(name="home-pvc")
        storage.get_workspace_by_session.return_value = None

        c = StorageContributor(storage=storage)
        result = await c.contribute(session, SessionContext())
        assert result.values["homeVolume"]["enabled"] is True
        assert result.values["homeVolume"]["existingClaim"] == "home-pvc"
        assert result.values["persistence"]["existingClaim"] == "ws-pvc"

    async def test_reuses_existing_workspace(self, session):
        existing = Workspace(
            id=session.id,
            session_id=session.id,
            user_id="user-1",
            tenant_id="tenant-1",
            pvc_name="existing-ws",
            status=WorkspaceStatus.ACTIVE,
        )
        storage = AsyncMock()
        storage.provision_user_storage.return_value = PVCRef(name="home-pvc")
        storage.get_workspace_by_session.return_value = existing

        c = StorageContributor(storage=storage)
        result = await c.contribute(session, SessionContext())
        assert result.values["persistence"]["existingClaim"] == "existing-ws"
        assert result.values["homeVolume"]["enabled"] is True
        assert result.values["homeVolume"]["existingClaim"] == "home-pvc"
        storage.create_session_workspace.assert_not_called()

    async def test_cleanup_archives_workspace(self, session):
        storage = AsyncMock()
        c = StorageContributor(storage=storage)
        await c.cleanup(session, SessionContext())
        storage.archive_session_workspace.assert_called_once_with(str(session.id))

    async def test_home_disabled_skips_provisioning(self, session):
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.get_workspace_by_session.return_value = None

        c = StorageContributor(storage=storage, home_enabled=False)
        result = await c.contribute(session, SessionContext())
        assert "homeVolume" not in result.values
        assert result.values["persistence"]["existingClaim"] == "ws-pvc"
        storage.provision_user_storage.assert_not_called()

    async def test_admin_settings_override(self, session):
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.get_workspace_by_session.return_value = None

        admin_settings = {"storage": {"home_enabled": False}}
        c = StorageContributor(storage=storage, home_enabled=True, admin_settings=admin_settings)
        result = await c.contribute(session, SessionContext())
        assert "homeVolume" not in result.values
        storage.provision_user_storage.assert_not_called()

    async def test_cleanup_noop_without_storage(self, session):
        c = StorageContributor()
        await c.cleanup(session, SessionContext())  # Should not raise
