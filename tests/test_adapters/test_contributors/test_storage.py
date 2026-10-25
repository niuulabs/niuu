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

    @pytest.mark.parametrize("runtime_backend", ["kubernetes", "openshell"])
    async def test_provisions_pvc(self, session, runtime_backend):
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.provision_user_storage.return_value = PVCRef(name="home-pvc")
        storage.get_workspace_by_session.return_value = None

        c = StorageContributor(storage=storage)
        result = await c.contribute(session, SessionContext(runtime_backend=runtime_backend))
        assert result.values["homeVolume"]["enabled"] is True
        assert result.values["homeVolume"]["existingClaim"] == "home-pvc"
        assert result.values["persistence"]["existingClaim"] == "ws-pvc"

    async def test_vm_uses_runtime_owned_local_storage(self, session):
        storage = AsyncMock()
        contributor = StorageContributor(storage=storage)

        result = await contributor.contribute(
            session,
            SessionContext(runtime_backend="vm"),
        )

        assert result.values == {}
        storage.get_workspace_by_session.assert_not_awaited()
        storage.create_session_workspace.assert_not_awaited()
        storage.provision_user_storage.assert_not_awaited()

    async def test_legacy_openshell_resume_does_not_replace_storage(self, session):
        storage = AsyncMock()
        storage.get_workspace_by_session.return_value = None
        contributor = StorageContributor(storage=storage)
        result = await contributor.contribute(
            session.with_pod_name("legacy"),
            SessionContext(runtime_backend="openshell"),
        )
        assert result.values == {}
        storage.create_session_workspace.assert_not_awaited()
        storage.provision_user_storage.assert_not_awaited()

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

    async def test_vm_cleanup_is_owned_by_runtime(self, session):
        storage = AsyncMock()
        contributor = StorageContributor(storage=storage)

        await contributor.cleanup(session, SessionContext(runtime_backend="vm"))

        storage.archive_session_workspace.assert_not_awaited()

    async def test_home_disabled_skips_provisioning(self, session):
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.get_workspace_by_session.return_value = None

        c = StorageContributor(storage=storage, home_enabled=False)
        result = await c.contribute(session, SessionContext())
        assert result.values["homeVolume"] == {"enabled": False}
        assert result.values["persistence"]["existingClaim"] == "ws-pvc"
        storage.provision_user_storage.assert_not_called()

    async def test_admin_settings_override(self, session):
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.get_workspace_by_session.return_value = None

        admin_settings = {"storage": {"home_enabled": False}}
        c = StorageContributor(storage=storage, home_enabled=True, admin_settings=admin_settings)
        result = await c.contribute(session, SessionContext())
        assert result.values["homeVolume"] == {"enabled": False}
        storage.provision_user_storage.assert_not_called()

    async def test_cleanup_noop_without_storage(self, session):
        c = StorageContributor()
        await c.cleanup(session, SessionContext())  # Should not raise

    async def test_excluded_workload_types_get_no_home(self, session):
        """Regin's workflow builds ran as the user, mounted the RWO home, and so
        all queued for the one node that held it."""
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.get_workspace_by_session.return_value = None
        contributor = StorageContributor(
            storage=storage, home_excluded_workload_types=["ravn_flock"], home_colocate=True
        )

        result = await contributor.contribute(
            session.model_copy(update={"workload_type": "ravn_flock"}), SessionContext()
        )

        # Explicitly off: valhalla's session defaults enable the home volume,
        # and without a claim every flock pod failed Deployment admission.
        assert result.values["homeVolume"] == {"enabled": False}
        assert "affinity" not in result.values
        assert result.values["persistence"]["existingClaim"] == "ws-pvc"
        storage.provision_user_storage.assert_not_awaited()

    async def test_a_colocated_home_pins_pods_by_claim_not_by_owner(self, session):
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.provision_user_storage.return_value = PVCRef(name="home-pvc")
        storage.get_workspace_by_session.return_value = None
        contributor = StorageContributor(
            storage=storage, home_excluded_workload_types=["ravn_flock"], home_colocate=True
        )

        result = await contributor.contribute(session, SessionContext())

        assert result.values["homeVolume"]["existingClaim"] == "home-pvc"
        assert result.values["podLabels"] == {"volundr/home-claim": "home-pvc"}
        (term,) = result.values["affinity"]["podAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]
        assert term == {
            "topologyKey": "kubernetes.io/hostname",
            "labelSelector": {"matchLabels": {"volundr/home-claim": "home-pvc"}},
        }

    async def test_a_shared_home_is_not_colocated(self, session):
        storage = AsyncMock()
        storage.create_session_workspace.return_value = PVCRef(name="ws-pvc")
        storage.provision_user_storage.return_value = PVCRef(name="home-pvc")
        storage.get_workspace_by_session.return_value = None

        result = await StorageContributor(storage=storage).contribute(session, SessionContext())

        assert "affinity" not in result.values
        assert "podLabels" not in result.values
