"""Tests for Tyr detailed health endpoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tyr.config import Settings
from tyr.main import create_app


def _database_pool_stub(pool):
    @asynccontextmanager
    async def _stub(*_args, **_kwargs):
        yield pool

    return _stub


@pytest.fixture
def app():
    """Create app instance."""
    return create_app(Settings())


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool that returns 1 for SELECT 1."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="OK")
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def mock_event_bus():
    bus = MagicMock()
    bus.client_count = 3
    return bus


@pytest.fixture
def mock_subscriber():
    sub = MagicMock()
    sub.running = True
    return sub


@pytest.fixture
def mock_notification_service():
    svc = MagicMock()
    svc.running = True
    return svc


@pytest.fixture
def mock_review_engine():
    engine = MagicMock()
    engine.running = True
    return engine


@pytest.fixture
def client(  # noqa: PLR0913
    app,
    mock_pool,
    mock_event_bus,
    mock_subscriber,
    mock_notification_service,
    mock_review_engine,
):
    """Test client with all services wired on app.state."""
    with patch("tyr.main.database_pool", _database_pool_stub(mock_pool)):
        with TestClient(app) as c:
            # Wire services on app.state after lifespan starts
            c.app.state.pool = mock_pool
            c.app.state.event_bus = mock_event_bus
            c.app.state.subscriber = mock_subscriber
            c.app.state.notification_service = mock_notification_service
            c.app.state.review_engine = mock_review_engine
            yield c


class TestDetailedHealthEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/api/v1/tyr/health/detailed")
        assert response.status_code == 200

    def test_overall_status_ok_when_db_ok_and_services_running(self, client: TestClient) -> None:
        response = client.get("/api/v1/tyr/health/detailed")
        assert response.json()["status"] == "ok"

    def test_database_ok(self, client: TestClient) -> None:
        response = client.get("/api/v1/tyr/health/detailed")
        assert response.json()["database"] == "ok"

    def test_event_bus_subscriber_count(self, client: TestClient) -> None:
        response = client.get("/api/v1/tyr/health/detailed")
        assert response.json()["event_bus_subscriber_count"] == 3

    def test_activity_subscriber_running(self, client: TestClient) -> None:
        response = client.get("/api/v1/tyr/health/detailed")
        assert response.json()["activity_subscriber_running"] is True

    def test_notification_service_running(self, client: TestClient) -> None:
        response = client.get("/api/v1/tyr/health/detailed")
        assert response.json()["notification_service_running"] is True

    def test_review_engine_running(self, client: TestClient) -> None:
        response = client.get("/api/v1/tyr/health/detailed")
        data = response.json()
        assert data["review_engine_running"] is True
        assert data["status"] == "ok"

    def test_database_unavailable_when_pool_missing(self, app, mock_pool) -> None:
        """When pool is not on app.state, database should be 'unavailable'."""
        with patch("tyr.main.database_pool", _database_pool_stub(mock_pool)):
            with TestClient(app) as c:
                # Remove pool from state
                if hasattr(c.app.state, "pool"):
                    del c.app.state.pool
                response = c.get("/api/v1/tyr/health/detailed")
        assert response.json()["database"] == "unavailable"
        assert response.json()["status"] == "degraded"

    def test_database_unavailable_on_exception(self, app, mock_pool) -> None:
        """When pool.fetchval raises, database should be 'unavailable'."""
        mock_pool.fetchval = AsyncMock(side_effect=OSError("connection refused"))
        with patch("tyr.main.database_pool", _database_pool_stub(mock_pool)):
            with TestClient(app) as c:
                c.app.state.pool = mock_pool
                response = c.get("/api/v1/tyr/health/detailed")
        assert response.json()["database"] == "unavailable"
        assert response.json()["status"] == "degraded"

    def test_services_report_false_when_stopped(self, app, mock_pool) -> None:
        """Services report False and overall is degraded when not running."""
        stopped_subscriber = MagicMock()
        stopped_subscriber.running = False
        stopped_notification = MagicMock()
        stopped_notification.running = False
        stopped_engine = MagicMock()
        stopped_engine.running = False

        with patch("tyr.main.database_pool", _database_pool_stub(mock_pool)):
            with TestClient(app) as c:
                c.app.state.pool = mock_pool
                c.app.state.subscriber = stopped_subscriber
                c.app.state.notification_service = stopped_notification
                c.app.state.review_engine = stopped_engine
                response = c.get("/api/v1/tyr/health/detailed")

        data = response.json()
        assert data["activity_subscriber_running"] is False
        assert data["notification_service_running"] is False
        assert data["review_engine_running"] is False
        assert data["status"] == "degraded"

    def test_services_default_false_when_absent(self, app, mock_pool) -> None:
        """When services are missing from app.state, values default to False/0."""
        with patch("tyr.main.database_pool", _database_pool_stub(mock_pool)):
            with TestClient(app) as c:
                c.app.state.pool = mock_pool
                # Don't set event_bus, subscriber, notification_service, review_engine
                for attr in ("event_bus", "subscriber", "notification_service", "review_engine"):
                    if hasattr(c.app.state, attr):
                        delattr(c.app.state, attr)
                response = c.get("/api/v1/tyr/health/detailed")

        data = response.json()
        assert data["event_bus_subscriber_count"] == 0
        assert data["activity_subscriber_running"] is False
        assert data["notification_service_running"] is False
        assert data["review_engine_running"] is False
        assert data["status"] == "degraded"

    def test_overall_degraded_when_single_service_down(self, app, mock_pool) -> None:
        """Overall status is 'degraded' when any one service is not running."""
        running = MagicMock()
        running.running = True
        stopped = MagicMock()
        stopped.running = False

        with patch("tyr.main.database_pool", _database_pool_stub(mock_pool)):
            with TestClient(app) as c:
                c.app.state.pool = mock_pool
                c.app.state.subscriber = running
                c.app.state.notification_service = running
                c.app.state.review_engine = stopped  # only review_engine down
                response = c.get("/api/v1/tyr/health/detailed")

        assert response.json()["status"] == "degraded"

    def test_response_has_correlation_id(self, client: TestClient) -> None:
        response = client.get("/api/v1/tyr/health/detailed")
        assert "x-correlation-id" in response.headers

    def test_response_schema(self, client: TestClient) -> None:
        """Response contains all expected fields with correct types."""
        data = client.get("/api/v1/tyr/health/detailed").json()
        assert set(data.keys()) == {
            "status",
            "database",
            "event_bus_subscriber_count",
            "activity_subscriber_running",
            "notification_service_running",
            "review_engine_running",
        }
        assert data["status"] in {"ok", "degraded"}
        assert data["database"] in {"ok", "unavailable"}
        assert isinstance(data["event_bus_subscriber_count"], int)
        assert isinstance(data["activity_subscriber_running"], bool)
        assert isinstance(data["notification_service_running"], bool)
        assert isinstance(data["review_engine_running"], bool)

    def test_event_bus_count_zero_when_absent(self, app, mock_pool) -> None:
        """event_bus_subscriber_count is 0 when event bus is not on app.state."""
        with patch("tyr.main.database_pool", _database_pool_stub(mock_pool)):
            with TestClient(app) as c:
                c.app.state.pool = mock_pool
                if hasattr(c.app.state, "event_bus"):
                    delattr(c.app.state, "event_bus")
                response = c.get("/api/v1/tyr/health/detailed")
        assert response.json()["event_bus_subscriber_count"] == 0
