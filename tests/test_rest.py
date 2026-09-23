"""Tests for the REST API adapter."""

from datetime import UTC
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from tests.conftest import MockEventBroadcaster
from volundr.adapters.inbound.rest import (
    _server_side_http_proxy_target,
    _server_side_ws_connect_overrides,
    create_router,
)
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.domain.models import DeviceToken, EventType, RealtimeEvent
from volundr.domain.ports import DeviceTokenRepository
from volundr.domain.services import SessionService, StatsService


def test_server_side_ws_connect_overrides_openshell_service_host():
    overrides = _server_side_ws_connect_overrides(
        "ws://forge-8093e93dc7634efeb2c382--skuld.openshell.localhost:8080/session",
        gateway_url="http://openshell.openshell.svc.cluster.local:8080",
    )

    assert overrides == {
        "host": "openshell.openshell.svc.cluster.local",
        "port": 8080,
        "proxy": None,
    }


def test_server_side_http_proxy_target_openshell_service_host():
    url, headers = _server_side_http_proxy_target(
        "http://forge-123--skuld.openshell.localhost:8080/api/conversation/history",
        gateway_url="http://openshell.openshell.svc.cluster.local:8080",
    )

    assert url == ("http://openshell.openshell.svc.cluster.local:8080/api/conversation/history")
    assert headers == {"Host": "forge-123--skuld.openshell.localhost:8080"}


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


class _StubIdentity(EnvoyHeaderAuthenticationAdapter):
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
            stats_service=StatsService(stats_repository, session_service),
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
        session_service = SessionService(repository=repository, pod_manager=pod_manager)
        router = create_router(
            session_service=session_service,
            stats_service=StatsService(stats_repository, session_service),
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
        stats_service = StatsService(stats_repository, session_service)

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
        stats_service = StatsService(stats_repository, session_service)

        router = create_router(
            session_service=session_service,
            stats_service=stats_service,
            pricing_provider=pricing_provider,
            broadcaster=broadcaster,
        )
        app.include_router(router)

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/forge/sessions/stream?scope=local")

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers["X-Forge-Session-Scope"] == "local"
        lines = [line for line in response.text.splitlines() if line]
        assert any("event: heartbeat" in line for line in lines)

    @staticmethod
    def _scoped_stream_app(repository, pod_manager, stats_repository, pricing_provider, events):
        from collections.abc import AsyncGenerator

        from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter

        class _PresetBroadcaster(InMemoryEventBroadcaster):
            async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
                for event in events:
                    yield event

        broadcaster = _PresetBroadcaster()
        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            broadcaster=broadcaster,
            authorization=SimpleRoleAuthorizationAdapter(),
        )
        app = FastAPI()
        app.include_router(
            create_router(
                session_service=session_service,
                stats_service=StatsService(stats_repository, session_service),
                pricing_provider=pricing_provider,
                broadcaster=broadcaster,
            )
        )
        return app

    @pytest.mark.asyncio
    async def test_sse_stream_hides_other_users_sessions(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """A non-owner receives no events for another user's session."""
        from datetime import datetime

        from httpx import ASGITransport, AsyncClient

        def event(event_type: EventType, **data) -> RealtimeEvent:
            return RealtimeEvent(type=event_type, data=data, timestamp=datetime.now(UTC))

        alice = {"owner_id": "alice", "tenant_id": "t1"}
        events = [
            event(EventType.SESSION_UPDATED, id="alice-session", **alice),
            event(EventType.SESSION_ACTIVITY, session_id="alice-session", state="idle", **alice),
            event(EventType.SESSION_NEEDS_INPUT, session_id="alice-session", **alice),
            event(EventType.SESSION_DELETED, id="alice-session", **alice),
            event(EventType.SESSION_UPDATED, id="bob-session", owner_id="bob", tenant_id="t1"),
            event(EventType.HEARTBEAT),
        ]
        app = self._scoped_stream_app(
            repository, pod_manager, stats_repository, pricing_provider, events
        )
        app.state.identity = _StubIdentity()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/forge/sessions/stream",
                headers={
                    "x-auth-user-id": "bob",
                    "x-auth-tenant": "t1",
                    "x-auth-roles": "volundr:developer",
                },
            )

        assert response.status_code == 200
        assert "alice-session" not in response.text
        assert "bob-session" in response.text
        assert "event: heartbeat" in response.text

    def test_sse_stream_rejects_guild_scope(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """A standalone Forge cannot serve guild scope and says so."""
        app = self._scoped_stream_app(
            repository, pod_manager, stats_repository, pricing_provider, []
        )
        app.state.identity = _StubIdentity()
        response = TestClient(app).get(
            "/api/v1/forge/sessions/stream?scope=guild",
            headers={"x-auth-user-id": "bob", "x-auth-tenant": "t1"},
        )
        assert response.status_code == 422

    def test_sse_stream_requires_a_principal_when_authorization_is_configured(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        app = self._scoped_stream_app(
            repository, pod_manager, stats_repository, pricing_provider, []
        )
        response = TestClient(app).get("/api/v1/forge/sessions/stream")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_sse_stream_stats_cover_only_the_subscribers_sessions(
        self, repository, pod_manager, stats_repository, pricing_provider
    ):
        """A stats tick reaches the subscriber as its own figures, never the publisher's."""
        from datetime import datetime

        from httpx import ASGITransport, AsyncClient

        tick = RealtimeEvent(
            type=EventType.STATS_UPDATED,
            data={"active_sessions": 1000},
            timestamp=datetime.now(UTC),
        )
        stats_repository.set_stats(active_sessions=2)
        app = self._scoped_stream_app(
            repository, pod_manager, stats_repository, pricing_provider, [tick]
        )
        app.state.identity = _StubIdentity()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/forge/sessions/stream",
                headers={
                    "x-auth-user-id": "bob",
                    "x-auth-tenant": "t1",
                    "x-auth-roles": "volundr:developer",
                },
            )

        assert response.status_code == 200
        assert "event: stats_updated" in response.text
        assert '"active_sessions": 2' in response.text
        assert "1000" not in response.text
        assert stats_repository.scopes == [("t1", "bob")]

    def test_router_refuses_a_stream_without_stats(self, repository, pod_manager, pricing_provider):
        """The stream cannot serve stats_updated without a stats service, so say so."""
        broadcaster = InMemoryEventBroadcaster()
        with pytest.raises(ValueError, match="stats_service"):
            create_router(
                session_service=SessionService(repository=repository, pod_manager=pod_manager),
                pricing_provider=pricing_provider,
                broadcaster=broadcaster,
            )


class TestStatsScope:
    """GET /stats is bounded like GET /sessions."""

    @staticmethod
    def _client(repository, pod_manager, stats_repository, *, identity: bool) -> TestClient:
        from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
            authorization=SimpleRoleAuthorizationAdapter(),
        )
        app = FastAPI()
        if identity:
            app.state.identity = _StubIdentity()
        app.include_router(
            create_router(
                session_service=session_service,
                stats_service=StatsService(stats_repository, session_service),
            )
        )
        return TestClient(app)

    @pytest.mark.parametrize(
        ("roles", "scope"),
        [("volundr:developer", ("t1", "bob")), ("volundr:admin", ("t1", None))],
    )
    def test_stats_are_bounded_to_the_callers_sessions(
        self, repository, pod_manager, stats_repository, roles, scope
    ):
        client = self._client(repository, pod_manager, stats_repository, identity=True)
        response = client.get(
            "/api/v1/forge/stats",
            headers={"x-auth-user-id": "bob", "x-auth-tenant": "t1", "x-auth-roles": roles},
        )
        assert response.status_code == 200
        assert stats_repository.scopes == [scope]

    def test_stats_require_a_principal_when_authorization_is_configured(
        self, repository, pod_manager, stats_repository
    ):
        client = self._client(repository, pod_manager, stats_repository, identity=False)
        response = client.get("/api/v1/forge/stats")
        assert response.status_code == 401
        assert stats_repository.scopes == []


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
        stats_service = StatsService(stats_repository, session_service)

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

    @pytest.mark.parametrize("query", ["scope=local", "scope=local&status=archived", "scope=guild"])
    def test_direct_session_scope_acknowledged(self, client, query):
        response = client.get("/api/v1/forge/sessions?" + query)
        assert response.status_code == 200
        assert response.headers["X-Forge-Session-Scope"] == "local"

    def test_direct_session_scope_rejects_typo(self, client):
        assert client.get("/api/v1/forge/sessions?scope=locla").status_code == 422

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
        stats_service = StatsService(stats_repository, session_service)

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
