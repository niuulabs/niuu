"""Tests for the authenticated access-only Codex token route."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_codex_credentials import create_codex_credentials_router
from volundr.adapters.outbound.codex_credential_broker import CodexCredentialBrokerError
from volundr.domain.models import Principal
from volundr.domain.ports import CodexAuthTokens


class _Broker:
    def __init__(self, *, error: bool = False) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def get_tokens(self, **kwargs) -> CodexAuthTokens:
        self.calls.append(kwargs)
        if self.error:
            raise CodexCredentialBrokerError("Codex authentication requires reconnection")
        return CodexAuthTokens(
            access_token="access-only-token",
            account_id="account-1",
            expires_in=1800,
            plan_type="pro",
        )


def _client(broker: _Broker) -> TestClient:
    app = FastAPI()
    app.include_router(create_codex_credentials_router(broker))
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="owner-1",
        tenant_id="tenant-1",
        email="owner@example.test",
        roles=["volundr:developer"],
    )
    return TestClient(app)


def test_route_scopes_broker_exchange_to_authenticated_user() -> None:
    broker = _Broker()

    response = _client(broker).post(
        "/api/v1/internal/credentials/codex/tokens",
        json={
            "credential_name": "codex-main",
            "credential_field": "auth.json",
            "force_refresh": True,
            "previous_access_token_sha256": "a" * 64,
        },
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "access_token": "access-only-token",
        "chatgpt_account_id": "account-1",
        "expires_in": 1800,
        "chatgpt_plan_type": "pro",
    }
    assert broker.calls == [
        {
            "owner_id": "owner-1",
            "credential_name": "codex-main",
            "credential_field": "auth.json",
            "force_refresh": True,
            "previous_access_token_sha256": "a" * 64,
        }
    ]


def test_route_returns_reconnect_conflict_without_refresh_token() -> None:
    response = _client(_Broker(error=True)).post(
        "/api/v1/internal/credentials/codex/tokens",
        json={},
    )

    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Codex authentication requires reconnection"}
    assert "refresh" not in response.text.lower()
