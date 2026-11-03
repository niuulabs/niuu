"""Guild facade for notification images: node-routed URLs and the image proxy."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import respx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from httpx import Response as HttpxResponse

from tests.test_niuu.test_rest_volundr import _client, _headers
from tests.test_niuu.test_rest_volundr_notifications import LOCAL, REMOTE, URL, _item, _page

LOCAL_NID = "11111111-1111-4111-8111-111111111111"
REMOTE_NID = "22222222-2222-4222-8222-222222222222"
PNG = b"\x89PNG-node-bytes"


def _image(nid: str) -> dict[str, Any]:
    return {
        "url": f"/api/v1/forge/notifications/{nid}/image",
        "thumbnail_url": f"/api/v1/forge/notifications/{nid}/image?variant=thumb",
        "mime": "image/png",
        "width": 1200,
        "height": 800,
        "alt": "UI",
    }


def _with_image(item: dict, nid: str) -> dict:
    return {**item, "id": nid, "image": _image(nid), "image_state": "ready"}


LOCAL_FEED = [_with_image(_item(1, 1), LOCAL_NID), _item(2, 3)]
REMOTE_FEED = [_with_image(_item(10, 2), REMOTE_NID)]


def _embedded(calls: list | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/forge/notifications")
    async def feed(request: Request):
        return _page(LOCAL_FEED, request.query_params, default_limit=10)

    @app.get("/api/v1/forge/notifications/{notification_id}/image")
    async def image(notification_id: str, request: Request):
        if calls is not None:
            calls.append((notification_id, dict(request.query_params), dict(request.headers)))
        if notification_id != LOCAL_NID:
            return JSONResponse({"detail": "Notification not found"}, status_code=404)
        return Response(
            content=PNG,
            media_type="image/png",
            headers={
                "cache-control": "private, max-age=60, immutable",
                "x-content-type-options": "nosniff",
            },
        )

    return app


def _remote_feed(request):
    return HttpxResponse(200, json=_page(REMOTE_FEED, request.url.params, default_limit=10))


@respx.mock
def test_fleet_items_route_their_image_urls_to_the_owning_node():
    respx.get("http://remote/api/v1/forge/notifications").mock(side_effect=_remote_feed)
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    items = client.get(URL, params={"all_instances": "true"}, headers=_headers()).json()["items"]
    by_id = {item["id"]: item for item in items}
    assert by_id[REMOTE_NID]["image"] == {
        **_image(REMOTE_NID),
        "url": f"/api/v1/forge/notifications/{REMOTE_NID}/image?instance_id=remote",
        "thumbnail_url": (
            f"/api/v1/forge/notifications/{REMOTE_NID}/image?variant=thumb&instance_id=remote"
        ),
    }
    assert by_id[LOCAL_NID]["image"]["url"] == (
        f"/api/v1/forge/notifications/{LOCAL_NID}/image?instance_id=local"
    )
    plain = next(item for item in items if item["seq"] == 2)
    assert "image" not in plain


def test_single_node_feed_items_are_routed_too():
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    items = client.get(URL, headers=_headers()).json()["items"]
    [with_image] = [item for item in items if item.get("image")]
    assert with_image["image"]["thumbnail_url"].endswith("?variant=thumb&instance_id=local")


def test_image_proxy_reaches_the_default_node_and_keeps_the_headers():
    calls: list = []
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded(calls))

    response = client.get(
        f"{URL}/{LOCAL_NID}/image", params={"variant": "thumb"}, headers=_headers()
    )

    assert response.status_code == 200 and response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=60, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    [(nid, params, forwarded)] = calls
    assert (nid, params) == (LOCAL_NID, {"variant": "thumb"})
    # The node sees the caller's own identity, so it applies the item's read access.
    assert forwarded["authorization"] == "Bearer test-token"
    assert forwarded["x-auth-user-id"] == "user-a"


@respx.mock
def test_image_proxy_follows_instance_id_and_drops_it_downstream():
    remote = respx.get(f"http://remote{URL}/{REMOTE_NID}/image").mock(
        return_value=HttpxResponse(200, content=PNG, headers={"content-type": "image/png"})
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())

    response = client.get(
        f"{URL}/{REMOTE_NID}/image",
        params={"instance_id": "remote", "variant": "thumb"},
        headers=_headers(),
    )

    assert response.status_code == 200 and response.content == PNG
    assert "cache-control" not in response.headers
    sent = remote.calls[0].request
    assert dict(sent.url.params) == {"variant": "thumb"}
    assert sent.headers["authorization"] == "Bearer test-token"


@respx.mock
def test_image_proxy_preserves_node_refusals():
    remote = respx.get(f"http://remote{URL}/{REMOTE_NID}/image")
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    for status in (401, 403, 404):
        remote.respond(status, json={"detail": "nope"})
        response = client.get(
            f"{URL}/{REMOTE_NID}/image", params={"instance_id": "remote"}, headers=_headers()
        )
        assert response.status_code == status
    missing_node = client.get(
        f"{URL}/{REMOTE_NID}/image", params={"instance_id": "unknown"}, headers=_headers()
    )
    assert missing_node.status_code == 404
    local_miss = client.get(f"{URL}/{REMOTE_NID}/image", headers=_headers())
    assert local_miss.status_code == 404
    assert client.get(f"{URL}/not-a-uuid/image", headers=_headers()).status_code == 422


@respx.mock
def test_session_notifications_and_submits_route_images():
    respx.get("http://remote/api/v1/forge/sessions/s9").mock(
        return_value=HttpxResponse(200, json={"id": "s9"})
    )
    respx.get("http://remote/api/v1/forge/sessions/s9/notifications").mock(
        return_value=HttpxResponse(200, json=[_with_image({"seq": 4}, REMOTE_NID)])
    )
    respx.post("http://remote/api/v1/forge/sessions/s9/notifications").mock(
        return_value=HttpxResponse(201, json=_with_image({"seq": 5}, REMOTE_NID))
    )
    client = _client([LOCAL, REMOTE], embedded_forge_app=_embedded())
    url = "/api/v1/forge/sessions/s9/notifications"
    [listed] = client.get(url, headers=_headers()).json()
    assert listed["image"]["url"].endswith("/image?instance_id=remote")
    submitted = client.post(
        url, json={"kind": "info", "title": "x", "idempotency_key": "k"}, headers=_headers()
    )
    assert submitted.status_code == 201
    assert submitted.json()["image"]["thumbnail_url"].endswith("?variant=thumb&instance_id=remote")


def test_live_notification_events_route_images_and_other_events_are_tagged(monkeypatch):
    from niuu.adapters.inbound import rest_volundr

    embedded = FastAPI()

    def event(name: str, **data):
        return SimpleNamespace(type=SimpleNamespace(value=name), data=data)

    async def subscribe(principal):
        yield event(
            "session_notification",
            id=LOCAL_NID,
            owner_id="user-a",
            tenant_id="tenant-a",
            image=_image(LOCAL_NID),
        )
        yield event("session_notification", id="plain", owner_id="user-a", tenant_id="tenant-a")
        yield event("session_updated", id="s1", chat_endpoint="/s/s1/session")

    embedded.state.session_event_stream = SimpleNamespace(
        authorize=lambda principal: None, subscribe=subscribe
    )
    seen: list[tuple[str, Any]] = []

    async def finite_merge(sources, **_kwargs):
        for source in sources.values():
            async for name, data in source():
                seen.append((name, data))
                yield b"."

    monkeypatch.setattr(rest_volundr, "merge_events", finite_merge)
    client = _client([LOCAL], embedded_forge_app=embedded)
    client.get("/api/v1/forge/sessions/stream", headers=_headers())

    (_, routed), (_, plain), (_, session) = seen
    assert routed["instance_id"] == "local"
    assert routed["image"]["url"] == (
        f"/api/v1/forge/notifications/{LOCAL_NID}/image?instance_id=local"
    )
    assert "image" not in plain and plain["instance_id"] == "local"
    assert session["instance_id"] == "local"
