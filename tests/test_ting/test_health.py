"""Tests for Ting health endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ting.config import Settings
from ting.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Create a test client with mocked database pool."""
    settings = Settings()
    app = create_app(settings)

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with patch("ting.main.database_pool") as mock_db:
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_pool)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
        with TestClient(app) as c:
            yield c


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_has_correlation_id(self, client: TestClient) -> None:
        response = client.get("/health")
        assert "x-correlation-id" in response.headers

    def test_health_preserves_correlation_id(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Correlation-ID": "test-123"})
        assert response.headers["x-correlation-id"] == "test-123"
