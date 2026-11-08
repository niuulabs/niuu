"""Tests for Ting health endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ting.config import AuthConfig, Settings, WorkflowRepositoryConfig
from ting.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Create a test client with mocked database pool."""
    settings = Settings(
        auth=AuthConfig(allow_anonymous_dev=True),
        workflow_repository=WorkflowRepositoryConfig(
            adapter="ting.adapters.postgres_workflows.PostgresWorkflowRepository",
            kwargs={},
            seed_bundled=True,
        ),
    )
    app = create_app(settings)

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value="INSERT 0 1")
    mock_pool.close = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_pool
    mock_pool.transaction.return_value.__aenter__.return_value = None

    with patch("ting.main.database_pool") as mock_db:
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_pool)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
        with TestClient(app) as c:
            yield c


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            # Default settings run without a Sleipnir bus: nothing to sign.
            "mesh_attestation": {"required": True, "state": "no_bus"},
        }

    def test_health_has_correlation_id(self, client: TestClient) -> None:
        response = client.get("/health")
        assert "x-correlation-id" in response.headers

    def test_health_preserves_correlation_id(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Correlation-ID": "test-123"})
        assert response.headers["x-correlation-id"] == "test-123"


class TestMeshSigningReport:
    def test_signing_on_with_a_bus_is_signed(self) -> None:
        from ting.main import mesh_signing_report

        assert mesh_signing_report(required=True, has_bus=True) == {
            "required": True,
            "state": "signed",
        }

    def test_signing_on_without_a_bus_has_nothing_to_sign(self) -> None:
        from ting.main import mesh_signing_report

        assert mesh_signing_report(required=True, has_bus=False) == {
            "required": True,
            "state": "no_bus",
        }

    def test_signing_off_is_unauthenticated_with_or_without_a_bus(self) -> None:
        from ting.main import mesh_signing_report

        for has_bus in (True, False):
            assert mesh_signing_report(required=False, has_bus=has_bus) == {
                "required": False,
                "state": "unauthenticated",
            }
