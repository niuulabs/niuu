"""Tests for the REST API adapter."""

from datetime import UTC
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import MockEventBroadcaster
from volundr.adapters.inbound.rest import _server_side_ws_connect_overrides, create_router
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.domain.models import DeviceToken, EventType, RealtimeEvent
from volundr.domain.ports import DeviceTokenRepository
from volundr.domain.services import SessionService, StatsService


def test_server_side_ws_connect_overrides_openshell_service_host(monkeypatch):
    monkeypatch.setenv(
        "OPENSHELL_INTERNAL_GATEWAY_URL",
        "http://openshell.openshell.svc.cluster.local:8080",
    )

    overrides = _server_side_ws_connect_overrides(
        "ws://forge-8093e93dc7634efeb2c382--skuld.openshell.localhost:8080/session"
    )

    assert overrides == {
        "host": "openshell.openshell.svc.cluster.local",
        "port": 8080,
        "proxy": None,
    }


class _FakeDeviceRepo(DeviceTokenRepository):
    """In-memory device repository for REST endpoint tests."""

    def __init__(self):
        self.devices: list[DeviceToken] = []

    async def upsert(self, device: DeviceToken) -> DeviceToken:
        self.devices = [
            d
            for d in self.devices
            if not (d.owner_id == device.owner_id and d.token == device.token)
        ]
        self.devices.append(device)
        return device

    async def list_for_owner(self, owner_id: str) -> list[DeviceToken]:
        return [d for d in self.devices if d.owner_id == owner_id]

    async def delete(self, owner_id: str, token: str) -> bool:
        before = len(self.devices)
        self.devices = [
            d for d in self.devices if not (d.owner_id == owner_id and d.token == token)
        ]
        return len(self.devices) < before


class _StubIdentity:
    """Minimal identity so _optional_principal resolves a header principal."""

    async def get_or_provision_user(self, principal):
        return None


class TestDeviceEndpoints:
    """Tests for the push device registration endpoints."""

    @pytest.fixture
    def device_repo(self):
        return _FakeDeviceRepo()

    @pytest.fixture
    def client(self, repository, pod_manager, stats_repository, pricing_provider, device_repo):
        app = FastAPI()
        app.state.identity = _StubIdentity()
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        router = create_router(
            session_service=session_service,
            stats_service=StatsService(stats_repository),
            pricing_provider=pricing_provider,
            device_repository=device_repo,
        )
        app.include_router(router)
        return TestClient(app)

    _AUTH = {"x-auth-user-id": "user-1"}

    def test_register_then_list(self, client, device_repo):
        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "tok-1", "app_bundle_id": "com.niuu.forge"},
            headers=self._AUTH,
        )
        assert resp.status_code == 201
        assert resp.json()["platform"] == "ios"
        assert resp.json()["token"] == "tok-1"

        listed = client.get("/api/v1/forge/devices", headers=self._AUTH)
        assert listed.status_code == 200
        assert [d["token"] for d in listed.json()] == ["tok-1"]

    def test_register_is_idempotent(self, client):
        body = {"platform": "ios", "token": "tok-1"}
        client.post("/api/v1/forge/devices", json=body, headers=self._AUTH)
        client.post("/api/v1/forge/devices", json=body, headers=self._AUTH)
        listed = client.get("/api/v1/forge/devices", headers=self._AUTH)
        assert len(listed.json()) == 1

    def test_register_rejects_bad_platform(self, client):
        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "blackberry", "token": "t"},
            headers=self._AUTH,
        )
        assert resp.status_code == 422

    def test_unregister(self, client):
        client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "tok-1"},
            headers=self._AUTH,
        )
        resp = client.request("DELETE", "/api/v1/forge/devices/tok-1", headers=self._AUTH)
        assert resp.status_code == 204
        listed = client.get("/api/v1/forge/devices", headers=self._AUTH)
        assert listed.json() == []

    def test_requires_authentication(self, client):
        # No x-auth-user-id header -> no principal -> 401.
        assert client.get("/api/v1/forge/devices").status_code == 401

    def test_owner_isolation(self, client):
        client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "tok-1"},
            headers={"x-auth-user-id": "user-1"},
        )
        other = client.get("/api/v1/forge/devices", headers={"x-auth-user-id": "user-2"})
        assert other.json() == []

    def test_503_when_repository_absent(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        app = FastAPI()
        app.state.identity = _StubIdentity()
        router = create_router(
            session_service=SessionService(repository=repository, pod_manager=pod_manager),
            stats_service=StatsService(stats_repository),
            pricing_provider=pricing_provider,
        )
        app.include_router(router)
        client = TestClient(app)
        resp = client.post(
            "/api/v1/forge/devices",
            json={"platform": "ios", "token": "t"},
            headers=self._AUTH,
        )
        assert resp.status_code == 503


class TestSSEEndpoint:
    """Tests for the SSE streaming endpoint."""

    def test_sse_endpoint_without_broadcaster_returns_503(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """SSE endpoint returns 503 when broadcaster is not available."""
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
        )
        stats_service = StatsService(stats_repository)

        # Create router without broadcaster
        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
            broadcaster=None,
        )
        app.include_router(router)

        client = TestClient(app)
        response = client.get("/api/v1/forge/sessions/stream")

        assert response.status_code == 503
        assert "Event streaming not available" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_sse_endpoint_streams_events(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """SSE endpoint streams events from broadcaster."""
        from collections.abc import AsyncGenerator
        from datetime import datetime

        class _FiniteBroadcaster(InMemoryEventBroadcaster):
            """Yields a preset list of events then terminates.

            HTTPX's ASGITransport runs the ASGI app to completion before
            returning, so infinite SSE generators deadlock. A finite
            broadcaster lets StreamingResponse close naturally.
            """

            def __init__(self, events: list[RealtimeEvent]):
                super().__init__()
                self._preset = events

            async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
                for ev in self._preset:
                    yield ev

        event = RealtimeEvent(
            type=EventType.HEARTBEAT,
            data={"test": "data"},
            timestamp=datetime.now(UTC),
        )
        broadcaster = _FiniteBroadcaster([event])
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
        )
        stats_service = StatsService(stats_repository)

        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
            broadcaster=broadcaster,
        )
        app.include_router(router)

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/forge/sessions/stream")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert response.headers.get("cache-control") == "no-cache"
        lines = [line for line in response.text.splitlines() if line]
        assert any("event: heartbeat" in line for line in lines)


class TestSessionEndpoints:
    """Tests for session CRUD endpoints."""

    @pytest.fixture
    def mock_broadcaster(self):
        """Create a mock broadcaster for testing."""
        return MockEventBroadcaster()

    @pytest.fixture
    def app(self, repository, pod_manager, stats_repository, pricing_provider, mock_broadcaster):
        """Create a FastAPI app with the router."""
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=mock_broadcaster,
        )
        stats_service = StatsService(stats_repository)

        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
            broadcaster=mock_broadcaster,
        )
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return TestClient(app)

    def test_create_session(self, client, mock_broadcaster):
        """Creating a session via API creates and starts it, publishes events."""
        response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "test-session",
                "model": "claude-sonnet-4-20250514",
                "repo": "https://github.com/test/repo",
                "branch": "main",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-session"
        assert data["status"] == "starting"

        # Verify events were published (created + updated for starting + updated for provisioning)
        assert len(mock_broadcaster.session_created_events) == 1

    def test_list_sessions(self, client):
        """List sessions endpoint returns empty list initially."""
        response = client.get("/api/v1/forge/sessions")

        assert response.status_code == 200
        assert response.json() == []

    def test_get_session_not_found(self, client):
        """Getting a non-existent session returns 404."""
        response = client.get("/api/v1/forge/sessions/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404

    def test_update_session(self, client, mock_broadcaster):
        """Updating a session via API publishes event."""
        # Create session first
        create_response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "test-session",
                "model": "claude-sonnet-4-20250514",
                "repo": "https://github.com/test/repo",
                "branch": "main",
            },
        )
        session_id = create_response.json()["id"]

        # Clear created events
        mock_broadcaster._session_updated_events.clear()

        # Update session
        response = client.put(
            f"/api/v1/forge/sessions/{session_id}",
            json={"name": "updated-name"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "updated-name"

        # Verify event was published
        assert len(mock_broadcaster.session_updated_events) == 1

    def test_delete_session(self, client, mock_broadcaster):
        """Deleting a session via API publishes event."""
        # Create session first
        create_response = client.post(
            "/api/v1/forge/sessions",
            json={
                "name": "test-session",
                "model": "claude-sonnet-4-20250514",
                "repo": "https://github.com/test/repo",
                "branch": "main",
            },
        )
        session_id = create_response.json()["id"]

        # Delete session
        response = client.delete(f"/api/v1/forge/sessions/{session_id}")

        assert response.status_code == 204

        # Verify event was published
        assert len(mock_broadcaster.session_deleted_events) == 1


class TestStatsEndpoint:
    """Tests for the stats endpoint."""

    @pytest.fixture
    def app(self, repository, pod_manager, stats_repository, pricing_provider):
        """Create a FastAPI app with the router."""
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
        )
        stats_service = StatsService(stats_repository)

        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
        )
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return TestClient(app)

    def test_get_stats(self, client, stats_repository):
        """Stats endpoint returns current statistics."""
        stats_repository.set_stats(
            active_sessions=3,
            total_sessions=10,
            tokens_today=5000,
            local_tokens=1000,
            cloud_tokens=4000,
            cost_today=Decimal("2.50"),
        )

        response = client.get("/api/v1/forge/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == 3
        assert data["total_sessions"] == 10
        assert data["tokens_today"] == 5000
        assert data["cost_today"] == 2.50
