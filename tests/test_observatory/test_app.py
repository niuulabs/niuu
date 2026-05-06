"""Tests for the Observatory backend app."""

from __future__ import annotations

import asyncio
import json

from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from observatory.app import create_app, create_router


def _extract_sse_payload(chunk: str) -> dict[str, object]:
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"No SSE payload found in chunk: {chunk!r}")


class TestObservatoryApp:
    def test_registry_returns_seed_payload(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/v1/observatory/registry")
        assert response.status_code == 200
        payload = response.json()
        assert payload["version"] == 7
        assert any(item["id"] == "mimir" for item in payload["types"])

    def test_settings_returns_mounted_schema(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/v1/observatory/settings")
        assert response.status_code == 200
        payload = response.json()
        assert payload["title"] == "Observatory"
        assert payload["sections"][0]["id"] == "streams"
        assert any(
            field["key"] == "keepalive_interval_seconds"
            for field in payload["sections"][0]["fields"]
        )

    def test_topology_stream_aliases_return_sse(self) -> None:
        for path in ("/api/v1/observatory/topology", "/api/v1/observatory/topology/stream"):
            response = asyncio.run(_route_response(path))
            assert response.media_type == "text/event-stream"
            first_chunk = asyncio.run(anext(response.body_iterator))
            payload = _extract_sse_payload(first_chunk)
            assert payload["nodes"]
            assert payload["edges"]
            assert payload["timestamp"].endswith("Z")

    def test_events_stream_aliases_return_seed_events(self) -> None:
        for path in ("/api/v1/observatory/events", "/api/v1/observatory/events/stream"):
            response = asyncio.run(_route_response(path))
            assert response.media_type == "text/event-stream"
            first_chunk = asyncio.run(anext(response.body_iterator))
            payload = _extract_sse_payload(first_chunk)
            assert payload["type"] == "RAID"
            assert payload["subject"] == "raid-omega"
            assert payload["body"]


async def _route_response(path: str):
    for route in create_router().routes:
        if isinstance(route, APIRoute) and route.path == path:
            return await route.endpoint()
    raise AssertionError(f"No route found for {path}")
