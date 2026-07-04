"""Unit tests for the shared realm governance REST adapter."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.inbound.rest_realms import create_realms_router
from niuu.domain.models import Principal
from niuu.domain.services.realm import RealmService
from tests.test_niuu.test_realm_service import InMemoryRealmRepository


def _make_principal() -> Principal:
    return Principal(
        user_id="user-1",
        email="test@example.com",
        tenant_id="tenant-1",
        roles=["user"],
    )


def _make_client(*, with_service: bool = True) -> TestClient:
    app = FastAPI()
    if with_service:
        app.state.realm_service = RealmService(InMemoryRealmRepository())

    async def extract_principal() -> Principal:
        return _make_principal()

    app.include_router(create_realms_router(extract_principal))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def test_router_default_prefix() -> None:
    async def extract() -> Principal:
        return _make_principal()

    router = create_realms_router(extract)
    assert router.prefix == "/api/v1/realms"


def test_router_custom_prefix() -> None:
    async def extract() -> Principal:
        return _make_principal()

    router = create_realms_router(extract, prefix="/api/v2/realms")
    assert router.prefix == "/api/v2/realms"


# ---------------------------------------------------------------------------
# Realms
# ---------------------------------------------------------------------------


def test_create_and_get_realm() -> None:
    client = _make_client()

    resp = client.post(
        "/api/v1/realms",
        json={"slug": "forge", "name": "Forge", "autonomy_profile": "autonomous"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "forge"
    assert body["autonomy_profile"] == "autonomous"

    got = client.get("/api/v1/realms/forge")
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


def test_list_realms_empty() -> None:
    client = _make_client()
    resp = client.get("/api/v1/realms")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_realms_returns_created() -> None:
    client = _make_client()
    client.post("/api/v1/realms", json={"slug": "a", "name": "A"})
    client.post("/api/v1/realms", json={"slug": "b", "name": "B"})

    resp = client.get("/api/v1/realms")
    assert resp.status_code == 200
    assert {r["slug"] for r in resp.json()} == {"a", "b"}


def test_get_realm_not_found() -> None:
    client = _make_client()
    resp = client.get("/api/v1/realms/ghost")
    assert resp.status_code == 404


def test_create_realm_rejects_invalid_slug() -> None:
    client = _make_client()
    resp = client.post("/api/v1/realms", json={"slug": "Bad Slug!", "name": "X"})
    assert resp.status_code == 422


def test_service_not_configured_returns_503() -> None:
    client = _make_client(with_service=False)
    resp = client.get("/api/v1/realms")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Trust grants
# ---------------------------------------------------------------------------


@pytest.fixture
def client_with_realm() -> tuple[TestClient, str]:
    client = _make_client()
    client.post("/api/v1/realms", json={"slug": "forge", "name": "Forge"})
    return client, "forge"


def test_create_and_list_trust_grants(client_with_realm) -> None:
    client, slug = client_with_realm

    resp = client.post(
        f"/api/v1/realms/{slug}/trust-grants",
        json={
            "action_class": "build",
            "level": 3,
            "limits": {"workflow": "tool-builder"},
            "granted_by": "admin",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["action_class"] == "build"
    assert body["level"] == 3
    assert body["limits"] == {"workflow": "tool-builder"}

    listed = client.get(f"/api/v1/realms/{slug}/trust-grants")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_trust_grants_unknown_realm_404(client_with_realm) -> None:
    client, _ = client_with_realm
    assert client.get("/api/v1/realms/ghost/trust-grants").status_code == 404
    assert (
        client.post(
            "/api/v1/realms/ghost/trust-grants",
            json={"action_class": "build"},
        ).status_code
        == 404
    )


def test_list_trust_grants_empty(client_with_realm) -> None:
    client, slug = client_with_realm
    resp = client.get(f"/api/v1/realms/{slug}/trust-grants")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_create_and_list_capabilities(client_with_realm) -> None:
    client, slug = client_with_realm

    resp = client.post(
        f"/api/v1/realms/{slug}/capabilities",
        json={"name": "grep-tool", "kind": "tool", "status": "building"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "grep-tool"
    assert body["status"] == "building"

    listed = client.get(f"/api/v1/realms/{slug}/capabilities")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_capability_upsert_by_name(client_with_realm) -> None:
    client, slug = client_with_realm
    client.post(
        f"/api/v1/realms/{slug}/capabilities",
        json={"name": "grep-tool", "kind": "tool", "status": "gap"},
    )
    client.post(
        f"/api/v1/realms/{slug}/capabilities",
        json={"name": "grep-tool", "kind": "tool", "status": "present"},
    )

    listed = client.get(f"/api/v1/realms/{slug}/capabilities")
    assert len(listed.json()) == 1
    assert listed.json()[0]["status"] == "present"


def test_capabilities_unknown_realm_404(client_with_realm) -> None:
    client, _ = client_with_realm
    assert client.get("/api/v1/realms/ghost/capabilities").status_code == 404
    assert (
        client.post(
            "/api/v1/realms/ghost/capabilities",
            json={"name": "x", "kind": "tool"},
        ).status_code
        == 404
    )
