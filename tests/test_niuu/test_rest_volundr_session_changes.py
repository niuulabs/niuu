"""The Guild host passes one Forge's changed_since read through, tagged with its host."""

from typing import Any

import pytest
import respx
from fastapi import FastAPI

from tests.test_niuu.test_rest_volundr import _client, _headers, _instance

CHANGES = {
    "cursor": "812",
    "ids": ["s1", "s2"],
    "sessions": [{"id": "s2", "name": "busy", "status": "running"}],
}


@respx.mock
def test_selected_host_changes_pass_through_tagged() -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    route = respx.get("http://bro/api/v1/forge/sessions").respond(200, json=CHANGES)

    response = client.get(
        "/api/v1/forge/sessions?instance_id=bro&changed_since=700&status=archived",
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cursor"] == "812" and body["ids"] == ["s1", "s2"]
    assert [(s["id"], s["instance_id"]) for s in body["sessions"]] == [("s2", "bro")]
    params = route.calls.last.request.url.params
    assert params["changed_since"] == "700" and params["status"] == "archived"
    assert "instance_id" not in params
    assert "X-Forge-Session-Changes" not in response.headers


@respx.mock
def test_a_host_without_changed_since_answers_with_its_whole_inventory() -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    respx.get("http://bro/api/v1/forge/sessions").respond(
        200, json=[{"id": "s1", "status": "running"}, {"id": "s2", "status": "stopped"}]
    )

    response = client.get(
        "/api/v1/forge/sessions?instance_id=bro&changed_since=5", headers=_headers()
    )

    assert response.status_code == 200
    assert response.headers["X-Forge-Session-Changes"] == "full"
    body = response.json()
    assert body["cursor"] == "0" and body["ids"] == ["s1", "s2"]
    assert [s["id"] for s in body["sessions"]] == ["s1", "s2"]


def test_changed_since_needs_one_host() -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    response = client.get("/api/v1/forge/sessions?changed_since=0", headers=_headers())
    assert response.status_code == 400
    assert "instance_id" in response.json()["detail"]


def test_local_scope_reads_the_embedded_forge() -> None:
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/sessions")
    async def local_changes(changed_since: int) -> dict[str, Any]:
        return {**CHANGES, "cursor": str(changed_since + 1)}

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

    response = client.get("/api/v1/forge/sessions?scope=local&changed_since=3", headers=_headers())

    assert response.status_code == 200
    assert response.headers["X-Forge-Session-Scope"] == "local"
    assert response.json()["cursor"] == "4"
    assert response.json()["sessions"][0]["instance_id"] == "local"


@pytest.mark.parametrize(
    "payload",
    [
        {"ids": [], "sessions": []},
        {"cursor": 1, "ids": [], "sessions": []},
        {"cursor": "1", "ids": [None], "sessions": []},
        {"cursor": "1", "ids": [], "sessions": [{"name": "no id"}]},
        "nonsense",
    ],
)
@respx.mock
def test_a_malformed_changes_answer_is_refused(payload: Any) -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    respx.get("http://bro/api/v1/forge/sessions").respond(200, json=payload)
    response = client.get(
        "/api/v1/forge/sessions?instance_id=bro&changed_since=0", headers=_headers()
    )
    assert response.status_code == 502


@respx.mock
def test_a_failed_changes_read_keeps_the_hosts_status() -> None:
    client = _client([_instance("bro", base_url="http://bro")])
    respx.get("http://bro/api/v1/forge/sessions").respond(503, json={"detail": "draining"})
    response = client.get(
        "/api/v1/forge/sessions?instance_id=bro&changed_since=0", headers=_headers()
    )
    assert response.status_code == 503
    assert "draining" in response.json()["detail"]
