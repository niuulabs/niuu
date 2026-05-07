"""Tests for the public auth config discovery endpoint."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import InMemorySessionRepository, MockPodManager
from volundr.adapters.inbound.rest import create_router
from volundr.config import AuthDiscoveryConfig, GatewayConfig, Settings
from volundr.domain.services import SessionService


@pytest.fixture
def session_service(
    repository: InMemorySessionRepository, pod_manager: MockPodManager
) -> SessionService:
    """Create a session service with test doubles."""
    return SessionService(repository, pod_manager)


def _make_app(session_service: SessionService, settings: Settings) -> FastAPI:
    """Create a test FastAPI app with settings on app.state."""
    app = FastAPI()
    app.state.settings = settings
    router = create_router(session_service)
    app.include_router(router)
    return app


class TestAuthConfigEndpoint:
    """Tests for GET /api/v1/forge/auth/config."""

    def test_returns_auth_config_when_issuer_set(self, session_service: SessionService):
        """Returns OIDC config when auth_discovery.issuer is configured."""
        settings = Settings(
            auth_discovery=AuthDiscoveryConfig(
                issuer="https://keycloak.example.com/realms/test",
                cli_client_id="my-cli",
                scopes="openid profile",
            ),
        )
        app = _make_app(session_service, settings)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/api/v1/forge/auth/config")

        assert response.status_code == 200
        data = response.json()
        assert data["issuer"] == "https://keycloak.example.com/realms/test"
        assert data["client_id"] == "my-cli"
        assert data["scopes"] == "openid profile"
        assert data["device_authorization_supported"] is True

    def test_falls_back_to_gateway_issuer_url(self, session_service: SessionService):
        """Falls back to gateway kwargs issuer_url when auth_discovery.issuer is empty."""
        settings = Settings(
            auth_discovery=AuthDiscoveryConfig(issuer=""),
            gateway=GatewayConfig(
                kwargs={"issuer_url": "https://idp.example.com/realms/v"},
            ),
        )
        app = _make_app(session_service, settings)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/api/v1/forge/auth/config")

        assert response.status_code == 200
        assert response.json()["issuer"] == "https://idp.example.com/realms/v"

    def test_returns_404_when_no_issuer_configured(self, session_service: SessionService):
        """Returns 404 when neither auth_discovery nor gateway issuer is set."""
        settings = Settings(
            auth_discovery=AuthDiscoveryConfig(issuer=""),
            gateway=GatewayConfig(kwargs={}),
        )
        app = _make_app(session_service, settings)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/api/v1/forge/auth/config")

        assert response.status_code == 404

    def test_uses_default_cli_client_id_and_scopes(self, session_service: SessionService):
        """Uses default cli_client_id and scopes when not explicitly set."""
        settings = Settings(
            auth_discovery=AuthDiscoveryConfig(
                issuer="https://keycloak.example.com/realms/test",
            ),
        )
        app = _make_app(session_service, settings)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/api/v1/forge/auth/config")

        assert response.status_code == 200
        data = response.json()
        assert data["client_id"] == "volundr-cli"
        assert data["scopes"] == "openid profile email"

    def test_endpoint_requires_no_authentication(self, session_service: SessionService):
        """The auth/config endpoint is accessible without auth headers."""
        settings = Settings(
            auth_discovery=AuthDiscoveryConfig(
                issuer="https://keycloak.example.com/realms/test",
            ),
        )
        app = _make_app(session_service, settings)
        # Deliberately do NOT set app.state.identity
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/api/v1/forge/auth/config")

        assert response.status_code == 200
