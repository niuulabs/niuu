"""Tests for registry-backed Volundr aggregate REST endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)


def _instance(
    instance_id: str,
    *,
    base_url: str,
    tenant_id: str = "tenant-a",
    enabled: bool = True,
) -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id=instance_id,
        kind=InstanceKind.VOLUNDR,
        slug=instance_id,
        name=f"Instance {instance_id}",
        base_url=base_url,
        visibility=InstanceVisibility.TENANT,
        owner_id=None,
        tenant_id=tenant_id,
        enabled=enabled,
        is_default=False,
        config={},
        created_at=now,
        updated_at=now,
    )


class StubInstanceService:
    def __init__(self, instances: list[RegisteredInstance]) -> None:
        self.instances = instances

    async def list_visible(
        self,
        principal: Principal,
        *,
        kind: InstanceKind | None = None,
        enabled_only: bool = False,
    ) -> list[RegisteredInstance]:
        return [
            instance
            for instance in self.instances
            if (kind is None or instance.kind == kind)
            and (instance.enabled or not enabled_only)
            and instance.tenant_id == principal.tenant_id
        ]

    async def get_visible(
        self,
        principal: Principal,
        instance_id: str,
    ) -> RegisteredInstance | None:
        for instance in await self.list_visible(principal, kind=InstanceKind.VOLUNDR):
            if instance.id == instance_id:
                return instance
        return None


def _client(instances: list[RegisteredInstance]) -> TestClient:
    app = FastAPI()
    app.include_router(create_volundr_router(StubInstanceService(instances)))  # type: ignore[arg-type]
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {
        "authorization": "Bearer test-token",
        "x-auth-user-id": "user-a",
        "x-auth-tenant": "tenant-a",
    }


@respx.mock
def test_list_sessions_merges_visible_instances_and_forwards_auth() -> None:
    client = _client(
        [
            _instance("tenant-a-volundr", base_url="http://volundr-a"),
            _instance("tenant-b-volundr", base_url="http://volundr-b", tenant_id="tenant-b"),
        ]
    )
    sessions_route = respx.get("http://volundr-a/api/v1/forge/sessions").mock(
        return_value=Response(
            200,
            json=[{"id": "s1", "name": "Session 1", "status": "running", "last_active": 5}],
        )
    )

    response = client.get("/api/v1/niuu/volundr/sessions", headers=_headers())

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "s1",
            "name": "Session 1",
            "status": "running",
            "last_active": 5,
            "instance_id": "tenant-a-volundr",
            "instance_name": "Instance tenant-a-volundr",
            "instance_slug": "tenant-a-volundr",
        }
    ]
    assert sessions_route.calls.last.request.headers["authorization"] == "Bearer test-token"
    assert sessions_route.calls.last.request.headers["x-auth-tenant"] == "tenant-a"


@respx.mock
def test_get_session_searches_visible_instances() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/forge/sessions/s2").mock(return_value=Response(404))
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "Session 2", "status": "running"})
    )

    response = client.get("/api/v1/niuu/volundr/sessions/s2", headers=_headers())

    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["id"] == "s2"
    assert payload["instance_id"] == "beta"
    assert payload["instance_name"] == "Instance beta"
