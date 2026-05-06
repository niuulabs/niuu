"""Tests for PAT revocation middleware."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.domain.services.pat_validator import PATValidator

SIGNING_KEY = "test-signing-key-for-middleware-at-least-32"


def _make_pat_jwt(sub: str = "user-1", jti: str = "jti-abc") -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "type": "pat",
        "jti": jti,
        "name": "test-token",
        "iat": now,
        "exp": now + timedelta(days=365),
    }
    return jwt.encode(payload, SIGNING_KEY, algorithm="HS256")


def _create_app(*, exists_by_hash: bool = True) -> FastAPI:
    """Create a test app with the revocation middleware."""
    app = FastAPI()

    mock_repo = AsyncMock()
    mock_repo.exists_by_hash = AsyncMock(return_value=exists_by_hash)
    mock_repo.touch_last_used = AsyncMock()

    validator = PATValidator(
        repo=mock_repo,
        cache_ttl=0,
        revoked_cache_ttl=0,
    )
    app.state.pat_validator = validator

    app.add_middleware(PATRevocationMiddleware)

    @app.get("/protected")
    async def protected():
        return {"status": "ok"}

    return app


class TestPATRevocationMiddleware:
    def test_valid_pat_passes_through(self):
        app = _create_app(exists_by_hash=True)
        client = TestClient(app)

        token = _make_pat_jwt()
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_revoked_pat_returns_401(self):
        app = _create_app(exists_by_hash=False)
        client = TestClient(app)

        token = _make_pat_jwt()
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "revoked" in resp.json()["detail"].lower()

    def test_no_auth_header_passes_through(self):
        app = _create_app()
        client = TestClient(app)

        resp = client.get("/protected")
        assert resp.status_code == 200

    def test_non_bearer_auth_passes_through(self):
        app = _create_app()
        client = TestClient(app)

        resp = client.get("/protected", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 200

    def test_non_pat_jwt_passes_through(self):
        """Non-PAT JWTs (e.g. OIDC tokens) are not checked for revocation."""
        app = _create_app(exists_by_hash=False)  # would fail if checked
        client = TestClient(app)

        payload = {"sub": "user-1", "type": "session"}
        token = jwt.encode(payload, SIGNING_KEY, algorithm="HS256")
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_no_validator_passes_through(self):
        """When pat_validator is not set on app.state, all requests pass."""
        app = FastAPI()

        @app.get("/protected")
        async def protected():
            return {"status": "ok"}

        app.add_middleware(PATRevocationMiddleware)
        client = TestClient(app)

        token = _make_pat_jwt()
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
