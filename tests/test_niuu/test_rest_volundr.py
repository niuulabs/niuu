"""Tests for registry-backed Volundr aggregate REST endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
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
    is_default: bool = False,
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


@respx.mock
def test_list_sessions_ignores_errors_and_sorts_last_active_descending() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
            _instance("gamma", base_url="http://gamma"),
        ]
    )
    respx.get("http://alpha/api/v1/forge/sessions?status=running").mock(
        return_value=Response(
            200,
            json=[{"id": "s1", "name": "Alpha", "status": "running", "last_active": "10"}],
        )
    )
    respx.get("http://beta/api/v1/forge/sessions?status=running").mock(
        return_value=Response(
            200,
            json=[
                {"id": "s2", "name": "Beta", "status": "running", "lastActive": 25},
                "invalid-item",
            ],
        )
    )
    respx.get("http://gamma/api/v1/forge/sessions?status=running").mock(return_value=Response(503))

    response = client.get(
        "/api/v1/niuu/volundr/sessions?status=running",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["s2", "s1"]


@respx.mock
def test_get_session_returns_404_when_no_visible_instance_owns_it() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/forge/sessions/missing").mock(return_value=Response(404))
    respx.get("http://beta/api/v1/forge/sessions/missing").mock(return_value=Response(403))

    response = client.get("/api/v1/niuu/volundr/sessions/missing", headers=_headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found: missing"


@respx.mock
def test_create_session_uses_requested_instance_and_strips_instance_hints() -> None:
    client = _client(
        [
            _instance("default", base_url="http://default", is_default=True),
            _instance("target", base_url="http://target"),
        ]
    )
    route = respx.post("http://target/api/v1/forge/sessions").mock(
        return_value=Response(200, json={"id": "s3", "name": "Created"})
    )

    response = client.post(
        "/api/v1/niuu/volundr/sessions",
        headers=_headers(),
        json={
            "instance_id": "target",
            "instanceName": "ignored",
            "instance_name": "ignored-again",
            "workspace": "repo-a",
        },
    )

    assert response.status_code == 201
    assert response.json()["instance_id"] == "target"
    assert route.calls.last.request.read() == b'{"workspace":"repo-a"}'


@respx.mock
def test_create_session_uses_default_instance_and_handles_missing_registry_or_bad_payload() -> None:
    client = _client([_instance("default", base_url="http://default", is_default=True)])
    respx.post("http://default/api/v1/forge/sessions").mock(
        return_value=Response(200, json=["bad"])
    )

    invalid_payload = client.post(
        "/api/v1/niuu/volundr/sessions",
        headers=_headers(),
        json={"workspace": "repo-a"},
    )
    assert invalid_payload.status_code == 502

    missing_target = client.post(
        "/api/v1/niuu/volundr/sessions",
        headers=_headers(),
        json={"instanceId": "missing"},
    )
    assert missing_target.status_code == 404

    empty_registry = _client([])
    unavailable = empty_registry.post(
        "/api/v1/niuu/volundr/sessions",
        headers=_headers(),
        json={"workspace": "repo-a"},
    )
    assert unavailable.status_code == 503


@respx.mock
def test_get_stats_aggregates_totals_and_merges_sparklines() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/forge/stats").mock(
        return_value=Response(
            200,
            json={
                "active_sessions": 2,
                "totalSessions": 5,
                "tokens_today": 10,
                "localTokens": 3,
                "cloud_tokens": 7,
                "costToday": 1.25,
                "sparklines": {"tokens": [1, 2], "cost": [0.5, 0.75]},
            },
        )
    )
    respx.get("http://beta/api/v1/forge/stats").mock(
        return_value=Response(
            200,
            json={
                "activeSessions": 4,
                "total_sessions": 6,
                "tokensToday": 20,
                "local_tokens": 5,
                "cloudTokens": 9,
                "cost_today": 2.5,
                "sparklines": {"tokens": [3, 4, 5]},
            },
        )
    )

    response = client.get("/api/v1/niuu/volundr/stats", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "active_sessions": 6,
        "total_sessions": 11,
        "tokens_today": 30,
        "local_tokens": 8,
        "cloud_tokens": 16,
        "cost_today": 3.75,
        "sparklines": {"tokens": [4.0, 6.0, 5.0], "cost": [0.5, 0.75]},
    }


@pytest.mark.parametrize(
    ("method", "path", "remote_path", "remote_response", "expected_status", "expected_body"),
    [
        ("post", "/sessions/s2/stop", "/sessions/s2/stop", {"ok": True}, 200, {"ok": True}),
        (
            "post",
            "/sessions/s2/archive",
            "/sessions/s2/archive",
            {"archived": True},
            200,
            {"archived": True},
        ),
        (
            "post",
            "/sessions/s2/restore",
            "/sessions/s2/restore",
            {"restored": True},
            200,
            {"restored": True},
        ),
        ("delete", "/sessions/s2?force=1", "/sessions/s2?force=1", None, 204, None),
        (
            "get",
            "/sessions/s2/conversation",
            "/sessions/s2/conversation",
            {"turns": [{"id": "turn-1"}]},
            200,
            {"turns": [{"id": "turn-1"}]},
        ),
        (
            "post",
            "/sessions/s2/messages",
            "/sessions/s2/messages",
            {"accepted": True},
            200,
            {"accepted": True},
        ),
        (
            "get",
            "/sessions/s2/logs?limit=5",
            "/sessions/s2/logs?limit=5",
            {"lines": ["a"]},
            200,
            {"lines": ["a"]},
        ),
        (
            "get",
            "/sessions/s2/logs/aggregate?tail=1",
            "/sessions/s2/logs/aggregate?tail=1",
            {"lines": ["agg"]},
            200,
            {"lines": ["agg"]},
        ),
        (
            "get",
            "/chronicles/s2/timeline?limit=3",
            "/chronicles/s2/timeline?limit=3",
            [{"message": "chronicle"}],
            200,
            [{"message": "chronicle"}],
        ),
    ],
)
@respx.mock
def test_proxy_routes_forward_to_session_owner(
    method: str,
    path: str,
    remote_path: str,
    remote_response: dict[str, Any] | list[dict[str, Any]] | None,
    expected_status: int,
    expected_body: dict[str, Any] | list[dict[str, Any]] | None,
) -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/forge/sessions/s2").mock(return_value=Response(404))
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "Session 2"})
    )
    route = respx.route(
        method=method.upper(),
        url=f"http://beta/api/v1/forge{remote_path}",
    ).mock(
        return_value=Response(200, json=remote_response)
        if remote_response is not None
        else Response(204)
    )
    request_kwargs: dict[str, Any] = {"headers": _headers()}
    if method == "post" and path.endswith("/messages"):
        request_kwargs["json"] = {"text": "hello"}

    response = getattr(client, method)(
        f"/api/v1/niuu/volundr{path}",
        **request_kwargs,
    )

    assert response.status_code == expected_status
    if expected_body is not None:
        payload = response.json()
        if isinstance(expected_body, dict):
            for key, value in expected_body.items():
                assert payload[key] == value
            if path.endswith(("/stop", "/archive", "/restore")):
                assert payload["instance_id"] == "beta"
                assert payload["instance_name"] == "Instance beta"
        else:
            assert payload == expected_body
    else:
        assert response.content == b""
    assert route.called


@respx.mock
def test_proxy_routes_fall_back_to_empty_payloads_when_remote_returns_non_dict_content() -> None:
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "Session 2"})
    )
    respx.get("http://beta/api/v1/forge/sessions/s2/conversation").mock(
        return_value=Response(200, json=["bad"])
    )
    respx.get("http://beta/api/v1/forge/sessions/s2/logs").mock(
        return_value=Response(200, json=["bad"])
    )
    respx.get("http://beta/api/v1/forge/sessions/s2/logs/aggregate").mock(
        return_value=Response(200, json=["bad"])
    )
    respx.post("http://beta/api/v1/forge/sessions/s2/messages").mock(
        return_value=Response(200, json=["bad"])
    )

    assert (
        client.get("/api/v1/niuu/volundr/sessions/s2/conversation", headers=_headers()).json()
        == {"turns": []}
    )
    assert client.get("/api/v1/niuu/volundr/sessions/s2/logs", headers=_headers()).json() == {
        "lines": []
    }
    assert client.get(
        "/api/v1/niuu/volundr/sessions/s2/logs/aggregate",
        headers=_headers(),
    ).json() == {"lines": []}
    assert client.post(
        "/api/v1/niuu/volundr/sessions/s2/messages",
        headers=_headers(),
        json={"text": "hello"},
    ).json() == {}
