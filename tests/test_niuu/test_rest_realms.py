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


def _make_client(
    *,
    with_service: bool = True,
    principal: Principal | None = None,
) -> TestClient:
    app = FastAPI()
    if with_service:
        app.state.realm_service = RealmService(InMemoryRealmRepository())

    async def extract_principal() -> Principal:
        return principal or _make_principal()

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


def test_delete_realm_removes_it_and_its_grants() -> None:
    client = _make_client()
    client.post("/api/v1/realms", json={"slug": "forge", "name": "Forge"})
    client.post("/api/v1/realms/forge/trust-grants", json={"action_class": "build", "level": 2})

    resp = client.delete("/api/v1/realms/forge")
    assert resp.status_code == 204

    assert client.get("/api/v1/realms/forge").status_code == 404
    assert client.get("/api/v1/realms/forge/trust-grants").json() == []
    assert client.get("/api/v1/realms").json() == []


def test_delete_realm_not_found() -> None:
    client = _make_client()
    resp = client.delete("/api/v1/realms/ghost")
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


def test_list_trust_grants_unknown_realm_returns_empty(client_with_realm) -> None:
    # Listing a sub-collection of an absent parent is [] (200), not a 404 — the
    # dashboard queries this for many environments and only some have a realm.
    client, _ = client_with_realm
    resp = client.get("/api/v1/realms/ghost/trust-grants")
    assert resp.status_code == 200
    assert resp.json() == []


def test_grant_trust_unknown_realm_still_404(client_with_realm) -> None:
    # Creating a grant under an absent realm is a real error and stays a 404.
    client, _ = client_with_realm
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


def test_list_capabilities_unknown_realm_returns_empty(client_with_realm) -> None:
    # Symmetry with trust-grants: listing capabilities of an absent realm is [].
    client, _ = client_with_realm
    resp = client.get("/api/v1/realms/ghost/capabilities")
    assert resp.status_code == 200
    assert resp.json() == []


def test_record_capability_unknown_realm_still_404(client_with_realm) -> None:
    client, _ = client_with_realm
    assert (
        client.post(
            "/api/v1/realms/ghost/capabilities",
            json={"name": "x", "kind": "tool"},
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# PUT /{realm_id} — cross-instance realm sync (Guild routing a resident
# create to a remote Völundr; see _sync_realm_to_instance in rest_volundr.py)
# ---------------------------------------------------------------------------


def test_upsert_realm_by_id_creates_it_with_the_given_id() -> None:
    client = _make_client()
    realm_id = "22222222-2222-2222-2222-222222222222"

    response = client.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "workshop", "name": "Workshop", "owner_id": "user-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == realm_id
    assert body["slug"] == "workshop"
    assert client.get("/api/v1/realms/workshop").json()["id"] == realm_id


def test_upsert_realm_by_id_updates_an_existing_row_it_owns() -> None:
    client = _make_client()
    realm_id = "33333333-3333-3333-3333-333333333333"
    client.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "workshop", "name": "Workshop", "owner_id": "user-1"},
    )

    response = client.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "workshop", "name": "Workshop Renamed", "owner_id": "user-1"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Workshop Renamed"


def test_upsert_realm_by_id_rejects_overwriting_another_owners_realm() -> None:
    client = _make_client()  # authenticates as user-1
    realm_id = "44444444-4444-4444-4444-444444444444"
    client.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "workshop", "name": "Workshop", "owner_id": "someone-else"},
    )

    response = client.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "workshop", "name": "Hijacked", "owner_id": "user-1"},
    )

    assert response.status_code == 403


def test_upsert_realm_by_id_allows_admin_to_override_another_owners_realm() -> None:
    client = _make_client()
    realm_id = "55555555-5555-5555-5555-555555555555"
    client.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "workshop", "name": "Workshop", "owner_id": "someone-else"},
    )

    admin_client = _make_client(
        principal=Principal(
            user_id="admin-1",
            email="admin@example.com",
            tenant_id="tenant-1",
            roles=["volundr:admin"],
        )
    )
    admin_client.app.state.realm_service = client.app.state.realm_service  # type: ignore[attr-defined]

    response = admin_client.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "workshop", "name": "Reassigned", "owner_id": "admin-1"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Reassigned"


def test_upsert_realm_by_id_rejects_invalid_slug() -> None:
    client = _make_client()
    realm_id = "66666666-6666-6666-6666-666666666666"

    response = client.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "Not A Slug", "name": "Workshop"},
    )

    assert response.status_code == 422
