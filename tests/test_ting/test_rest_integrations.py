"""Tests for the Ting Telegram setup REST API endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.adapters.inbound.rest_integrations import create_telegram_setup_router
from ting.config import AuthConfig, Settings


def _make_client(*, allow_anonymous_dev: bool = False) -> TestClient:
    app = FastAPI()
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=allow_anonymous_dev))
    app.include_router(
        create_telegram_setup_router(
            telegram_bot_username="TestBot",
            telegram_hmac_key="test-secret",
        )
    )
    return TestClient(app)


def _auth_headers(user_id: str = "user-1") -> dict[str, str]:
    return {"x-auth-user-id": user_id}


class TestTelegramSetup:
    def test_returns_deeplink(self) -> None:
        client = _make_client()

        resp = client.get("/api/v1/ting/telegram/setup", headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["deeplink"].startswith("https://t.me/TestBot?start=")
        assert "user-1:" in data["token"]

    def test_token_contains_user_id(self) -> None:
        client = _make_client()

        resp = client.get(
            "/api/v1/ting/telegram/setup",
            headers=_auth_headers("user-42"),
        )

        data = resp.json()
        assert data["token"].startswith("user-42:")

    def test_integrations_alias_returns_same_payload(self) -> None:
        client = _make_client()

        legacy = client.get("/api/v1/ting/telegram/setup", headers=_auth_headers())
        canonical = client.get("/api/v1/ting/integrations/telegram/setup", headers=_auth_headers())

        assert legacy.status_code == 200
        assert canonical.status_code == 200
        assert canonical.json() == legacy.json()


class TestAuthRequired:
    def test_no_auth_returns_401(self) -> None:
        client = _make_client()

        resp = client.get("/api/v1/ting/telegram/setup")

        assert resp.status_code == 401
