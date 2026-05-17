"""Tests for the Ting integration REST API endpoints.

Tests GET/POST/DELETE/PATCH /api/v1/ting/integrations and
GET /api/v1/ting/telegram/setup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    IntegrationConnection,
    IntegrationType,
    Principal,
    RegisteredInstance,
)
from ting.adapters.inbound.rest_integrations import (
    create_integrations_router,
    create_telegram_setup_router,
)
from ting.config import AuthConfig, Settings

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_connection(
    owner_id: str = "user-1",
    integration_type: IntegrationType = IntegrationType.CODE_FORGE,
    adapter: str = "ting.adapters.volundr_http.VolundrHTTPAdapter",
    credential_name: str = "volundr-pat",
    config: dict | None = None,
    enabled: bool = True,
) -> IntegrationConnection:
    now = datetime.now(UTC)
    return IntegrationConnection(
        id=str(uuid4()),
        owner_id=owner_id,
        integration_type=integration_type,
        adapter=adapter,
        credential_name=credential_name,
        config=config or {"url": "http://volundr"},
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_test_connection():
    """Skip real connection testing in unit tests."""
    from niuu.domain.services.connection_tester import ConnectionTestResult

    with patch(
        "ting.adapters.inbound.rest_integrations.test_connection",
        return_value=ConnectionTestResult(success=True, message="mock ok"),
    ):
        yield


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.list_connections = AsyncMock(return_value=[])
    repo.get_connection = AsyncMock(return_value=None)
    repo.save_connection = AsyncMock(side_effect=lambda c: c)
    repo.delete_connection = AsyncMock()
    return repo


@pytest.fixture
def mock_credential_store() -> AsyncMock:
    store = AsyncMock()
    store.store = AsyncMock()
    store.delete = AsyncMock()
    return store


@pytest.fixture
def client(mock_repo: AsyncMock, mock_credential_store: AsyncMock) -> TestClient:
    return _make_client(mock_repo, mock_credential_store)


def _make_client(
    mock_repo: AsyncMock,
    mock_credential_store: AsyncMock,
    *,
    allow_anonymous_dev: bool = False,
) -> TestClient:
    app = FastAPI()
    app.state.integration_repo = mock_repo
    app.state.credential_store = mock_credential_store
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=allow_anonymous_dev))
    app.state.instance_service = _StubInstanceService()
    app.include_router(create_integrations_router())
    app.include_router(
        create_telegram_setup_router(
            telegram_bot_username="TestBot",
            telegram_hmac_key="test-secret",
        )
    )
    return TestClient(app)


def _auth_headers(user_id: str = "user-1") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


class _StubInstanceService:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.instances = [
            RegisteredInstance(
                id="system-volundr",
                kind=InstanceKind.VOLUNDR,
                slug="system-volundr",
                name="System Volundr",
                base_url="http://system-volundr:8000",
                visibility=InstanceVisibility.SYSTEM,
                owner_id=None,
                tenant_id=None,
                enabled=True,
                is_default=True,
                config={},
                created_at=now,
                updated_at=now,
            )
        ]

    async def list_visible(
        self,
        principal: Principal,
        *,
        kind: InstanceKind | None = None,
        enabled_only: bool = False,
    ) -> list[RegisteredInstance]:
        assert principal.user_id
        assert kind == InstanceKind.VOLUNDR
        assert enabled_only is True
        return list(self.instances)


# -------------------------------------------------------------------
# GET /api/v1/ting/integrations
# -------------------------------------------------------------------


class TestListIntegrations:
    def test_returns_empty_array(self, client: TestClient, mock_repo: AsyncMock):
        mock_repo.list_connections.return_value = []

        resp = client.get("/api/v1/ting/integrations", headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_connections(self, client: TestClient, mock_repo: AsyncMock):
        conn = _make_connection()
        mock_repo.list_connections.return_value = [conn]

        resp = client.get("/api/v1/ting/integrations", headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == conn.id
        assert data[0]["integration_type"] == "code_forge"
        assert data[0]["integrationType"] == "code_forge"
        assert data[0]["adapter"] == conn.adapter

    def test_scoped_by_user_id(self, client: TestClient, mock_repo: AsyncMock):
        client.get("/api/v1/ting/integrations", headers=_auth_headers("user-42"))

        mock_repo.list_connections.assert_called_once_with("user-42")

    def test_returns_boolean_config_values(self, client: TestClient, mock_repo: AsyncMock):
        conn = _make_connection(
            integration_type=IntegrationType.MESSAGING,
            adapter="ting.adapters.telegram_notification.TelegramNotificationAdapter",
            credential_name="telegram-main",
            config={"notify_only": True},
        )
        mock_repo.list_connections.return_value = [conn]

        resp = client.get("/api/v1/ting/integrations", headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["config"]["notify_only"] is True


# -------------------------------------------------------------------
# POST /api/v1/ting/integrations
# -------------------------------------------------------------------


class TestCreateIntegration:
    def test_returns_201(self, client: TestClient, mock_repo: AsyncMock):
        resp = client.post(
            "/api/v1/ting/integrations",
            json={
                "integration_type": "code_forge",
                "adapter": "ting.adapters.volundr_http.VolundrHTTPAdapter",
                "credential_name": "volundr-pat",
                "credential_value": "secret-token",
                "config": {"url": "http://volundr"},
            },
            headers=_auth_headers(),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["integration_type"] == "code_forge"
        assert data["credential_name"] == "volundr-pat"
        assert data["integrationType"] == "code_forge"
        assert data["credentialName"] == "volundr-pat"
        assert data["createdAt"] == data["created_at"]
        assert data["updatedAt"] == data["updated_at"]
        assert data["enabled"] is True

    def test_stores_credential(
        self,
        client: TestClient,
        mock_credential_store: AsyncMock,
    ):
        client.post(
            "/api/v1/ting/integrations",
            json={
                "integration_type": "source_control",
                "adapter": "ting.adapters.git.github.GitHubAdapter",
                "credential_name": "github-pat",
                "credential_value": "ghp_abc123",
                "config": {"org": "niuulabs"},
            },
            headers=_auth_headers("user-1"),
        )

        mock_credential_store.store.assert_called_once()
        call_kwargs = mock_credential_store.store.call_args
        assert call_kwargs.kwargs["name"] == "github-pat"
        assert call_kwargs.kwargs["data"] == {"token": "ghp_abc123"}
        assert call_kwargs.kwargs["owner_id"] == "user-1"

    def test_connection_test_uses_registered_volundr_targets(
        self,
        mock_repo: AsyncMock,
        mock_credential_store: AsyncMock,
    ) -> None:
        from niuu.domain.services.connection_tester import ConnectionTestResult

        client = _make_client(mock_repo, mock_credential_store)
        with patch(
            "ting.adapters.inbound.rest_integrations.test_connection",
            return_value=ConnectionTestResult(success=True, message="mock ok"),
        ) as test_connection_mock:
            response = client.post(
                "/api/v1/ting/integrations",
                json={
                    "integration_type": "code_forge",
                    "adapter": "ting.adapters.volundr_http.VolundrHTTPAdapter",
                    "credential_name": "volundr-pat",
                    "credential_value": "secret-token",
                    "config": {"url": "http://volundr"},
                },
                headers=_auth_headers(),
            )

        assert response.status_code == 201
        assert (
            test_connection_mock.call_args.kwargs["trusted_code_forge_base_urls"]
            == ("http://system-volundr:8000", "http://localhost:8080")
        )

    def test_rejects_missing_fields(self, client: TestClient):
        resp = client.post(
            "/api/v1/ting/integrations",
            json={"integration_type": "code_forge"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_rejects_empty_credential_value_outside_anonymous_dev(
        self,
        mock_repo: AsyncMock,
        mock_credential_store: AsyncMock,
    ) -> None:
        client = _make_client(mock_repo, mock_credential_store, allow_anonymous_dev=False)

        resp = client.post(
            "/api/v1/ting/integrations",
            json={
                "integration_type": "code_forge",
                "adapter": "ting.adapters.volundr_http.VolundrHTTPAdapter",
                "credential_name": "volundr-dev",
                "credential_value": "",
                "config": {"url": "http://volundr"},
            },
            headers=_auth_headers(),
        )

        assert resp.status_code == 422
        mock_credential_store.store.assert_not_called()

    def test_allows_empty_code_forge_credential_value_in_anonymous_dev(
        self,
        mock_repo: AsyncMock,
        mock_credential_store: AsyncMock,
    ) -> None:
        client = _make_client(mock_repo, mock_credential_store, allow_anonymous_dev=True)

        resp = client.post(
            "/api/v1/ting/integrations",
            json={
                "integration_type": "code_forge",
                "adapter": "ting.adapters.volundr_http.VolundrHTTPAdapter",
                "credential_name": "volundr-dev",
                "credential_value": "",
                "config": {"url": "http://volundr"},
            },
            headers=_auth_headers(),
        )

        assert resp.status_code == 201
        mock_credential_store.store.assert_not_called()

    def test_credential_value_not_in_response(self, client: TestClient, mock_repo: AsyncMock):
        resp = client.post(
            "/api/v1/ting/integrations",
            json={
                "integration_type": "code_forge",
                "adapter": "ting.adapters.volundr_http.VolundrHTTPAdapter",
                "credential_name": "volundr-pat",
                "credential_value": "secret-token",
                "config": {},
            },
            headers=_auth_headers(),
        )

        data = resp.json()
        assert "credential_value" not in data


# -------------------------------------------------------------------
# DELETE /api/v1/ting/integrations/{id}
# -------------------------------------------------------------------


class TestDeleteIntegration:
    def test_returns_204(self, client: TestClient, mock_repo: AsyncMock):
        conn = _make_connection()
        mock_repo.get_connection.return_value = conn

        resp = client.delete(
            f"/api/v1/ting/integrations/{conn.id}",
            headers=_auth_headers(),
        )

        assert resp.status_code == 204
        mock_repo.delete_connection.assert_called_once_with(conn.id)

    def test_returns_404_when_not_found(self, client: TestClient, mock_repo: AsyncMock):
        mock_repo.get_connection.return_value = None

        resp = client.delete(
            f"/api/v1/ting/integrations/{uuid4()}",
            headers=_auth_headers(),
        )

        assert resp.status_code == 404

    def test_returns_404_for_other_user(self, client: TestClient, mock_repo: AsyncMock):
        conn = _make_connection(owner_id="other-user")
        mock_repo.get_connection.return_value = conn

        resp = client.delete(
            f"/api/v1/ting/integrations/{conn.id}",
            headers=_auth_headers("user-1"),
        )

        assert resp.status_code == 404


# -------------------------------------------------------------------
# PATCH /api/v1/ting/integrations/{id}
# -------------------------------------------------------------------


class TestToggleIntegration:
    def test_toggles_enabled(self, client: TestClient, mock_repo: AsyncMock):
        conn = _make_connection(enabled=True)
        mock_repo.get_connection.return_value = conn

        resp = client.patch(
            f"/api/v1/ting/integrations/{conn.id}",
            json={"enabled": False},
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        saved = mock_repo.save_connection.call_args[0][0]
        assert saved.enabled is False

    def test_returns_404_when_not_found(self, client: TestClient, mock_repo: AsyncMock):
        mock_repo.get_connection.return_value = None

        resp = client.patch(
            f"/api/v1/ting/integrations/{uuid4()}",
            json={"enabled": True},
            headers=_auth_headers(),
        )

        assert resp.status_code == 404

    def test_returns_404_for_other_user(self, client: TestClient, mock_repo: AsyncMock):
        conn = _make_connection(owner_id="other-user")
        mock_repo.get_connection.return_value = conn

        resp = client.patch(
            f"/api/v1/ting/integrations/{conn.id}",
            json={"enabled": False},
            headers=_auth_headers("user-1"),
        )

        assert resp.status_code == 404


# -------------------------------------------------------------------
# GET /api/v1/ting/telegram/setup
# -------------------------------------------------------------------


class TestTelegramSetup:
    def test_returns_deeplink(self, client: TestClient):
        resp = client.get("/api/v1/ting/telegram/setup", headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["deeplink"].startswith("https://t.me/TestBot?start=")
        assert "user-1:" in data["token"]

    def test_token_contains_user_id(self, client: TestClient):
        resp = client.get(
            "/api/v1/ting/telegram/setup",
            headers=_auth_headers("user-42"),
        )

        data = resp.json()
        assert data["token"].startswith("user-42:")

    def test_integrations_alias_returns_same_payload(self, client: TestClient):
        legacy = client.get("/api/v1/ting/telegram/setup", headers=_auth_headers())
        canonical = client.get("/api/v1/ting/integrations/telegram/setup", headers=_auth_headers())

        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()


# -------------------------------------------------------------------
# Auth / scoping
# -------------------------------------------------------------------


class TestAuthRequired:
    def test_no_auth_returns_401(self, client: TestClient, mock_repo: AsyncMock):
        """Without Envoy headers and no allow_anonymous_dev, returns 401."""
        resp = client.get("/api/v1/ting/integrations")
        assert resp.status_code == 401
        mock_repo.list_connections.assert_not_called()
