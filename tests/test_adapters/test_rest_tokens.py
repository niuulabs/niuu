"""Tests for token usage REST endpoint."""

from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from bifrost.config import ManagedModelConfig
from tests.conftest import (
    InMemorySessionRepository,
    InMemoryStatsRepository,
    InMemoryTokenTracker,
    MockPodManager,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.domain.models import GitSource, Session, SessionStatus
from volundr.domain.services import (
    SessionService,
    StatsService,
    TokenService,
)


@pytest.fixture
def session_repository() -> InMemorySessionRepository:
    """Create an in-memory session repository."""
    return InMemorySessionRepository()


@pytest.fixture
def token_tracker() -> InMemoryTokenTracker:
    """Create an in-memory token tracker."""
    return InMemoryTokenTracker()


@pytest.fixture
def session_service(session_repository: InMemorySessionRepository) -> SessionService:
    """Create a session service."""
    return SessionService(session_repository, MockPodManager())


@pytest.fixture
def stats_service() -> StatsService:
    """Create a stats service."""
    return StatsService(InMemoryStatsRepository())


@pytest.fixture
def token_service(
    token_tracker: InMemoryTokenTracker,
    session_repository: InMemorySessionRepository,
) -> TokenService:
    """Create a token service."""
    return TokenService(token_tracker, session_repository)


@pytest.fixture
def client(
    session_service: SessionService,
    stats_service: StatsService,
    token_service: TokenService,
) -> TestClient:
    """Create a test client with the router."""
    from fastapi import FastAPI

    app = FastAPI()
    router = create_router(session_service, stats_service, token_service)
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def running_session() -> Session:
    """Create a running session."""
    return Session(
        name="Test Session",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/test/repo", branch="main"),
        status=SessionStatus.RUNNING,
    )


class TestReportTokenUsageEndpoint:
    """Tests for POST /sessions/{session_id}/usage endpoint."""

    def test_report_usage_success(
        self,
        client: TestClient,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test successful token usage report."""
        import asyncio

        asyncio.run(session_repository.create(running_session))

        response = client.post(
            f"/api/v1/forge/sessions/{running_session.id}/usage",
            json={
                "tokens": 1000,
                "provider": "cloud",
                "model": "claude-sonnet-4-20250514",
                "message_count": 1,
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["tokens"] == 1000
        assert data["provider"] == "cloud"
        assert data["model"] == "claude-sonnet-4-20250514"

    def test_report_usage_accepts_matching_openshell_session_binding(
        self,
        client: TestClient,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        import asyncio

        asyncio.run(session_repository.create(running_session))
        response = client.post(
            f"/api/v1/forge/sessions/{running_session.id}/usage",
            headers={
                "x-auth-token-use": "openshell_session",
                "x-auth-workload-session-id": str(running_session.id),
            },
            json={"tokens": 13, "provider": "cloud", "model": "gpt-5.6-sol"},
        )

        assert response.status_code == status.HTTP_201_CREATED

    def test_report_usage_rejects_mismatched_openshell_session_binding(
        self,
        client: TestClient,
        running_session: Session,
    ) -> None:
        response = client.post(
            f"/api/v1/forge/sessions/{running_session.id}/usage",
            headers={
                "x-auth-token-use": "openshell_session",
                "x-auth-workload-session-id": str(uuid4()),
            },
            json={"tokens": 13, "provider": "cloud", "model": "gpt-5.6-sol"},
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_report_usage_local_provider(
        self,
        client: TestClient,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test reporting usage with local provider."""
        import asyncio

        asyncio.run(session_repository.create(running_session))

        response = client.post(
            f"/api/v1/forge/sessions/{running_session.id}/usage",
            json={
                "tokens": 500,
                "provider": "local",
                "model": "llama-3",
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["provider"] == "local"
        assert data["cost"] is None

    def test_report_usage_session_not_found(
        self,
        client: TestClient,
    ) -> None:
        """Test reporting usage for non-existent session."""
        session_id = uuid4()
        response = client.post(
            f"/api/v1/forge/sessions/{session_id}/usage",
            json={
                "tokens": 1000,
                "provider": "cloud",
                "model": "claude-sonnet-4-20250514",
            },
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_report_usage_session_not_running(
        self,
        client: TestClient,
        session_repository: InMemorySessionRepository,
    ) -> None:
        """Test reporting usage for non-running session."""
        import asyncio

        stopped_session = Session(
            name="Stopped Session",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
            status=SessionStatus.STOPPED,
        )
        asyncio.run(session_repository.create(stopped_session))

        response = client.post(
            f"/api/v1/forge/sessions/{stopped_session.id}/usage",
            json={
                "tokens": 1000,
                "provider": "cloud",
                "model": "claude-sonnet-4-20250514",
            },
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_report_usage_invalid_provider(
        self,
        client: TestClient,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test reporting usage with invalid provider."""
        import asyncio

        asyncio.run(session_repository.create(running_session))

        response = client.post(
            f"/api/v1/forge/sessions/{running_session.id}/usage",
            json={
                "tokens": 1000,
                "provider": "invalid",
                "model": "test-model",
            },
        )

        assert response.status_code == 422

    def test_report_usage_zero_tokens(
        self,
        client: TestClient,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test reporting usage with zero tokens fails validation."""
        import asyncio

        asyncio.run(session_repository.create(running_session))

        response = client.post(
            f"/api/v1/forge/sessions/{running_session.id}/usage",
            json={
                "tokens": 0,
                "provider": "cloud",
                "model": "claude-sonnet-4-20250514",
            },
        )

        assert response.status_code == 422

    def test_report_usage_negative_tokens(
        self,
        client: TestClient,
        session_repository: InMemorySessionRepository,
        running_session: Session,
    ) -> None:
        """Test reporting usage with negative tokens fails validation."""
        import asyncio

        asyncio.run(session_repository.create(running_session))

        response = client.post(
            f"/api/v1/forge/sessions/{running_session.id}/usage",
            json={
                "tokens": -100,
                "provider": "cloud",
                "model": "claude-sonnet-4-20250514",
            },
        )

        assert response.status_code == 422


class TestReportUsageWithPricing:
    """Tests for token usage reporting with pricing provider."""

    def test_report_usage_calculates_cost(self) -> None:
        """Test that cost is calculated for cloud models when pricing provider exists."""
        import asyncio

        from fastapi import FastAPI

        session_repository = InMemorySessionRepository()
        token_tracker = InMemoryTokenTracker()
        pricing_provider = HardcodedPricingProvider(
            [
                ManagedModelConfig(
                    id="claude-sonnet-4-20250514",
                    name="Sonnet",
                    cost_per_million_tokens=3.0,
                ),
            ]
        )
        token_service = TokenService(token_tracker, session_repository, pricing_provider)
        session_service = SessionService(session_repository, MockPodManager())

        app = FastAPI()
        router = create_router(session_service, None, token_service)
        app.include_router(router)
        client = TestClient(app)

        running_session = Session(
            name="Test Session",
            model="claude-sonnet-4-20250514",
            source=GitSource(repo="https://github.com/test/repo", branch="main"),
            status=SessionStatus.RUNNING,
        )
        asyncio.run(session_repository.create(running_session))

        response = client.post(
            f"/api/v1/forge/sessions/{running_session.id}/usage",
            json={
                "tokens": 1_000_000,  # 1M tokens
                "provider": "cloud",
                "model": "claude-sonnet-4-20250514",
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        # claude-sonnet-4-20250514 costs $3.00 per million tokens
        assert data["cost"] == pytest.approx(3.00)


class TestTokenServiceUnavailable:
    """Tests for when token service is not configured."""

    def test_report_usage_service_unavailable(self) -> None:
        """Test that 503 is returned when token service is None."""
        from fastapi import FastAPI

        app = FastAPI()
        session_service = SessionService(InMemorySessionRepository(), MockPodManager())
        router = create_router(session_service, None, None)
        app.include_router(router)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/forge/sessions/{uuid4()}/usage",
            json={
                "tokens": 1000,
                "provider": "cloud",
                "model": "claude-sonnet-4-20250514",
            },
        )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
