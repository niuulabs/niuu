"""Unit tests for the shared PAT REST adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.inbound.rest_pats import (
    CreatePATRequest,
    CreatePATResponse,
    PATResponse,
    create_pats_router,
)
from niuu.domain.models import Principal
from tests.helpers.http_contracts import RouteCallSpec, assert_route_equivalence

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_principal(user_id: str = "user-1") -> Principal:
    return Principal(
        user_id=user_id,
        email="test@example.com",
        tenant_id="tenant-1",
        roles=["user"],
    )


def _make_app(pat_service: AsyncMock) -> tuple[FastAPI, TestClient]:
    """Build a minimal FastAPI app with the PAT router mounted."""
    app = FastAPI()
    app.state.pat_service = pat_service

    async def extract_principal() -> Principal:
        return _make_principal()

    router = create_pats_router(extract_principal)
    app.include_router(router)
    return app, TestClient(app)


def _make_dual_router_app(pat_service: AsyncMock) -> TestClient:
    """Build an app with both canonical and legacy PAT routes mounted."""
    app = FastAPI()
    app.state.pat_service = pat_service

    async def extract_principal() -> Principal:
        return _make_principal()

    app.include_router(create_pats_router(extract_principal, prefix="/api/v1/tokens"))
    app.include_router(
        create_pats_router(
            extract_principal,
            prefix="/api/v1/users/tokens",
            deprecated=True,
            canonical_prefix="/api/v1/tokens",
        )
    )
    app.include_router(
        create_pats_router(
            extract_principal,
            prefix="/api/v1/volundr/tokens",
            deprecated=True,
            canonical_prefix="/api/v1/tokens",
        )
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestCreatePATRequest:
    def test_valid_name(self) -> None:
        req = CreatePATRequest(name="my-token")
        assert req.name == "my-token"

    def test_name_with_spaces(self) -> None:
        req = CreatePATRequest(name="My Token")
        assert req.name == "My Token"

    def test_empty_name_fails(self) -> None:
        with pytest.raises(Exception):
            CreatePATRequest(name="")


class TestPATResponse:
    def test_construction(self) -> None:
        now = datetime.now(UTC)
        resp = PATResponse(id="abc", name="tok", created_at=now, last_used_at=None)
        assert resp.name == "tok"
        assert resp.last_used_at is None
        dumped = resp.model_dump(mode="json")
        assert dumped["createdAt"] == dumped["created_at"]
        assert dumped["lastUsedAt"] == dumped["last_used_at"]


class TestCreatePATResponse:
    def test_construction(self) -> None:
        now = datetime.now(UTC)
        resp = CreatePATResponse(id="abc", name="tok", token="raw.jwt.here", created_at=now)
        assert resp.token == "raw.jwt.here"
        dumped = resp.model_dump(mode="json")
        assert dumped["createdAt"] == dumped["created_at"]


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


class TestCreatePATsRouter:
    def test_creates_router_with_default_prefix(self) -> None:
        async def extract() -> Principal:
            return _make_principal()

        router = create_pats_router(extract)
        assert router.prefix == "/api/v1/users/tokens"

    def test_creates_router_with_custom_prefix(self) -> None:
        async def extract() -> Principal:
            return _make_principal()

        router = create_pats_router(extract, prefix="/api/v2/tokens")
        assert router.prefix == "/api/v2/tokens"


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------


class TestCreateToken:
    def test_create_returns_201_with_token(self) -> None:
        pat_id = uuid4()
        now = datetime.now(UTC)

        mock_pat = AsyncMock()
        mock_pat.id = pat_id
        mock_pat.name = "my-pat"
        mock_pat.created_at = now

        service = AsyncMock()
        service.create = AsyncMock(return_value=(mock_pat, "raw.jwt.token"))

        _, client = _make_app(service)
        resp = client.post(
            "/api/v1/users/tokens",
            json={"name": "my-pat"},
            headers={"Authorization": "Bearer user-access-token"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "my-pat"
        assert data["token"] == "raw.jwt.token"
        assert data["createdAt"] == data["created_at"]

    def test_create_passes_subject_token(self) -> None:
        pat_id = uuid4()
        now = datetime.now(UTC)

        mock_pat = AsyncMock()
        mock_pat.id = pat_id
        mock_pat.name = "tok"
        mock_pat.created_at = now

        service = AsyncMock()
        service.create = AsyncMock(return_value=(mock_pat, "raw"))

        _, client = _make_app(service)
        client.post(
            "/api/v1/users/tokens",
            json={"name": "tok"},
            headers={"Authorization": "Bearer my-subject-token"},
        )
        service.create.assert_called_once()
        call_kwargs = service.create.call_args[1]
        assert call_kwargs["subject_token"] == "my-subject-token"

    def test_create_without_auth_header_passes_empty_subject(self) -> None:
        pat_id = uuid4()
        now = datetime.now(UTC)

        mock_pat = AsyncMock()
        mock_pat.id = pat_id
        mock_pat.name = "tok"
        mock_pat.created_at = now

        service = AsyncMock()
        service.create = AsyncMock(return_value=(mock_pat, "raw"))

        _, client = _make_app(service)
        client.post("/api/v1/users/tokens", json={"name": "tok"})
        service.create.assert_called_once()
        call_kwargs = service.create.call_args[1]
        assert call_kwargs["subject_token"] == ""


class TestListTokens:
    def test_list_returns_200_with_pats(self) -> None:
        now = datetime.now(UTC)

        mock_pat = AsyncMock()
        mock_pat.id = uuid4()
        mock_pat.name = "my-pat"
        mock_pat.created_at = now
        mock_pat.last_used_at = None

        service = AsyncMock()
        service.list = AsyncMock(return_value=[mock_pat])

        _, client = _make_app(service)
        resp = client.get("/api/v1/users/tokens")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "my-pat"
        assert data[0]["createdAt"] == data[0]["created_at"]
        assert data[0]["lastUsedAt"] == data[0]["last_used_at"]

    def test_list_empty_returns_empty_list(self) -> None:
        service = AsyncMock()
        service.list = AsyncMock(return_value=[])

        _, client = _make_app(service)
        resp = client.get("/api/v1/users/tokens")
        assert resp.status_code == 200
        assert resp.json() == []


class TestRevokeToken:
    def test_revoke_returns_204(self) -> None:
        service = AsyncMock()
        service.revoke = AsyncMock(return_value=True)

        pat_id = str(uuid4())
        _, client = _make_app(service)
        resp = client.delete(f"/api/v1/users/tokens/{pat_id}")
        assert resp.status_code == 204

    def test_revoke_not_found_returns_404(self) -> None:
        service = AsyncMock()
        service.revoke = AsyncMock(return_value=False)

        pat_id = str(uuid4())
        _, client = _make_app(service)
        resp = client.delete(f"/api/v1/users/tokens/{pat_id}")
        assert resp.status_code == 404

    def test_revoke_invalid_uuid_returns_404(self) -> None:
        service = AsyncMock()
        _, client = _make_app(service)
        resp = client.delete("/api/v1/users/tokens/not-a-uuid")
        assert resp.status_code == 404


class TestCanonicalTokenRoutes:
    def test_canonical_list_matches_legacy_route(self) -> None:
        now = datetime.now(UTC)

        mock_pat = AsyncMock()
        mock_pat.id = uuid4()
        mock_pat.name = "my-pat"
        mock_pat.created_at = now
        mock_pat.last_used_at = None

        service = AsyncMock()
        service.list = AsyncMock(return_value=[mock_pat])

        client = _make_dual_router_app(service)
        legacy = client.get("/api/v1/users/tokens")
        assert legacy.headers["Deprecation"] == "true"
        assert legacy.headers["Link"] == '</api/v1/tokens>; rel="successor-version"'

        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(path="/api/v1/users/tokens"),
            canonical=RouteCallSpec(path="/api/v1/tokens"),
        )

    def test_canonical_create_matches_legacy_route(self) -> None:
        pat_id = uuid4()
        now = datetime.now(UTC)

        mock_pat = AsyncMock()
        mock_pat.id = pat_id
        mock_pat.name = "my-pat"
        mock_pat.created_at = now

        service = AsyncMock()
        service.create = AsyncMock(return_value=(mock_pat, "raw.jwt.token"))

        client = _make_dual_router_app(service)
        legacy = client.post(
            "/api/v1/users/tokens",
            json={"name": "my-pat"},
            headers={"Authorization": "Bearer user-access-token"},
        )
        assert legacy.headers["Deprecation"] == "true"

        service.create.reset_mock()
        service.create.return_value = (mock_pat, "raw.jwt.token")
        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(
                path="/api/v1/users/tokens",
                method="POST",
                json_body={"name": "my-pat"},
                headers={"Authorization": "Bearer user-access-token"},
            ),
            canonical=RouteCallSpec(
                path="/api/v1/tokens",
                method="POST",
                json_body={"name": "my-pat"},
                headers={"Authorization": "Bearer user-access-token"},
            ),
            expected_status=201,
        )

    def test_volundr_scoped_list_matches_canonical_route(self) -> None:
        now = datetime.now(UTC)

        mock_pat = AsyncMock()
        mock_pat.id = uuid4()
        mock_pat.name = "my-pat"
        mock_pat.created_at = now
        mock_pat.last_used_at = None

        service = AsyncMock()
        service.list = AsyncMock(return_value=[mock_pat])

        client = _make_dual_router_app(service)
        legacy = client.get("/api/v1/volundr/tokens")
        assert legacy.headers["Deprecation"] == "true"
        assert legacy.headers["Link"] == '</api/v1/tokens>; rel="successor-version"'

        assert_route_equivalence(
            client,
            legacy=RouteCallSpec(path="/api/v1/volundr/tokens"),
            canonical=RouteCallSpec(path="/api/v1/tokens"),
        )
