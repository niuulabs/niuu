"""Guild discovery and routing preserve the target identity and caller authorization."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_knowledge_deployments import create_knowledge_deployments_router
from niuu.domain.models import InstanceKind

HEADERS = {
    "x-auth-user-id": "user-a",
    "x-auth-roles": base64.b64encode(b'["admin"]').decode(),
    "authorization": "Bearer test-token",
}


def instance(name, base):
    return SimpleNamespace(id=name, name=name, base_url=base, enabled=True, kind=InstanceKind.MIMIR)


def setup(instances):
    service = AsyncMock()
    service.list_visible.return_value = instances
    service.get_visible.side_effect = lambda principal, key: next(
        (i for i in instances if i.id == key), None
    )
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(create_knowledge_deployments_router(service))
    return TestClient(app), service


def target(cluster, **extra):
    return {
        "cluster": cluster,
        "namespace": "knowledge",
        "target": "cluster",
        "backends": ["gbrain", "mimir"],
        "releases": [{"name": "brain", "backend": "gbrain", "ready": True, "message": "Ready"}],
        **extra,
    }


@respx.mock
def test_discovers_each_visible_guild_service_and_preserves_duplicate_names():
    client, service = setup(
        [
            instance("ymir", "https://ymir.test/api/v1"),
            instance("noatun", "https://noatun.test/api/v1/mimir"),
            instance("offline", "https://offline.test"),
        ]
    )
    respx.get("https://ymir.test/api/v1/mimir/deployments").respond(200, json=target("ymir"))
    respx.get("https://noatun.test/api/v1/mimir/deployments").respond(
        200, json={"targets": [{**target("noatun"), "id": "remote"}]}
    )
    respx.get("https://offline.test/api/v1/mimir/deployments").respond(
        501, json={"detail": "No target configured"}
    )
    result = client.get("/knowledge/deployments", headers=HEADERS)
    assert result.status_code == 200
    body = result.json()
    assert [r["target"] for r in body["releases"]] == ["ymir:cluster", "noatun:remote"]
    assert body["targets"][2]["error"] == "offline: No target configured"
    assert body["targets"][2]["backends"] == []
    assert service.list_visible.call_args.kwargs == {
        "kind": InstanceKind.MIMIR,
        "enabled_only": True,
    }
    assert respx.calls[0].request.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_routes_creation_inspection_and_control_to_selected_guild_service():
    client, _ = setup([instance("noatun", "https://noatun.test/api/v1")])
    create = respx.post("https://noatun.test/api/v1/mimir/deployments").respond(
        202, json={"name": "brain"}
    )
    inspect = respx.get(
        "https://noatun.test/api/v1/mimir/deployments/brain", params={"target": "remote"}
    ).respond(200, json={"logs": {"server": "listening"}})
    update = respx.post(
        "https://noatun.test/api/v1/mimir/deployments/brain/update", params={"target": "remote"}
    ).respond(200, json={"ready": False})
    result = client.post(
        "/knowledge/deployments",
        headers=HEADERS,
        json={
            "name": "brain",
            "backend": "gbrain",
            "target": "noatun:remote",
            "dream": {"enabled": True},
        },
    )
    assert result.status_code == 202
    assert json.loads(create.calls[0].request.content) == {
        "name": "brain",
        "backend": "gbrain",
        "target": "remote",
        "dream": {"enabled": True},
    }
    assert (
        client.get("/knowledge/deployments/brain?target=noatun:remote", headers=HEADERS).json()[
            "logs"
        ]["server"]
        == "listening"
    )
    assert (
        client.post(
            "/knowledge/deployments/brain/update?target=noatun:remote", headers=HEADERS
        ).status_code
        == 200
    )
    assert inspect.called and update.called


@pytest.mark.parametrize(
    "headers",
    [{}, {"x-auth-user-id": "user-a", "x-auth-roles": "developer"}, {"x-auth-roles": "admin"}],
)
def test_requires_real_admin_identity(headers):
    client, service = setup([])
    assert client.get("/knowledge/deployments", headers=headers).status_code == 403
    assert client.post("/knowledge/deployments", headers=headers, json={}).status_code == 403
    service.list_visible.assert_not_called()


@respx.mock
def test_unavailable_targets_cannot_be_used_and_remote_errors_are_preserved():
    hidden = instance("disabled", "https://disabled.test")
    hidden.enabled = False
    wrong = instance("forge", "https://forge.test")
    wrong.kind = InstanceKind.VOLUNDR
    client, _ = setup([hidden, wrong, instance("ymir", "https://ymir.test")])
    for key in ("disabled:cluster", "forge:cluster", "invisible:cluster"):
        assert (
            client.post("/knowledge/deployments", headers=HEADERS, json={"target": key}).status_code
            == 404
        )
    for body in ({}, {"target": []}, {"target": "https://evil.test"}):
        assert client.post("/knowledge/deployments", headers=HEADERS, json=body).status_code in (
            404,
            422,
        )
    assert not respx.calls
    respx.post("https://ymir.test/api/v1/mimir/deployments").respond(
        409, json={"detail": "Already exists"}
    )
    result = client.post("/knowledge/deployments", headers=HEADERS, json={"target": "ymir:cluster"})
    assert result.status_code == 409
    assert result.json()["detail"] == "ymir: Already exists"


@respx.mock
def test_bad_remote_response_is_reported_without_hiding_healthy_targets():
    client, _ = setup([instance("broken", "https://broken.test")])
    respx.get("https://broken.test/api/v1/mimir/deployments").respond(200, text="not json")
    body = client.get("/knowledge/deployments", headers=HEADERS).json()
    assert body["targets"][0]["error"] == "broken: invalid deployment response"
