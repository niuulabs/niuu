"""Guild facade for artifact publications, bursts and host identity (F1, F1n, F7)."""

from __future__ import annotations

from uuid import uuid4

import respx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import Response

from tests.test_niuu.test_rest_volundr import _client, _headers, _instance

PREFIX = "/api/v1/forge"
LOCAL = _instance(
    "local", base_url="embedded://local", is_default=True, config={"transport": "embedded"}
)
REMOTE = _instance("remote", base_url="http://remote")


def _embedded(calls: list) -> FastAPI:
    app = FastAPI()

    @app.get(f"{PREFIX}/artifact-publications")
    async def publications(request: Request):
        calls.append(("publications", dict(request.query_params)))
        return {"host": "thor", "items": [{"seq": 1}], "next_after": 1, "has_more": False}

    @app.get(f"{PREFIX}/artifact-bursts")
    async def bursts(request: Request):
        calls.append(("bursts", dict(request.query_params)))
        return {"host": "thor", "items": [], "next_after": 0, "has_more": False}

    @app.get(f"{PREFIX}/artifact-bursts/{{burst_id}}")
    async def burst(burst_id: str):
        calls.append(("burst", burst_id))
        if burst_id.startswith("0"):
            return JSONResponse({"detail": "Burst not found"}, status_code=404)
        return {"burst_id": burst_id, "items": []}

    @app.post(f"{PREFIX}/artifact-publications/backfill")
    async def backfill(body: dict):
        calls.append(("backfill", body))
        return {"recorded": 3, "scanned_rows": 5}

    @app.get(f"{PREFIX}/instance")
    async def instance():
        return {"host": "thor", "tailnet_name": "thor.ts.net", "instance_id": "proj-thor"}

    return app


def test_publication_and_burst_feeds_proxy_to_the_local_node():
    calls: list = []
    client = _client([LOCAL], embedded_forge_app=_embedded(calls))
    pubs = client.get(
        f"{PREFIX}/artifact-publications",
        params={"after": 4, "limit": 10, "scope": "local"},
        headers=_headers(),
    ).json()
    assert pubs["instance_id"] == "local" and pubs["items"] == [{"seq": 1}]
    bursts = client.get(f"{PREFIX}/artifact-bursts", params={"after": 2}, headers=_headers()).json()
    assert bursts["instance_id"] == "local" and bursts["next_after"] == 0
    # Fleet selectors are dropped; the cursor is forwarded raw.
    assert calls == [
        ("publications", {"after": "4", "limit": "10"}),
        ("bursts", {"after": "2"}),
    ]


def test_one_burst_and_its_upstream_404():
    calls: list = []
    client = _client([LOCAL], embedded_forge_app=_embedded(calls))
    found = "1" + str(uuid4())[1:]
    body = client.get(f"{PREFIX}/artifact-bursts/{found}", headers=_headers()).json()
    assert body == {"burst_id": found, "items": [], "instance_id": "local"}
    missing = "0" + str(uuid4())[1:]
    assert client.get(f"{PREFIX}/artifact-bursts/{missing}", headers=_headers()).status_code == 404


@respx.mock
def test_selected_remote_node_is_read_over_http():
    route = respx.get("http://remote/api/v1/forge/artifact-publications").mock(
        return_value=Response(200, json={"host": "mini-m6", "items": [], "next_after": 0})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded([]))
    body = client.get(
        f"{PREFIX}/artifact-publications", params={"instance_id": "remote"}, headers=_headers()
    ).json()
    assert route.called and body["instance_id"] == "remote" and body["host"] == "mini-m6"


@respx.mock
def test_non_object_artifact_payload_is_a_gateway_error():
    respx.get("http://remote/api/v1/forge/artifact-bursts").mock(
        return_value=Response(200, json=[1, 2])
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded([]))
    response = client.get(
        f"{PREFIX}/artifact-bursts", params={"instance_id": "remote"}, headers=_headers()
    )
    assert response.status_code == 502


def test_backfill_runs_on_the_local_node():
    calls: list = []
    client = _client([LOCAL], embedded_forge_app=_embedded(calls))
    body = client.post(
        f"{PREFIX}/artifact-publications/backfill",
        json={"since": "2026-10-01T00:00:00Z"},
        headers=_headers(),
    ).json()
    assert body == {"recorded": 3, "scanned_rows": 5, "instance_id": "local"}
    assert calls == [("backfill", {"since": "2026-10-01T00:00:00Z"})]


def test_instance_reports_self_id_slug_and_forge_names():
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded([]))
    body = client.get(f"{PREFIX}/instance", headers=_headers()).json()
    assert body == {
        "instance_id": "local",
        "slug": "local",
        "name": "Instance local",
        "host": "thor",
        "tailnet_name": "thor.ts.net",
        "projects_instance_id": "proj-thor",
    }
