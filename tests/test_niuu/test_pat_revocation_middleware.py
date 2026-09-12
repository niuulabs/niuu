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


async def _websocket_run(token, validator=None, *, query=b"", revoke=False, identity=None):
    import asyncio
    from types import SimpleNamespace

    messages = []
    stopped = asyncio.Event()
    accepted = asyncio.Event()

    async def application(scope, receive, send):
        try:
            await send({"type": "websocket.accept"})
            accepted.set()
            await asyncio.Future()
        finally:
            stopped.set()

    async def send(message):
        messages.append(message)

    app = SimpleNamespace(state=SimpleNamespace(pat_validator=validator, identity=identity))
    middleware = PATRevocationMiddleware(application, websocket_check_interval=0.01)
    scope = {
        "type": "websocket",
        "app": app,
        "query_string": query,
        "headers": [(b"authorization", f"Bearer {token}".encode())] if token else [],
    }
    task = asyncio.create_task(middleware(scope, AsyncMock(), send))
    if revoke:
        await asyncio.wait_for(accepted.wait(), 1)
        validator.is_valid.return_value = False
    await asyncio.wait_for(task, 1)
    return messages, stopped.is_set()


async def test_websocket_expires_and_cancels_application_without_inbound_messages():
    import time

    token = jwt.encode({"sub": "alice", "exp": time.time() + 0.1}, SIGNING_KEY, algorithm="HS256")
    messages, stopped = await _websocket_run(token)
    assert messages == [{"type": "websocket.accept"}, {"type": "websocket.close", "code": 1008}]
    assert stopped


async def test_websocket_revocation_terminates_idle_connection():
    validator = AsyncMock()
    validator.is_valid.return_value = True
    messages, stopped = await _websocket_run(_make_pat_jwt(), validator, revoke=True)
    assert messages[-1] == {"type": "websocket.close", "code": 1008}
    assert stopped
    assert validator.is_valid.await_count >= 2


async def test_revoked_websocket_is_never_accepted():
    validator = AsyncMock()
    validator.is_valid.return_value = False
    messages, stopped = await _websocket_run(_make_pat_jwt(), validator)
    assert messages == [{"type": "websocket.close", "code": 1008}]
    assert not stopped


async def test_conflicting_websocket_credentials_are_denied():
    messages, stopped = await _websocket_run(_make_pat_jwt(), query=b"access_token=different")
    assert messages == [{"type": "websocket.close", "code": 1008}]
    assert not stopped


async def test_websocket_without_expiry_is_denied():
    token = jwt.encode({"sub": "alice"}, SIGNING_KEY, algorithm="HS256")
    messages, _ = await _websocket_run(token)
    assert messages == [{"type": "websocket.close", "code": 1008}]


async def test_query_credential_expiry_is_enforced():
    import time

    token = jwt.encode({"sub": "alice", "exp": time.time() + 0.1}, SIGNING_KEY, algorithm="HS256")
    messages, stopped = await _websocket_run("", query=f"access_token={token}".encode())
    assert messages[-1] == {"type": "websocket.close", "code": 1008}
    assert stopped


def test_revoked_query_pat_is_rejected_for_http():
    with TestClient(_create_app(exists_by_hash=False)) as client:
        response = client.get("/protected", params={"access_token": _make_pat_jwt()})
    assert response.status_code == 401


def test_conflicting_http_credentials_are_rejected():
    with TestClient(_create_app()) as client:
        response = client.get(
            "/protected",
            params={"access_token": "other"},
            headers={"Authorization": f"Bearer {_make_pat_jwt()}"},
        )
    assert response.status_code == 401


async def test_websocket_revalidates_account_status_and_closes_on_suspension():
    from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError

    identity = AsyncMock(spec=HeaderAuthenticationPort)
    identity.validate_headers.side_effect = [None, None, InvalidTokenError("Suspended")]
    messages, stopped = await _websocket_run(_make_pat_jwt(), identity=identity)
    assert messages == [{"type": "websocket.accept"}, {"type": "websocket.close", "code": 1008}]
    assert stopped
