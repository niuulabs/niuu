"""Tests for K8sStorageAdapter."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from volundr.adapters.outbound.k8s_storage_adapter import K8sStorageAdapter
from volundr.domain.models import StorageQuota


@pytest.fixture(autouse=True)
def _install_k8s_mock(monkeypatch):
    """Install a mock kubernetes_asyncio module tree into sys.modules."""
    k8s = ModuleType("kubernetes_asyncio")
    k8s_client = ModuleType("kubernetes_asyncio.client")
    k8s_config = ModuleType("kubernetes_asyncio.config")

    # Minimal client model stubs — just store kwargs as attributes
    def _make_model(name: str):
        def init(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        return type(name, (), {"__init__": init})

    k8s_client.V1PersistentVolumeClaim = _make_model("V1PersistentVolumeClaim")
    k8s_client.V1ObjectMeta = _make_model("V1ObjectMeta")
    k8s_client.V1PersistentVolumeClaimSpec = _make_model("V1PersistentVolumeClaimSpec")
    k8s_client.V1VolumeResourceRequirements = _make_model("V1VolumeResourceRequirements")
    k8s_client.CoreV1Api = MagicMock

    k8s_config.load_incluster_config = MagicMock(
        side_effect=Exception("not in cluster"),
    )
    k8s_config.ConfigException = Exception
    k8s_config.load_kube_config = AsyncMock()

    k8s.client = k8s_client
    k8s.config = k8s_config

    monkeypatch.setitem(sys.modules, "kubernetes_asyncio", k8s)
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio.client", k8s_client)
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio.config", k8s_config)

    return k8s_client


@pytest.fixture
def mock_core_api() -> AsyncMock:
    """Create a mock CoreV1Api."""
    return AsyncMock()


@pytest.fixture
def adapter(mock_core_api: AsyncMock) -> K8sStorageAdapter:
    """Create a K8sStorageAdapter with a mocked K8s API."""
    a = K8sStorageAdapter(
        namespace="test-ns",
        home_storage_class="test-home-sc",
        workspace_storage_class="test-workspace-sc",
        home_access_mode="ReadWriteMany",
        workspace_access_mode="ReadWriteOnce",
    )
    # Inject the mock API client directly, bypassing lazy-load
    a._api_client = mock_core_api
    return a


class TestProvisionUserStorage:
    """Tests for provision_user_storage."""

    async def test_creates_pvc_with_correct_name(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.create_namespaced_persistent_volume_claim = AsyncMock()

        quota = StorageQuota(home_gb=10)
        pvc = await adapter.provision_user_storage("user-1", quota)

        assert pvc.name == "volundr-user-user-1-home"
        assert pvc.namespace == "test-ns"
        mock_core_api.create_namespaced_persistent_volume_claim.assert_awaited_once()

    async def test_creates_pvc_with_correct_labels(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.create_namespaced_persistent_volume_claim = AsyncMock()

        quota = StorageQuota(home_gb=15)
        await adapter.provision_user_storage("user-2", quota)

        call_kwargs = mock_core_api.create_namespaced_persistent_volume_claim.call_args
        body = call_kwargs.kwargs["body"]
        assert body.metadata.labels["volundr/owner"] == "user-2"
        assert body.metadata.labels["volundr/pvc-type"] == "home"
        assert body.metadata.labels["app.kubernetes.io/managed-by"] == "volundr"

    async def test_idempotent_returns_existing_on_409(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.create_namespaced_persistent_volume_claim = AsyncMock(
            side_effect=Exception("(409) Reason: AlreadyExists"),
        )

        mock_core_api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
            metadata=SimpleNamespace(labels={"volundr/owner": "user-1"}, resource_version="7"),
            spec=SimpleNamespace(resources=SimpleNamespace(requests={"storage": "10Gi"})),
        )
        quota = StorageQuota(home_gb=10)
        pvc = await adapter.provision_user_storage("user-1", quota)

        assert pvc.name == "volundr-user-user-1-home"
        assert pvc.namespace == "test-ns"

    async def test_raises_on_non_409_error(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.create_namespaced_persistent_volume_claim = AsyncMock(
            side_effect=Exception("(500) Internal Server Error"),
        )

        quota = StorageQuota(home_gb=10)
        with pytest.raises(Exception, match="500"):
            await adapter.provision_user_storage("user-1", quota)


class TestCreateSessionWorkspace:
    """Tests for create_session_workspace."""

    async def test_creates_pvc_with_correct_name(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.create_namespaced_persistent_volume_claim = AsyncMock()

        pvc = await adapter.create_session_workspace("session-abc")

        assert pvc.name == "volundr-session-session-abc-workspace"
        assert pvc.namespace == "test-ns"
        mock_core_api.create_namespaced_persistent_volume_claim.assert_awaited_once()

    async def test_creates_pvc_with_correct_labels(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.create_namespaced_persistent_volume_claim = AsyncMock()

        await adapter.create_session_workspace("s1", workspace_gb=100)

        call_kwargs = mock_core_api.create_namespaced_persistent_volume_claim.call_args
        body = call_kwargs.kwargs["body"]
        assert body.metadata.labels["volundr/session-id"] == "s1"
        assert body.metadata.labels["volundr/pvc-type"] == "workspace"
        assert body.spec.resources.requests["storage"] == "100Gi"

    async def test_creates_pvc_with_user_and_tenant_labels(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.create_namespaced_persistent_volume_claim = AsyncMock()

        await adapter.create_session_workspace(
            "s1",
            user_id="u1",
            tenant_id="t1",
        )

        call_kwargs = mock_core_api.create_namespaced_persistent_volume_claim.call_args
        body = call_kwargs.kwargs["body"]
        assert body.metadata.labels["volundr/owner"] == "u1"
        assert body.metadata.labels["volundr/tenant-id"] == "t1"


class TestArchiveSessionWorkspace:
    """Tests for archive_session_workspace."""

    async def test_archive_is_noop(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        # archive should not call any K8s API
        await adapter.archive_session_workspace("s1")
        mock_core_api.delete_namespaced_persistent_volume_claim.assert_not_awaited()


class TestDeleteWorkspace:
    """Tests for delete_workspace."""

    async def test_deletes_pvc(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.delete_namespaced_persistent_volume_claim = AsyncMock()

        await adapter.delete_workspace("s1")

        mock_core_api.delete_namespaced_persistent_volume_claim.assert_awaited_once_with(
            name="volundr-session-s1-workspace",
            namespace="test-ns",
        )

    async def test_handles_404_gracefully(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.delete_namespaced_persistent_volume_claim = AsyncMock(
            side_effect=Exception("(404) Not Found"),
        )

        # Should not raise
        await adapter.delete_workspace("ghost")

    async def test_raises_on_non_404_error(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.delete_namespaced_persistent_volume_claim = AsyncMock(
            side_effect=Exception("(500) Internal Server Error"),
        )

        with pytest.raises(Exception, match="500"):
            await adapter.delete_workspace("s1")


class TestGetUserStorageUsage:
    """Tests for get_user_storage_usage."""

    async def test_returns_zero(
        self,
        adapter: K8sStorageAdapter,
    ):
        usage = await adapter.get_user_storage_usage("u1")
        assert usage == 0


class TestDeprovisionUserStorage:
    """Tests for deprovision_user_storage."""

    async def test_deletes_pvc(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.delete_namespaced_persistent_volume_claim = AsyncMock()

        await adapter.deprovision_user_storage("user-1")

        mock_core_api.delete_namespaced_persistent_volume_claim.assert_awaited_once_with(
            name="volundr-user-user-1-home",
            namespace="test-ns",
        )

    async def test_handles_404_gracefully(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.delete_namespaced_persistent_volume_claim = AsyncMock(
            side_effect=Exception("(404) NotFound"),
        )

        # Should not raise
        await adapter.deprovision_user_storage("ghost")

    async def test_raises_on_non_404_error(
        self,
        adapter: K8sStorageAdapter,
        mock_core_api: AsyncMock,
    ):
        mock_core_api.delete_namespaced_persistent_volume_claim = AsyncMock(
            side_effect=Exception("(403) Forbidden"),
        )

        with pytest.raises(Exception, match="403"):
            await adapter.deprovision_user_storage("user-1")


@pytest.mark.parametrize(
    "current,expected",
    [("1Gi", "64Gi"), ("1024Mi", "64Gi"), ("64Gi", None), ("1Ti", None), ("1e12", None)],
)
async def test_home_expands_without_shrinking_or_losing_owner(
    adapter, mock_core_api, current, expected
):
    adapter._home_size_gb = 64
    mock_core_api.create_namespaced_persistent_volume_claim.side_effect = Exception(
        "409 AlreadyExists"
    )
    mock_core_api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(labels={"volundr/owner": "user-1"}, resource_version="7"),
        spec=SimpleNamespace(resources=SimpleNamespace(requests={"storage": current})),
    )
    await adapter.provision_user_storage("user-1", StorageQuota())
    if expected is None:
        mock_core_api.patch_namespaced_persistent_volume_claim.assert_not_awaited()
        return
    call = mock_core_api.patch_namespaced_persistent_volume_claim.call_args.kwargs
    assert call["body"] == {
        "metadata": {"resourceVersion": "7"},
        "spec": {"resources": {"requests": {"storage": expected}}},
    }


async def test_home_refuses_another_users_claim(adapter, mock_core_api):
    mock_core_api.create_namespaced_persistent_volume_claim.side_effect = Exception(
        "409 AlreadyExists"
    )
    mock_core_api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(labels={"volundr/owner": "other"}),
    )
    with pytest.raises(ValueError, match="not owned"):
        await adapter.provision_user_storage("user-1", StorageQuota())
    mock_core_api.patch_namespaced_persistent_volume_claim.assert_not_awaited()


async def test_home_expansion_failure_is_not_silently_ignored(adapter, mock_core_api):
    adapter._home_size_gb = 64
    mock_core_api.create_namespaced_persistent_volume_claim.side_effect = Exception(
        "409 AlreadyExists"
    )
    mock_core_api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(labels={"volundr/owner": "user-1"}, resource_version="7"),
        spec=SimpleNamespace(resources=SimpleNamespace(requests={"storage": "1Gi"})),
    )
    mock_core_api.patch_namespaced_persistent_volume_claim.side_effect = RuntimeError(
        "expansion denied"
    )
    with pytest.raises(RuntimeError, match="expansion denied"):
        await adapter.provision_user_storage("user-1", StorageQuota())


async def test_concurrent_session_home_expansion_is_idempotent(adapter, mock_core_api):
    adapter._home_size_gb = 64
    mock_core_api.create_namespaced_persistent_volume_claim.side_effect = Exception(
        "409 AlreadyExists"
    )

    def claim(size):
        return SimpleNamespace(
            metadata=SimpleNamespace(labels={"volundr/owner": "user-1"}, resource_version="7"),
            spec=SimpleNamespace(resources=SimpleNamespace(requests={"storage": size})),
        )

    mock_core_api.read_namespaced_persistent_volume_claim.side_effect = [
        claim("1Gi"),
        claim("64Gi"),
    ]
    mock_core_api.patch_namespaced_persistent_volume_claim.side_effect = RuntimeError(
        "409 Conflict"
    )
    result = await adapter.provision_user_storage("user-1", StorageQuota())
    assert result.name == "volundr-user-user-1-home"
