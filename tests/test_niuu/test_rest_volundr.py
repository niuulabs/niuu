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
        def _matches(instance: RegisteredInstance) -> bool:
            if not tags:
                return True
            have = set(instance.tags)
            want = set(tags)
            return bool(have & want) if match == "any" else want <= have

        return [
            instance
            for instance in self.instances
            if (kind is None or instance.kind == kind)
            and (instance.enabled or not enabled_only)
            and instance.tenant_id == principal.tenant_id
            and _matches(instance)
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
        create_volundr_router(
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


def test_list_sessions_can_dispatch_to_embedded_local_target() -> None:
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def list_local_sessions() -> list[dict[str, Any]]:
        return [{"id": "local-s1", "name": "Local", "status": "running"}]

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

    response = client.get("/api/v1/forge/sessions", headers=_headers())

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "local-s1",
            "name": "Local",
            "status": "running",
            "instance_id": "local",
            "instance_name": "Instance local",
            "instance_slug": "local",
        }
    ]


def test_embedded_target_fails_loud_without_local_app() -> None:
    client = _client(
        [
            _instance(
                "local",
                base_url="embedded://local-forge",
                config={"transport": "embedded"},
            )
        ]
    )

    response = client.post(
        "/api/v1/forge/sessions",
        headers=_headers(),
        json={"workspace": "repo-a"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Embedded Forge target is not available in this process"


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

    response = client.get("/api/v1/forge/sessions", headers=_headers())

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

    response = client.get("/api/v1/forge/sessions/s2", headers=_headers())

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
        "/api/v1/forge/sessions?status=running",
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

    response = client.get("/api/v1/forge/sessions/missing", headers=_headers())

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found: missing"


def test_get_session_rejects_invalid_remote_base_urls() -> None:
    client = _client([_instance("alpha", base_url="ftp://alpha")])

    response = client.get("/api/v1/forge/sessions/s2", headers=_headers())

    assert response.status_code == 502
    assert "http or https" in response.json()["detail"]


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
        "/api/v1/forge/sessions",
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
def test_create_session_targets_instance_by_tags() -> None:
    client = _client(
        [
            _instance("cpu", base_url="http://cpu", is_default=True, tags=["us-west"]),
            _instance("gpu", base_url="http://gpu", tags=["gpu", "us-west"]),
        ]
    )
    route = respx.post("http://gpu/api/v1/forge/sessions").mock(
        return_value=Response(200, json={"id": "s9", "name": "Created"})
    )

    response = client.post(
        "/api/v1/forge/sessions",
        headers=_headers(),
        json={"target_tags": ["gpu"], "workspace": "repo-a"},
    )

    assert response.status_code == 201
    assert response.json()["instance_id"] == "gpu"
    # The targeting hints are stripped before forwarding to the backend.
    assert route.calls.last.request.read() == b'{"workspace":"repo-a"}'


def test_create_session_fails_loud_when_no_instance_matches_tags() -> None:
    client = _client([_instance("cpu", base_url="http://cpu", is_default=True, tags=["us-west"])])

    response = client.post(
        "/api/v1/forge/sessions",
        headers=_headers(),
        json={"target_tags": ["gpu"], "workspace": "repo-a"},
    )

    assert response.status_code == 503
    assert "gpu" in response.json()["detail"]


@respx.mock
def test_create_session_uses_default_instance_and_handles_missing_registry_or_bad_payload() -> None:
    client = _client([_instance("default", base_url="http://default", is_default=True)])
    respx.post("http://default/api/v1/forge/sessions").mock(
        return_value=Response(200, json=["bad"])
    )

    invalid_payload = client.post(
        "/api/v1/forge/sessions",
        headers=_headers(),
        json={"workspace": "repo-a"},
    )
    assert invalid_payload.status_code == 502

    missing_target = client.post(
        "/api/v1/forge/sessions",
        headers=_headers(),
        json={"instanceId": "missing"},
    )
    assert missing_target.status_code == 404

    empty_registry = _client([])
    unavailable = empty_registry.post(
        "/api/v1/forge/sessions",
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

    response = client.get("/api/v1/forge/stats", headers=_headers())

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


@respx.mock
def test_get_cluster_resources_returns_camel_and_snake_resource_keys() -> None:
    client = _client([_instance("alpha", base_url="http://alpha")])
    respx.get("http://alpha/api/v1/forge/cluster/resources").mock(
        return_value=Response(
            200,
            json={
                "resource_types": [
                    {
                        "name": "gpu",
                        "resource_key": "nvidia.com/gpu",
                        "display_name": "GPU",
                        "unit": "count",
                    }
                ],
                "nodes": [
                    {
                        "name": "node-a",
                        "labels": {},
                        "allocatable": {"nvidia.com/gpu": "1"},
                        "allocated": {},
                        "available": {"nvidia.com/gpu": "1"},
                    }
                ],
            },
        )
    )

    response = client.get("/api/v1/forge/cluster/resources", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource_types"] == payload["resourceTypes"]
    assert payload["resourceTypes"][0]["resource_key"] == "nvidia.com/gpu"
    assert payload["resourceTypes"][0]["resourceKey"] == "nvidia.com/gpu"
    assert payload["resourceTypes"][0]["display_name"] == "GPU"
    assert payload["resourceTypes"][0]["displayName"] == "GPU"
    assert payload["nodes"][0]["name"] == "alpha/node-a"


@respx.mock
def test_list_mcp_servers_proxies_to_default_instance_credentials_surface() -> None:
    client = _client(
        [
            _instance("default", base_url="http://default", is_default=True),
            _instance("gpu", base_url="http://gpu", tags=["gpu"]),
        ]
    )
    route = respx.get("http://default/api/v1/credentials/mcp-servers").mock(
        return_value=Response(
            200,
            json=[
                {
                    "name": "linear",
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@linear/mcp-server"],
                    "description": "Linear",
                }
            ],
        )
    )

    response = client.get("/api/v1/forge/mcp-servers", headers=_headers())

    assert response.status_code == 200
    assert response.json()[0]["name"] == "linear"
    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_list_mcp_servers_can_target_instance_by_tags() -> None:
    client = _client(
        [
            _instance("cpu", base_url="http://cpu", is_default=True, tags=["cpu"]),
            _instance("gpu", base_url="http://gpu", tags=["gpu"]),
        ]
    )
    route = respx.get("http://gpu/api/v1/credentials/mcp-servers").mock(
        return_value=Response(200, json=[{"name": "mimir", "type": "sse"}])
    )

    response = client.get(
        "/api/v1/forge/mcp-servers?target_tags=gpu",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == [{"name": "mimir", "type": "sse"}]
    assert route.called


@respx.mock
def test_get_mcp_server_proxies_to_credentials_surface() -> None:
    client = _client([_instance("default", base_url="http://default", is_default=True)])
    route = respx.get("http://default/api/v1/credentials/mcp-servers/linear").mock(
        return_value=Response(
            200,
            json={
                "name": "linear",
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@linear/mcp-server"],
            },
        )
    )

    response = client.get("/api/v1/forge/mcp-servers/linear", headers=_headers())

    assert response.status_code == 200
    assert response.json()["name"] == "linear"
    assert route.called


@pytest.mark.parametrize(
    ("method", "path", "remote_path", "remote_response", "expected_status", "expected_body"),
    [
        ("post", "/sessions/s2/stop", "/sessions/s2/stop", {"ok": True}, 200, {"ok": True}),
        (
            "post",
            "/sessions/s2/start",
            "/sessions/s2/start",
            {"status": "starting"},
            200,
            {"status": "starting"},
        ),
        (
            "post",
            "/sessions/s2/resume",
            "/sessions/s2/resume",
            {"status": "starting"},
            200,
            {"status": "starting"},
        ),
        (
            "patch",
            "/sessions/s2/archive",
            "/sessions/s2/archive",
            {"archived": True},
            200,
            {"archived": True},
        ),
        (
            "patch",
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
        f"/api/v1/forge{path}",
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

    assert client.get("/api/v1/forge/sessions/s2/conversation", headers=_headers()).json() == {
        "turns": []
    }
    assert client.get("/api/v1/forge/sessions/s2/logs", headers=_headers()).json() == {"lines": []}
    assert client.get(
        "/api/v1/forge/sessions/s2/logs/aggregate",
        headers=_headers(),
    ).json() == {"lines": []}
    assert (
        client.post(
            "/api/v1/forge/sessions/s2/messages",
            headers=_headers(),
            json={"text": "hello"},
        ).json()
        == {}
    )


@respx.mock
def test_external_sessions_aggregates_visible_instances() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    respx.get("http://alpha/api/v1/forge/external-sessions").mock(
        return_value=Response(
            200,
            json=[
                {
                    "provider": "claude-code",
                    "external_id": "ext-old",
                    "updated_at": "2026-06-01T10:00:00Z",
                }
            ],
        )
    )
    respx.get("http://beta/api/v1/forge/external-sessions").mock(
        return_value=Response(
            200,
            json=[
                {
                    "provider": "codex",
                    "external_id": "ext-new",
                    "updated_at": "2026-06-09T10:00:00Z",
                }
            ],
        )
    )

    response = client.get("/api/v1/forge/external-sessions", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert [item["external_id"] for item in payload] == ["ext-new", "ext-old"]
    assert payload[0]["instance_id"] == "beta"
    assert payload[1]["instance_id"] == "alpha"


@respx.mock
def test_external_sessions_returns_503_when_no_instance_supports_discovery() -> None:
    client = _client([_instance("alpha", base_url="http://alpha")])
    respx.get("http://alpha/api/v1/forge/external-sessions").mock(return_value=Response(503))

    response = client.get("/api/v1/forge/external-sessions", headers=_headers())

    assert response.status_code == 503


@respx.mock
def test_import_session_routes_to_default_instance() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha", is_default=True),
            _instance("beta", base_url="http://beta"),
        ]
    )
    route = respx.post("http://alpha/api/v1/forge/sessions/import").mock(
        return_value=Response(
            201,
            json={"id": "imported-1", "origin": "claude"},
        )
    )

    response = client.post(
        "/api/v1/forge/sessions/import",
        headers=_headers(),
        json={"provider": "claude-code", "external_id": "ext-1"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] == "imported-1"
    assert payload["instance_id"] == "alpha"
    assert route.called


@respx.mock
def test_feature_flags_proxies_to_default_instance() -> None:
    client = _client([_instance("alpha", base_url="http://alpha", is_default=True)])
    respx.get("http://alpha/api/v1/forge/feature-flags").mock(
        return_value=Response(
            200,
            json={"mini_mode": True, "local_mounts_allowed_prefixes": []},
        )
    )

    response = client.get("/api/v1/forge/feature-flags", headers=_headers())

    assert response.status_code == 200
    assert response.json()["mini_mode"] is True
