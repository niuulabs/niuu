"""Tests for registry-backed Ravn aggregate REST endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from starlette.websockets import WebSocketDisconnect

from niuu.adapters.inbound.rest_ravn import (
    _safe_log_value,
    create_ravn_router,
    create_ravn_session_proxy_router,
)
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
            and (
                not tags
                or (match == "all" and all(tag in instance.tags for tag in tags))
                or (match == "any" and any(tag in instance.tags for tag in tags))
            )
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
    service = StubInstanceService(instances)
    app.include_router(  # type: ignore[arg-type]
        create_ravn_router(
            service,
            embedded_forge_app=embedded_forge_app,
        )
    )
    app.include_router(  # type: ignore[arg-type]
        create_ravn_session_proxy_router(
            service,
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


def test_safe_log_value_removes_record_delimiters() -> None:
    assert _safe_log_value("resident\r\nforged") == "residentforged"


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
def test_ravn_aggregate_keeps_relative_chat_endpoint_on_yggdrasil() -> None:
    client = _client([_instance("noatun", base_url="https://niuu.noatun.test")])
    respx.get("https://niuu.noatun.test/api/v1/ravn/sessions").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "resident-id",
                    "chat_endpoint": "/s/resident-id/session",
                }
            ],
        )
    )

    response = client.get("/api/v1/ravn/sessions", headers=_headers())

    assert response.status_code == 200
    assert response.json()[0]["chat_endpoint"] == "/s/resident-id/session"


@respx.mock
def test_ravn_session_proxy_finds_owner_and_relays_browser_auth() -> None:
    client = _client([_instance("noatun", base_url="https://niuu.noatun.test")])
    owner = respx.get("https://niuu.noatun.test/api/v1/ravn/sessions/resident-id").mock(
        return_value=Response(200, json={"id": "resident-id"})
    )
    captured: dict[str, Any] = {}

    def _connect(url: str, **kwargs: Any):
        captured["url"] = url
        captured["kwargs"] = kwargs
        raise OSError("target unavailable after route resolution")

    with patch("websockets.asyncio.client.connect", side_effect=_connect):
        with client.websocket_connect(
            "/s/resident-id/session?access_token=browser-token",
            headers=_headers(),
        ) as websocket:
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

    assert captured["url"] == (
        "wss://niuu.noatun.test/s/resident-id/session?access_token=browser-token"
    )
    assert captured["kwargs"]["additional_headers"]["authorization"] == "Bearer test-token"
    assert owner.calls.last.request.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_resident_session_proxy_promotes_query_token_to_upstream_authorization() -> None:
    client = _client([_instance("noatun", base_url="https://niuu.noatun.test")])
    owner = respx.get("https://niuu.noatun.test/api/v1/ravn/ravens/resident-id").mock(
        return_value=Response(200, json={"id": "resident-id"})
    )
    captured: dict[str, Any] = {}

    def _connect(url: str, **kwargs: Any):
        captured["url"] = url
        captured["kwargs"] = kwargs
        raise OSError("target unavailable after route resolution")

    with patch("websockets.asyncio.client.connect", side_effect=_connect):
        with client.websocket_connect(
            "/s/resident-id/sessions/session-id/session"
            "?instance_id=noatun&token=machine-jwt&devUserId=user-a&devTenantId=tenant-a"
        ) as websocket:
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

    assert captured["url"] == (
        "wss://niuu.noatun.test/api/v1/forge/resident-runtimes/"
        "resident-id/sessions/session-id/chat?token=machine-jwt"
        "&devUserId=user-a&devTenantId=tenant-a"
    )
    assert captured["kwargs"]["additional_headers"]["authorization"] == "Bearer machine-jwt"
    assert owner.calls.last.request.headers["authorization"] == "Bearer machine-jwt"


def test_resident_session_proxy_bridges_embedded_forge_chat() -> None:
    runtime_id = "f0e2bb14-c2c2-48ab-b7fb-ffab722d43d9"
    session_id = "ca9986e4-4d7b-405e-bfe2-ff2dce94976d"

    class Connection:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []
            self.closed = False

        async def send(self, message: dict[str, Any]) -> None:
            self.sent.append(message)

        async def receive(self) -> dict[str, Any]:
            while not self.sent:
                await asyncio.sleep(0)
            return {"type": "message", "content": self.sent[0]["content"]}

        async def close(self) -> None:
            self.closed = True

    connection = Connection()

    class ResidentService:
        async def connect_chat(
            self,
            principal: Principal,
            requested_runtime_id: UUID,
            requested_session_id: UUID,
        ) -> Connection:
            assert principal.user_id == "user-a"
            assert requested_runtime_id == UUID(runtime_id)
            assert requested_session_id == UUID(session_id)
            return connection

    embedded = FastAPI()
    embedded.state.resident_runtime_service = ResidentService()
    client = _client(
        [
            _instance(
                "local",
                base_url="embedded://local-forge",
                config={"transport": "embedded"},
            )
        ],
        embedded_forge_app=embedded,
    )

    with client.websocket_connect(
        f"/s/{runtime_id}/sessions/{session_id}/session?instance_id=local",
        headers=_headers(),
    ) as websocket:
        websocket.send_json({"type": "message", "content": "hello locally"})
        assert websocket.receive_json() == {"type": "message", "content": "hello locally"}

    assert connection.sent == [{"type": "message", "content": "hello locally"}]


@respx.mock
def test_ravn_routes_use_split_service_base_url() -> None:
    client = _client(
        [
            _instance(
                "ymir",
                base_url="http://volundr",
                config={"ravn_base_url": "http://ravn"},
            )
        ]
    )
    ravn_route = respx.get("http://ravn/api/v1/ravn/ravens").mock(
        return_value=Response(200, json=[{"id": "muninn"}])
    )

    response = client.get("/api/v1/ravn/ravens", headers=_headers())

    assert response.status_code == 200
    assert response.json()[0]["id"] == "muninn"
    assert ravn_route.called


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
            _instance("control-only", base_url="http://control-only"),
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


@respx.mock
def test_list_ravens_preserves_same_legacy_id_from_multiple_instances() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/ravn/ravens").mock(
        return_value=Response(200, json=[{"id": "muninn", "name": "Alpha Muninn"}])
    )
    respx.get("http://beta/api/v1/ravn/ravens").mock(
        return_value=Response(200, json=[{"id": "muninn", "name": "Beta Muninn"}])
    )

    response = client.get("/api/v1/ravn/ravens", headers=_headers())

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert {item["instance_id"] for item in response.json()} == {"alpha", "beta"}


@respx.mock
def test_list_deployment_profiles_is_target_aware() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/ravn/deployment-profiles").mock(
        return_value=Response(200, json=[{"id": "ravn-helm", "backend": "helmrelease"}])
    )
    respx.get("http://beta/api/v1/ravn/deployment-profiles").mock(
        return_value=Response(200, json=[{"id": "ravn-openshell", "backend": "openshell"}])
    )
    respx.get("http://control-only/api/v1/ravn/deployment-profiles").mock(
        return_value=Response(200, json=[])
    )

    response = client.get("/api/v1/ravn/deployment-profiles", headers=_headers())

    assert response.status_code == 200
    assert {(item["id"], item["instance_id"]) for item in response.json()} == {
        ("ravn-helm", "alpha"),
        ("ravn-openshell", "beta"),
    }


@respx.mock
def test_create_raven_uses_existing_target_routing_and_strips_hints() -> None:
    client = _client(
        [
            _instance("default", base_url="http://default", is_default=True),
            _instance("helm", base_url="http://helm", tags=["resident", "flux"]),
        ]
    )
    route = respx.post("http://helm/api/v1/ravn/ravens").mock(
        return_value=Response(201, json={"id": "resident-id", "managed": True})
    )

    response = client.post(
        "/api/v1/ravn/ravens",
        headers=_headers(),
        json={
            "target_tags": ["flux"],
            "profile_id": "ravn-helm",
            "name": "Muninn",
        },
    )

    assert response.status_code == 201
    assert response.json()["instance_id"] == "helm"
    assert route.calls.last.request.read() == (b'{"profile_id":"ravn-helm","name":"Muninn"}')
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"


def test_create_raven_uses_resident_command_timeout() -> None:
    client = _client([_instance("noatun", base_url="http://noatun")])
    response = Response(201, json={"id": "resident-id", "managed": True})

    with (
        patch(
            "niuu.adapters.inbound.rest_ravn._request_remote",
            new=AsyncMock(return_value=response),
        ) as request_remote,
        patch(
            "niuu.adapters.inbound.rest_ravn._sync_persona_to_instance",
            new=AsyncMock(),
        ) as sync_persona,
    ):
        result = client.post(
            "/api/v1/ravn/ravens",
            headers=_headers(),
            json={
                "instance_id": "noatun",
                "profile_id": "ravn-openshell",
                "name": "Muninn",
                "persona_name": "reviewer",
            },
        )

    assert result.status_code == 201
    assert request_remote.await_args.kwargs["timeout"] == 900.0
    assert sync_persona.await_args.args[3] == "reviewer"


@respx.mock
def test_resident_lifecycle_commands_target_only_the_selected_instance() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    beta = respx.post("http://beta/api/v1/ravn/ravens/resident-id/suspend").mock(
        return_value=Response(200, json={"id": "resident-id", "status": "suspended"})
    )

    response = client.post(
        "/api/v1/ravn/ravens/resident-id/suspend?instance_id=beta",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["instance_id"] == "beta"
    assert beta.called


@respx.mock
def test_delete_raven_targets_selected_instance() -> None:
    client = _client([_instance("beta", base_url="http://beta")])
    route = respx.delete("http://beta/api/v1/ravn/ravens/resident-id").mock(
        return_value=Response(204)
    )

    response = client.delete(
        "/api/v1/ravn/ravens/resident-id?instance_id=beta",
        headers=_headers(),
    )

    assert response.status_code == 204
    assert route.called


@respx.mock
def test_resident_session_commands_use_registered_instance_routing() -> None:
    client = _client([_instance("noatun", base_url="http://noatun")])
    session = {
        "id": "11111111-2222-4333-8444-555555555555",
        "ravn_id": "resident-id",
        "status": "running",
        "model": "niuu/gpt-5.6-sol",
        "created_at": "2026-07-11T12:00:00Z",
        "title": "Persistent work",
        "chat_endpoint": "/s/resident-id/sessions/11111111-2222-4333-8444-555555555555/session",
    }
    list_route = respx.get("http://noatun/api/v1/ravn/ravens/resident-id/sessions").mock(
        return_value=Response(200, json=[session])
    )
    create_route = respx.post("http://noatun/api/v1/ravn/ravens/resident-id/sessions").mock(
        return_value=Response(201, json=session)
    )
    delete_route = respx.delete(
        "http://noatun/api/v1/ravn/ravens/resident-id/sessions/11111111-2222-4333-8444-555555555555"
    ).mock(return_value=Response(204))

    listed = client.get(
        "/api/v1/ravn/ravens/resident-id/sessions?instance_id=noatun",
        headers=_headers(),
    )
    created = client.post(
        "/api/v1/ravn/ravens/resident-id/sessions?instance_id=noatun",
        headers=_headers(),
        json={"title": "Persistent work", "model": "niuu/gpt-5.6-sol"},
    )
    deleted = client.delete(
        "/api/v1/ravn/ravens/resident-id/sessions/"
        "11111111-2222-4333-8444-555555555555?instance_id=noatun",
        headers=_headers(),
    )

    assert listed.status_code == 200
    assert listed.json()[0]["instance_id"] == "noatun"
    assert created.status_code == 201
    assert created.json()["instance_id"] == "noatun"
    assert deleted.status_code == 204
    assert list_route.called and create_route.called and delete_route.called
    assert create_route.calls.last.request.read() == (
        b'{"title":"Persistent work","model":"niuu/gpt-5.6-sol"}'
    )


@respx.mock
def test_get_raven_with_instance_id_does_not_probe_other_targets() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    alpha = respx.get("http://alpha/api/v1/ravn/ravens/muninn").mock(
        return_value=Response(200, json={"id": "muninn", "name": "Alpha Muninn"})
    )
    beta = respx.get("http://beta/api/v1/ravn/ravens/muninn").mock(
        return_value=Response(200, json={"id": "muninn", "name": "Beta Muninn"})
    )

    response = client.get(
        "/api/v1/ravn/ravens/muninn?instance_id=beta",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["instance_id"] == "beta"
    assert not alpha.called
    assert beta.called


@respx.mock
def test_get_raven_logs_forwards_filters_to_selected_target() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    route = respx.get("http://beta/api/v1/ravn/ravens/resident-id/logs").mock(
        return_value=Response(
            200,
            json={
                "entries": [{"source": "ravn", "message": "ready"}],
                "bufferTotal": 1,
            },
        )
    )

    response = client.get(
        "/api/v1/ravn/ravens/resident-id/logs"
        "?instance_id=beta&lines=25&source=ravn&source=skuld&min_level=info",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["instance_id"] == "beta"
    assert response.json()["entries"][0]["message"] == "ready"
    assert route.calls.last.request.url.params.multi_items() == [
        ("lines", "25"),
        ("source", "ravn"),
        ("source", "skuld"),
        ("min_level", "info"),
    ]
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_get_raven_logs_searches_visible_targets_without_instance_hint() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/ravn/ravens/resident-id/logs").mock(return_value=Response(404))
    beta = respx.get("http://beta/api/v1/ravn/ravens/resident-id/logs").mock(
        return_value=Response(200, json={"entries": [], "bufferTotal": 0})
    )

    response = client.get(
        "/api/v1/ravn/ravens/resident-id/logs?lines=10",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["instance_id"] == "beta"
    assert beta.called
