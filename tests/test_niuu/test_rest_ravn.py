"""Tests for registry-backed Ravn aggregate REST endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from niuu.adapters.inbound.rest_ravn import create_ravn_router
from niuu.domain.models import InstanceKind, InstanceVisibility, Principal, RegisteredInstance


def _instance(
    instance_id: str,
    *,
    base_url: str,
    tenant_id: str = "tenant-a",
    enabled: bool = True,
    is_default: bool = False,
    tags: list[str] | None = None,
    config: dict[str, Any] | None = None,
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
        is_default=is_default,
        config=config or {},
        created_at=now,
        updated_at=now,
        tags=tags or [],
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
        tags: list[str] | None = None,
        match: str = "all",
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


def _client(
    instances: list[RegisteredInstance],
    *,
    embedded_forge_app: FastAPI | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(  # type: ignore[arg-type]
        create_ravn_router(
            StubInstanceService(instances),
            embedded_forge_app=embedded_forge_app,
        )
    )
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {
        "authorization": "Bearer test-token",
        "x-auth-user-id": "user-a",
        "x-auth-tenant": "tenant-a",
    }


@respx.mock
def test_list_ravens_merges_visible_instances_and_forwards_auth() -> None:
    client = _client(
        [
            _instance("tenant-a-volundr", base_url="http://volundr-a"),
            _instance("tenant-b-volundr", base_url="http://volundr-b", tenant_id="tenant-b"),
        ]
    )
    ravens_route = respx.get("http://volundr-a/api/v1/ravn/ravens").mock(
        return_value=Response(
            200,
            json=[{"id": "huginn", "name": "Huginn", "status": "running"}],
        )
    )

    response = client.get("/api/v1/ravn/ravens", headers=_headers())

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "huginn",
            "name": "Huginn",
            "status": "running",
            "instance_id": "tenant-a-volundr",
            "instance_name": "Instance tenant-a-volundr",
            "instance_slug": "tenant-a-volundr",
        }
    ]
    assert ravens_route.calls.last.request.headers["authorization"] == "Bearer test-token"
    assert ravens_route.calls.last.request.headers["x-auth-tenant"] == "tenant-a"


@respx.mock
def test_list_ravens_ignores_failing_instances_and_bad_payloads() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
            _instance("gamma", base_url="http://gamma"),
            _instance("delta", base_url="http://delta"),
        ]
    )
    respx.get("http://alpha/api/v1/ravn/ravens").mock(
        return_value=Response(200, json=[{"id": "huginn"}, "invalid-item", {"name": "no-id"}])
    )
    respx.get("http://beta/api/v1/ravn/ravens").mock(return_value=Response(503))
    respx.get("http://gamma/api/v1/ravn/ravens").mock(
        return_value=Response(200, json={"not": "a list"})
    )
    respx.get("http://delta/api/v1/ravn/ravens").mock(side_effect=ConnectionError)

    response = client.get("/api/v1/ravn/ravens", headers=_headers())

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["huginn"]


@respx.mock
def test_list_sessions_merges_and_sorts_last_active_descending() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/ravn/sessions").mock(
        return_value=Response(
            200,
            json=[{"id": "s1", "name": "Alpha flock", "last_active": "10"}],
        )
    )
    respx.get("http://beta/api/v1/ravn/sessions").mock(
        return_value=Response(
            200,
            json=[{"id": "s2", "name": "Beta flock", "lastActive": 25}],
        )
    )

    response = client.get("/api/v1/ravn/sessions", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["s2", "s1"]
    assert payload[0]["instance_id"] == "beta"
    assert payload[1]["instance_id"] == "alpha"


def test_list_endpoints_return_empty_when_no_visible_instances() -> None:
    client = _client([_instance("other", base_url="http://other", tenant_id="tenant-b")])

    assert client.get("/api/v1/ravn/ravens", headers=_headers()).json() == []
    assert client.get("/api/v1/ravn/sessions", headers=_headers()).json() == []


@respx.mock
def test_get_raven_searches_visible_instances_until_owner_found() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/ravn/ravens/huginn").mock(return_value=Response(404))
    beta_route = respx.get("http://beta/api/v1/ravn/ravens/huginn").mock(
        return_value=Response(200, json={"id": "huginn", "name": "Huginn"})
    )

    response = client.get("/api/v1/ravn/ravens/huginn", headers=_headers())

    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["id"] == "huginn"
    assert payload["instance_id"] == "beta"
    assert payload["instance_name"] == "Instance beta"
    assert beta_route.calls.last.request.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_get_raven_returns_404_when_no_visible_instance_owns_it() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/ravn/ravens/missing").mock(return_value=Response(404))
    respx.get("http://beta/api/v1/ravn/ravens/missing").mock(return_value=Response(403))

    response = client.get("/api/v1/ravn/ravens/missing", headers=_headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "Ravn not found: missing"


@respx.mock
def test_get_session_searches_visible_instances() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/ravn/sessions/s2").mock(return_value=Response(404))
    respx.get("http://beta/api/v1/ravn/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "Flock room"})
    )

    response = client.get("/api/v1/ravn/sessions/s2", headers=_headers())

    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["id"] == "s2"
    assert payload["instance_id"] == "beta"


@respx.mock
def test_get_session_returns_404_when_no_visible_instance_owns_it() -> None:
    client = _client([_instance("alpha", base_url="http://alpha")])
    respx.get("http://alpha/api/v1/ravn/sessions/missing").mock(return_value=Response(404))

    response = client.get("/api/v1/ravn/sessions/missing", headers=_headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found: missing"


def test_get_raven_returns_404_when_no_visible_instances() -> None:
    # Same shape as the forge single-session lookup with an empty registry.
    client = _client([])

    response = client.get("/api/v1/ravn/ravens/huginn", headers=_headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "Ravn not found: huginn"


def test_get_raven_rejects_invalid_remote_base_urls() -> None:
    client = _client([_instance("alpha", base_url="ftp://alpha")])

    response = client.get("/api/v1/ravn/ravens/huginn", headers=_headers())

    assert response.status_code == 502
    assert "http or https" in response.json()["detail"]


@respx.mock
def test_get_raven_skips_non_dict_owner_payloads() -> None:
    client = _client([_instance("alpha", base_url="http://alpha")])
    respx.get("http://alpha/api/v1/ravn/ravens/huginn").mock(
        return_value=Response(200, json=["bad"])
    )

    response = client.get("/api/v1/ravn/ravens/huginn", headers=_headers())

    assert response.status_code == 404


def test_list_ravens_can_dispatch_to_embedded_local_target() -> None:
    embedded = FastAPI()

    @embedded.get("/api/v1/ravn/ravens")
    async def list_local_ravens() -> list[dict[str, Any]]:
        return [{"id": "local-huginn", "name": "Huginn"}]

    client = _client(
        [
            _instance(
                "local",
                base_url="embedded://local-forge",
                is_default=True,
                config={"transport": "embedded"},
            )
        ],
        embedded_forge_app=embedded,
    )

    response = client.get("/api/v1/ravn/ravens", headers=_headers())

    assert response.status_code == 200
    assert response.json()[0]["id"] == "local-huginn"
    assert response.json()[0]["instance_slug"] == "local"


def test_list_ravens_degrades_when_embedded_target_missing() -> None:
    # An embedded instance without a local app raises inside the fan-out;
    # the aggregate skips it like any other failing instance.
    client = _client(
        [
            _instance(
                "local",
                base_url="embedded://local-forge",
                config={"transport": "embedded"},
            )
        ]
    )

    response = client.get("/api/v1/ravn/ravens", headers=_headers())

    assert response.status_code == 200
    assert response.json() == []
