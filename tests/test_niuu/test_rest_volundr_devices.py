"""Guild facade for Forge push devices: every visible node holds the registration."""

from __future__ import annotations

from typing import Any

import respx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ConnectError, Response

from tests.test_niuu.test_rest_volundr import _client, _headers, _instance

URL = "/api/v1/forge/devices"
BODY = {"platform": "ios", "token": "tok-1", "app_bundle_id": "com.niuu.lexi"}

LOCAL = _instance(
    "local", base_url="embedded://local", is_default=True, config={"transport": "embedded"}
)
REMOTE_A = _instance("remote-a", base_url="http://remote-a")
REMOTE_B = _instance("remote-b", base_url="http://remote-b")


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
