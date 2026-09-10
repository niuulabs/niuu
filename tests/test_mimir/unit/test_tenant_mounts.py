from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.flux import FluxKnowledgeDeploymentAdapter
from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.ports.deployment import DeploymentRequest, KnowledgeDeploymentPort
from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from mimir.router import MimirRouter
from ravn.adapters.mimir.http import HttpMimirAdapter


def identity(tenant):
    return {"x-auth-user-id": "user", "x-auth-tenant": tenant, "x-auth-roles": "volundr:admin"}


class TenantDeployments(KnowledgeDeploymentPort):
    def __init__(self, roots):
        self.roots = roots
        self.deploy = AsyncMock(return_value={"ready": False})

    async def deploy(self, request):
        raise AssertionError("Replaced by the instance mock")

    async def list_deployments(self, *, tenant_id=""):
        return {"releases": []}

    async def discover_mounts(self, tenant_id, authorization=""):
        if tenant_id not in self.roots:
            return []
        return [
            {
                "name": "brain",
                "kind": "remote",
                "role": "shared",
                "categories": None,
                "priority": 10,
                "port": self.roots[tenant_id],
            }
        ]


def test_mount_discovery_reads_writes_and_credentials_are_tenant_scoped(tmp_path):
    roots = {t: MarkdownMimirAdapter(root=tmp_path / t) for t in ("a", "b")}
    deployment = TenantDeployments(roots)
    app = FastAPI()
    app.include_router(
        MimirRouter(
            MarkdownMimirAdapter(root=tmp_path / "shared"),
            deployment=deployment,
            public_url="https://mimir.test/api/v1",
        ).router
    )
    with TestClient(app) as client:
        assert [m["name"] for m in client.get("/mounts").json()] == ["local"]
        for tenant in ("a", "b"):
            assert (
                client.put(
                    "/page",
                    headers=identity(tenant),
                    json={
                        "path": "wiki/note.md",
                        "content": f"# Tenant {tenant}",
                        "mount": "brain",
                    },
                ).status_code
                == 204
            )
        for tenant in ("a", "b"):
            response = client.get(
                "/page", headers=identity(tenant), params={"path": "wiki/note.md", "mount": "brain"}
            )
            assert response.status_code == 200
            assert f"Tenant {tenant}" in response.json()["content"]
            registry = client.get("/registry/mounts", headers=identity(tenant)).json()
            mount = next(m for m in registry if m["name"] == "brain")
            assert mount["kind"] == "remote"
            assert mount["kwargs"] == {"base_url": "https://mimir.test/api/v1", "mount": "brain"}
            assert mount["auth_ref"] == "workload:mimir"
            assert not mount["secret_kwargs_env"]
            assert (
                client.get(
                    "/graph", headers=identity(tenant), params={"mount": "brain"}
                ).status_code
                == 200
            )
        assert (
            client.get(
                "/page",
                headers=identity("other"),
                params={"path": "wiki/note.md", "mount": "brain"},
            ).status_code
            == 404
        )
        client.post(
            "/deployments",
            headers=identity("a"),
            json={"name": "new", "backend": "mimir", "tenant_id": "b"},
        )
        assert deployment.deploy.call_args.args[0].tenant_id == "a"


def test_registry_cannot_read_edit_or_delete_another_tenant_connection(tmp_path):
    store = MimirRegistryStore(tmp_path / "registry.json")
    entry = store.save_entry(MimirRegistryEntry(name="private", tenant_id="a", enabled=False))
    app = FastAPI()
    app.include_router(
        MimirRouter(MarkdownMimirAdapter(root=tmp_path / "wiki"), registry_store=store).router
    )
    with TestClient(app) as client:
        assert client.get("/registry/mounts", headers=identity("b")).json() == []
        assert (
            client.put(
                f"/registry/mounts/{entry.id}", headers=identity("b"), json={"name": "stolen"}
            ).status_code
            == 404
        )
        client.delete(f"/registry/mounts/{entry.id}", headers=identity("b"))
        assert store.get_entry(entry.id).name == "private"


@pytest.mark.asyncio
async def test_flux_filters_ownership_before_loading_services_or_secrets():
    a = FluxKnowledgeDeploymentAdapter(
        namespace="knowledge", source_name="niuu", chart_versions={}, images={}
    )
    foreign = {
        "metadata": {
            "name": "foreign",
            "annotations": {"niuu.world/tenant-id": "b"},
            "labels": {"niuu.world/knowledge-backend": "gbrain"},
        }
    }
    api = AsyncMock()
    api.list_namespaced_custom_object.return_value = {"items": [foreign]}
    a._get_api = AsyncMock(return_value=api)
    with patch("kubernetes_asyncio.client.CoreV1Api") as core:
        assert await a.discover_mounts("a") == []
        core.return_value.read_namespaced_secret.assert_not_called()
    with pytest.raises(ValueError, match="not found"):
        await a.control("foreign", "stop", tenant_id="a")
    api.get_namespaced_custom_object.assert_not_called()
    with pytest.raises(ValueError, match="authenticated tenant"):
        await a.deploy(DeploymentRequest(name="brain", backend="mimir"))
    assert a._release_name("brain", "a") != a._release_name("brain", "b")


@pytest.mark.asyncio
async def test_http_mount_binding_applies_to_reads_and_writes():
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(200, json={})

    adapter = HttpMimirAdapter("https://mimir.test/api/v1", mount="my-brain")
    adapter._client = httpx.AsyncClient(
        base_url=adapter._base_url, transport=httpx.MockTransport(respond)
    )
    try:
        await adapter._request("GET", "/mimir/pages")
        await adapter._request(
            "PUT", "/mimir/page", json={"mount": "wrong", "path": "a", "content": "b"}
        )
    finally:
        await adapter.aclose()
    assert all(r.url.params["mount"] == "my-brain" for r in received)
    assert b'"mount":"my-brain"' in received[1].content


@pytest.mark.asyncio
async def test_ready_gbrain_is_discovered_with_its_own_native_secret():
    import base64
    from types import SimpleNamespace

    adapter = FluxKnowledgeDeploymentAdapter(
        namespace="knowledge", source_name="niuu", chart_versions={}, images={}
    )
    adapter.list_deployments = AsyncMock(
        return_value={
            "releases": [
                {
                    "name": "brain",
                    "release_name": "brain-tenant",
                    "backend": "gbrain",
                    "ready": True,
                }
            ]
        }
    )
    service = SimpleNamespace(
        metadata=SimpleNamespace(
            name="brain-tenant-gbrain", labels={"app.kubernetes.io/name": "gbrain"}
        ),
        spec=SimpleNamespace(ports=[SimpleNamespace(port=3131)]),
    )
    core = AsyncMock()
    core.list_namespaced_service.return_value = SimpleNamespace(items=[service])
    core.read_namespaced_secret.return_value = SimpleNamespace(
        data={"token": base64.b64encode(b"native-secret").decode()}
    )
    with patch("kubernetes_asyncio.client.CoreV1Api", return_value=core):
        mounts = await adapter.discover_mounts("a")
    assert mounts[0]["name"] == "brain"
    assert mounts[0]["tenant_id"] == "a"
    assert mounts[0]["port"]._api_token == "native-secret"
    assert "connection" not in mounts[0]
    core.read_namespaced_secret.assert_awaited_once_with(
        "brain-tenant-gbrain-connection", "knowledge"
    )


def test_session_proxy_mount_keeps_workload_identity():
    from ravn.cli.runtime_builders import _build_mimir
    from ravn.config import Settings

    settings = Settings(
        mimir={
            "enabled": True,
            "instances": [
                {
                    "name": "brain",
                    "adapter": "ravn.adapters.mimir.http.HttpMimirAdapter",
                    "kwargs": {"base_url": "https://mimir.test/api/v1", "mount": "brain"},
                    "auth": {
                        "type": "workload",
                        "token_file": "/run/identity/token",
                        "audiences": ["mimir"],
                    },
                }
            ],
        }
    )
    port = _build_mimir(settings)._mounts[0].port
    assert port._mount == "brain"
    assert port._auth.type == "workload"
    assert port._auth.token_file == "/run/identity/token"


def test_standalone_instance_enforces_tenant_on_rest_and_mcp(tmp_path):
    from mimir.app import create_app
    from mimir.config import MimirServiceConfig

    app = create_app(MimirServiceConfig(path=str(tmp_path), tenant_id="a"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/mimir/pages", headers=identity("a")).status_code == 200
        assert client.get("/mimir/pages", headers=identity("b")).status_code == 403
        assert client.post("/mcp", headers=identity("b"), json={}).status_code == 403
        assert client.post("/api/v1/mimir/mcp", json={}).status_code == 403


@pytest.mark.parametrize(
    "settings",
    [
        {"kind": "local", "path": "/data/other-tenant"},
        {
            "adapter": "ravn.adapters.mimir.gbrain.GBrainMimirAdapter",
            "kwargs": {"api_token_file": "/run/secrets/token"},
        },
        {"secret_kwargs_env": {"token": "OTHER_TENANT_TOKEN"}},
    ],
)
def test_tenant_registry_cannot_import_adapters_or_read_host_secrets(tmp_path, settings):
    app = FastAPI()
    app.include_router(
        MimirRouter(
            MarkdownMimirAdapter(root=tmp_path / "wiki"), registry_store=MimirRegistryStore()
        ).router
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/registry/mounts", headers=identity("a"), json={"name": "unsafe", **settings}
            ).status_code
            == 422
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["mimir", "gbrain"])
async def test_operator_global_mounts_are_shared_but_not_tenant_managed(backend):
    import base64
    from types import SimpleNamespace

    adapter = FluxKnowledgeDeploymentAdapter(
        namespace="knowledge",
        source_name="niuu",
        chart_versions={},
        images={},
        global_instances=["global-brain"],
    )
    release = {
        "metadata": {
            "name": "global-brain",
            "generation": 1,
            "annotations": {"niuu.world/scope": "global"},
            "labels": {"niuu.world/knowledge-backend": backend},
        },
        "status": {"observedGeneration": 1, "conditions": [{"type": "Ready", "status": "True"}]},
    }
    api = AsyncMock()
    api.list_namespaced_custom_object.return_value = {"items": [release]}
    api.get_namespaced_custom_object.return_value = release
    adapter._get_api = AsyncMock(return_value=api)
    core = AsyncMock()
    core.list_namespaced_service.return_value = SimpleNamespace(
        items=[
            SimpleNamespace(
                metadata=SimpleNamespace(
                    name="global-brain", labels={"app.kubernetes.io/name": backend}
                ),
                spec=SimpleNamespace(ports=[SimpleNamespace(port=80)]),
            )
        ]
    )
    core.read_namespaced_secret.return_value = SimpleNamespace(
        data={"token": base64.b64encode(b"native-global-token").decode()}
    )
    with patch("kubernetes_asyncio.client.CoreV1Api", return_value=core):
        for tenant in ("a", "b"):
            mounts = await adapter.discover_mounts(tenant, "Bearer caller-token")
            assert [m["name"] for m in mounts] == ["global-brain"]
            assert (await adapter.list_deployments(tenant_id=tenant))["releases"] == []
            with pytest.raises(ValueError, match="not found"):
                await adapter.control("global-brain", "stop", tenant_id=tenant)
            if backend == "mimir":
                await mounts[0]["port"].aclose()
        with pytest.raises(ValueError, match="reserved"):
            await adapter.deploy(
                DeploymentRequest(name="global-brain", backend=backend, tenant_id="a")
            )
        release["metadata"]["annotations"]["niuu.world/tenant-id"] = "a"
        with pytest.raises(ValueError, match="not explicitly global"):
            await adapter.discover_mounts("b", "Bearer caller-token")
        release["metadata"]["annotations"] = {}
        with pytest.raises(ValueError, match="not explicitly global"):
            await adapter.discover_mounts("b", "Bearer caller-token")
    api.patch_namespaced_custom_object.assert_not_called()
