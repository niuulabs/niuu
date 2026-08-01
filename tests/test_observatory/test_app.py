"""Tests for the Observatory backend app."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from starlette.testclient import TestClient

from niuu.domain.agent_directory import AgentDirectoryEntry, AgentDirectoryPage
from observatory.app import _events_stream, _topology_stream, create_app
from observatory.registry import (
    InMemoryObservatoryRegistryRepository,
    RegistryNotFoundError,
    RegistryValidationError,
    seed_registry_payload,
)


class _FakeDiscoveryService:
    guild_url = "http://guild.test"
    base_url = "http://guild.test"

    async def get_topology_snapshot(
        self,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        del headers
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

    async def get_events(
        self,
        headers: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        del headers
        return [
            {
                "id": "ev-1",
                "time": "12:00:00",
                "type": "TING",
                "subject": "run summary",
                "body": "queued=1",
            }
        ]


class _SequenceDiscoveryService:
    guild_url = "http://guild.test"
    base_url = "http://guild.test"

    def __init__(self) -> None:
        self._topology_calls = 0
        self._events_calls = 0

    async def get_topology_snapshot(
        self,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        del headers
        self._topology_calls += 1
        return {
            "nodes": [{"id": "realm:test"}],
            "edges": [],
            "timestamp": "2026-05-13T12:00:00Z",
        }

    async def get_events(
        self,
        headers: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        del headers
        self._events_calls += 1
        return [
            {
                "id": "ev-1",
                "time": "12:00:00",
                "type": "TING",
                "subject": "run summary",
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


def _directory_entry() -> AgentDirectoryEntry:
    return AgentDirectoryEntry(
        id="agent-1",
        canonicalId="source:observatory-a:session-a",
        sourceAgentId="session-a",
        sourceInstanceId="observatory-a",
        clusterId="noatun",
        environmentId="environment-a",
        topologyNodeId="runtime:noatun:skuld:skuld:session-a",
        name="Builder",
        description="Builds software",
        kind="workflow-session",
        cardUrl="https://agents.example.test/.well-known/agent-card.json",
        cardVersion="1.0.0",
        cardHash="card-hash",
        skillIds=["code"],
        tags=["engineering"],
        observedStatus="healthy",
        activity="tooling",
        ownerId="user-a",
        tenantId="tenant-a",
        visibility="user",
    )


class _StubAgentDirectory:
    def __init__(self) -> None:
        self.entry = _directory_entry()
        self.list_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []

    async def list_agents(self, principal, *, headers, filters):
        self.list_calls.append({"principal": principal, "headers": headers, "filters": filters})
        return AgentDirectoryPage(items=[self.entry], revision="revision-a")

    async def get_agent(self, agent_id, principal, *, headers):
        self.get_calls.append({"agent_id": agent_id, "principal": principal, "headers": headers})
        return self.entry if agent_id == self.entry.id else None


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
    def test_health_aliases_return_status_and_guild_url(self) -> None:
        with _make_client() as client:
            root = client.get("/health")
            api = client.get("/api/v1/observatory/health")

        assert root.status_code == 200
        assert api.status_code == 200
        assert root.json() == {"status": "healthy", "guildUrl": "http://guild.test"}
        assert api.json() == root.json()

    def test_registry_returns_seed_payload(self) -> None:
        with _make_client() as client:
            response = client.get("/api/v1/observatory/registry")
            assert response.status_code == 200
            payload = response.json()
            assert payload["version"] == 10
            assert any(item["id"] == "mimir" for item in payload["types"])
            assert any(item["id"] == "namespace" for item in payload["types"])

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
            assert any(field["key"] == "guild_url" for field in payload["sections"][0]["fields"])

    def test_agent_directory_forwards_principal_auth_and_all_filters(self) -> None:
        directory = _StubAgentDirectory()
        app = create_app(
            registry_repository=InMemoryObservatoryRegistryRepository(),
            discovery_service=_FakeDiscoveryService(),
            agent_directory_service=directory,
        )
        headers = {
            "authorization": "Bearer token",
            "x-auth-user-id": "user-a",
            "x-auth-tenant": "tenant-a",
        }
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/observatory/agents"
                "?skill=code&tag=engineering&kind=workflow-session&status=healthy"
                "&environmentId=environment-a&cluster=noatun&instance=observatory-a",
                headers=headers,
            )

        assert response.status_code == 200
        assert response.json()["items"][0]["topologyNodeId"].endswith("session-a")
        call = directory.list_calls[-1]
        assert call["principal"].user_id == "user-a"
        assert call["headers"]["authorization"] == "Bearer token"
        assert call["filters"].skills == ("code",)
        assert call["filters"].tags == ("engineering",)
        assert call["filters"].kinds == ("workflow-session",)
        assert call["filters"].statuses == ("healthy",)
        assert call["filters"].environment_ids == ("environment-a",)
        assert call["filters"].cluster_ids == ("noatun",)
        assert call["filters"].instance_ids == ("observatory-a",)

    def test_agent_directory_detail_does_not_disclose_missing_agent(self) -> None:
        directory = _StubAgentDirectory()
        app = create_app(
            registry_repository=InMemoryObservatoryRegistryRepository(),
            discovery_service=_FakeDiscoveryService(),
            agent_directory_service=directory,
        )
        with TestClient(app) as client:
            found = client.get(
                "/api/v1/observatory/agents/agent-1",
                headers={"x-auth-user-id": "user-a", "x-auth-tenant": "tenant-a"},
            )
            missing = client.get(
                "/api/v1/observatory/agents/inaccessible",
                headers={"x-auth-user-id": "user-a", "x-auth-tenant": "tenant-a"},
            )

        assert found.status_code == 200
        assert found.json()["sourceAgentId"] == "session-a"
        assert missing.status_code == 404
        assert missing.json() == {"detail": "Agent not found"}

    def test_topology_stream_aliases_return_sse(self) -> None:
        first_chunk = asyncio.run(anext(_topology_stream(_FakeDiscoveryService())))
        payload = _extract_sse_payload(first_chunk)
        assert payload["nodes"]
        assert payload["timestamp"].endswith("Z")

    def test_events_stream_aliases_return_events(self) -> None:
        first_chunk = asyncio.run(anext(_events_stream(_FakeDiscoveryService())))
        payload = _extract_sse_payload(first_chunk)
        assert payload["type"] == "TING"
        assert payload["subject"] == "run summary"
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

    def test_topology_stream_emits_keepalive_when_only_the_timestamp_moved(
        self, monkeypatch
    ) -> None:
        """The bug this replaces: every materialization gets a fresh timestamp,
        so comparing it resent the whole snapshot on every tick."""

        class _RestampingService(_FakeDiscoveryService):
            def __init__(self) -> None:
                self._tick = 0

            async def get_topology_snapshot(
                self, headers: dict[str, str] | None = None
            ) -> dict[str, object]:
                snapshot = await super().get_topology_snapshot(headers=headers)
                self._tick += 1
                snapshot["timestamp"] = f"2026-05-13T12:00:0{self._tick}Z"
                snapshot["revision"] = "unchanged"
                return snapshot

        async def _fast_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("observatory.app.asyncio.sleep", _fast_sleep)
        stream = _topology_stream(_RestampingService())
        first_chunk = asyncio.run(anext(stream))
        second_chunk = asyncio.run(anext(stream))

        assert "event: topology.snapshot" in first_chunk
        assert second_chunk == ": keepalive\n\n"

    def test_topology_stream_resends_when_the_revision_changes(self, monkeypatch) -> None:
        class _ChangingService(_FakeDiscoveryService):
            def __init__(self) -> None:
                self._tick = 0

            async def get_topology_snapshot(
                self, headers: dict[str, str] | None = None
            ) -> dict[str, object]:
                snapshot = await super().get_topology_snapshot(headers=headers)
                self._tick += 1
                snapshot["revision"] = f"rev-{self._tick}"
                return snapshot

        async def _fast_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr("observatory.app.asyncio.sleep", _fast_sleep)
        stream = _topology_stream(_ChangingService())
        asyncio.run(anext(stream))
        second_chunk = asyncio.run(anext(stream))

        assert "event: topology.snapshot" in second_chunk

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
            response = client.delete("/api/v1/observatory/registry/types/missing")
            assert response.status_code == 404

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


class TestObservatoryFragment:
    """Fragment is the authenticated endpoint an aggregator should move to."""

    def test_returns_this_sources_view_with_its_identity(self) -> None:
        app = create_app(
            registry_repository=InMemoryObservatoryRegistryRepository(),
            discovery_service=_FakeDiscoveryService(),
        )
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/observatory/fragment",
                headers={"x-auth-user-id": "user-a", "x-auth-tenant": "tenant-a"},
            )

        assert response.status_code == 200
        body = response.json()
        assert [node["id"] for node in body["nodes"]] == ["realm:test"]
        assert body["meta"]["sourceKind"] == "observatory"
        assert body["meta"]["sourceId"]

    def test_is_camel_case_on_the_wire(self) -> None:
        app = create_app(
            registry_repository=InMemoryObservatoryRegistryRepository(),
            discovery_service=_FakeDiscoveryService(),
        )
        with TestClient(app) as client:
            body = client.get(
                "/api/v1/observatory/fragment",
                headers={"x-auth-user-id": "user-a", "x-auth-tenant": "tenant-a"},
            ).json()

        assert "typeId" in body["nodes"][0]
        assert "type_id" not in body["nodes"][0]
        assert "sourceId" in body["meta"]

    def test_carries_the_events_alongside_the_graph(self) -> None:
        app = create_app(
            registry_repository=InMemoryObservatoryRegistryRepository(),
            discovery_service=_FakeDiscoveryService(),
        )
        with TestClient(app) as client:
            body = client.get(
                "/api/v1/observatory/fragment",
                headers={"x-auth-user-id": "user-a", "x-auth-tenant": "tenant-a"},
            ).json()

        assert body["events"]
