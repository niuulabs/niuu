"""Unit tests for the shared realm governance REST adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.ports import AuthorizationEvaluationError
from niuu.adapters.inbound import rest_realms
from niuu.adapters.inbound.rest_realms import create_realms_router
from niuu.domain.models import Principal
from niuu.domain.services.realm import RealmService
from tests.test_niuu.test_realm_service import InMemoryRealmRepository


def _make_principal() -> Principal:
    return Principal(
        user_id="user-1",
        email="test@example.com",
        tenant_id="tenant-1",
        roles=["volundr:developer"],
    )


def _make_client(
    *,
    with_service: bool = True,
    principal: Principal | None = None,
    service: RealmService | None = None,
) -> TestClient:
    app = FastAPI()
    app.state.authorization = CedarAuthorizationAdapter()
    if with_service:
        app.state.realm_service = service or RealmService(InMemoryRealmRepository())

    async def extract_principal() -> Principal:
        return principal or _make_principal()

    app.include_router(create_realms_router(extract_principal, stream_interval_seconds=15.0))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def test_router_default_prefix() -> None:
    async def extract() -> Principal:
        return _make_principal()

    router = create_realms_router(extract, stream_interval_seconds=15.0)
    assert router.prefix == "/api/v1/realms"


def test_router_custom_prefix() -> None:
    async def extract() -> Principal:
        return _make_principal()

    router = create_realms_router(extract, prefix="/api/v2/realms", stream_interval_seconds=15.0)
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
        ),
        service=client.app.state.realm_service,  # type: ignore[attr-defined]
    )

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


@pytest.mark.asyncio
async def test_realm_detail_stream_emits_complete_named_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The authenticated realm-detail stream starts with a usable snapshot."""
    app = FastAPI()
    app.state.authorization = CedarAuthorizationAdapter()
    service = RealmService(InMemoryRealmRepository())
    app.state.realm_service = service
    realm = await service.create_realm("forge", "Forge", tenant_id="tenant-1")
    await service.grant_trust(realm.id, "build", level=2)
    await service.record_capability(realm.id, "search", "tool", status="present")

    async def extract_principal() -> Principal:
        return _make_principal()

    stream_interval_seconds = 3.25
    sleep = AsyncMock()
    monkeypatch.setattr(rest_realms.asyncio, "sleep", sleep)
    router = create_realms_router(
        extract_principal,
        stream_interval_seconds=stream_interval_seconds,
    )
    route = next(route for route in router.routes if route.path == "/api/v1/realms/{slug}/stream")
    request = Request({"type": "http", "app": app, "headers": []})
    response = await route.endpoint(request, "forge", _make_principal())
    frame = await anext(response.body_iterator)
    keepalive = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert frame.startswith("event: realm.detail\n")
    payload = frame.removeprefix("event: realm.detail\ndata: ").removesuffix("\n\n")
    detail = json.loads(payload)
    assert detail["realm"]["slug"] == "forge"
    assert detail["trust_grants"][0]["action_class"] == "build"
    assert detail["capabilities"][0]["name"] == "search"
    assert keepalive == ": keepalive\n\n"
    sleep.assert_awaited_once_with(stream_interval_seconds)


@pytest.mark.asyncio
async def test_realm_detail_stream_emits_changed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stream emits a replacement frame after realm detail changes."""
    app = FastAPI()
    app.state.authorization = CedarAuthorizationAdapter()
    service = RealmService(InMemoryRealmRepository())
    app.state.realm_service = service
    realm = await service.create_realm("forge", "Forge", tenant_id="tenant-1")

    async def extract_principal() -> Principal:
        return _make_principal()

    stream_interval_seconds = 3.25
    sleep = AsyncMock()
    monkeypatch.setattr(rest_realms.asyncio, "sleep", sleep)
    router = create_realms_router(
        extract_principal,
        stream_interval_seconds=stream_interval_seconds,
    )
    route = next(route for route in router.routes if route.path == "/api/v1/realms/{slug}/stream")
    request = Request({"type": "http", "app": app, "headers": []})
    response = await route.endpoint(request, "forge", _make_principal())
    initial_frame = await anext(response.body_iterator)

    await service.record_capability(realm.id, "search", "tool", status="present")
    changed_frame = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert initial_frame.startswith("event: realm.detail\n")
    assert changed_frame.startswith("event: realm.detail\n")
    assert changed_frame != initial_frame
    payload = changed_frame.removeprefix("event: realm.detail\ndata: ").removesuffix("\n\n")
    detail = json.loads(payload)
    assert [capability["name"] for capability in detail["capabilities"]] == ["search"]
    sleep.assert_awaited_once_with(stream_interval_seconds)


# ---------------------------------------------------------------------------
# Tenant and ownership boundaries
# ---------------------------------------------------------------------------


def _principal(
    user_id: str = "user-1",
    *,
    tenant_id: str = "tenant-1",
    role: str = "volundr:developer",
) -> Principal:
    return Principal(
        user_id=user_id,
        email=f"{user_id}@example.com",
        tenant_id=tenant_id,
        roles=[role],
    )


@pytest.fixture
def shared_service() -> RealmService:
    return RealmService(InMemoryRealmRepository())


@pytest.fixture
def owner_client(shared_service: RealmService) -> TestClient:
    client = _make_client(principal=_principal("owner"), service=shared_service)
    created = client.post("/api/v1/realms", json={"slug": "forge", "name": "Forge"})
    assert created.status_code == 201
    return client


def test_created_realm_belongs_to_the_callers_tenant_and_user(owner_client: TestClient) -> None:
    body = owner_client.get("/api/v1/realms/forge").json()

    assert body["tenant_id"] == "tenant-1"
    assert body["owner_id"] == "owner"


def test_other_tenant_cannot_see_a_realm(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    outsider = _make_client(
        principal=_principal("outsider", tenant_id="tenant-2"), service=shared_service
    )

    assert outsider.get("/api/v1/realms").json() == []
    assert outsider.get("/api/v1/realms/forge").status_code == 404
    assert outsider.get("/api/v1/realms/forge/stream").status_code == 404
    assert outsider.get("/api/v1/realms/forge/trust-grants").json() == []
    assert outsider.get("/api/v1/realms/forge/capabilities").json() == []


def test_other_tenant_admin_cannot_change_a_realm(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    outsider = _make_client(
        principal=_principal("outsider", tenant_id="tenant-2", role="volundr:admin"),
        service=shared_service,
    )
    grant = {"action_class": "build", "level": 5}
    capability = {"name": "x", "kind": "tool"}

    assert outsider.delete("/api/v1/realms/forge").status_code == 404
    assert outsider.post("/api/v1/realms/forge/trust-grants", json=grant).status_code == 404
    assert outsider.post("/api/v1/realms/forge/capabilities", json=capability).status_code == 404
    assert owner_client.get("/api/v1/realms/forge/trust-grants").json() == []


def test_other_tenant_cannot_overwrite_a_realm_by_its_id(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    realm_id = owner_client.get("/api/v1/realms/forge").json()["id"]
    outsider = _make_client(
        principal=_principal("outsider", tenant_id="tenant-2", role="volundr:admin"),
        service=shared_service,
    )

    response = outsider.put(
        f"/api/v1/realms/by-id/{realm_id}",
        json={"slug": "forge", "name": "Hijacked"},
    )

    assert response.status_code == 404
    assert owner_client.get("/api/v1/realms/forge").json()["name"] == "Forge"


def test_two_tenants_can_create_the_same_slug(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    other = _make_client(
        principal=_principal("other", tenant_id="tenant-2"), service=shared_service
    )

    response = other.post("/api/v1/realms", json={"slug": "forge", "name": "Other Forge"})

    assert response.status_code == 201
    assert other.get("/api/v1/realms/forge").json()["name"] == "Other Forge"
    assert owner_client.get("/api/v1/realms/forge").json()["name"] == "Forge"


def test_member_who_does_not_own_a_realm_cannot_change_it(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    member = _make_client(principal=_principal("member"), service=shared_service)

    assert member.get("/api/v1/realms/forge").status_code == 200
    assert (
        member.post(
            "/api/v1/realms/forge/trust-grants",
            json={"action_class": "build", "level": 5},
        ).status_code
        == 403
    )
    assert member.delete("/api/v1/realms/forge").status_code == 403


def test_tenant_admin_can_change_a_realm_they_do_not_own(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    admin = _make_client(
        principal=_principal("admin", role="volundr:admin"), service=shared_service
    )

    granted = admin.post(
        "/api/v1/realms/forge/trust-grants",
        json={"action_class": "build", "level": 2},
    )

    assert granted.status_code == 201
    assert admin.delete("/api/v1/realms/forge").status_code == 204


@pytest.mark.asyncio
async def test_realm_without_an_owner_is_changed_only_by_an_admin(
    shared_service: RealmService,
) -> None:
    await shared_service.create_realm("legacy", "Legacy", tenant_id="tenant-1")
    member = _make_client(principal=_principal("member"), service=shared_service)
    admin = _make_client(
        principal=_principal("admin", role="volundr:admin"), service=shared_service
    )

    assert member.delete("/api/v1/realms/legacy").status_code == 403
    assert admin.delete("/api/v1/realms/legacy").status_code == 204


def test_resident_can_record_a_capability_without_owning_the_realm(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    resident = _make_client(principal=_principal("resident-runtime"), service=shared_service)

    response = resident.post(
        "/api/v1/realms/forge/capabilities",
        json={"name": "grep-tool", "kind": "tool", "status": "present"},
    )

    assert response.status_code == 201


def test_viewer_reads_but_cannot_write(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    viewer = _make_client(
        principal=_principal("viewer", role="volundr:viewer"), service=shared_service
    )

    assert [realm["slug"] for realm in viewer.get("/api/v1/realms").json()] == ["forge"]
    assert viewer.post("/api/v1/realms", json={"slug": "new", "name": "New"}).status_code == 403
    assert (
        viewer.post(
            "/api/v1/realms/forge/capabilities",
            json={"name": "x", "kind": "tool"},
        ).status_code
        == 403
    )


def test_caller_without_a_tenant_gets_no_realms(
    owner_client: TestClient, shared_service: RealmService
) -> None:
    no_tenant = _make_client(principal=_principal("nobody", tenant_id=""), service=shared_service)

    assert no_tenant.get("/api/v1/realms").status_code == 403
    assert no_tenant.get("/api/v1/realms/forge").status_code == 403


def test_missing_authorization_is_a_503() -> None:
    client = _make_client()
    del client.app.state.authorization  # type: ignore[attr-defined]

    assert client.post("/api/v1/realms", json={"slug": "a", "name": "A"}).status_code == 503


def test_failed_authorization_evaluation_is_a_503(owner_client: TestClient) -> None:
    owner_client.app.state.authorization = AsyncMock(  # type: ignore[attr-defined]
        filter_allowed=AsyncMock(side_effect=AuthorizationEvaluationError("boom"))
    )

    assert owner_client.get("/api/v1/realms/forge").status_code == 503


PROJECT_ID = "5c3115f1-0b77-44c3-a4a6-ae4eca22239a"


def test_create_realm_in_a_project() -> None:
    client = _make_client()

    response = client.post(
        "/api/v1/realms", json={"slug": "copy", "name": "Copy", "project_id": PROJECT_ID}
    )

    assert response.status_code == 201
    assert response.json()["project_id"] == PROJECT_ID
    assert client.get("/api/v1/realms/copy").json()["project_id"] == PROJECT_ID


def test_a_realm_stands_alone_until_it_joins_a_project() -> None:
    client = _make_client()
    client.post("/api/v1/realms", json={"slug": "copy", "name": "Copy"})

    assert client.get("/api/v1/realms/copy").json()["project_id"] is None


def test_move_a_realm_into_and_out_of_a_project() -> None:
    client = _make_client()
    client.post("/api/v1/realms", json={"slug": "copy", "name": "Copy"})

    moved = client.patch("/api/v1/realms/copy/project", json={"project_id": PROJECT_ID})
    assert moved.status_code == 200
    assert moved.json()["project_id"] == PROJECT_ID

    left = client.patch("/api/v1/realms/copy/project", json={"project_id": None})
    assert left.status_code == 200
    assert left.json()["project_id"] is None


def test_moving_an_unknown_realm_is_not_found() -> None:
    client = _make_client()

    response = client.patch("/api/v1/realms/ghost/project", json={"project_id": PROJECT_ID})

    assert response.status_code == 404


def test_a_project_must_be_a_uuid() -> None:
    client = _make_client()
    client.post("/api/v1/realms", json={"slug": "copy", "name": "Copy"})

    response = client.patch("/api/v1/realms/copy/project", json={"project_id": "not-a-uuid"})

    assert response.status_code == 422
