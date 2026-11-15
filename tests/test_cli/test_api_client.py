"""Tests for cli.api.client — base HTTP client with auth and retry."""

from __future__ import annotations

import httpx
import pytest
import respx

from cli.api.client import APIClient

BASE = "http://volundr.test"


class TestAPIClientBasicRequests:
    async def test_get_sends_bearer_token(self) -> None:
        client = APIClient(base_url=BASE, access_token="tok123")
        with respx.mock:
            route = respx.get(f"{BASE}/api/v1/health").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert route.called
        assert route.calls[0].request.headers["authorization"] == "Bearer tok123"

    async def test_get_without_token(self) -> None:
        client = APIClient(base_url=BASE)
        with respx.mock:
            route = respx.get(f"{BASE}/test").mock(return_value=httpx.Response(200, json={}))
            resp = await client.get("/test")
        assert resp.status_code == 200
        assert "authorization" not in route.calls[0].request.headers

    async def test_post_sends_json(self) -> None:
        client = APIClient(base_url=BASE, access_token="t")
        with respx.mock:
            route = respx.post(f"{BASE}/create").mock(
                return_value=httpx.Response(201, json={"id": "1"})
            )
            resp = await client.post("/create", json={"name": "test"})
        assert resp.status_code == 201
        import json

        assert json.loads(route.calls[0].request.content) == {"name": "test"}

    async def test_delete_request(self) -> None:
        client = APIClient(base_url=BASE, access_token="t")
        with respx.mock:
            respx.delete(f"{BASE}/item/1").mock(return_value=httpx.Response(204))
            resp = await client.delete("/item/1")
        assert resp.status_code == 204

    async def test_trailing_slash_stripped_from_base_url(self) -> None:
        client = APIClient(base_url="http://host:8080/")
        assert client.base_url == "http://host:8080"


class TestAPIClientTokenRefresh:
    async def test_401_triggers_refresh_and_retry(self) -> None:
        refreshed = False

        async def refresh_fn() -> str:
            nonlocal refreshed
            refreshed = True
            return "new-token"

        client = APIClient(base_url=BASE, access_token="old", refresh_token_fn=refresh_fn)

        with respx.mock:
            route = respx.get(f"{BASE}/data")
            route.side_effect = [
                httpx.Response(401),
                httpx.Response(200, json={"v": 1}),
            ]
            resp = await client.get("/data")

        assert resp.status_code == 200
        assert refreshed
        # Second request should use new token.
        assert route.calls[1].request.headers["authorization"] == "Bearer new-token"

    async def test_401_no_refresh_fn_returns_401(self) -> None:
        client = APIClient(base_url=BASE, access_token="old")
        with respx.mock:
            respx.get(f"{BASE}/data").mock(return_value=httpx.Response(401))
            resp = await client.get("/data")
        assert resp.status_code == 401

    async def test_401_refresh_raises_instead_of_returning_401(self) -> None:
        from cli.auth.oidc import LoginExpiredError

        async def expired() -> str:
            raise LoginExpiredError("Your niuu login can no longer be refreshed. Run `niuu login`.")

        client = APIClient(base_url=BASE, access_token="old", refresh_token_fn=expired)
        with respx.mock:
            respx.get(f"{BASE}/data").mock(return_value=httpx.Response(401))
            with pytest.raises(LoginExpiredError, match="niuu login"):
                await client.get("/data")

    async def test_set_token(self) -> None:
        client = APIClient(base_url=BASE, access_token="old")
        client.set_token("brand-new")
        with respx.mock:
            route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200))
            await client.get("/x")
        assert route.calls[0].request.headers["authorization"] == "Bearer brand-new"


class TestAPIClientSSE:
    async def test_stream_sse_yields_events(self) -> None:
        sse_body = (
            'event: session_activity\ndata: {"session_id": "s1"}\n\nevent: keepalive\ndata: {}\n\n'
        )
        client = APIClient(base_url=BASE, access_token="t")
        with respx.mock:
            respx.get(f"{BASE}/stream").mock(return_value=httpx.Response(200, text=sse_body))
            events = []
            async for event_type, data in client.stream_sse("/stream"):
                events.append((event_type, data))

        assert len(events) == 2
        assert events[0] == ("session_activity", '{"session_id": "s1"}')
        assert events[1] == ("keepalive", "{}")

    async def test_stream_sse_filters_keepalive_comments(self) -> None:
        sse_body = ": keepalive\nevent: update\ndata: ok\n\n: another comment\n"
        client = APIClient(base_url=BASE, access_token="t")
        with respx.mock:
            respx.get(f"{BASE}/stream").mock(return_value=httpx.Response(200, text=sse_body))
            events = []
            async for event_type, data in client.stream_sse("/stream"):
                events.append((event_type, data))

        assert len(events) == 1
        assert events[0] == ("update", "ok")

    async def test_stream_sse_retries_on_401(self) -> None:
        sse_body = "event: ping\ndata: pong\n\n"

        async def refresh_fn() -> str:
            return "refreshed-token"

        client = APIClient(base_url=BASE, access_token="old", refresh_token_fn=refresh_fn)
        with respx.mock:
            route = respx.get(f"{BASE}/sse")
            route.side_effect = [
                httpx.Response(401),
                httpx.Response(200, text=sse_body),
            ]
            events = []
            async for event_type, data in client.stream_sse("/sse"):
                events.append((event_type, data))

        assert len(events) == 1
        assert events[0] == ("ping", "pong")

    async def test_stream_sse_no_refresh_raises_on_401(self) -> None:
        import pytest

        client = APIClient(base_url=BASE, access_token="old")
        with respx.mock:
            respx.get(f"{BASE}/sse").mock(return_value=httpx.Response(401))
            with pytest.raises(httpx.HTTPStatusError):
                async for _ in client.stream_sse("/sse"):
                    pass
