"""Guild facade for Forge push devices: every visible node holds an alert token; the
gateway-only kinds and the push routes reach the push node."""

from __future__ import annotations

from typing import Any

import pytest
import respx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ConnectError, Response

from niuu.domain.services.forge_session_policy import policy_refusal
from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
)
from tests.test_niuu.test_rest_volundr import _client, _headers, _instance

KNOWN_FORGE_SESSION_SCOPES = (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
)

URL = "/api/v1/forge/devices"
BODY = {"platform": "ios", "token": "tok-1", "app_bundle_id": "com.niuu.lexi"}

LOCAL = _instance(
    "local", base_url="embedded://local", is_default=True, config={"transport": "embedded"}
)
#: The same node, marked in the registry as the Guild's push gateway.
GATEWAY = _instance(
    "local",
    base_url="embedded://local",
    config={"transport": "embedded", "push_gateway": True},
)
REMOTE_A = _instance("remote-a", base_url="http://remote-a")
REMOTE_B = _instance("remote-b", base_url="http://remote-b")
REMOTE = _instance("remote", base_url="http://remote")
GATEWAY_BODY = {
    "platform": "ios",
    "token": "0a1b2c3d",
    "kind": "live_activity",
    "environment": "sandbox",
    "app_bundle_id": "com.xteo.lexi.chat",
    "activity_id": "ACT-1",
}


def _device(token: str, device_id: str) -> dict[str, Any]:
    return {
        "id": device_id,
        "platform": "ios",
        "token": token,
        "app_bundle_id": "com.niuu.lexi",
        "created_at": "2026-10-28T10:00:00+00:00",
        "updated_at": "2026-10-28T10:00:00+00:00",
    }


def _embedded(calls: list[tuple[str, str, Any]] | None = None) -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/forge/devices", status_code=201)
    async def register(request: Request, body: dict):
        if calls is not None:
            calls.append(("POST", "/devices", body))
        return _device(body["token"], "local-id")

    @app.get("/api/v1/forge/devices")
    async def listing():
        return [_device("tok-1", "local-1"), _device("tok-local", "local-2")]

    @app.delete("/api/v1/forge/devices/{token}", status_code=204)
    async def unregister(token: str):
        if calls is not None:
            calls.append(("DELETE", f"/devices/{token}", None))
        return None

    return app


def _embedded_refusing(status_code: int, detail: str) -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/forge/devices")
    async def register(body: dict):
        return JSONResponse({"detail": detail}, status_code=status_code)

    return app


@respx.mock
def test_register_fans_out_and_answers_201_when_every_node_accepts():
    calls: list = []
    a = respx.post("http://remote-a/api/v1/forge/devices").mock(
        return_value=Response(201, json=_device("tok-1", "a-id"))
    )
    b = respx.post("http://remote-b/api/v1/forge/devices").mock(
        return_value=Response(201, json=_device("tok-1", "b-id"))
    )
    client = _client([LOCAL, REMOTE_A, REMOTE_B], embedded_forge_app=_embedded(calls))

    response = client.post(URL, json=BODY, headers=_headers())

    assert response.status_code == 201
    assert response.json()["token"] == "tok-1"
    assert calls == [("POST", "/devices", BODY)]
    assert a.calls.last.request.read() == b.calls.last.request.read()
    assert a.calls.last.request.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_register_refused_by_one_node_fails_naming_it_and_still_calls_the_others():
    calls: list = []
    refused = respx.post("http://remote-a/api/v1/forge/devices").mock(
        return_value=Response(500, json={"detail": "rule store down"})
    )
    other = respx.post("http://remote-b/api/v1/forge/devices").mock(
        return_value=Response(201, json=_device("tok-1", "b-id"))
    )
    client = _client([LOCAL, REMOTE_A, REMOTE_B], embedded_forge_app=_embedded(calls))

    response = client.post(URL, json=BODY, headers=_headers())

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "remote-a" in detail and "rule store down" in detail
    assert "remote-b" not in detail and "local" not in detail
    assert refused.called and other.called and len(calls) == 1


@respx.mock
def test_register_unreachable_node_is_502_naming_it():
    respx.post("http://remote-a/api/v1/forge/devices").mock(side_effect=ConnectError("down"))
    client = _client([LOCAL, REMOTE_A], embedded_forge_app=_embedded())

    response = client.post(URL, json=BODY, headers=_headers())

    assert response.status_code == 502
    assert "remote-a (unreachable)" in response.json()["detail"]


@respx.mock
def test_register_node_4xx_propagates_as_that_status():
    respx.post("http://remote-a/api/v1/forge/devices").mock(
        return_value=Response(201, json=_device("tok-1", "a-id"))
    )
    client = _client(
        [LOCAL, REMOTE_A],
        embedded_forge_app=_embedded_refusing(422, "Invalid device platform: tv"),
    )

    response = client.post(URL, json=BODY, headers=_headers())

    assert response.status_code == 422
    assert "local (HTTP 422: Invalid device platform: tv)" in response.json()["detail"]


def test_register_without_a_visible_instance_is_not_success():
    client = _client([])

    response = client.post(URL, json=BODY, headers=_headers())

    assert response.status_code == 502
    assert "No Forge instance" in response.json()["detail"]


@respx.mock
def test_delete_treats_a_404_node_as_already_gone():
    calls: list = []
    gone = respx.delete("http://remote-a/api/v1/forge/devices/tok-1").mock(
        return_value=Response(404, json={"detail": "not found"})
    )
    removed = respx.delete("http://remote-b/api/v1/forge/devices/tok-1").mock(
        return_value=Response(204)
    )
    client = _client([LOCAL, REMOTE_A, REMOTE_B], embedded_forge_app=_embedded(calls))

    response = client.delete(f"{URL}/tok-1", headers=_headers())

    assert response.status_code == 204
    assert gone.called and removed.called
    assert calls == [("DELETE", "/devices/tok-1", None)]


@respx.mock
def test_delete_failure_names_the_instances_that_did_not_remove_it():
    respx.delete("http://remote-a/api/v1/forge/devices/tok-1").mock(
        return_value=Response(503, json={"detail": "busy"})
    )
    respx.delete("http://remote-b/api/v1/forge/devices/tok-1").mock(side_effect=ConnectError("x"))
    client = _client([LOCAL, REMOTE_A, REMOTE_B], embedded_forge_app=_embedded())

    response = client.delete(f"{URL}/tok-1", headers=_headers())

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "remote-a (HTTP 503" in detail and "remote-b (unreachable)" in detail
    assert "local" not in detail


@respx.mock
def test_list_returns_the_union_deduplicated_by_token():
    respx.get("http://remote-a/api/v1/forge/devices").mock(
        return_value=Response(200, json=[_device("tok-1", "a-1"), _device("tok-a", "a-2")])
    )
    client = _client([LOCAL, REMOTE_A], embedded_forge_app=_embedded())

    response = client.get(URL, headers=_headers())

    assert response.status_code == 200
    rows = response.json()
    assert [row["token"] for row in rows] == ["tok-1", "tok-local", "tok-a"]
    assert rows[0]["id"] == "local-1"  # the first node's row wins


@respx.mock
def test_list_with_an_unreachable_node_fails_naming_it():
    respx.get("http://remote-a/api/v1/forge/devices").mock(side_effect=ConnectError("down"))
    client = _client([LOCAL, REMOTE_A], embedded_forge_app=_embedded())

    response = client.get(URL, headers=_headers())

    assert response.status_code == 502
    assert "remote-a (unreachable)" in response.json()["detail"]


@respx.mock
def test_list_rejects_a_malformed_node_answer():
    respx.get("http://remote-a/api/v1/forge/devices").mock(
        return_value=Response(200, json={"devices": []})
    )
    client = _client([LOCAL, REMOTE_A], embedded_forge_app=_embedded())

    response = client.get(URL, headers=_headers())

    assert response.status_code == 502
    assert "remote-a" in response.json()["detail"]


@respx.mock
def test_list_rejects_a_row_without_a_token():
    respx.get("http://remote-a/api/v1/forge/devices").mock(
        return_value=Response(200, json=[{"id": "x"}])
    )
    client = _client([LOCAL, REMOTE_A], embedded_forge_app=_embedded())

    response = client.get(URL, headers=_headers())

    assert response.status_code == 502
    assert "Unexpected device row" in response.json()["detail"]


def _push_node(calls: list[dict[str, Any]], *, push: bool = True) -> FastAPI:
    """A Forge node's device and push routes, recording what the facade forwarded."""
    app = FastAPI()

    def record(request: Request, **extra: Any) -> None:
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query": dict(request.query_params),
                "user": request.headers.get("x-auth-user-id"),
                "tenant": request.headers.get("x-auth-tenant"),
                "authorization": request.headers.get("authorization"),
                **extra,
            }
        )

    @app.post("/api/v1/forge/devices", status_code=201)
    async def register(request: Request, body: dict):
        record(request, body=body)
        if not push:
            return JSONResponse({"detail": "Push is not enabled on this Forge"}, status_code=503)
        return {"id": "d1", **body}

    @app.get("/api/v1/forge/devices")
    async def listing(request: Request):
        record(request)
        return [{"id": "d1", "token": "0a1b2c3d", "kind": "alert"}]

    @app.delete("/api/v1/forge/devices/{token:path}", status_code=204)
    async def unregister(request: Request, token: str):
        record(request, token=token)
        return None

    @app.get("/api/v1/forge/push/status")
    async def status(request: Request):
        record(request)
        return {"running": True, "alerts_sent": 3}

    @app.post("/api/v1/forge/push/test")
    async def test_push(request: Request, body: dict):
        record(request, body=body)
        if body.get("kind") == "widget":
            return JSONResponse({"detail": "You have no registered widget token"}, status_code=404)
        return {"kind": body.get("kind", "alert"), "sent": 1, "accepted": 1, "devices": []}

    return app


def test_a_gateway_only_kind_registers_on_the_push_gateway_node_alone():
    calls: list[dict[str, Any]] = []
    # The default node is another one: the registry's mark decides, not the default.
    remote_default = _instance("remote", base_url="http://remote", is_default=True)
    client = _client([GATEWAY, remote_default], embedded_forge_app=_push_node(calls))

    response = client.post(URL, json=GATEWAY_BODY, headers=_headers())

    assert response.status_code == 201
    assert response.json() == {
        "id": "d1",
        **GATEWAY_BODY,
        "instance_id": "local",
        "instance_name": "Instance local",
        "instance_slug": "local",
    }
    assert calls == [
        {
            "method": "POST",
            "path": URL,
            "query": {},
            "user": "user-a",
            "tenant": "tenant-a",
            "authorization": "Bearer test-token",
            "body": GATEWAY_BODY,
        }
    ]


def test_unregister_of_a_gateway_only_kind_stays_on_the_push_gateway_node():
    calls: list[dict[str, Any]] = []
    client = _client([GATEWAY], embedded_forge_app=_push_node(calls))

    response = client.delete(f"{URL}/0a1b2c3d", params={"kind": "widget"}, headers=_headers())

    assert response.status_code == 204 and response.content == b""
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == f"{URL}/0a1b2c3d"
    assert calls[0]["token"] == "0a1b2c3d"
    assert calls[0]["query"] == {"kind": "widget"}


def test_a_token_with_reserved_characters_stays_one_path_segment():
    calls: list[dict[str, Any]] = []
    client = _client([LOCAL], embedded_forge_app=_push_node(calls))

    client.delete(f"{URL}/endpoint%3Fa%3Db", headers=_headers())

    assert calls[0]["token"] == "endpoint?a=b"
    assert calls[0]["query"] == {}


@respx.mock
def test_instance_id_selects_another_node_and_is_not_forwarded():
    route = respx.post("http://remote/api/v1/forge/devices").mock(
        return_value=Response(201, json={"id": "d9", "token": "0a1b2c3d"})
    )
    calls: list[dict[str, Any]] = []
    client = _client([LOCAL, REMOTE], embedded_forge_app=_push_node(calls))

    response = client.post(
        URL, params={"instance_id": "remote"}, json=GATEWAY_BODY, headers=_headers()
    )

    assert response.status_code == 201
    assert response.json()["instance_id"] == "remote"
    assert calls == []
    request = route.calls.last.request
    assert request.url.query == b""
    # A remote node re-verifies the caller's bearer; x-auth-* headers never cross.
    assert request.headers["authorization"] == "Bearer test-token"
    assert "x-auth-user-id" not in request.headers


def test_no_push_gateway_in_the_registry_is_a_conflict_naming_the_remedy():
    calls: list[dict[str, Any]] = []
    client = _client([LOCAL, REMOTE], embedded_forge_app=_push_node(calls))

    response = client.post(URL, json=GATEWAY_BODY, headers=_headers())

    assert response.status_code == 409
    assert "config.push_gateway: true" in response.json()["detail"]
    assert "found: none" in response.json()["detail"]
    assert calls == []


def test_two_marked_push_gateways_are_a_conflict():
    marked = _instance("remote", base_url="http://remote", config={"push_gateway": True})
    client = _client([GATEWAY, marked], embedded_forge_app=_push_node([]))

    response = client.get("/api/v1/forge/push/status", headers=_headers())

    assert response.status_code == 409
    assert "found: local, remote" in response.json()["detail"]


def test_a_node_without_push_answers_503_through_the_facade():
    calls: list[dict[str, Any]] = []
    client = _client([GATEWAY], embedded_forge_app=_push_node(calls, push=False))

    response = client.post(URL, json=GATEWAY_BODY, headers=_headers())

    assert response.status_code == 503
    assert "Push is not enabled on this Forge" in response.json()["detail"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", URL),
        ("GET", URL),
        ("DELETE", f"{URL}/0a1b2c3d"),
        ("GET", "/api/v1/forge/push/status"),
        ("POST", "/api/v1/forge/push/test"),
    ],
)
def test_a_session_credential_may_not_use_the_device_or_push_routes(method, path):
    # An agent's own session token never registers a device or triggers a push:
    # the facade's edge guard (and Forge) only admit the allow-listed routes.
    assert policy_refusal(method, path, tuple(KNOWN_FORGE_SESSION_SCOPES)) is not None


def test_push_status_and_test_push_reach_the_push_node():
    calls: list[dict[str, Any]] = []
    client = _client([GATEWAY, REMOTE], embedded_forge_app=_push_node(calls))

    status = client.get("/api/v1/forge/push/status", headers=_headers())
    sent = client.post("/api/v1/forge/push/test", json={"kind": "alert"}, headers=_headers())
    default = client.post("/api/v1/forge/push/test", headers=_headers())
    missing = client.post("/api/v1/forge/push/test", json={"kind": "widget"}, headers=_headers())

    assert status.json()["alerts_sent"] == 3 and status.json()["instance_id"] == "local"
    assert sent.status_code == 200 and sent.json()["accepted"] == 1
    assert default.status_code == 200
    assert missing.status_code == 404
    assert [call["path"] for call in calls] == [
        "/api/v1/forge/push/status",
        "/api/v1/forge/push/test",
        "/api/v1/forge/push/test",
        "/api/v1/forge/push/test",
    ]
    assert calls[1]["body"] == {"kind": "alert"} and calls[2]["body"] == {}
    assert {call["user"] for call in calls} == {"user-a"}


@respx.mock
def test_list_keeps_one_row_per_token_and_kind():
    respx.get("http://remote/api/v1/forge/devices").mock(
        return_value=Response(
            200,
            json=[
                {"id": "r1", "token": "tok", "kind": "alert"},
                {"id": "r2", "token": "tok", "kind": "widget"},
            ],
        )
    )
    app = FastAPI()

    @app.get("/api/v1/forge/devices")
    async def listing():
        return [{"id": "l1", "token": "tok", "kind": "alert"}]

    client = _client([LOCAL, REMOTE], embedded_forge_app=app)

    response = client.get(URL, headers=_headers())

    assert response.status_code == 200
    assert [(row["id"], row["kind"]) for row in response.json()] == [
        ("l1", "alert"),
        ("r2", "widget"),
    ]


@respx.mock
def test_unregister_of_an_alert_token_passes_its_kind_to_every_node():
    remote = respx.delete("http://remote/api/v1/forge/devices/tok").mock(return_value=Response(204))
    calls: list[dict[str, Any]] = []
    client = _client([LOCAL, REMOTE], embedded_forge_app=_push_node(calls))

    response = client.delete(f"{URL}/tok", params={"kind": "alert"}, headers=_headers())

    assert response.status_code == 204
    assert calls[0]["query"] == {"kind": "alert"}
    assert remote.calls.last.request.url.params["kind"] == "alert"
