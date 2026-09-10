import base64
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.flux import FluxKnowledgeDeploymentAdapter
from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.ports.deployment import DeploymentRequest
from mimir.router import MimirRouter


def adapter():
    return FluxKnowledgeDeploymentAdapter(
        namespace="knowledge",
        source_name="niuu",
        chart_versions={"gbrain": "0.1.0", "mimir": "1.0.0"},
        images={"gbrain": "registry/gbrain:0.48.5.0", "mimir": "registry/niuu:1.0.0"},
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
            name="research",
            backend="gbrain",
            secret="brain",
            dream={"enabled": True, "schedule": "0 3 * * *", "phases": ["lint"]},
        )
    )
    body = api.create_namespaced_custom_object.call_args.args[-1]
    assert body["spec"]["values"]["dream"] == {
        "enabled": True,
        "schedule": "0 3 * * *",
        "phases": ["lint"],
    }
    assert body["spec"]["values"]["engine"] == "postgres"
    assert body["spec"]["values"]["existingSecret"] == "brain"
    assert not result["ready"]
    assert body["spec"]["chart"]["spec"]["sourceRef"]["name"] == "niuu"
    await a.deploy(DeploymentRequest(name="new-brain", backend="gbrain"))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert values["postgres"]["enabled"] is True
    assert values["existingSecret"] == ""


def test_deployment_requires_admin_and_inspection_reports_real_counts(tmp_path):
    a = adapter()
    a.list_deployments = AsyncMock(return_value={"releases": []})
    a.deploy = AsyncMock(return_value={"ready": False})
    app = FastAPI()
    app.include_router(MimirRouter(MarkdownMimirAdapter(root=tmp_path), deployment=a).router)
    with TestClient(app) as client:
        assert client.get("/deployments").status_code == 403
        assert (
            client.post("/deployments", json={"name": "test", "backend": "mimir"}).status_code
            == 403
        )
        response = client.get("/instances/inspect")
        assert response.json()[0]["metrics"]["Pages"] == 0
        headers = {"x-auth-user-id": "admin", "x-auth-roles": "volundr:admin"}
        assert (
            client.post(
                "/deployments", headers=headers, json={"name": "test", "backend": "mimir"}
            ).status_code
            == 202
        )
        assert client.get("/deployments", headers=headers).status_code == 200


@pytest.mark.asyncio
async def test_status_ignores_stale_ready_and_mimir_keeps_its_storage():
    a = adapter()
    api = AsyncMock()
    obj = {
        "metadata": {
            "name": "notes",
            "generation": 2,
            "labels": {"niuu.world/knowledge-backend": "mimir"},
        },
        "status": {"observedGeneration": 1, "conditions": [{"type": "Ready", "status": "True"}]},
    }
    api.list_namespaced_custom_object.return_value = {"items": [obj]}
    api.create_namespaced_custom_object.side_effect = lambda *args: args[-1]
    a._get_api = AsyncMock(return_value=api)
    result = await a.list_deployments()
    assert result["releases"][0]["ready"] is False
    await a.deploy(DeploymentRequest(name="notes", backend="mimir"))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert values["config"]["name"] == "notes"
    assert "engine" not in values


@pytest.mark.asyncio
async def test_remote_inspection_keeps_backend_metrics():
    import httpx

    from ravn.adapters.mimir.http import HttpMimirAdapter

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
async def test_flux_uses_operator_credentials_and_attaches_warden():
    a = adapter()
    a.secrets = {"gbrain": "brain-{name}"}
    a.warden = {"image": "registry/ravn:1", "config": {"llm": {"model": "test-model"}}}
    api = AsyncMock()
    api.create_namespaced_custom_object.side_effect = lambda *args: args[-1]
    a._get_api = AsyncMock(return_value=api)
    await a.deploy(DeploymentRequest(name="research", backend="gbrain"))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert values["existingSecret"] == "brain-research"
    assert "warden" not in values
    await a.deploy(DeploymentRequest(name="research", backend="mimir", warden=True))
    values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
    assert values["warden"]["enabled"]
    assert values["warden"]["config"]["llm"]["model"] == "test-model"
    assert values["config"]["name"] == "research"


@pytest.mark.parametrize(
    "backend, options",
    [
        ("gbrain", {"warden": True}),
        ("mimir", {"dream": {"enabled": True}}),
    ],
)
def test_maintenance_must_match_backend(backend, options):
    with pytest.raises(ValueError, match="only supported"):
        DeploymentRequest(name="research", backend=backend, **options)


@pytest.mark.asyncio
async def test_remote_inspection_reports_older_server_capability():
    import httpx

    from ravn.adapters.mimir.http import HttpMimirAdapter

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


@pytest.mark.asyncio
async def test_warden_overrides_preserve_target_settings_and_secrets():
    a = adapter()
    a.warden = {
        "image": "registry/ravn:1",
        "spec": {"schedules": {"staleness_trigger_schedule_hours": 12}},
        "config": {"llm": {"model": "old-model"}},
        "envFrom": [{"secretRef": {"name": "provider"}}],
    }
    api = AsyncMock()
    api.create_namespaced_custom_object.side_effect = lambda *args: args[-1]
    a._get_api = AsyncMock(return_value=api)
    await a.deploy(
        DeploymentRequest(
            name="custom",
            backend="mimir",
            warden=True,
            warden_overrides={
                "model": "new-model",
                "persona": "research",
                "dream_cycle_cron_expression": "0 5 * * *",
            },
        )
    )
    w = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]["warden"]
    assert w["spec"]["model"] == "new-model"
    assert w["spec"]["schedules"] == {
        "staleness_trigger_schedule_hours": 12,
        "dream_cycle_cron_expression": "0 5 * * *",
    }
    assert w["config"]["llm"]["model"] == "new-model"
    assert w["envFrom"] == a.warden["envFrom"]
    assert a.warden["config"]["llm"]["model"] == "old-model"
    with pytest.raises(ValueError):
        DeploymentRequest(
            name="custom", backend="mimir", warden=True, warden_overrides={"api_key": "not-allowed"}
        )


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
def test_deployment_auth_accepts_envoy_array_claims(tmp_path, roles, user, expected):
    a = adapter()
    a.list_deployments = AsyncMock(return_value={"releases": []})
    app = FastAPI()
    app.include_router(MimirRouter(MarkdownMimirAdapter(root=tmp_path), deployment=a).router)
    headers = {
        "x-auth-user-id": user,
        "x-auth-roles": base64.b64encode(json.dumps(roles).encode()).decode(),
    }
    with TestClient(app) as client:
        assert client.get("/deployments", headers=headers).status_code == expected


@pytest.mark.asyncio
async def test_flux_target_supplies_storage_class_to_both_engines():
    a = adapter()
    a.storage_class = "harvester-data"
    api = AsyncMock()
    api.create_namespaced_custom_object.side_effect = lambda *args: args[-1]
    a._get_api = AsyncMock(return_value=api)
    for backend in ("mimir", "gbrain"):
        await a.deploy(DeploymentRequest(name="brain", backend=backend))
        values = api.create_namespaced_custom_object.call_args.args[-1]["spec"]["values"]
        assert values["persistence"]["storageClass"] == "harvester-data"
        assert values["niuu"] == {"cluster": a.cluster, "instanceId": "brain"}
        if backend == "gbrain":
            assert values["postgres"]["storageClass"] == "harvester-data"


@pytest.mark.asyncio
async def test_update_uses_target_release_versions_without_replacing_instance_settings():
    a = adapter()
    obj = {
        "metadata": {
            "name": "brain",
            "labels": {"niuu.world/managed-by": "mimir", "niuu.world/knowledge-backend": "gbrain"},
        }
    }
    api = AsyncMock()
    api.get_namespaced_custom_object.return_value = obj
    api.patch_namespaced_custom_object.return_value = obj
    a._get_api = AsyncMock(return_value=api)
    await a.control("cluster/brain", "update")
    patch = api.patch_namespaced_custom_object.call_args.kwargs["body"]["spec"]
    assert (
        api.patch_namespaced_custom_object.call_args.kwargs["_content_type"]
        == "application/merge-patch+json"
    )
    assert patch["chart"]["spec"]["version"] == "0.1.0"
    assert patch["values"] == {
        "image": {"repository": "registry/gbrain", "tag": "0.48.5.0"},
        "niuu": {"cluster": a.cluster, "instanceId": "brain"},
    }
    obj["metadata"]["labels"]["niuu.world/managed-by"] = "other"
    with pytest.raises(ValueError, match="not managed"):
        await a.control("brain", "update")


@pytest.mark.asyncio
@pytest.mark.parametrize("action,replicas", [("start", 1), ("stop", 0)])
async def test_lifecycle_actions_use_shared_merge_patch(action, replicas):
    a = adapter()
    obj = {
        "metadata": {
            "name": "brain",
            "labels": {"niuu.world/managed-by": "mimir", "niuu.world/knowledge-backend": "gbrain"},
        }
    }
    api = AsyncMock()
    api.get_namespaced_custom_object.return_value = obj
    api.patch_namespaced_custom_object.return_value = obj
    a._get_api = AsyncMock(return_value=api)
    await a.control("brain", action)
    call = api.patch_namespaced_custom_object.call_args.kwargs
    assert call["_content_type"] == "application/merge-patch+json"
    assert call["body"]["spec"]["values"] == {
        "replicaCount": replicas,
        "dream": {"suspend": action == "stop"},
    }
