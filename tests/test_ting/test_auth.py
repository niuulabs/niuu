"""Tests for Ting extract_principal authentication dependency."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_principal
from ting.config import AuthConfig


def _create_app(*, allow_anonymous_dev: bool = False) -> FastAPI:
    """Create a minimal app with an endpoint using extract_principal."""
    app = FastAPI()
    settings = MagicMock()
    settings.auth = AuthConfig(allow_anonymous_dev=allow_anonymous_dev)
    app.state.settings = settings

    @app.get("/whoami")
    async def whoami(principal: Principal = Depends(extract_principal)) -> dict:
        return {
            "user_id": principal.user_id,
            "email": principal.email,
            "tenant_id": principal.tenant_id,
            "roles": principal.roles,
        }

    return app


@pytest.fixture
def app() -> FastAPI:
    """Default app with anonymous dev DISABLED (production-like)."""
    return _create_app(allow_anonymous_dev=False)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def dev_app() -> FastAPI:
    """App with anonymous dev ENABLED (dev/test mode)."""
    return _create_app(allow_anonymous_dev=True)


@pytest.fixture
def dev_client(dev_app: FastAPI) -> TestClient:
    return TestClient(dev_app)


class TestExtractPrincipalWithHeaders:
    """Tests for extract_principal when Envoy headers are present."""

    def test_extracts_all_headers(self, client: TestClient):
        resp = client.get(
            "/whoami",
            headers={
                "x-auth-user-id": "user-42",
                "x-auth-email": "user@example.com",
                "x-auth-tenant": "tenant-1",
                "x-auth-roles": "volundr:admin,volundr:developer",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "user-42"
        assert data["email"] == "user@example.com"
        assert data["tenant_id"] == "tenant-1"
        assert data["roles"] == ["volundr:admin", "volundr:developer"]

    def test_single_role(self, client: TestClient):
        resp = client.get(
            "/whoami",
            headers={
                "x-auth-user-id": "user-1",
                "x-auth-roles": "volundr:viewer",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["roles"] == ["volundr:viewer"]

    def test_defaults_missing_optional_headers(self, client: TestClient):
        resp = client.get(
            "/whoami",
            headers={"x-auth-user-id": "user-99"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "user-99"
        assert data["email"] == ""
        assert data["tenant_id"] == ""
        assert data["roles"] == ["volundr:developer"]


class TestExtractPrincipalProduction:
    """Tests for extract_principal when allow_anonymous_dev=False (production)."""

    def test_returns_401_without_auth_headers(self, client: TestClient):
        resp = client.get("/whoami")
        assert resp.status_code == 401

    def test_returns_401_with_empty_user_id(self, client: TestClient):
        resp = client.get(
            "/whoami",
            headers={"x-auth-user-id": ""},
        )
        assert resp.status_code == 401


class TestExtractPrincipalDevFallback:
    """Tests for extract_principal when allow_anonymous_dev=True (dev/test)."""

    def test_returns_default_principal(self, dev_client: TestClient):
        resp = dev_client.get("/whoami")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "dev-user"
        assert data["email"] == ""
        assert data["tenant_id"] == ""
        assert data["roles"] == ["volundr:developer"]

    def test_empty_user_id_header_triggers_fallback(self, dev_client: TestClient):
        resp = dev_client.get(
            "/whoami",
            headers={"x-auth-user-id": ""},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "dev-user"
