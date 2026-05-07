"""Tests for the PAT REST API endpoints.

Tests POST/GET/DELETE /api/v1/tokens by mocking app.state.pat_service.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from niuu.adapters.inbound.rest_pats import create_pats_router
from niuu.domain.models import PersonalAccessToken

# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


def _make_pat(
    owner_id: str = "user-1",
    name: str = "my-token",
) -> PersonalAccessToken:
    return PersonalAccessToken(
        id=uuid4(),
        owner_id=owner_id,
        name=name,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_service() -> AsyncMock:
    service = AsyncMock()
    service.create = AsyncMock()
    service.list = AsyncMock(return_value=[])
    service.revoke = AsyncMock(return_value=True)
    return service


async def _stub_extract_principal(request: Request):
    """Stub auth — reads user_id from x-auth-user-id header, returns 401 if missing."""
    from niuu.domain.models import Principal

    user_id = request.headers.get("x-auth-user-id", "")
    if not user_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Missing auth")
    return Principal(user_id=user_id, email="", tenant_id="", roles=[])


@pytest.fixture
def client(mock_service: AsyncMock) -> TestClient:
    app = FastAPI()
    app.state.pat_service = mock_service
    app.include_router(create_pats_router(_stub_extract_principal))
    return TestClient(app)


def _auth_headers(user_id: str = "user-1") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


# -------------------------------------------------------------------
# POST /api/v1/tokens
# -------------------------------------------------------------------


class TestCreateToken:
    def test_returns_201_with_token(self, client: TestClient, mock_service: AsyncMock):
        pat = _make_pat()
        mock_service.create.return_value = (pat, "raw-jwt-token")

        resp = client.post(
            "/api/v1/tokens",
            json={"name": "ci-token"},
            headers=_auth_headers(),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == str(pat.id)
        assert data["name"] == pat.name
        assert data["token"] == "raw-jwt-token"
        assert "created_at" in data

    def test_calls_service_with_principal_user_id(
        self, client: TestClient, mock_service: AsyncMock
    ):
        pat = _make_pat(owner_id="user-42")
        mock_service.create.return_value = (pat, "tok")

        client.post(
            "/api/v1/tokens",
            json={"name": "test"},
            headers=_auth_headers("user-42"),
        )

        mock_service.create.assert_called_once_with("user-42", "test", subject_token="")

    def test_rejects_empty_name(self, client: TestClient):
        resp = client.post(
            "/api/v1/tokens",
            json={"name": ""},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_rejects_name_too_long(self, client: TestClient):
        resp = client.post(
            "/api/v1/tokens",
            json={"name": "x" * 101},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422


# -------------------------------------------------------------------
# GET /api/v1/tokens
# -------------------------------------------------------------------


class TestListTokens:
    def test_returns_empty_list(self, client: TestClient, mock_service: AsyncMock):
        mock_service.list.return_value = []

        resp = client.get("/api/v1/tokens", headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_pats_without_raw_token(self, client: TestClient, mock_service: AsyncMock):
        pat1 = _make_pat(name="tok-a")
        pat2 = _make_pat(name="tok-b")
        mock_service.list.return_value = [pat1, pat2]

        resp = client.get("/api/v1/tokens", headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "tok-a"
        assert data[1]["name"] == "tok-b"
        # No raw token on list responses
        assert "token" not in data[0]
        assert "token" not in data[1]

    def test_includes_last_used_at(self, client: TestClient, mock_service: AsyncMock):
        pat = PersonalAccessToken(
            id=uuid4(),
            owner_id="user-1",
            name="tok",
            created_at=datetime.now(UTC),
            last_used_at=datetime.now(UTC),
        )
        mock_service.list.return_value = [pat]

        resp = client.get("/api/v1/tokens", headers=_auth_headers())

        data = resp.json()
        assert data[0]["last_used_at"] is not None

    def test_calls_service_with_principal_user_id(
        self, client: TestClient, mock_service: AsyncMock
    ):
        client.get("/api/v1/tokens", headers=_auth_headers("user-99"))

        mock_service.list.assert_called_once_with("user-99")


# -------------------------------------------------------------------
# DELETE /api/v1/tokens/{pat_id}
# -------------------------------------------------------------------


class TestRevokeToken:
    def test_returns_204_on_success(self, client: TestClient, mock_service: AsyncMock):
        pat_id = uuid4()
        mock_service.revoke.return_value = True

        resp = client.delete(
            f"/api/v1/tokens/{pat_id}",
            headers=_auth_headers(),
        )

        assert resp.status_code == 204

    def test_returns_404_when_not_found(self, client: TestClient, mock_service: AsyncMock):
        mock_service.revoke.return_value = False

        resp = client.delete(
            f"/api/v1/tokens/{uuid4()}",
            headers=_auth_headers(),
        )

        assert resp.status_code == 404

    def test_returns_404_for_invalid_uuid(self, client: TestClient):
        resp = client.delete(
            "/api/v1/tokens/not-a-uuid",
            headers=_auth_headers(),
        )

        assert resp.status_code == 404

    def test_calls_service_with_principal_user_id(
        self, client: TestClient, mock_service: AsyncMock
    ):
        pat_id = uuid4()
        mock_service.revoke.return_value = True

        client.delete(
            f"/api/v1/tokens/{pat_id}",
            headers=_auth_headers("user-7"),
        )

        mock_service.revoke.assert_called_once_with(pat_id, "user-7")


# -------------------------------------------------------------------
# Auth / 401 behaviour
# -------------------------------------------------------------------


class TestAuthRequired:
    def test_no_auth_headers_returns_401(self, client: TestClient, mock_service: AsyncMock):
        """Without Envoy headers and no allow_anonymous_dev, returns 401."""
        resp = client.get("/api/v1/tokens")
        assert resp.status_code == 401
        mock_service.list.assert_not_called()
