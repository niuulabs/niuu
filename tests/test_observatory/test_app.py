"""Tests for the Observatory backend app."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from starlette.testclient import TestClient

from observatory.app import _events_stream, _topology_stream, create_app
from observatory.registry import (
    InMemoryObservatoryRegistryRepository,
    RegistryNotFoundError,
    RegistryValidationError,
    seed_registry_payload,
)


class _FakeDiscoveryService:
    base_url = "http://testserver"

    async def get_topology_snapshot(self) -> dict[str, object]:
        return {
            "nodes": [
                {
                    "id": "realm:test",
                    "typeId": "realm",
                    "label": "test",
                    "parentId": None,
                    "status": "healthy",
                }
            ],
            "edges": [],
            "timestamp": "2026-05-13T12:00:00Z",
        }

    async def get_events(self) -> list[dict[str, str]]:
        return [
            {
                "id": "ev-1",
                "time": "12:00:00",
                "type": "TYR",
                "subject": "raid summary",
                "body": "queued=1",
            }
        ]


class _SequenceDiscoveryService:
    base_url = "http://sequence.test"

    def __init__(self) -> None:
        self._topology_calls = 0
        self._events_calls = 0

    async def get_topology_snapshot(self) -> dict[str, object]:
        self._topology_calls += 1
        return {
            "nodes": [{"id": "realm:test"}],
            "edges": [],
            "timestamp": "2026-05-13T12:00:00Z",
        }

    async def get_events(self) -> list[dict[str, str]]:
        self._events_calls += 1
        return [
            {
                "id": "ev-1",
                "time": "12:00:00",
                "type": "TYR",
                "subject": "raid summary",
                "body": "queued=1",
            }
        ]


class _RaisingRepository(InMemoryObservatoryRegistryRepository):
    async def save_registry(self, registry: dict[str, object]) -> dict[str, object]:
        raise RegistryValidationError("save failed")

    async def create_type(self, entity_type: dict[str, object]) -> dict[str, object]:
        raise RegistryValidationError("create failed")

    async def update_type(self, type_id: str, patch: dict[str, object]) -> dict[str, object]:
        if type_id == "missing":
            raise RegistryNotFoundError(type_id)
        raise RegistryValidationError("update failed")

    async def delete_type(self, type_id: str) -> dict[str, object]:
        raise RegistryNotFoundError(type_id)


def _extract_sse_payload(chunk: str) -> dict[str, object]:
    for line in chunk.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"No SSE payload found in chunk: {chunk!r}")


def _make_client() -> TestClient:
    app = create_app(
        registry_repository=InMemoryObservatoryRegistryRepository(),
        discovery_service=_FakeDiscoveryService(),
    )
    return TestClient(app)


class TestObservatoryApp:
    def test_registry_returns_seed_payload(self) -> None:
        with _make_client() as client:
            response = client.get("/api/v1/observatory/registry")
            assert response.status_code == 200
            payload = response.json()
            assert payload["version"] == 7
            assert any(item["id"] == "mimir" for item in payload["types"])

    def test_registry_put_persists_changes(self) -> None:
        with _make_client() as client:
            payload = client.get("/api/v1/observatory/registry").json()
            realm = next(item for item in payload["types"] if item["id"] == "realm")
            realm["label"] = "Realm Prime"

            response = client.put("/api/v1/observatory/registry", json=payload)
            assert response.status_code == 200
            saved = response.json()
            saved_realm = next(item for item in saved["types"] if item["id"] == "realm")
            assert saved_realm["label"] == "Realm Prime"
            assert saved["version"] == payload["version"] + 1

    def test_type_patch_renames_and_rewrites_references(self) -> None:
        with _make_client() as client:
            response = client.patch(
                "/api/v1/observatory/registry/types/cluster",
                json={"id": "cluster_prime", "label": "Cluster Prime"},
            )
            assert response.status_code == 200
            payload = response.json()
            realm = next(item for item in payload["types"] if item["id"] == "realm")
            volundr = next(item for item in payload["types"] if item["id"] == "volundr")
            assert "cluster_prime" in realm["canContain"]
            assert "cluster_prime" in volundr["parentTypes"]

    def test_type_delete_cleans_references(self) -> None:
        with _make_client() as client:
            response = client.delete("/api/v1/observatory/registry/types/cluster")
            assert response.status_code == 200
            payload = response.json()
            realm = next(item for item in payload["types"] if item["id"] == "realm")
            volundr = next(item for item in payload["types"] if item["id"] == "volundr")
            assert "cluster" not in realm["canContain"]
            assert "cluster" not in volundr["parentTypes"]

    def test_settings_returns_discovery_metadata(self) -> None:
        with _make_client() as client:
            response = client.get("/api/v1/observatory/settings")
            assert response.status_code == 200
            payload = response.json()
            assert payload["title"] == "Observatory"
            assert payload["sections"][0]["id"] == "streams"
            assert any(
                field["key"] == "discovery_base_url"
                for field in payload["sections"][0]["fields"]
            )

    def test_topology_stream_aliases_return_sse(self) -> None:
        first_chunk = asyncio.run(anext(_topology_stream(_FakeDiscoveryService())))
        payload = _extract_sse_payload(first_chunk)
        assert payload["nodes"]
        assert payload["timestamp"].endswith("Z")

    def test_events_stream_aliases_return_events(self) -> None:
        first_chunk = asyncio.run(anext(_events_stream(_FakeDiscoveryService())))
        payload = _extract_sse_payload(first_chunk)
        assert payload["type"] == "TYR"
        assert payload["subject"] == "raid summary"
        assert payload["body"] == "queued=1"

    def test_topology_stream_emits_keepalive_when_timestamp_is_unchanged(self, monkeypatch) -> None:
        async def _fast_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("observatory.app.asyncio.sleep", _fast_sleep)
        stream = _topology_stream(_SequenceDiscoveryService())
        first_chunk = asyncio.run(anext(stream))
        second_chunk = asyncio.run(anext(stream))
        assert "event: topology.snapshot" in first_chunk
        assert second_chunk == ": keepalive\n\n"

    def test_events_stream_emits_keepalive_when_no_new_events(self, monkeypatch) -> None:
        async def _fast_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("observatory.app.asyncio.sleep", _fast_sleep)
        stream = _events_stream(_SequenceDiscoveryService())
        first_chunk = asyncio.run(anext(stream))
        second_chunk = asyncio.run(anext(stream))
        assert "event: observatory.event" in first_chunk
        assert second_chunk == ": keepalive\n\n"

    def test_registry_errors_return_http_statuses(self) -> None:
        app = create_app(
            registry_repository=_RaisingRepository(),
            discovery_service=_FakeDiscoveryService(),
        )
        with TestClient(app) as client:
            registry = client.get("/api/v1/observatory/registry").json()
            assert client.put("/api/v1/observatory/registry", json=registry).status_code == 422
            assert client.post("/api/v1/observatory/registry/types", json={}).status_code == 422
            assert (
                client.patch(
                    "/api/v1/observatory/registry/types/missing",
                    json={"label": "Missing"},
                ).status_code
                == 404
            )
            assert (
                client.patch(
                    "/api/v1/observatory/registry/types/realm",
                    json={"label": "Invalid"},
                ).status_code
                == 422
            )
            assert client.delete("/api/v1/observatory/registry/types/missing").status_code == 404

    def test_create_app_uses_postgres_repo_in_lifespan(self, monkeypatch) -> None:
        class _FakePostgresRepository:
            def __init__(self, pool: object) -> None:
                self.pool = pool
                self.seeded = False

            async def ensure_seeded(self) -> None:
                self.seeded = True

            async def get_registry(self) -> dict[str, object]:
                return seed_registry_payload()

        fake_pool = object()

        @asynccontextmanager
        async def _fake_database_pool(_settings: object):
            yield fake_pool

        monkeypatch.setattr("observatory.app.database_pool", _fake_database_pool)
        monkeypatch.setattr(
            "observatory.app.PostgresObservatoryRegistryRepository",
            _FakePostgresRepository,
        )

        app = create_app(discovery_service=_FakeDiscoveryService())
        with TestClient(app) as client:
            response = client.get("/api/v1/observatory/registry")
            assert response.status_code == 200
            repo = client.app.state.registry_repository
            assert isinstance(repo, _FakePostgresRepository)
            assert repo.pool is fake_pool
            assert repo.seeded is True
