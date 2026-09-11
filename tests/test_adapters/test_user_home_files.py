from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kubernetes_asyncio.client.exceptions import ApiException

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_user_storage import create_user_storage_router
from volundr.adapters.outbound.k8s_home_files import browser_manifest, browser_pod_name, manage_home
from volundr.adapters.outbound.k8s_storage_adapter import K8sStorageAdapter
from volundr.adapters.outbound.local_storage_adapter import LocalStorageAdapter
from volundr.adapters.outbound.user_home_files import home_operation
from volundr.domain.models import Principal, StorageQuota
from volundr.domain.ports import HomeStorageBusyError


def test_files_stay_inside_owner_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("private")
    (home / "escape").symlink_to(tmp_path, target_is_directory=True)
    (home / "tmp" / "cache").mkdir(parents=True)
    (home / "tmp" / "cache" / "build").write_text("cache")
    listing = home_operation(str(home), "list", "")
    assert listing["capacity_bytes"] > 0
    assert {e["name"]: e["kind"] for e in listing["entries"]} == {
        "escape": "symlink",
        "tmp": "directory",
    }
    for path in ["../secret", "/secret", "tmp/../../secret", "\0"]:
        with pytest.raises(ValueError):
            home_operation(str(home), "delete", path)
    with pytest.raises(ValueError):
        home_operation(str(home), "delete", "")
    with pytest.raises(OSError):
        home_operation(str(home), "list", "escape")
    with pytest.raises(OSError):
        home_operation(str(home), "delete", "escape/secret")
    home_operation(str(home), "delete", "escape")
    home_operation(str(home), "delete", "tmp/cache")
    assert outside.read_text() == "private"
    assert home_operation(str(home), "list", "tmp")["entries"] == []


async def test_local_adapter_uses_authenticated_owner(tmp_path):
    adapter = LocalStorageAdapter(base_dir=str(tmp_path))
    await adapter.provision_user_storage("alice", StorageQuota())
    await adapter.provision_user_storage("bob", StorageQuota())
    (tmp_path / "home" / "bob" / "secret").write_text("private")
    assert (await adapter.manage_user_home("alice", "list"))["entries"] == []
    with pytest.raises(ValueError):
        await adapter.manage_user_home("../bob", "list")


def pod(phase="Running", owner="alice", claim="home", name=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name or browser_pod_name("alice"), labels={"volundr/owner": owner}, uid="uid"
        ),
        status=SimpleNamespace(phase=phase),
        spec=SimpleNamespace(
            volumes=[SimpleNamespace(persistent_volume_claim=SimpleNamespace(claim_name=claim))]
        ),
    )


async def test_browser_lifecycle_and_busy_delete(monkeypatch):
    api = AsyncMock()
    args = (api, "skuld", "alice", "home", "image", 600, 20)
    api.read_namespaced_pod.side_effect = ApiException(status=404)
    assert (await manage_home(*args, "list", ""))["status"] == "starting"
    manifest = api.create_namespaced_pod.call_args.args[1]
    assert manifest["spec"]["volumes"] == [
        {"name": "home", "persistentVolumeClaim": {"claimName": "home"}}
    ]
    assert manifest["spec"]["automountServiceAccountToken"] is False
    assert manifest["spec"]["securityContext"]["runAsNonRoot"] is True
    api.read_namespaced_pod.side_effect = None
    api.read_namespaced_pod.return_value = pod("Pending")
    assert (await manage_home(*args, "list", ""))["status"] == "starting"
    api.read_namespaced_pod.return_value = pod("Failed")
    assert (await manage_home(*args, "list", ""))["status"] == "starting"
    api.delete_namespaced_pod.assert_awaited_once()
    api.read_namespaced_pod.return_value = pod()
    execute = AsyncMock(return_value={"status": "ready", "entries": []})
    monkeypatch.setattr("volundr.adapters.outbound.k8s_home_files._execute", execute)
    assert (await manage_home(*args, "list", "tmp"))["status"] == "ready"
    execute.assert_awaited_once_with("skuld", browser_pod_name("alice"), "list", "tmp", 20)
    api.list_namespaced_pod.return_value = SimpleNamespace(items=[pod(name="coder")])
    with pytest.raises(HomeStorageBusyError):
        await manage_home(*args, "delete", "tmp")
    api.list_namespaced_pod.return_value = SimpleNamespace(items=[pod()])
    assert (await manage_home(*args, "delete", "tmp"))["status"] == "ready"
    for bad in [pod(owner="bob"), pod(claim="other")]:
        api.read_namespaced_pod.return_value = bad
        with pytest.raises(PermissionError):
            await manage_home(*args, "list", "")
    assert browser_pod_name("alice") != browser_pod_name("bob")
    assert browser_manifest("bob", "b", "image", 123)["spec"]["activeDeadlineSeconds"] == 123


async def test_k8s_owner_and_missing_home():
    adapter = K8sStorageAdapter(file_browser_image="image")
    adapter._api_client = api = AsyncMock()
    api.read_namespaced_persistent_volume_claim.side_effect = ApiException(status=404)
    with pytest.raises(FileNotFoundError):
        await adapter.manage_user_home("alice", "list")
    api.read_namespaced_persistent_volume_claim.side_effect = None
    api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(labels={"volundr/owner": "bob"})
    )
    with pytest.raises(PermissionError):
        await adapter.manage_user_home("alice", "list")
    with pytest.raises(ValueError):
        K8sStorageAdapter(file_browser_timeout_seconds=0)


@pytest.mark.parametrize(
    "error,code",
    [
        (FileNotFoundError("missing"), 404),
        (PermissionError("owner"), 403),
        (ValueError("path"), 400),
        (HomeStorageBusyError("busy"), 409),
        (NotImplementedError("unsupported"), 501),
        (TimeoutError(), 503),
    ],
)
def test_route_errors(error, code):
    storage = AsyncMock()
    storage.manage_user_home.side_effect = error
    app = FastAPI()
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="alice", tenant_id="tenant", email="alice@example.test", roles=[]
    )
    app.include_router(create_user_storage_router(storage))
    response = TestClient(app).get(
        "/api/v1/forge/storage/home", headers={"x-auth-user-id": "alice"}
    )
    assert response.status_code == code
    storage.manage_user_home.assert_awaited_once_with("alice", "list", "")


def test_route_delete_never_reports_preparation_as_success():
    storage = AsyncMock()
    storage.manage_user_home.return_value = {"status": "starting"}
    app = FastAPI()
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="alice", tenant_id="tenant", email="alice@example.test", roles=[]
    )
    app.include_router(create_user_storage_router(storage))
    client = TestClient(app)
    assert (
        client.delete(
            "/api/v1/forge/storage/home?path=tmp", headers={"x-auth-user-id": "alice"}
        ).status_code
        == 409
    )
    storage.manage_user_home.assert_awaited_once_with("alice", "delete", "tmp")
    storage.manage_user_home.return_value = {"status": "ready", "deleted": "tmp"}
    assert (
        client.delete(
            "/api/v1/forge/storage/home?path=tmp", headers={"x-auth-user-id": "alice"}
        ).status_code
        == 200
    )


async def test_k8s_browser_expands_existing_home_and_deprovisions_helper(monkeypatch):
    adapter = K8sStorageAdapter(file_browser_image="image", home_size_gb=64)
    api = adapter._api_client = AsyncMock()
    api.read_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        metadata=SimpleNamespace(labels={"volundr/owner": "alice"}),
        spec=SimpleNamespace(resources=SimpleNamespace(requests={"storage": "1Gi"})),
    )
    provision = AsyncMock()
    monkeypatch.setattr(adapter, "provision_user_storage", provision)
    manage = AsyncMock(return_value={"status": "ready"})
    monkeypatch.setattr("volundr.adapters.outbound.k8s_home_files.manage_home", manage)
    assert await adapter.manage_user_home("alice", "list") == {"status": "ready"}
    provision.assert_awaited_once_with("alice", StorageQuota(home_gb=64))
    api.read_namespaced_pod.return_value = pod()
    await adapter.deprovision_user_storage("alice")
    api.delete_namespaced_pod.assert_awaited_once()
    api.delete_namespaced_persistent_volume_claim.assert_awaited_once()
