"""Guild facade for Forge notifications (contract §6)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import respx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ConnectError, Response

from niuu.adapters.inbound.forge_notification_feed import decode_cursor, encode_cursor
from tests.test_niuu.test_rest_volundr import _client, _headers, _instance

URL = "/api/v1/forge/notifications"
T0 = datetime(2026, 9, 23, 10, tzinfo=UTC)


def _item(seq: int, minute: int, owner: str = "user-a") -> dict[str, Any]:
    return {
        "id": f"n{seq}",
        "seq": seq,
        "owner_id": owner,
        "kind": "milestone",
        "title": f"t{seq}",
        "created_at": (T0 + timedelta(minutes=minute)).isoformat(),
        "read": False,
    }


def _page(items: list[dict], params, *, default_limit: int = 2) -> dict[str, Any]:
    """Volundr's feed semantics over a fixed item list."""
    limit = int(params.get("limit", default_limit))
    head = max((i["seq"] for i in items), default=0)
    if "after" in params:
        rows = sorted((i for i in items if i["seq"] > int(params["after"])), key=lambda i: i["seq"])
        return {
            "items": rows[:limit],
            "next_before": None,
            "head_seq": head,
            "read_through_seq": 0,
            "unread_count": len(items),
        }
    before = int(params["before"]) if "before" in params else None
    rows = sorted(
        (i for i in items if before is None or i["seq"] < before),
        key=lambda i: i["seq"],
        reverse=True,
    )
    page = rows[:limit]
    more = len(rows) > limit
    return {
        "items": page,
        "next_before": page[-1]["seq"] if more else None,
        "head_seq": head,
        "read_through_seq": 0,
        "unread_count": len(items),
    }


LOCAL_ITEMS = [_item(1, 1), _item(2, 3), _item(3, 5)]
REMOTE_ITEMS = [_item(10, 2), _item(11, 4), _item(12, 6)]


def _embedded(calls: list | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/forge/notifications")
    async def feed(request: Request):
        if calls is not None:
            calls.append(dict(request.query_params))
        if request.query_params.get("kind") == "bogus":
            return JSONResponse({"detail": "Unknown kind"}, status_code=422)
        return _page(LOCAL_ITEMS, request.query_params)

    @app.get("/api/v1/forge/notifications/read-state")
    async def read_state():
        return {"read_through_seq": 1, "revision": 1, "unread_count": 2, "head_seq": 3}

    @app.put("/api/v1/forge/notifications/read-state")
    async def put_read_state(body: dict):
        if body.get("read_through_seq", 0) > 3:
            return JSONResponse({"detail": "unseen"}, status_code=422)
        return {
            "read_through_seq": body["read_through_seq"],
            "revision": 2,
            "unread_count": 0,
            "head_seq": 3,
        }

    @app.get("/api/v1/forge/notifications/rules")
    async def rules():
        return [{"id": "r1", "name": "ops"}]

    @app.post("/api/v1/forge/notifications/rules", status_code=201)
    async def create_rule(body: dict):
        return {"id": "r2", **body}

    @app.put("/api/v1/forge/notifications/rules/{rule_id}")
    async def update_rule(rule_id: str, body: dict):
        return {"id": rule_id, **body}

    @app.delete("/api/v1/forge/notifications/rules/{rule_id}", status_code=204)
    async def delete_rule(rule_id: str):
        return None

    @app.get("/api/v1/forge/notifications/sinks")
    async def sinks():
        return [{"name": "ops", "label": "Ops", "requires_integration": False}]

    @app.get("/api/v1/forge/notifications/{notification_id}/deliveries")
    async def deliveries(notification_id: str):
        if notification_id != "local-n":
            return JSONResponse({"detail": "Notification not found"}, status_code=404)
        return [{"id": "d1", "status": "pending"}]

    @app.get("/api/v1/forge/sessions/{session_id}")
    async def session(session_id: str):
        if session_id != "local-s":
            return JSONResponse({"detail": "missing"}, status_code=404)
        return {"id": session_id}

    return app


LOCAL = _instance(
    "local", base_url="embedded://local", is_default=True, config={"transport": "embedded"}
)
REMOTE = _instance("remote", base_url="http://remote")


def _remote_feed(request):
    return Response(200, json=_page(REMOTE_ITEMS, request.url.params))


def _fleet(client, **params):
    return client.get(URL, params={"all_instances": "true", **params}, headers=_headers())


@respx.mock
def test_fleet_pages_newest_first_with_per_node_cursors():
    respx.get("http://remote/api/v1/forge/notifications").mock(side_effect=_remote_feed)
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    first = _fleet(client, limit=2).json()
    assert [(i["instance_id"], i["seq"]) for i in first["items"]] == [("remote", 12), ("local", 3)]
    assert decode_cursor(first["next_before"]) == {"local": 3, "remote": 12}
    assert decode_cursor(first["next_after"]) == {"local": 3, "remote": 12}
    assert first["unread_count"] == 6 and first["head_seq"] is None
    assert first["instances"]["remote"]["head_seq"] == 12

    second = _fleet(client, limit=2, before=first["next_before"]).json()
    assert [(i["instance_id"], i["seq"]) for i in second["items"]] == [("remote", 11), ("local", 2)]
    third = _fleet(client, limit=2, before=second["next_before"]).json()
    assert [i["seq"] for i in third["items"]] == [10, 1]
    assert third["next_before"] is None


@respx.mock
def test_fleet_without_limit_uses_the_nodes_page_size():
    respx.get("http://remote/api/v1/forge/notifications").mock(side_effect=_remote_feed)
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    page = _fleet(client).json()
    assert [i["seq"] for i in page["items"]] == [12, 3]
    assert decode_cursor(page["next_before"]) == {"local": 3, "remote": 12}


@respx.mock
def test_fleet_gap_fill_is_ascending_and_only_reads_named_nodes():
    remote = respx.get("http://remote/api/v1/forge/notifications").mock(side_effect=_remote_feed)
    calls: list = []
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded(calls))

    local_only = _fleet(client, after=encode_cursor({"local": 1}), limit=5).json()
    assert [(i["instance_id"], i["seq"]) for i in local_only["items"]] == [
        ("local", 2),
        ("local", 3),
    ]
    assert not remote.called  # a node missing from the after-cursor is skipped
    assert calls[-1]["after"] == "1" and "all_instances" not in calls[-1]
    assert decode_cursor(local_only["next_after"]) == {"local": 3}
    assert local_only["next_before"] is None

    page = _fleet(client, after=encode_cursor({"local": 1, "remote": 0}), limit=5).json()
    assert [(i["instance_id"], i["seq"]) for i in page["items"]] == [
        ("remote", 10),
        ("local", 2),
        ("remote", 11),
        ("local", 3),
        ("remote", 12),
    ]
    assert remote.calls[-1].request.url.params["after"] == "0"
    assert decode_cursor(page["next_after"]) == {"local": 3, "remote": 12}
    empty = _fleet(client, after=page["next_after"]).json()
    assert empty["items"] == [] and decode_cursor(empty["next_after"]) == {"local": 3, "remote": 12}


@respx.mock
def test_unavailable_nodes_are_reported_and_retried():
    respx.get("http://remote/api/v1/forge/notifications").mock(return_value=Response(500))
    respx.get("http://down/api/v1/forge/notifications").mock(side_effect=ConnectError("x"))
    respx.get("http://garbled/api/v1/forge/notifications").mock(
        return_value=Response(200, json={"items": "nope"})
    )
    client = _client(
        [
            LOCAL,
            REMOTE,
            _instance("down", base_url="http://down"),
            _instance("garbled", base_url="http://garbled"),
        ],
        embedded_forge_app=_embedded(),
    )
    response = _fleet(client, limit=2)
    assert response.status_code == 200
    assert response.headers["X-Forge-Unavailable-Instances"] == "down,garbled,remote"
    cursor = decode_cursor(response.json()["next_before"])
    assert cursor == {"local": 2, "remote": None, "down": None, "garbled": None}


@respx.mock
def test_all_nodes_down_is_a_gateway_error():
    respx.get("http://remote/api/v1/forge/notifications").mock(return_value=Response(503))
    client = _client([REMOTE])
    assert _fleet(client).status_code == 502


def test_fleet_request_validation():
    client = _client([LOCAL], embedded_forge_app=_embedded())
    assert _fleet(client, before="%%%").status_code == 422
    assert _fleet(client, before=encode_cursor({"local": -1})).status_code == 422
    bad = base64.urlsafe_b64encode(json.dumps([1]).encode()).decode()
    assert _fleet(client, after=bad).status_code == 422
    both = encode_cursor({"local": 1})
    assert _fleet(client, before=both, after=both).status_code == 422
    assert _fleet(client, limit="x").status_code == 422
    assert _fleet(client, limit=0).status_code == 422
    assert _fleet(client, kind="bogus").status_code == 422  # node validation is surfaced
    gone = _fleet(client, before=encode_cursor({"elsewhere": 5})).json()
    assert gone["items"] == [] and gone["next_before"] is None


def test_single_node_request_proxies_raw_cursor_and_tags_items():
    calls: list = []
    client = _client([LOCAL], embedded_forge_app=_embedded(calls))
    payload = client.get(URL, params={"before": 3, "limit": 5}, headers=_headers()).json()
    assert calls == [{"before": "3", "limit": "5"}]
    assert payload["instance_id"] == "local"
    assert [(i["seq"], i["instance_id"]) for i in payload["items"]] == [(2, "local"), (1, "local")]


@respx.mock
def test_read_state_aggregates_and_puts_per_node():
    respx.get("http://remote/api/v1/forge/notifications/read-state").mock(
        return_value=Response(
            200, json={"read_through_seq": 0, "revision": 0, "unread_count": 5, "head_seq": 12}
        )
    )
    put_remote = respx.put("http://remote/api/v1/forge/notifications/read-state").mock(
        return_value=Response(409, json={"detail": "changed"})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    url = f"{URL}/read-state"

    fleet = client.get(url, params={"all_instances": "true"}, headers=_headers()).json()
    assert fleet["unread_count"] == 7 and set(fleet["instances"]) == {"local", "remote"}
    single = client.get(url, headers=_headers()).json()
    assert single["instance_id"] == "local" and single["unread_count"] == 2

    ok = client.put(url, json={"read_through_seq": 3, "expected_revision": 1}, headers=_headers())
    assert ok.json()["instance_id"] == "local" and ok.json()["revision"] == 2

    both = {
        "instances": {
            "local": {"read_through_seq": 3, "expected_revision": 1},
            "remote": {"read_through_seq": 12, "expected_revision": 0},
        }
    }
    conflict = client.put(url, json=both, headers=_headers())
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["conflicts"] == ["remote"] and detail["instances"]["local"]["revision"] == 2
    assert json.loads(put_remote.calls[0].request.content) == {
        "read_through_seq": 12,
        "expected_revision": 0,
    }

    local_only = client.put(
        url,
        json={"instances": {"local": {"read_through_seq": 2, "expected_revision": 1}}},
        headers=_headers(),
    )
    assert local_only.json() == {
        "unread_count": 0,
        "instances": {
            "local": {"read_through_seq": 2, "revision": 2, "unread_count": 0, "head_seq": 3}
        },
    }
    future = client.put(
        url,
        json={"instances": {"local": {"read_through_seq": 9, "expected_revision": 1}}},
        headers=_headers(),
    )
    assert future.status_code == 422
    assert client.put(url, json={"instances": []}, headers=_headers()).status_code == 422
    unknown = {"instances": {"ghost": {"read_through_seq": 1, "expected_revision": 0}}}
    assert client.put(url, json=unknown, headers=_headers()).status_code == 404


@respx.mock
def test_read_state_partial_outage():
    respx.get("http://remote/api/v1/forge/notifications/read-state").mock(
        return_value=Response(502)
    )
    respx.put("http://remote/api/v1/forge/notifications/read-state").mock(
        side_effect=ConnectError("down")
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    url = f"{URL}/read-state"
    fleet = client.get(url, params={"all_instances": "true"}, headers=_headers())
    assert fleet.headers["X-Forge-Unavailable-Instances"] == "remote"
    assert fleet.json()["unread_count"] == 2
    put = client.put(
        url,
        json={"instances": {"remote": {"read_through_seq": 1, "expected_revision": 0}}},
        headers=_headers(),
    )
    assert put.status_code == 502
    only_remote = _client([REMOTE])
    assert (
        only_remote.get(url, params={"all_instances": "true"}, headers=_headers()).status_code
        == 502
    )


def test_rules_and_sinks_go_to_the_selected_or_local_node():
    client = _client([LOCAL], embedded_forge_app=_embedded())
    rules = f"{URL}/rules"
    assert client.get(rules, headers=_headers()).json() == [
        {
            "id": "r1",
            "name": "ops",
            "instance_id": "local",
            "instance_name": "Instance local",
            "instance_slug": "local",
        }
    ]
    created = client.post(rules, json={"name": "new"}, headers=_headers())
    assert created.status_code == 201 and created.json()["instance_id"] == "local"
    updated = client.put(f"{rules}/r1", json={"name": "x"}, headers=_headers())
    assert updated.json()["id"] == "r1"
    assert client.delete(f"{rules}/r1", headers=_headers()).status_code == 204
    sinks = client.get(f"{URL}/sinks", params={"scope": "local"}, headers=_headers()).json()
    assert sinks[0]["name"] == "ops" and sinks[0]["instance_id"] == "local"


@respx.mock
def test_deliveries_are_found_on_the_owning_node():
    respx.get("http://remote/api/v1/forge/notifications/remote-n/deliveries").mock(
        return_value=Response(200, json=[{"id": "d9"}])
    )
    respx.get("http://remote/api/v1/forge/notifications/nowhere/deliveries").mock(
        return_value=Response(404, json={"detail": "no"})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    found = client.get(f"{URL}/remote-n/deliveries", headers=_headers()).json()
    assert found == [
        {
            "id": "d9",
            "instance_id": "remote",
            "instance_name": "Instance remote",
            "instance_slug": "remote",
        }
    ]
    local = client.get(
        f"{URL}/local-n/deliveries", params={"instance_id": "local"}, headers=_headers()
    )
    assert local.json()[0]["instance_id"] == "local"
    assert client.get(f"{URL}/nowhere/deliveries", headers=_headers()).status_code == 404


@respx.mock
def test_session_routes_go_to_the_owning_node():
    respx.get("http://remote/api/v1/forge/sessions/s9").mock(
        return_value=Response(200, json={"id": "s9"})
    )
    listed = respx.get("http://remote/api/v1/forge/sessions/s9/notifications").mock(
        return_value=Response(200, json=[{"id": "n1", "seq": 4}])
    )
    submitted = respx.post("http://remote/api/v1/forge/sessions/s9/notifications").mock(
        return_value=Response(200, json={"id": "n1", "seq": 4})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    url = "/api/v1/forge/sessions/s9/notifications"
    items = client.get(url, params={"after": 2}, headers=_headers()).json()
    assert items[0]["instance_id"] == "remote"
    assert listed.calls[0].request.url.params["after"] == "2"
    deduped = client.post(
        url, json={"kind": "info", "title": "x", "idempotency_key": "k"}, headers=_headers()
    )
    assert deduped.status_code == 200 and deduped.json()["instance_id"] == "remote"
    assert json.loads(submitted.calls[0].request.content)["idempotency_key"] == "k"


def test_feature_flags_advertise_notifications():
    embedded = FastAPI()

    @embedded.get("/api/v1/forge/feature-flags")
    async def node_flags():
        return {"capabilities": {}}

    client = _client([LOCAL], embedded_forge_app=embedded)
    flags = client.get("/api/v1/forge/feature-flags", headers=_headers()).json()
    assert flags["capabilities"]["notifications"] is True


@pytest.mark.parametrize(
    ("roles", "expected"),
    [("volundr:developer", ["mine"]), ("volundr:admin", ["mine", "theirs"])],
)
@respx.mock
def test_fleet_stream_owner_scopes_notifications(monkeypatch, roles, expected):
    from niuu.adapters.inbound import rest_volundr

    embedded = FastAPI()

    def event(name: str, **data):
        return SimpleNamespace(type=SimpleNamespace(value=name), data=data)

    async def subscribe(principal):
        yield event("session_notification", id="mine", owner_id="user-a", tenant_id="tenant-a")
        yield event("session_notification", id="theirs", owner_id="user-b", tenant_id="tenant-a")
        yield event("session_notification", id="alien", owner_id="user-c", tenant_id="tenant-z")
        yield event("session_activity", id="activity", session_id="s")

    embedded.state.session_event_stream = SimpleNamespace(
        authorize=lambda principal: None, subscribe=subscribe
    )
    respx.get("http://remote/api/v1/forge/sessions/stream").mock(
        return_value=Response(
            200,
            text=(
                'event: session_notification\ndata: {"id":"leak","owner_id":"user-z",'
                '"tenant_id":"tenant-z"}\n\n'
                "event: session_notification\ndata: []\n\n"
            ),
        )
    )

    async def finite_merge(sources, **_kwargs):
        for source in sources.values():
            async for name, data in source():
                yield f"{name}:{data['id'] if isinstance(data, dict) else data}\n".encode()

    monkeypatch.setattr(rest_volundr, "merge_events", finite_merge)
    client = _client([LOCAL, REMOTE], embedded_forge_app=embedded)
    headers = {**_headers(), "x-auth-roles": roles}
    response = client.get(
        "/api/v1/forge/sessions/stream", params={"all_instances": "true"}, headers=headers
    )
    delivered = [
        line.split(":", 1)[1]
        for line in response.text.splitlines()
        if line.startswith("session_notification:")
    ]
    assert delivered == expected
    assert "session_activity:activity" in response.text


@pytest.mark.parametrize(
    "body",
    [
        {
            "items": [],
            "next_before": None,
            "head_seq": "1",
            "read_through_seq": 0,
            "unread_count": 0,
        },
        {"items": [], "next_before": "x", "head_seq": 1, "read_through_seq": 0, "unread_count": 0},
        ["not", "a", "page"],
    ],
)
@respx.mock
def test_malformed_node_pages_count_as_unavailable(body):
    respx.get("http://remote/api/v1/forge/notifications").mock(
        return_value=Response(200, json=body)
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    response = _fleet(client)
    assert response.headers["X-Forge-Unavailable-Instances"] == "remote"
    assert [i["instance_id"] for i in response.json()["items"]] == ["local", "local"]


@respx.mock
def test_non_json_node_responses_are_gateway_errors():
    respx.get("http://remote/api/v1/forge/notifications").mock(
        return_value=Response(200, text="<html>")
    )
    respx.get("http://remote/api/v1/forge/notifications/rules").mock(
        return_value=Response(200, json="scalar")
    )
    respx.get("http://remote/api/v1/forge/notifications/sinks").mock(
        return_value=Response(200, text="<html>")
    )
    respx.get("http://remote/api/v1/forge/sessions/s1").mock(
        return_value=Response(200, json={"id": "s1"})
    )
    respx.get("http://remote/api/v1/forge/sessions/s1/notifications").mock(
        return_value=Response(200, json={"items": []})
    )
    respx.put("http://remote/api/v1/forge/notifications/read-state").mock(
        return_value=Response(200, json=["x"])
    )
    client = _client([REMOTE])
    assert client.get(URL, headers=_headers()).status_code == 502
    assert _fleet(client).status_code == 502  # the only node answered garbage
    assert client.get(f"{URL}/rules", headers=_headers()).json() == "scalar"
    assert client.get(f"{URL}/sinks", headers=_headers()).status_code == 502
    listed = client.get("/api/v1/forge/sessions/s1/notifications", headers=_headers())
    assert listed.status_code == 502
    put = client.put(
        f"{URL}/read-state",
        json={"instances": {"remote": {"read_through_seq": 1, "expected_revision": 0}}},
        headers=_headers(),
    )
    assert put.status_code == 502
    feed = client.get(URL, params={"instance_id": "remote"}, headers=_headers())
    assert feed.status_code == 502


@respx.mock
def test_individual_read_targets_only_selected_host_and_preserves_upstream_errors():
    nid = "459f6fca-52a5-405d-a044-626c114f607d"
    path = f"{URL}/{nid}/read"
    state = {"read_through_seq": 0, "revision": 4, "unread_count": 2, "head_seq": 3}
    remote = respx.put(f"http://remote{path}").respond(
        200, json={"id": nid, "read": True, "read_state": state}
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    response = client.put(path, params={"instance_id": "remote"}, headers=_headers())
    assert response.status_code == 200
    assert response.json() == {
        "id": nid,
        "instance_id": "remote",
        "read": True,
        "read_state": state,
    }
    assert remote.call_count == 1
    assert "instance_id" not in remote.calls[0].request.url.params
    for status in [403, 404, 503]:
        remote.respond(status, json={"detail": "unavailable"})
        assert (
            client.put(path, params={"instance_id": "remote"}, headers=_headers()).status_code
            == status
        )
    remote.respond(200, json=[])
    assert client.put(path, params={"instance_id": "remote"}, headers=_headers()).status_code == 502
    assert (
        client.put(path, params={"instance_id": "unknown"}, headers=_headers()).status_code == 404
    )


@respx.mock
def test_feed_propagates_optional_read_revision_and_individual_read_flag():
    payload = _page(REMOTE_ITEMS, {})
    payload["revision"] = 7
    payload["items"][0] = {**payload["items"][0], "read": True}
    respx.get(f"http://remote{URL}").respond(200, json=payload)
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    feed = _fleet(client).json()
    assert feed["instances"]["remote"]["revision"] == 7
    assert feed["instances"]["local"].get("revision") is None
    assert feed["items"][0]["read"] is True
