"""Tests for shared instance registry REST endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from niuu.adapters.inbound.rest_instances import create_instances_router
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.services.instances import (
    InstanceAccessError,
    InstanceValidationError,
)


def _instance(
    instance_id: str,
    *,
    kind: InstanceKind = InstanceKind.VOLUNDR,
    visibility: InstanceVisibility = InstanceVisibility.TENANT,
    owner_id: str | None = None,
    tenant_id: str | None = "tenant-a",
    enabled: bool = True,
    is_default: bool = False,
    base_url: str = "https://registry.example.com",
) -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id=instance_id,
        kind=kind,
        slug=f"{instance_id}-slug",
        name=f"Instance {instance_id}",
        base_url=base_url,
        visibility=visibility,
        owner_id=owner_id,
        tenant_id=tenant_id,
        enabled=enabled,
        is_default=is_default,
        config={"region": "ca-central-1"},
        created_at=now,
        updated_at=now,
    )


class StubInstanceService:
    def __init__(self) -> None:
        self.visible_instances: list[RegisteredInstance] = []
        self.single_instance: RegisteredInstance | None = None
        self.create_result: RegisteredInstance | Exception | None = None
        self.update_result: RegisteredInstance | Exception | None = None
        self.delete_error: Exception | None = None
        self.list_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[str] = []
        self.get_calls: list[str] = []

    async def list_visible(
        self,
        principal: Principal,
        *,
        kind: InstanceKind | None = None,
        enabled_only: bool = False,
    ) -> list[RegisteredInstance]:
        self.list_calls.append(
            {
                "principal": principal,
                "kind": kind,
                "enabled_only": enabled_only,
            }
        )
        return list(self.visible_instances)

    async def create_instance(self, principal: Principal, **kwargs: Any) -> RegisteredInstance:
        self.create_calls.append({"principal": principal, **kwargs})
        if isinstance(self.create_result, Exception):
            raise self.create_result
        assert self.create_result is not None
        return self.create_result

    async def update_instance(
        self,
        principal: Principal,
        instance_id: str,
        **kwargs: Any,
    ) -> RegisteredInstance:
        self.update_calls.append(
            {
                "principal": principal,
                "instance_id": instance_id,
                **kwargs,
            }
        )
        if isinstance(self.update_result, Exception):
            raise self.update_result
        assert self.update_result is not None
        return self.update_result

    async def delete_instance(self, principal: Principal, instance_id: str) -> None:
        self.delete_calls.append(instance_id)
        if self.delete_error is not None:
            raise self.delete_error

    async def get_visible(
        self,
        principal: Principal,
        instance_id: str,
    ) -> RegisteredInstance | None:
        self.get_calls.append(instance_id)
        return self.single_instance


def _client(
    service: StubInstanceService,
    *,
    catalog: list[Any] | None = None,
) -> TestClient:
    app = FastAPI()
    if catalog is not None:
        app.state.settings = SimpleNamespace(
            niuu=SimpleNamespace(catalog=catalog),
        )
    app.include_router(create_instances_router(service))  # type: ignore[arg-type]
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {
        "authorization": "Bearer test-token",
        "x-auth-user-id": "user-a",
        "x-auth-email": "user-a@example.com",
        "x-auth-tenant": "tenant-a",
        "x-auth-roles": "member",
    }


def test_list_instances_serializes_aliases_and_forwards_filters() -> None:
    service = StubInstanceService()
    service.visible_instances = [
        _instance("volundr-1", is_default=True),
        _instance("ting-1", kind=InstanceKind.TING, enabled=False),
    ]
    client = _client(service)

    response = client.get(
        "/api/v1/niuu/instances?kind=ting&enabledOnly=true",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()[0]["baseUrl"] == "https://registry.example.com"
    assert response.json()[0]["isDefault"] is True
    assert service.list_calls[-1]["kind"] == InstanceKind.TING
    assert service.list_calls[-1]["enabled_only"] is True


def test_get_instance_catalog_reads_catalog_from_settings() -> None:
    client = _client(
        StubInstanceService(),
        catalog=[
            SimpleNamespace(
                kind=InstanceKind.VOLUNDR,
                label="Volundr",
                rune="ᚲ",
                summary="sessions",
                detail="spawns remote pods",
                registerable=True,
                filterable=True,
            ),
            SimpleNamespace(
                kind=InstanceKind.OBSERVATORY,
                label="",
                rune="ᛞ",
                summary="telemetry",
                detail="shared control plane",
                registerable=False,
                filterable=True,
            ),
        ],
    )

    response = client.get("/api/v1/niuu/instances/catalog")

    assert response.status_code == 200
    assert response.json() == [
        {
            "kind": "volundr",
            "label": "Volundr",
            "rune": "ᚲ",
            "summary": "sessions",
            "detail": "spawns remote pods",
            "registerable": True,
            "filterable": True,
        },
        {
            "kind": "observatory",
            "label": "Observatory",
            "rune": "ᛞ",
            "summary": "telemetry",
            "detail": "shared control plane",
            "registerable": False,
            "filterable": True,
        },
    ]


def test_create_instance_passes_payload_to_service_and_maps_access_errors() -> None:
    service = StubInstanceService()
    service.create_result = _instance(
        "created",
        visibility=InstanceVisibility.USER,
        owner_id="user-a",
    )
    client = _client(service)

    response = client.post(
        "/api/v1/niuu/instances",
        headers=_headers(),
        json={
            "kind": "volundr",
            "slug": "created",
            "name": "Created",
            "baseUrl": "https://created.example.com",
            "visibility": "user",
            "isDefault": True,
            "ownerId": "user-a",
            "config": {"region": "ca-central-1"},
        },
    )

    assert response.status_code == 201
    assert service.create_calls[-1]["kind"] == InstanceKind.VOLUNDR
    assert service.create_calls[-1]["visibility"] == InstanceVisibility.USER
    assert service.create_calls[-1]["is_default"] is True
    assert service.create_calls[-1]["owner_id"] == "user-a"

    service.create_result = InstanceValidationError("forbidden")
    forbidden = client.post(
        "/api/v1/niuu/instances",
        headers=_headers(),
        json={
            "slug": "bad",
            "name": "Bad",
            "baseUrl": "https://bad.example.com",
        },
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "forbidden"


def test_update_instance_maps_lookup_and_access_errors() -> None:
    service = StubInstanceService()
    service.update_result = _instance(
        "updated",
        visibility=InstanceVisibility.SYSTEM,
        tenant_id=None,
    )
    client = _client(service)

    response = client.patch(
        "/api/v1/niuu/instances/updated",
        headers=_headers(),
        json={"visibility": "system", "baseUrl": "https://updated.example.com"},
    )

    assert response.status_code == 200
    assert service.update_calls[-1]["visibility"] == InstanceVisibility.SYSTEM
    assert service.update_calls[-1]["base_url"] == "https://updated.example.com"

    service.update_result = LookupError("missing")
    missing = client.patch("/api/v1/niuu/instances/missing", headers=_headers(), json={"name": "x"})
    assert missing.status_code == 404

    service.update_result = InstanceAccessError("nope")
    forbidden = client.patch(
        "/api/v1/niuu/instances/blocked",
        headers=_headers(),
        json={"name": "x"},
    )
    assert forbidden.status_code == 403


def test_delete_instance_maps_access_error() -> None:
    service = StubInstanceService()
    client = _client(service)

    response = client.delete("/api/v1/niuu/instances/instance-1", headers=_headers())

    assert response.status_code == 204
    assert service.delete_calls == ["instance-1"]

    service.delete_error = InstanceAccessError("blocked")
    forbidden = client.delete("/api/v1/niuu/instances/instance-2", headers=_headers())
    assert forbidden.status_code == 403


@respx.mock
def test_test_instance_probes_health_and_returns_404_when_missing() -> None:
    service = StubInstanceService()
    service.single_instance = _instance("instance-1", base_url="https://health.example.com")
    client = _client(service)
    route = respx.get("https://health.example.com/health").mock(return_value=Response(200))

    response = client.post("/api/v1/niuu/instances/instance-1/test", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "statusCode": 200,
        "message": "Instance instance-1 is reachable",
    }
    assert route.called

    service.single_instance = None
    missing = client.post("/api/v1/niuu/instances/missing/test", headers=_headers())
    assert missing.status_code == 404


@respx.mock
def test_list_instance_sessions_forwards_headers_and_status_filter() -> None:
    service = StubInstanceService()
    service.single_instance = _instance("instance-1", base_url="https://volundr.example.com")
    client = _client(service)
    route = respx.get("https://volundr.example.com/api/v1/forge/sessions?status=running").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "sess-1",
                    "name": "Session 1",
                    "status": "running",
                    "model": "gpt-5",
                    "owner_id": "user-a",
                    "tenant_id": "tenant-a",
                    "archived_at": None,
                }
            ],
        )
    )

    response = client.get(
        "/api/v1/niuu/instances/instance-1/sessions?status=running",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "sess-1",
            "name": "Session 1",
            "status": "running",
            "model": "gpt-5",
            "ownerId": "user-a",
            "tenantId": "tenant-a",
            "archivedAt": None,
        }
    ]
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"
    assert route.calls.last.request.headers["x-auth-tenant"] == "tenant-a"

    respx.get("https://volundr.example.com/api/v1/forge/sessions").mock(
        side_effect=RuntimeError("boom")
    )
    failed = client.get("/api/v1/niuu/instances/instance-1/sessions", headers=_headers())
    assert failed.status_code == 502


def test_list_volundr_targets_requests_enabled_visible_volundr_instances() -> None:
    service = StubInstanceService()
    service.visible_instances = [_instance("volundr-1", enabled=True)]
    client = _client(service)

    response = client.get("/api/v1/niuu/targets/volundr", headers=_headers())

    assert response.status_code == 200
    assert response.json()[0]["id"] == "volundr-1"
    assert service.list_calls[-1]["kind"] == InstanceKind.VOLUNDR
    assert service.list_calls[-1]["enabled_only"] is True


@respx.mock
def test_observatory_snapshot_builds_registry_backed_topology() -> None:
    service = StubInstanceService()
    service.visible_instances = [
        _instance(
            "volundr-1",
            kind=InstanceKind.VOLUNDR,
            base_url="https://volundr.example.com",
            is_default=True,
        ),
        _instance(
            "bifrost-1",
            kind=InstanceKind.BIFROST,
            base_url="https://bifrost.example.com",
        ),
    ]
    client = _client(service)
    respx.get("https://volundr.example.com/health").mock(return_value=Response(200))
    respx.get("https://bifrost.example.com/health").mock(return_value=Response(503))

    response = client.get("/api/v1/niuu/observatory/snapshot", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["nodes"][0]["id"] == "service:guild"
    assert any(node.get("svcType") == "volundr" for node in payload["nodes"])
    assert any(node.get("svcType") == "bifrost" for node in payload["nodes"])
    assert any(event["service"] == "volundr" for event in payload["events"])
    assert any(event["service"] == "bifrost" for event in payload["events"])
