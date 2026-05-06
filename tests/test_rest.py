"""Tests for the REST API adapter."""

from datetime import UTC
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import MockEventBroadcaster
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.domain.models import EventType, RealtimeEvent
from volundr.domain.services import SessionService, StatsService


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
        response = client.get("/api/v1/volundr/sessions/stream")

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
            response = await client.get("/api/v1/volundr/sessions/stream")

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
            "/api/v1/volundr/sessions",
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
        response = client.get("/api/v1/volundr/sessions")

        assert response.status_code == 200
        assert response.json() == []

    def test_get_session_not_found(self, client):
        """Getting a non-existent session returns 404."""
        response = client.get("/api/v1/volundr/sessions/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404

    def test_update_session(self, client, mock_broadcaster):
        """Updating a session via API publishes event."""
        # Create session first
        create_response = client.post(
            "/api/v1/volundr/sessions",
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
            f"/api/v1/volundr/sessions/{session_id}",
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
            "/api/v1/volundr/sessions",
            json={
                "name": "test-session",
                "model": "claude-sonnet-4-20250514",
                "repo": "https://github.com/test/repo",
                "branch": "main",
            },
        )
        session_id = create_response.json()["id"]

        # Delete session
        response = client.delete(f"/api/v1/volundr/sessions/{session_id}")

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

        response = client.get("/api/v1/volundr/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["active_sessions"] == 3
        assert data["total_sessions"] == 10
        assert data["tokens_today"] == 5000
        assert data["cost_today"] == 2.50


class TestModelsEndpoint:
    """Tests for the models endpoint."""

    @pytest.fixture
    def app(self, repository, pod_manager, pricing_provider):
        """Create a FastAPI app with the router."""
        app = FastAPI()

        session_service = SessionService(
            repository=repository,
            pod_manager=pod_manager,
        )

        router = create_router(
            session_service=session_service,
            pricing_provider=pricing_provider,
        )
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return TestClient(app)

    def test_list_models(self, client):
        """Models endpoint returns available models."""
        response = client.get("/api/v1/volundr/models")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3  # Based on InMemoryPricingProvider fixture

        # Check cloud model
        sonnet = next((m for m in data if m["id"] == "claude-sonnet-4-20250514"), None)
        assert sonnet is not None
        assert sonnet["provider"] == "cloud"
        assert sonnet["cost_per_million_tokens"] == 3.00

        # Check local model
        llama = next((m for m in data if m["id"] == "llama3.2:latest"), None)
        assert llama is not None
        assert llama["provider"] == "local"
        assert llama["cost_per_million_tokens"] is None
        assert llama["vram_required"] == "8GB"
