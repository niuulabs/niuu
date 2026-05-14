"""Tests for Observatory local discovery materialization."""

from __future__ import annotations

from collections import Counter

import httpx
import pytest

from observatory.discovery import (
    ObservatoryDiscoveryService,
    _cluster_identity,
    _discovery_base_url,
    _node_status,
    _safe_int,
    _slug,
)


@pytest.mark.asyncio
async def test_discovery_materializes_local_platform_graph() -> None:
    payloads = {
        "/health": {"status": "ok"},
        "/api/v1/forge/resources": {
            "nodes": [
                {
                    "name": "node-a",
                    "labels": {"kubernetes.io/os": "linux", "nvidia.com/gpu.product": "L40"},
                }
            ]
        },
        "/api/v1/ravn/status": {"healthy": True},
        "/api/v1/ravn/wardens": [{"id": "w1", "name": "Watcher", "persona": "memory"}],
        "/api/v1/tyr/health": {"status": "ok"},
        "/api/v1/tyr/raids/summary": {"queued": 1, "running": 2},
        "/api/v1/tyr/raids/active": [
            {"tracker_id": "raid-1", "identifier": "RAID-1", "title": "Patch", "status": "running"}
        ],
        "/api/v1/mimir/stats": {"page_count": 42, "healthy": True},
        "/api/v1/mimir/mounts": [{"name": "shared", "kind": "local"}],
        "/api/v1/bifrost/providers/health": [{"key": "openai", "state": "healthy"}],
        "/api/v1/bifrost/models": [{"id": "gpt-5", "name": "GPT-5", "provider": "openai"}],
    }

    async def fetch(path: str):
        return payloads[path]

    service = ObservatoryDiscoveryService(fetch_json=fetch, ttl_seconds=0)
    topology = await service.get_topology_snapshot()
    events = await service.get_events()

    type_ids = {node["typeId"] for node in topology["nodes"]}
    assert "realm" in type_ids
    assert "cluster" in type_ids
    assert "volundr" in type_ids
    assert "tyr" in type_ids
    assert "mimir" in type_ids
    assert "bifrost" in type_ids
    assert "ravn_long" in type_ids
    assert "host" in type_ids
    assert "mimir_sub" not in type_ids
    mimir = next(node for node in topology["nodes"] if node["id"] == "service:mimir")
    assert mimir["mountCount"] == 1
    assert mimir["mounts"] == ["shared"]
    assert any(event["type"] == "RAID" for event in events)


@pytest.mark.asyncio
async def test_discovery_survives_missing_surfaces() -> None:
    async def fetch(_path: str):
        raise RuntimeError("unavailable")

    service = ObservatoryDiscoveryService(fetch_json=fetch, ttl_seconds=0)
    topology = await service.get_topology_snapshot()
    assert any(node["id"] == "service:observatory" for node in topology["nodes"])
    assert any(node["id"] == "service:niuu" for node in topology["nodes"])


@pytest.mark.asyncio
async def test_discovery_uses_http_transport_and_cache(monkeypatch) -> None:
    calls: Counter[str] = Counter()
    payloads = {
        "/health": {"status": "ok"},
        "/api/v1/forge/resources": {"nodes": []},
        "/api/v1/ravn/status": {"healthy": False},
        "/api/v1/ravn/wardens": [],
        "/api/v1/tyr/health": {"status": "ok"},
        "/api/v1/tyr/raids/summary": {"queued": 0},
        "/api/v1/tyr/raids/active": [],
        "/api/v1/mimir/stats": {"page_count": "7"},
        "/api/v1/mimir/mounts": [],
        "/api/v1/bifrost/providers/health": [{"vendor": "openai", "state": "degraded"}],
        "/api/v1/bifrost/models": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls[request.url.path] += 1
        payload = payloads.get(request.url.path)
        if payload is None:
            return httpx.Response(503, json={"error": "missing"})
        return httpx.Response(200, json=payload)

    monkeypatch.setenv("POD_NAMESPACE", "observed")
    monkeypatch.setenv("OBSERVATORY_CLUSTER", "valaskjalf")
    service = ObservatoryDiscoveryService(
        base_url="http://observatory.test",
        ttl_seconds=60,
        transport=httpx.MockTransport(handler),
    )

    topology = await service.get_topology_snapshot()
    topology["nodes"].append({"id": "mutated"})
    second = await service.get_topology_snapshot()
    events = await service.get_events()

    assert calls["/health"] == 1
    assert not any(node["id"] == "mutated" for node in second["nodes"])
    assert any(node["id"] == "cluster:valaskjalf" for node in second["nodes"])
    assert any(event["type"] == "BIFROST" for event in events)


def test_discovery_helpers_cover_common_normalization_cases(monkeypatch) -> None:
    monkeypatch.delenv("POD_NAMESPACE", raising=False)
    monkeypatch.setenv("NAMESPACE", "kanuck")
    monkeypatch.setenv("NIUU_CLUSTER", "noatun")
    assert _cluster_identity() == ("kanuck", "noatun")
    assert _slug("Valhalla / Cluster") == "valhalla---cluster"
    assert _node_status(None) == "unknown"
    assert _node_status(False) == "failed"
    assert _node_status(True, degraded=True) == "degraded"
    assert _safe_int(True, default=9) == 9
    assert _safe_int(7.5) == 7
    assert _safe_int("12") == 12
    assert _safe_int("oops", default=3) == 3


def test_discovery_base_url_follows_bound_server_host(monkeypatch) -> None:
    assert _discovery_base_url("192.168.1.106", 8080) == "http://192.168.1.106:8080"
    assert _discovery_base_url("0.0.0.0", 18080) == "http://127.0.0.1:18080"
