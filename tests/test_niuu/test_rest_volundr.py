"""Tests for registry-backed Volundr aggregate REST endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import ConnectError, ConnectTimeout, Response
from starlette.responses import Response as StarletteResponse

from identity.adapters.identity import AllowAllIdentityAdapter
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
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
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


DELIVERY_OPERATIONS = (
    "refs/resolve",
    "forge/reviews",
    "forge/branches",
    "workspaces/allocate",
    "workspaces/verify",
    "workspaces/integration/inspect",
    "workspaces/integration/inspect-chain",
    "evidence/policy",
    "evidence/validate",
    "workspaces/integrate",
    "forge/inspect",
    "forge/merge",
    "forge/reconcile",
)


def test_delivery_operation_dispatches_to_embedded_target_without_mutating_evidence() -> None:
    embedded = FastAPI()
    observed: dict[str, Any] = {}
    response_body = b'{"valid":true,"evidence":{"signature":"signed"}}'

    @embedded.post("/api/v1/forge/delivery/evidence/validate")
    async def validate_evidence(request: Request) -> StarletteResponse:
        observed["body"] = await request.body()
        observed["query"] = str(request.url.query)
        observed["authorization"] = request.headers.get("authorization")
        observed["user_id"] = request.headers.get("x-auth-user-id")
        return StarletteResponse(content=response_body, media_type="application/json")

    client = _client(
        [
            _instance(
                "local",
                base_url="embedded://local-forge",
                tags=["delivery"],
                config={"transport": "embedded"},
            )
        ],
        embedded_forge_app=embedded,
    )
    request_body = (
        b'{"evidence":{"payload":{"candidate_sha":"aaaaaaaa"},'
        b'"signature":"signed"},"policy_id":"strict"}'
    )

    response = client.post(
        "/api/v1/forge/delivery/evidence/validate?target_tags=delivery&target_match=all",
        headers={**_headers(), "content-type": "application/json"},
        content=request_body,
    )

    assert response.status_code == 200
    assert response.content == response_body
    assert observed == {
        "body": request_body,
        "query": "",
        "authorization": "Bearer test-token",
        "user_id": "user-a",
    }


@pytest.mark.parametrize("operation", DELIVERY_OPERATIONS)
@respx.mock
def test_delivery_typed_operations_proxy_to_selected_remote_instance(operation: str) -> None:
    client = _client(
        [
            _instance("default", base_url="http://default", is_default=True),
            _instance("target", base_url="http://target"),
        ]
    )
    status_code = 201 if operation == "workspaces/allocate" else 200
    upstream_body = b'{"evidence":{"signature":"upstream-signed"}}'
    route = respx.post(f"http://target/api/v1/forge/delivery/{operation}").mock(
        return_value=Response(
            status_code,
            content=upstream_body,
            headers={"content-type": "application/json"},
        )
    )

    response = client.post(
        f"/api/v1/forge/delivery/{operation}?instance_id=target",
        headers=_headers(),
        json={"operation": operation},
    )

    assert response.status_code == status_code
    assert response.content == upstream_body
    assert route.called
    request = route.calls.last.request
    assert request.url.query == b""
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["x-auth-user-id"] == "user-a"


@respx.mock
def test_delivery_proxy_preserves_upstream_authorization_failure() -> None:
    client = _client([_instance("target", base_url="http://target")])
    route = respx.post("http://target/api/v1/forge/delivery/refs/resolve").mock(
        return_value=Response(
            403,
            content=b'{"detail":"Delivery operation denied"}',
            headers={"content-type": "application/json"},
        )
    )

    response = client.post(
        "/api/v1/forge/delivery/refs/resolve?instance_id=target",
        headers=_headers(),
        json={"repository": "org/repo", "ref": "main"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Delivery operation denied"}
    assert route.called


@respx.mock
def test_delivery_proxy_rejects_inaccessible_instance_and_unknown_operation() -> None:
    client = _client([_instance("hidden", base_url="http://hidden", tenant_id="other")])

    inaccessible = client.post(
        "/api/v1/forge/delivery/refs/resolve?instance_id=hidden",
        headers=_headers(),
        json={"repository": "org/repo", "ref": "main"},
    )
    unknown = client.post(
        "/api/v1/forge/delivery/admin/reload?instance_id=hidden",
        headers=_headers(),
        json={},
    )

    assert inaccessible.status_code == 404
    assert inaccessible.json()["detail"] == "Target not found: hidden"
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Unknown delivery operation"
    assert len(respx.calls) == 0


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


def test_resident_control_dispatches_through_embedded_target() -> None:
    embedded = FastAPI()
    runtime = {
        "id": "resident-1",
        "name": "Local NemoClaw",
        "backend": "local",
        "engine": "openclaw",
    }

    @embedded.get("/api/v1/forge/resident-profiles")
    async def profiles() -> list[dict[str, Any]]:
        return [{"id": "nemoclaw-local", "backend": "local", "engine": "openclaw"}]

    @embedded.get("/api/v1/forge/resident-runtimes")
    async def runtimes() -> list[dict[str, Any]]:
        return [runtime]

    @embedded.post("/api/v1/forge/resident-runtimes", status_code=201)
    async def create_runtime(body: dict[str, Any]) -> dict[str, Any]:
        assert body["profile_id"] == "nemoclaw-local"
        assert "instance_id" not in body
        return runtime

    @embedded.get("/api/v1/forge/resident-runtimes/{runtime_id}")
    async def get_runtime(runtime_id: str) -> dict[str, Any]:
        assert runtime_id == runtime["id"]
        return runtime

    @embedded.post("/api/v1/forge/resident-runtimes/{runtime_id}/restart")
    async def restart_runtime(runtime_id: str) -> dict[str, Any]:
        return {**runtime, "id": runtime_id, "observed_state": "active"}

    @embedded.get("/api/v1/forge/resident-runtimes/{runtime_id}/sessions")
    async def list_native_sessions(runtime_id: str) -> list[dict[str, Any]]:
        return [{"id": "session-1", "resident_id": runtime_id}]

    @embedded.post("/api/v1/forge/resident-runtimes/{runtime_id}/sessions", status_code=201)
    async def create_native_session(runtime_id: str) -> dict[str, Any]:
        return {"id": "session-2", "resident_id": runtime_id}

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

    profiles_response = client.get("/api/v1/forge/resident-profiles", headers=_headers())
    assert profiles_response.status_code == 200
    assert profiles_response.json()[0]["instance_id"] == "local"
    assert client.get("/api/v1/forge/resident-runtimes", headers=_headers()).status_code == 200
    created = client.post(
        "/api/v1/forge/resident-runtimes",
        headers=_headers(),
        json={"profile_id": "nemoclaw-local", "name": "Nemo", "instance_id": "local"},
    )
    assert created.status_code == 201
    assert created.json()["instance_id"] == "local"
    assert (
        client.post(
            "/api/v1/forge/resident-runtimes/resident-1/restart",
            headers=_headers(),
        ).status_code
        == 200
    )
    sessions = client.get(
        "/api/v1/forge/resident-runtimes/resident-1/sessions",
        headers=_headers(),
    )
    assert sessions.json()[0]["instance_id"] == "local"
    created_session = client.post(
        "/api/v1/forge/resident-runtimes/resident-1/sessions",
        headers=_headers(),
        json={"title": "New session"},
    )
    assert created_session.status_code == 201
    assert created_session.json()["instance_id"] == "local"


def test_upstream_error_detail_is_forwarded_without_a_second_wrapper() -> None:
    """A Forge 409 arrives as its own message, not as a JSON body inside detail."""
    from fastapi import HTTPException

    from niuu.adapters.inbound.rest_volundr import _ensure_remote_success

    body = {"detail": "No session slot is free: 4 of 4 sessions are running on this host."}
    response = httpx.Response(409, json=body, request=httpx.Request("POST", "http://forge/x"))
    with pytest.raises(HTTPException) as info:
        _ensure_remote_success(response)
    assert info.value.status_code == 409
    assert info.value.detail == body["detail"]

    plain = httpx.Response(502, text="Bad Gateway", request=httpx.Request("GET", "http://f/y"))
    with pytest.raises(HTTPException) as info:
        _ensure_remote_success(plain)
    assert info.value.detail == "Bad Gateway"

    empty = httpx.Response(503, request=httpx.Request("GET", "http://f/z"))
    with pytest.raises(HTTPException) as info:
        _ensure_remote_success(empty)
    assert info.value.detail == "Service Unavailable"

    _ensure_remote_success(httpx.Response(200, request=httpx.Request("GET", "http://f/ok")))


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


@pytest.mark.parametrize("resource", ["sessions", "resident-runtimes"])
@respx.mock
def test_owner_lookup_finds_resource_despite_unreachable_instance(resource: str) -> None:
    client = _client(
        [
            _instance("offline", base_url="http://offline"),
            _instance("noatun", base_url="http://noatun"),
        ]
    )
    respx.get(f"http://offline/api/v1/forge/{resource}/s2").mock(
        side_effect=ConnectTimeout("offline")
    )
    respx.get(f"http://noatun/api/v1/forge/{resource}/s2").mock(
        return_value=Response(200, json={"id": "s2", "status": "running"})
    )
    response = client.get(f"/api/v1/forge/{resource}/s2", headers=_headers())
    assert response.status_code == 200
    assert response.json()["instance_id"] == "noatun"


@respx.mock
def test_incomplete_owner_lookup_is_not_reported_as_missing() -> None:
    client = _client(
        [
            _instance("offline", base_url="http://offline"),
            _instance("noatun", base_url="http://noatun"),
        ]
    )
    respx.get("http://offline/api/v1/forge/sessions/s2").mock(side_effect=ConnectTimeout("offline"))
    respx.get("http://noatun/api/v1/forge/sessions/s2").mock(return_value=Response(404))
    response = client.get("/api/v1/forge/sessions/s2", headers=_headers())
    assert response.status_code == 502


async def test_owner_lookup_cancels_stalled_probes_after_finding_owner(monkeypatch) -> None:
    import asyncio

    from fastapi import Request

    from niuu.adapters.inbound import rest_volundr

    cancelled = asyncio.Event()

    async def probe(instance, request, **kwargs):
        if instance.id == "offline":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return Response(200, json={"id": "s2"})

    monkeypatch.setattr(rest_volundr, "_request_remote", probe)
    service = StubInstanceService(
        [
            _instance("offline", base_url="http://offline"),
            _instance("noatun", base_url="http://noatun"),
        ]
    )
    principal = Principal(user_id="user-a", email="", tenant_id="tenant-a", roles=[])
    owner, _ = await asyncio.wait_for(
        rest_volundr._find_session_owner(
            service, principal, Request({"type": "http", "query_string": b""}), "s2"
        ),
        timeout=1,
    )
    assert owner.id == "noatun"
    assert cancelled.is_set()


@respx.mock
def test_get_session_rebases_target_relative_chat_endpoint() -> None:
    client = _client([_instance("noatun", base_url="https://niuu.noatun.asgard.niuu.world")])
    respx.get("https://niuu.noatun.asgard.niuu.world/api/v1/forge/sessions/s2").mock(
        return_value=Response(
            200,
            json={
                "id": "s2",
                "status": "running",
                "chat_endpoint": "/s/s2/session",
            },
        )
    )

    response = client.get("/api/v1/forge/sessions/s2", headers=_headers())

    assert response.status_code == 200
    assert response.json()["chat_endpoint"] == ("wss://niuu.noatun.asgard.niuu.world/s/s2/session")


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


@pytest.mark.parametrize("archived", [False, True])
@respx.mock
def test_selected_host_uses_its_own_registry_and_preserves_filters(archived: bool) -> None:
    """Two real router layers with independent registry IDs, as on Thor/Build Bro."""
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def local_sessions(status: str | None = None) -> list[dict[str, Any]]:
        return [{"id": "bro-session", "status": status or "running"}]

    remote = _client(
        [_instance("bro-local-id", base_url="embedded://local", config={"transport": "embedded"})],
        embedded_forge_app=embedded,
    )
    outer = _client(
        [
            _instance("thor-bro-id", base_url="http://build-bro"),
            _instance("unreachable", base_url="http://unreachable"),
        ]
    )

    def downstream(request):
        response = remote.get(request.url.raw_path.decode(), headers=_headers())
        return Response(response.status_code, json=response.json())

    route = respx.get("http://build-bro/api/v1/forge/sessions").mock(side_effect=downstream)
    suffix = "&status=archived" if archived else ""
    response = outer.get(
        "/api/v1/forge/sessions?instance_id=thor-bro-id&tag=a&tag=b" + suffix,
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()[0]["id"] == "bro-session"
    assert response.json()[0]["instance_id"] == "thor-bro-id"
    assert response.json()[0]["status"] == ("archived" if archived else "running")
    assert len(route.calls) == 1
    assert "instance_id" not in route.calls.last.request.url.params
    assert route.calls.last.request.url.params.get_list("tag") == ["a", "b"]
    assert len(respx.calls) == 1  # An unrelated unavailable host is never contacted.


@pytest.mark.parametrize("status_code", [403, 404, 503])
@respx.mock
def test_selected_session_inventory_preserves_remote_http_failures(status_code: int) -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    respx.get("http://bro/api/v1/forge/sessions").respond(
        status_code, json={"detail": "Target is unavailable"}
    )
    response = client.get("/api/v1/forge/sessions?instance_id=bro", headers=_headers())
    assert response.status_code == status_code
    assert "Target is unavailable" in response.json()["detail"]


@respx.mock
def test_selected_session_inventory_reports_transport_failure() -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    respx.get("http://bro/api/v1/forge/sessions").mock(side_effect=ConnectError("Offline"))
    response = client.get("/api/v1/forge/sessions?instance_id=bro", headers=_headers())
    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]


@pytest.mark.parametrize("payload", [{}, ["invalid"], [{"status": "running"}]])
@respx.mock
def test_selected_session_inventory_rejects_malformed_responses(payload: Any) -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    respx.get("http://bro/api/v1/forge/sessions").respond(200, json=payload)
    response = client.get("/api/v1/forge/sessions?instance_id=bro", headers=_headers())
    assert response.status_code == 502


@respx.mock
def test_selected_session_inventory_rejects_html_response() -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    respx.get("http://bro/api/v1/forge/sessions").respond(200, text="<html>Login</html>")
    response = client.get("/api/v1/forge/sessions?instance_id=bro", headers=_headers())
    assert response.status_code == 502


def test_selected_session_inventory_reports_missing_embedded_app() -> None:
    client = _client(
        [_instance("local", base_url="embedded://local", config={"transport": "embedded"})]
    )
    response = client.get("/api/v1/forge/sessions?instance_id=local", headers=_headers())
    assert response.status_code == 502
    assert "Embedded Forge" in response.json()["detail"]


@respx.mock
def test_selected_session_inventory_rejects_hidden_target_without_contacting_it() -> None:
    client = _client([_instance("private", base_url="http://private", tenant_id="other")])
    response = client.get("/api/v1/forge/sessions?instance_id=private", headers=_headers())
    assert response.status_code == 404
    assert len(respx.calls) == 0


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
def test_create_session_syncs_custom_persona_to_selected_target() -> None:
    embedded = FastAPI()
    embedded.state.persona_registry = SimpleNamespace(
        get_persona=AsyncMock(
            return_value=SimpleNamespace(
                has_override=True,
                payload={"name": "custom-reviewer", "system_prompt_template": "Review"},
            )
        )
    )
    client = _client(
        [_instance("target", base_url="http://target", is_default=True)],
        embedded_forge_app=embedded,
    )
    respx.get("http://target/api/v1/personas/custom-reviewer").mock(return_value=Response(404))
    sync = respx.post("http://target/api/v1/personas").mock(
        return_value=Response(201, json={"name": "custom-reviewer"})
    )
    launch = respx.post("http://target/api/v1/forge/sessions").mock(
        return_value=Response(201, json={"id": "s-persona"})
    )

    response = client.post(
        "/api/v1/forge/sessions",
        headers=_headers(),
        json={"name": "review-session", "persona_name": "custom-reviewer"},
    )

    assert response.status_code == 201
    assert sync.called
    assert launch.called
    embedded.state.persona_registry.get_persona.assert_awaited_once_with(
        "user-a", "custom-reviewer"
    )


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
                "sessions_today": 1,
                "tokens_today": 10,
                "localTokens": 3,
                "cloud_tokens": 7,
                "costToday": 1.25,
                "sparklines": {"tokens": [1, 2], "cost": [0.5, 0.75], "sessionsToday": [1, 0]},
            },
        )
    )
    respx.get("http://beta/api/v1/forge/stats").mock(
        return_value=Response(
            200,
            json={
                "activeSessions": 4,
                "total_sessions": 6,
                "sessionsToday": 2,
                "tokensToday": 20,
                "local_tokens": 5,
                "cloudTokens": 9,
                "cost_today": 2.5,
                "sparklines": {"tokens": [3, 4, 5], "sessionsToday": [0, 2, 1]},
            },
        )
    )

    response = client.get("/api/v1/forge/stats", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "active_sessions": 6,
        "total_sessions": 11,
        "sessions_today": 3,
        "tokens_today": 30,
        "local_tokens": 8,
        "cloud_tokens": 16,
        "cost_today": 3.75,
        "sparklines": {
            "tokens": [4.0, 6.0, 5.0],
            "cost": [0.5, 0.75],
            "sessionsToday": [1.0, 2.0, 1.0],
        },
    }


@pytest.mark.parametrize(
    "endpoint,remote_path",
    [("stats", "/api/v1/forge/stats"), ("cluster/resources", "/api/v1/volundr/resources")],
)
@respx.mock
def test_dashboard_reads_target_only_and_preserve_remote_failures(
    endpoint: str, remote_path: str
) -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("offline", base_url="http://offline"),
        ]
    )
    route = respx.get(f"http://alpha{remote_path}").mock(
        return_value=Response(200, json={"tokens_today": 42, "nodes": []})
    )
    response = client.get(f"/api/v1/forge/{endpoint}?instance_id=alpha", headers=_headers())
    assert response.status_code == 200
    assert len(respx.calls) == 1
    if endpoint == "stats":
        assert response.json()["tokens_today"] == 42
        assert response.json()["instance_id"] == "alpha"
    else:
        assert response.json()["instances"][0]["id"] == "alpha"
    route.mock(return_value=Response(503, text="Host unavailable"))
    response = client.get(f"/api/v1/forge/{endpoint}?instance_id=alpha", headers=_headers())
    assert response.status_code == 503
    assert response.json()["detail"] == "Host unavailable"
    route.mock(return_value=Response(200, json=[]))
    assert (
        client.get(f"/api/v1/forge/{endpoint}?instance_id=alpha", headers=_headers()).status_code
        == 502
    )


@pytest.mark.parametrize("endpoint", ["stats", "cluster/resources"])
@respx.mock
def test_dashboard_target_must_be_visible(endpoint: str) -> None:
    client = _client([_instance("private", base_url="http://private", tenant_id="other")])
    response = client.get(f"/api/v1/forge/{endpoint}?instance_id=private", headers=_headers())
    assert response.status_code == 404
    assert len(respx.calls) == 0


@respx.mock
def test_get_cluster_resources_returns_camel_and_snake_resource_keys() -> None:
    client = _client([_instance("alpha", base_url="http://alpha")])
    respx.get("http://alpha/api/v1/volundr/resources").mock(
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
    assert payload["instances"][0]["slug"] == "alpha"
    assert payload["nodes"][0]["name"] == "alpha/node-a"
    assert payload["nodes"][0]["instance_slug"] == "alpha"


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
            "/sessions/s2/workflow/gates",
            "/sessions/s2/workflow/gates",
            {"gates": [{"id": "approval"}]},
            200,
            {"gates": [{"id": "approval"}]},
        ),
        (
            "get",
            "/sessions/s2/trace",
            "/sessions/s2/trace",
            {"spans": [{"id": "span-1"}], "lanes": [{"key": "skuld"}]},
            200,
            {"spans": [{"id": "span-1"}], "lanes": [{"key": "skuld"}]},
        ),
        (
            "get",
            "/sessions/s2/trace/summary",
            "/sessions/s2/trace/summary",
            {"turn_count": 1, "tool_call_count": 2},
            200,
            {"turn_count": 1, "tool_call_count": 2},
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


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        ("trace", {"spans": [{"id": "span-1"}], "lanes": []}),
        ("trace/summary", {"turn_count": 1, "tool_call_count": 0}),
    ],
)
@respx.mock
def test_trace_routes_fall_back_to_resident_owner(suffix: str, payload: dict[str, Any]) -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha"),
            _instance("beta", base_url="http://beta"),
        ]
    )
    for base_url in ("http://alpha", "http://beta"):
        respx.get(f"{base_url}/api/v1/forge/sessions/resident-1").mock(return_value=Response(404))
    respx.get("http://alpha/api/v1/forge/resident-runtimes/resident-1").mock(
        return_value=Response(404)
    )
    respx.get("http://beta/api/v1/forge/resident-runtimes/resident-1").mock(
        return_value=Response(200, json={"id": "resident-1", "name": "Hermes"})
    )
    route = respx.get(f"http://beta/api/v1/forge/sessions/resident-1/{suffix}").mock(
        return_value=Response(200, json=payload)
    )

    response = client.get(
        f"/api/v1/forge/sessions/resident-1/{suffix}",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == payload
    assert route.called


@respx.mock
def test_resolve_workflow_gate_proxies_to_owner_with_encoded_gate_id_and_intent() -> None:
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "Session 2"})
    )
    route = respx.post(
        "http://beta/api/v1/forge/sessions/s2/workflow/gates/prd%20review%3Fstep%3D1/resolve"
    ).mock(return_value=Response(200, json={"status": "resolved"}))

    response = client.post(
        "/api/v1/forge/sessions/s2/workflow/gates/prd%20review%3Fstep%3D1/resolve",
        headers={**_headers(), "x-niuu-workflow-gate-intent": "resolve"},
        json={"decision": "approved", "notes": "looks good"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "resolved"}
    assert route.called
    assert route.calls.last.request.headers["x-niuu-workflow-gate-intent"] == "resolve"
    import json as _json

    assert _json.loads(route.calls.last.request.content) == {
        "decision": "approved",
        "notes": "looks good",
    }


@respx.mock
def test_update_session_proxies_put_rename_to_owner_with_body() -> None:
    # PUT /sessions/{id} (rename/update, contract §2.1) — the aggregate only
    # registered GET/DELETE on this path, so web + iOS renames 405'd.
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "old-name"})
    )
    route = respx.put("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "new-name"})
    )

    response = client.put(
        "/api/v1/forge/sessions/s2",
        headers=_headers(),
        json={"name": "new-name"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "new-name"
    assert payload["instance_id"] == "beta"
    assert route.called
    import json as _json

    assert _json.loads(route.calls.last.request.content) == {"name": "new-name"}


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
    capabilities = response.json()["capabilities"]
    assert capabilities["message_delivery"] is True
    assert capabilities["chronicles"] is False
    assert capabilities["session_events"] is False


@respx.mock
def test_send_preserves_pending_http_status_and_client_request_identity() -> None:
    client = _client([_instance("alpha", base_url="http://alpha")])
    respx.get("http://alpha/api/v1/forge/sessions/s1").mock(
        return_value=Response(200, json={"id": "s1"})
    )
    payload = {"request_id": "client-1", "status": "pending", "delivery": "pending"}
    route = respx.post("http://alpha/api/v1/forge/sessions/s1/messages").mock(
        return_value=Response(202, json=payload)
    )
    response = client.post(
        "/api/v1/forge/sessions/s1/messages",
        headers=_headers(),
        json={"content": "hello", "request_id": "client-1"},
    )
    assert response.status_code == 202
    assert response.json() == payload
    import json

    assert json.loads(route.calls.last.request.content)["request_id"] == "client-1"


@respx.mock
@pytest.mark.parametrize("operation", [None, "claim", "settle"])
def test_delivery_state_routes_reach_session_owner(operation) -> None:
    client = _client([_instance("alpha", base_url="http://alpha")])
    respx.get("http://alpha/api/v1/forge/sessions/s1").mock(
        return_value=Response(200, json={"id": "s1"})
    )
    path = "/api/v1/forge/sessions/s1/message-deliveries/req-1"
    method = "GET" if operation is None else "POST"
    if operation:
        path += f"/{operation}"
    route = respx.request(method, f"http://alpha{path}").mock(
        return_value=Response(200, json={"request_id": "req-1", "status": "pending"})
    )
    response = client.request(method, path, headers=_headers(), json={} if operation else None)
    assert response.status_code == 200
    assert route.called
    assert response.json()["request_id"] == "req-1"


@respx.mock
def test_delivery_get_query_cannot_change_the_forwarded_path() -> None:
    client = _client([_instance("alpha", base_url="http://alpha")])
    respx.get("http://alpha/api/v1/forge/sessions/s1").mock(
        return_value=Response(200, json={"id": "s1"})
    )
    path = "/api/v1/forge/sessions/s1/message-deliveries/req-1"
    route = respx.get(f"http://alpha{path}").mock(
        return_value=Response(200, json={"request_id": "req-1", "status": "pending"})
    )
    response = client.get(
        path, headers=_headers(), params={"operation": "../../../../feature-flags"}
    )
    assert response.status_code == 200
    assert route.called
    response = client.post(path + "/claim", headers=_headers(), content="not JSON")
    assert response.status_code == 422


@respx.mock
def test_activity_report_proxies_to_owner_with_body() -> None:
    """Broker activity heartbeats (incl. cli_session_id for resume) must reach
    the owning instance through the aggregate, not 404 at the gateway."""
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2"})
    )
    route = respx.post("http://beta/api/v1/forge/sessions/s2/activity").mock(
        return_value=Response(204)
    )

    response = client.post(
        "/api/v1/forge/sessions/s2/activity",
        headers=_headers(),
        json={"state": "active", "metadata": {"cli_session_id": "sess-1"}},
    )

    assert response.status_code == 204
    assert route.called
    import json as _json

    sent = _json.loads(route.calls.last.request.content)
    assert sent["metadata"]["cli_session_id"] == "sess-1"


@respx.mock
def test_usage_report_proxies_to_owner() -> None:
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2"})
    )
    route = respx.post("http://beta/api/v1/forge/sessions/s2/usage").mock(
        return_value=Response(201, json={"recorded": True})
    )

    response = client.post(
        "/api/v1/forge/sessions/s2/usage",
        headers=_headers(),
        json={"tokens": 1200, "model": "claude-opus-4-8"},
    )

    assert response.status_code == 201
    assert response.json()["recorded"] is True
    assert route.called


@respx.mock
def test_chronicle_timeline_post_proxies_to_owner() -> None:
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2"})
    )
    route = respx.post("http://beta/api/v1/forge/chronicles/s2/timeline").mock(
        return_value=Response(201, json={"recorded": True})
    )

    response = client.post(
        "/api/v1/forge/chronicles/s2/timeline",
        headers=_headers(),
        json={"event": "message_user"},
    )

    assert response.status_code == 201
    assert route.called


@respx.mock
def test_span_and_event_telemetry_forward_to_default_instance() -> None:
    client = _client([_instance("alpha", base_url="http://alpha", is_default=True)])
    start = respx.post("http://alpha/api/v1/forge/spans/start").mock(
        return_value=Response(201, json={"span_id": "sp-1"})
    )
    complete = respx.post("http://alpha/api/v1/forge/spans/complete").mock(
        return_value=Response(201, json={"ok": True})
    )
    finish = respx.post("http://alpha/api/v1/forge/spans/sp-1/finish").mock(
        return_value=Response(200, json={"ok": True})
    )
    events = respx.post("http://alpha/api/v1/forge/events").mock(
        return_value=Response(201, json={"ok": True})
    )

    assert client.post("/api/v1/forge/spans/start", headers=_headers(), json={}).status_code == 201
    assert (
        client.post("/api/v1/forge/spans/complete", headers=_headers(), json={}).status_code == 201
    )
    assert (
        client.post("/api/v1/forge/spans/sp-1/finish", headers=_headers(), json={}).status_code
        == 200
    )
    assert client.post("/api/v1/forge/events", headers=_headers(), json={}).status_code == 201
    assert start.called and complete.called and finish.called and events.called


def test_sse_stream_embedded_without_broadcaster_returns_503() -> None:
    embedded = FastAPI()
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

    response = client.get("/api/v1/forge/sessions/stream", headers=_headers())

    assert response.status_code == 503


@pytest.mark.parametrize(
    "query, expected",
    [("?all_instances=true", ["local", "bro"]), ("?instance_id=bro", ["bro"]), ("", ["local"])],
)
@respx.mock
def test_session_stream_scopes_visible_hosts_and_stamps_owning_instance(
    monkeypatch, query, expected
):
    from niuu.adapters.inbound import rest_volundr

    embedded = FastAPI()

    async def subscribe():
        yield SimpleNamespace(
            type=SimpleNamespace(value="session_activity"),
            data={"session_id": "local-session", "state": "idle"},
        )

    embedded.state.broadcaster = SimpleNamespace(subscribe=subscribe)
    remote = respx.get("http://bro/api/v1/forge/sessions/stream").mock(
        return_value=Response(
            200,
            text='event: session_activity\ndata: {"session_id":"bro-session","state":"active"}\n\n',
        )
    )
    captured = []

    async def finite_merge(sources, **_kwargs):
        captured.extend(sources)
        for source in sources.values():
            reader = source()
            try:
                name, data = await anext(reader)
                yield f"{name}:{data['instance_id']}:{data['session_id']}\n".encode()
            finally:
                await reader.aclose()

    monkeypatch.setattr(rest_volundr, "merge_events", finite_merge)
    client = _client(
        [
            _instance(
                "local",
                base_url="embedded://local",
                is_default=True,
                config={"transport": "embedded"},
            ),
            _instance("bro", base_url="http://bro"),
            _instance("private", base_url="http://private", tenant_id="other"),
            _instance("disabled", base_url="http://disabled", enabled=False),
        ],
        embedded_forge_app=embedded,
    )
    response = client.get("/api/v1/forge/sessions/stream" + query, headers=_headers())
    assert response.status_code == 200
    assert captured == expected
    for host in expected:
        assert f"session_activity:{host}:{host}-session" in response.text
    if "bro" in expected:
        assert remote.calls[0].request.url.query == b""  # No recursive fleet fan-out.
        assert remote.calls[0].request.headers["x-auth-user-id"] == "user-a"


@respx.mock
def test_event_log_proxies_forward_to_owner() -> None:
    """Broker log ingest + cursor replay route through the aggregate."""
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2"})
    )
    ingest = respx.post("http://beta/api/v1/forge/sessions/s2/log").mock(
        return_value=Response(201, json={"appended": 2})
    )
    head = respx.get("http://beta/api/v1/forge/sessions/s2/log/head").mock(
        return_value=Response(200, json={"latest_seq": 41})
    )

    assert (
        client.post(
            "/api/v1/forge/sessions/s2/log",
            headers=_headers(),
            json={"entries": [{"seq": 40}, {"seq": 41}]},
        ).status_code
        == 201
    )
    assert (
        client.get("/api/v1/forge/sessions/s2/log/head", headers=_headers()).json()["latest_seq"]
        == 41
    )
    assert ingest.called and head.called


@respx.mock
def test_event_log_replay_passes_list_through_verbatim() -> None:
    """The replay endpoint returns a LIST — coercing it to a dict previously
    discarded the entire transcript."""
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2"})
    )
    respx.get("http://beta/api/v1/forge/sessions/s2/log").mock(
        return_value=Response(
            200,
            json=[{"seq": 1, "kind": "assistant"}, {"seq": 2, "kind": "result"}],
        )
    )

    payload = client.get(
        "/api/v1/forge/sessions/s2/log",
        headers=_headers(),
        params={"after": 0},
    ).json()

    assert isinstance(payload, list)
    assert [entry["seq"] for entry in payload] == [1, 2]


@respx.mock
def test_event_log_page_forwards_cursor_visibility_and_authorization() -> None:
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2"})
    )
    page = respx.get(
        "http://beta/api/v1/forge/sessions/s2/log/page",
        params={"after": "3", "limit": "25", "show_internal": "false"},
    ).mock(
        return_value=Response(
            200,
            json={"entries": [], "scannedThrough": 28, "hasMore": True},
        )
    )

    response = client.get(
        "/api/v1/forge/sessions/s2/log/page",
        headers=_headers(),
        params={"after": 3, "limit": 25, "show_internal": "false"},
    )

    assert response.status_code == 200
    assert response.json() == {"entries": [], "scannedThrough": 28, "hasMore": True}
    assert page.called
    assert page.calls[0].request.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_event_log_page_preserves_upstream_authorization_failure() -> None:
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2"})
    )
    respx.get("http://beta/api/v1/forge/sessions/s2/log/page").mock(
        return_value=Response(403, json={"detail": "Session log access denied"})
    )

    response = client.get(
        "/api/v1/forge/sessions/s2/log/page",
        headers=_headers(),
        params={"after": 0, "limit": 25, "show_internal": "false"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Session log access denied"}


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@respx.mock
def test_home_storage_routes_only_to_selected_visible_cluster(method):
    instances = [
        _instance("a", base_url="https://a.test"),
        _instance("b", base_url="https://b.test"),
    ]
    route = respx.route(
        method=method, url="https://b.test/api/v1/forge/storage/home?path=tmp%2Fcache"
    ).mock(return_value=Response(200, json={"status": "ready"}))
    response = _client(instances).request(
        method, "/api/v1/forge/storage/home?instance_id=b&path=tmp%2Fcache", headers=_headers()
    )
    assert response.status_code == 200
    assert route.called
    assert route.calls[0].request.headers["authorization"] == "Bearer test-token"
    assert (
        _client(instances)
        .request(method, "/api/v1/forge/storage/home?instance_id=hidden", headers=_headers())
        .status_code
        == 404
    )


@respx.mock
def test_feature_flags_select_requested_instance() -> None:
    client = _client(
        [
            _instance("alpha", base_url="http://alpha", is_default=True),
            _instance("beta", base_url="http://beta"),
        ]
    )
    route = respx.get("http://beta/api/v1/forge/feature-flags").mock(
        return_value=Response(200, json={"mini_mode": False, "local_mounts_enabled": False})
    )
    response = client.get("/api/v1/forge/feature-flags?instance_id=beta", headers=_headers())
    assert response.status_code == 200
    assert response.json()["mini_mode"] is False
    assert route.called


def test_feature_flags_reject_invisible_instance() -> None:
    client = _client([_instance("private", base_url="http://private", tenant_id="other")])
    response = client.get("/api/v1/forge/feature-flags?instance_id=private", headers=_headers())
    assert response.status_code == 404


@respx.mock
def test_tool_result_preview_byte_proxies_jpeg_from_owner() -> None:
    """The aggregate preview route forwards RAW image bytes (not JSON) from the
    owning instance, preserving content-type and the immutable cache-control the
    volundr tier stamps — the phone's URLSession caches on it."""
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "Session 2"})
    )
    jpeg_bytes = b"\xff\xd8\xff\xe0fake-jpeg-payload\xff\xd9"
    route = respx.get("http://beta/api/v1/forge/sessions/s2/tool-result/tu-img/preview").mock(
        return_value=Response(
            200,
            content=jpeg_bytes,
            headers={
                "content-type": "image/jpeg",
                "cache-control": "public, max-age=31536000, immutable",
            },
        )
    )

    response = client.get(
        "/api/v1/forge/sessions/s2/tool-result/tu-img/preview",
        headers=_headers(),
        params={"image_index": 1},
    )

    assert response.status_code == 200
    assert response.content == jpeg_bytes
    assert response.headers["content-type"] == "image/jpeg"
    assert "immutable" in response.headers["cache-control"]
    assert route.called
    assert route.calls[0].request.url.params["image_index"] == "1"


@respx.mock
def test_tool_result_preview_passes_owner_404_through() -> None:
    """A non-image tool_result 404s at the volundr tier; the aggregate must pass
    that through (NOT wrap it in a 502) so the client falls back to the full
    tool-result fetch."""
    client = _client([_instance("beta", base_url="http://beta")])
    respx.get("http://beta/api/v1/forge/sessions/s2").mock(
        return_value=Response(200, json={"id": "s2", "name": "Session 2"})
    )
    respx.get("http://beta/api/v1/forge/sessions/s2/tool-result/tu-text/preview").mock(
        return_value=Response(404, json={"detail": "tool_result is not an image"})
    )

    response = client.get(
        "/api/v1/forge/sessions/s2/tool-result/tu-text/preview",
        headers=_headers(),
    )

    assert response.status_code == 404


@respx.mock
def test_native_history_import_routes_to_owner_with_auth() -> None:
    instance = _instance("owner", base_url="https://forge.example")
    session_id = "b2a2aee6-f8ca-41ea-ae3a-bd9fa392c8e1"
    respx.get(f"https://forge.example/api/v1/forge/sessions/{session_id}").mock(
        return_value=Response(200, json={"id": session_id, "status": "stopped"})
    )
    import_route = respx.post(
        f"https://forge.example/api/v1/forge/sessions/{session_id}/history/import"
    ).mock(return_value=Response(200, json={"session_id": session_id, "imported_frames": 19}))
    client = _client([instance])
    response = client.post(
        f"/api/v1/forge/sessions/{session_id}/history/import", headers=_headers()
    )
    assert response.status_code == 200
    assert response.json()["imported_frames"] == 19
    assert response.json()["instance_id"] == "owner"
    assert import_route.calls[0].request.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_native_history_import_preserves_conflict_response() -> None:
    instance = _instance("owner", base_url="https://forge.example")
    session_id = "b2a2aee6-f8ca-41ea-ae3a-bd9fa392c8e1"
    respx.get(f"https://forge.example/api/v1/forge/sessions/{session_id}").mock(
        return_value=Response(200, json={"id": session_id, "status": "running"})
    )
    respx.post(f"https://forge.example/api/v1/forge/sessions/{session_id}/history/import").mock(
        return_value=Response(409, json={"detail": "Session must be stopped before import"})
    )
    response = _client([instance]).post(
        f"/api/v1/forge/sessions/{session_id}/history/import", headers=_headers()
    )
    assert response.status_code == 409
    assert "stopped" in response.json()["detail"]


@pytest.mark.parametrize("method,status_code", [("GET", 200), ("PATCH", 200), ("PATCH", 409)])
def test_read_state_is_forwarded_to_owning_instance(method, status_code):
    instance = _instance("local", base_url="https://forge.example.test", is_default=True)
    client = _client([instance])
    state = {
        "latest_output_seq": 20,
        "read_through_seq": 10,
        "revision": 1,
        "manually_unread": False,
        "is_unread": True,
    }
    body = {"state": "read", "through_seq": 10, "expected_revision": 0}
    with respx.mock:
        respx.get("https://forge.example.test/api/v1/forge/sessions/s1").mock(
            return_value=Response(200, json={"id": "s1", "name": "Session", "status": "running"})
        )
        route = respx.route(
            method=method, url="https://forge.example.test/api/v1/forge/sessions/s1/read-state"
        ).mock(
            return_value=Response(
                status_code, json=state if status_code == 200 else {"detail": "Read state changed"}
            )
        )
        response = client.request(
            method,
            "/api/v1/forge/sessions/s1/read-state",
            headers=_headers(),
            **({"json": body} if method == "PATCH" else {}),
        )
    assert response.status_code == status_code
    assert route.called
    assert route.calls[0].request.headers["x-auth-user-id"] == "user-a"
    if method == "PATCH":
        import json

        assert json.loads(route.calls[0].request.content) == body
