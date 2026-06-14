"""Tests for Observatory Guild-backed discovery materialization."""

from __future__ import annotations

from collections import Counter

import httpx
import pytest

from niuu.adapters.outbound.http_auth import NoAuthHeaderAdapter
from observatory.discovery import ObservatoryDiscoveryService


@pytest.mark.asyncio
async def test_discovery_materializes_guild_snapshot() -> None:
    payload = {
        "timestamp": "2026-05-17T12:00:00Z",
        "layoutHints": {"mode": "hybrid", "scope": "world"},
        "nodes": [
            {
                "id": "service:guild",
                "typeId": "service",
                "label": "Guild",
                "status": "healthy",
            },
            {
                "id": "instance:volundr:alpha",
                "typeId": "service",
                "label": "Volundr Alpha",
                "parentId": "kind:volundr",
                "status": "healthy",
                "svcType": "volundr",
            },
            {
                "id": "instance:bifrost:shared",
                "typeId": "service",
                "label": "Bifrost Shared",
                "parentId": "kind:bifrost",
                "status": "healthy",
                "svcType": "bifrost",
            },
        ],
        "edges": [
            {
                "id": "edge:guild:volundr",
                "sourceId": "service:guild",
                "targetId": "kind:volundr",
                "kind": "solid",
            }
        ],
        "events": [
            {
                "id": "instance:1:up",
                "level": "info",
                "service": "volundr",
                "message": "Volundr Alpha is reachable",
                "timestamp": "2026-05-17T12:00:00Z",
            }
        ],
    }

    async def fetch(path: str):
        assert path == "/api/v1/niuu/observatory/snapshot"
        return payload

    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        auth=NoAuthHeaderAdapter(),
        fetch_json=fetch,
        ttl_seconds=0,
    )
    topology = await service.get_topology_snapshot()
    events = await service.get_events()

    assert topology["timestamp"] == "2026-05-17T12:00:00Z"
    assert topology["layoutHints"] == {"mode": "hybrid", "scope": "world"}
    assert any(node["id"] == "service:guild" for node in topology["nodes"])
    assert any(node.get("svcType") == "volundr" for node in topology["nodes"])
    assert any(node.get("svcType") == "bifrost" for node in topology["nodes"])
    assert events[0]["service"] == "volundr"


@pytest.mark.asyncio
async def test_discovery_survives_missing_guild() -> None:
    async def fetch(_path: str):
        raise RuntimeError("unavailable")

    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        auth=NoAuthHeaderAdapter(),
        fetch_json=fetch,
        ttl_seconds=0,
    )
    topology = await service.get_topology_snapshot()
    events = await service.get_events()
    assert any(node["id"] == "service:observatory" for node in topology["nodes"])
    assert events[0]["message"] == "Guild discovery endpoint unavailable"


@pytest.mark.asyncio
async def test_discovery_uses_http_transport_and_cache() -> None:
    calls: Counter[str] = Counter()
    payload = {
        "timestamp": "2026-05-17T12:00:00Z",
        "nodes": [{"id": "service:guild", "typeId": "service", "label": "Guild"}],
        "edges": [],
        "events": [{"id": "ev-1", "service": "guild", "message": "ok"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls[request.url.path] += 1
        assert request.headers.get("authorization") is None
        return httpx.Response(200, json=payload)

    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        auth=NoAuthHeaderAdapter(),
        ttl_seconds=60,
        transport=httpx.MockTransport(handler),
    )

    topology = await service.get_topology_snapshot()
    topology["nodes"].append({"id": "mutated"})
    second = await service.get_topology_snapshot()
    events = await service.get_events()

    assert calls["/api/v1/niuu/observatory/snapshot"] == 1
    assert not any(node["id"] == "mutated" for node in second["nodes"])
    assert second["nodes"][0]["id"] == "service:guild"
    assert events[0]["service"] == "guild"


@pytest.mark.asyncio
async def test_discovery_forwards_request_auth_headers_to_guild() -> None:
    payload = {
        "timestamp": "2026-05-17T12:00:00Z",
        "nodes": [{"id": "service:guild", "typeId": "service", "label": "Guild"}],
        "edges": [],
        "events": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer request-token"
        assert request.headers.get("x-auth-user-id") == "user-123"
        return httpx.Response(200, json=payload)

    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        auth=NoAuthHeaderAdapter(),
        ttl_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    topology = await service.get_topology_snapshot(
        headers={
            "authorization": "Bearer request-token",
            "x-auth-user-id": "user-123",
        }
    )

    assert topology["nodes"][0]["id"] == "service:guild"
