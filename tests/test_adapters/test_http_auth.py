"""Tests for reusable outbound HTTP auth adapters."""

from __future__ import annotations

import os
from urllib.parse import parse_qs

import httpx
import pytest

from niuu.adapters.outbound.http_auth import ClientCredentialsBearerTokenAuthAdapter


def test_client_credentials_adapter_mints_and_caches_bearer_token() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["client_credentials"]
        assert body["client_id"] == ["volundr"]
        assert body["client_secret"] == ["secret"]
        assert body["audience"] == ["volundr-api"]
        return httpx.Response(200, json={"access_token": "jwt-123", "expires_in": 300})

    adapter = ClientCredentialsBearerTokenAuthAdapter(
        token_url="https://keycloak.test/token",
        client_id="volundr",
        client_secret="secret",
        audience="volundr-api",
        transport=httpx.MockTransport(handler),
    )

    assert adapter.headers() == {"Authorization": "Bearer jwt-123"}
    assert adapter.headers() == {"Authorization": "Bearer jwt-123"}
    assert calls == 1


def test_client_credentials_adapter_reads_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIENT_SECRET", "env-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        assert body["client_secret"] == ["env-secret"]
        return httpx.Response(200, json={"access_token": "jwt-456"})

    adapter = ClientCredentialsBearerTokenAuthAdapter(
        token_url="https://keycloak.test/token",
        client_id="volundr",
        client_secret_env="CLIENT_SECRET",
        transport=httpx.MockTransport(handler),
    )

    assert adapter.headers() == {"Authorization": "Bearer jwt-456"}


def test_client_credentials_adapter_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    adapter = ClientCredentialsBearerTokenAuthAdapter(
        token_url="https://keycloak.test/token",
        client_id="volundr",
        client_secret_env="MISSING_SECRET",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    with pytest.raises(RuntimeError, match="client secret"):
        adapter.headers()
