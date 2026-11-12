import base64
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from mimir.adapters.flux import FluxKnowledgeDeploymentAdapter
from mimir.ports.deployment import DeploymentRequest
from mimir.router import MimirRouter
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter


class _InspectableStore(InMemoryMimirAdapter):
    async def inspect_instance(self) -> dict:
        return {"backend": "memory", "metrics": {"Pages": len(self.pages)}, "unavailable": []}


def adapter():
    return FluxKnowledgeDeploymentAdapter(
        namespace="knowledge",
        source_name="niuu",
        chart_versions={"gbrain": "0.1.0"},
        images={"gbrain": "registry/gbrain:0.48.5.0"},
    )


@pytest.mark.asyncio
async def test_gbrain_creates_postgres_flux_release():
    a = adapter()
    api = AsyncMock()

    async def create(*args):
        return args[-1]

    api.create_namespaced_custom_object.side_effect = create
    a._get_api = AsyncMock(return_value=api)
    result = await a.deploy(
        DeploymentRequest(
            tenant_id="tenant-a",
            name="research",
            backend="gbrain",
            secret="brain",
        )
    )
    body = api.create_namespaced_custom_object.call_args.args[-1]
    assert "dream" not in body["spec"]["values"]
    assert body["spec"]["values"]["engine"] == "postgres"
    assert body["spec"]["values"]["existingSecret"] == "brain"
    assert not result["ready"]
    assert body["spec"]["chart"]["spec"]["sourceRef"]["name"] == "niuu"
    await a.deploy(DeploymentRequest(tenant_id="tenant-a", name="new-brain", backend="gbrain"))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert values["postgres"]["enabled"] is True
    assert values["existingSecret"] == ""


def test_deployment_requires_admin_and_inspection_reports_real_counts():
    a = adapter()
    a.list_deployments = AsyncMock(return_value={"releases": []})
    a.deploy = AsyncMock(return_value={"ready": False})
    app = FastAPI()
    app.include_router(
        MimirRouter(
            _InspectableStore(),
            deployment=a,
            auth=EnvoyHeaderAuthenticationAdapter(),
        ).router
    )
    with TestClient(app) as client:
        assert client.get("/deployments").status_code == 403
        assert (
            client.post("/deployments", json={"name": "test", "backend": "gbrain"}).status_code
            == 403
        )
        response = client.get("/instances/inspect")
        assert response.json()[0]["metrics"]["Pages"] == 0
        headers = {
            "x-auth-user-id": "admin",
            "x-auth-roles": "volundr:admin",
            "x-auth-tenant": "tenant-a",
        }
        assert (
            client.post(
                "/deployments", headers=headers, json={"name": "test", "backend": "gbrain"}
            ).status_code
            == 202
        )
        assert client.get("/deployments", headers=headers).status_code == 200


@pytest.mark.asyncio
async def test_status_ignores_stale_ready():
    a = adapter()
    api = AsyncMock()
    obj = {
        "metadata": {
            "name": "notes",
            "generation": 2,
            "annotations": {"niuu.world/tenant-id": "tenant-a"},
            "labels": {"niuu.world/knowledge-backend": "gbrain"},
        },
        "status": {"observedGeneration": 1, "conditions": [{"type": "Ready", "status": "True"}]},
    }
    api.list_namespaced_custom_object.return_value = {"items": [obj]}
    a._get_api = AsyncMock(return_value=api)
    result = await a.list_deployments(tenant_id="tenant-a")
    assert result["releases"][0]["ready"] is False


def test_the_markdown_mimir_backend_is_no_longer_deployable():
    with pytest.raises(ValueError, match="gbrain"):
        DeploymentRequest(tenant_id="tenant-a", name="notes", backend="mimir")


@pytest.mark.asyncio
async def test_only_gbrain_is_listed_as_a_backend():
    api = AsyncMock()
    api.list_namespaced_custom_object.return_value = {"items": []}

    a = adapter()
    a._get_api = AsyncMock(return_value=api)
    assert (await a.list_deployments(tenant_id="tenant-a"))["backends"] == ["gbrain"]

    unconfigured = FluxKnowledgeDeploymentAdapter(
        namespace="knowledge", source_name="niuu", chart_versions={}, images={}
    )
    unconfigured._get_api = AsyncMock(return_value=api)
    assert (await unconfigured.list_deployments(tenant_id="tenant-a"))["backends"] == []


@pytest.mark.asyncio
async def test_deploying_an_unconfigured_backend_is_refused():
    unconfigured = FluxKnowledgeDeploymentAdapter(
        namespace="knowledge", source_name="niuu", chart_versions={}, images={}
    )
    unconfigured._get_api = AsyncMock(return_value=AsyncMock())
    with pytest.raises(ValueError, match="No chart and image configured for gbrain"):
        await unconfigured.deploy(
            DeploymentRequest(tenant_id="tenant-a", name="notes", backend="gbrain")
        )


def test_the_flux_target_no_longer_takes_an_envoy_profile():
    with pytest.raises(TypeError, match="envoy"):
        FluxKnowledgeDeploymentAdapter(
            namespace="knowledge",
            source_name="niuu",
            chart_versions={"gbrain": "0.1.0"},
            images={"gbrain": "registry/gbrain:0.48.5.0"},
            envoy={"enabled": True},
        )


@pytest.mark.asyncio
async def test_remote_inspection_keeps_backend_metrics():
    import httpx

    from mimir.adapters.http import HttpMimirAdapter

    port = HttpMimirAdapter(base_url="http://mimir.test")
    port._request = AsyncMock(
        return_value=httpx.Response(
            200,
            request=httpx.Request("GET", "http://mimir.test/mimir/instances/inspect"),
            json=[
                {"mount": "remote", "backend": "gbrain", "metrics": {"Pages": 6}, "unavailable": []}
            ],
        )
    )
    result = await port.inspect_instance()
    assert result["backend"] == "gbrain"
    assert result["metrics"]["Pages"] == 6
    assert "mount" not in result


@pytest.mark.asyncio
async def test_flux_uses_operator_credentials():
    a = adapter()
    a.secrets = {"gbrain": "brain-{name}"}
    api = AsyncMock()
    api.create_namespaced_custom_object.side_effect = lambda *args: args[-1]
    a._get_api = AsyncMock(return_value=api)
    await a.deploy(DeploymentRequest(tenant_id="tenant-a", name="research", backend="gbrain"))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert values["existingSecret"] == "brain-research"
    assert "warden" not in values


@pytest.mark.asyncio
async def test_gbrain_config_is_applied_to_new_tenant_releases_only():
    a = adapter()
    a.gbrain_config = {
        "provider_base_urls": {
            "deepseek": "http://niuu-bifrost-internal.volundr.svc.cluster.local/api/v1/bifrost/v1"
        },
        "deepseek_api_key": "unused-bifrost-open-mode",
    }
    api = AsyncMock()
    api.create_namespaced_custom_object.side_effect = lambda *args: args[-1]
    a._get_api = AsyncMock(return_value=api)

    await a.deploy(DeploymentRequest(tenant_id="tenant-a", name="research", backend="gbrain"))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert values["config"] == a.gbrain_config
    # Mutating the returned values must not corrupt the adapter's own copy.
    values["config"]["deepseek_api_key"] = "tampered"
    assert a.gbrain_config["deepseek_api_key"] == "unused-bifrost-open-mode"


@pytest.mark.asyncio
async def test_no_gbrain_config_means_no_config_values_key():
    a = adapter()
    api = AsyncMock()
    api.create_namespaced_custom_object.side_effect = lambda *args: args[-1]
    a._get_api = AsyncMock(return_value=api)
    await a.deploy(DeploymentRequest(tenant_id="tenant-a", name="research", backend="gbrain"))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert "config" not in values


@pytest.mark.asyncio
async def test_remote_inspection_reports_older_server_capability():
    import httpx

    from mimir.adapters.http import HttpMimirAdapter

    port = HttpMimirAdapter(base_url="http://mimir.test")
    port._request = AsyncMock(return_value=httpx.Response(404))
    result = await port.inspect_instance()
    assert result["metrics"] == {}
    assert "Upgrade" in result["unavailable"][0]
    port._request = AsyncMock(
        return_value=httpx.Response(503, request=httpx.Request("GET", "http://mimir.test"))
    )
    with pytest.raises(httpx.HTTPStatusError):
        await port.inspect_instance()


@pytest.mark.parametrize(
    "roles,user,expected",
    [
        (["volundr:developer", "volundr:admin"], "admin", 200),
        (["admin"], "admin", 200),
        (["developer"], "user", 403),
        (["admin"], "", 403),
        (["volundr:developer"], "user", 403),
        (["volundr:admin"], "", 403),
    ],
)
def test_deployment_auth_accepts_envoy_array_claims(roles, user, expected):
    a = adapter()
    a.list_deployments = AsyncMock(return_value={"releases": []})
    app = FastAPI()
    app.include_router(
        MimirRouter(
            _InspectableStore(),
            deployment=a,
            auth=EnvoyHeaderAuthenticationAdapter(),
        ).router
    )
    headers = {
        "x-auth-user-id": user,
        "x-auth-tenant": "tenant-a",
        "x-auth-roles": base64.b64encode(json.dumps(roles).encode()).decode(),
    }
    with TestClient(app) as client:
        assert client.get("/deployments", headers=headers).status_code == expected


@pytest.mark.asyncio
async def test_flux_target_supplies_storage_class_to_gbrain():
    a = adapter()
    a.storage_class = "harvester-data"
    api = AsyncMock()
    api.create_namespaced_custom_object.side_effect = lambda *args: args[-1]
    a._get_api = AsyncMock(return_value=api)
    await a.deploy(DeploymentRequest(tenant_id="tenant-a", name="brain", backend="gbrain"))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert values["persistence"]["storageClass"] == "harvester-data"
    assert values["niuu"] == {
        "cluster": a.cluster,
        "instanceId": "brain",
        "tenantId": "tenant-a",
    }
    assert values["postgres"]["storageClass"] == "harvester-data"


@pytest.mark.asyncio
async def test_update_uses_target_release_versions_without_replacing_instance_settings():
    a = adapter()
    obj = {
        "metadata": {
            "name": "brain",
            "annotations": {"niuu.world/tenant-id": "tenant-a"},
            "labels": {"niuu.world/managed-by": "mimir", "niuu.world/knowledge-backend": "gbrain"},
        }
    }
    api = AsyncMock()
    api.get_namespaced_custom_object.return_value = obj
    api.list_namespaced_custom_object.return_value = {"items": [obj]}
    api.patch_namespaced_custom_object.return_value = obj
    a._get_api = AsyncMock(return_value=api)
    await a.control("cluster/brain", "update", tenant_id="tenant-a")
    patch = api.patch_namespaced_custom_object.call_args.kwargs["body"]["spec"]
    assert (
        api.patch_namespaced_custom_object.call_args.kwargs["_content_type"]
        == "application/merge-patch+json"
    )
    assert patch["chart"]["spec"]["version"] == "0.1.0"
    assert patch["values"] == {
        "connection": {"enabled": True},
        "image": {"repository": "registry/gbrain", "tag": "0.48.5.0"},
        "niuu": {"cluster": a.cluster, "instanceId": "brain", "tenantId": "tenant-a"},
    }
    obj["metadata"]["labels"]["niuu.world/managed-by"] = "other"
    with pytest.raises(ValueError, match="not managed"):
        await a.control("brain", "update", tenant_id="tenant-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("action,replicas", [("start", 1), ("stop", 0)])
async def test_lifecycle_actions_use_shared_merge_patch(action, replicas):
    a = adapter()
    obj = {
        "metadata": {
            "name": "brain",
            "annotations": {"niuu.world/tenant-id": "tenant-a"},
            "labels": {"niuu.world/managed-by": "mimir", "niuu.world/knowledge-backend": "gbrain"},
        }
    }
    api = AsyncMock()
    api.get_namespaced_custom_object.return_value = obj
    api.list_namespaced_custom_object.return_value = {"items": [obj]}
    api.patch_namespaced_custom_object.return_value = obj
    a._get_api = AsyncMock(return_value=api)
    await a.control("brain", action, tenant_id="tenant-a")
    call = api.patch_namespaced_custom_object.call_args.kwargs
    assert call["_content_type"] == "application/merge-patch+json"
    assert call["body"]["spec"]["values"] == {"replicaCount": replicas}
