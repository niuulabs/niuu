"""Unit tests for KeycloakTokenIssuer."""

from __future__ import annotations

import time

import jwt
import pytest
import respx
from httpx import Response

from niuu.adapters.keycloak_token_issuer import KeycloakTokenIssuer

TOKEN_URL = "https://keycloak.example.com/realms/test/protocol/openid-connect/token"
CLIENT_ID = "test-client"
CLIENT_SECRET = "secret"
SUBJECT_TOKEN = "user-access-token"
JWT_SIGNING_KEY = "jwt-signing-key-at-least-32-bytes-long"


def _make_jwt(sub: str = "user-1", jti: str = "tok-123", exp_offset: int = 3600) -> str:
    payload = {
        "sub": sub,
        "jti": jti,
        "exp": int(time.time()) + exp_offset,
    }
    return jwt.encode(payload, JWT_SIGNING_KEY, algorithm="HS256")


class TestConstruction:
    def test_stores_config(self) -> None:
        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            audience="volundr-api",
        )
        assert issuer._token_url == TOKEN_URL
        assert issuer._client_id == CLIENT_ID
        assert issuer._client_secret == CLIENT_SECRET
        assert issuer._audience == "volundr-api"
        assert issuer._client is None

    def test_audience_defaults_empty(self) -> None:
        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        assert issuer._audience == ""

    def test_extra_kwargs_ignored(self) -> None:
        # Should not raise even if extra kwargs are passed
        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            unknown_kwarg="ignored",
        )
        assert issuer._client_id == CLIENT_ID


class TestGetClient:
    @pytest.mark.asyncio
    async def test_creates_client_on_first_call(self) -> None:
        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        assert issuer._client is None
        client = await issuer._get_client()
        assert client is not None
        assert issuer._client is client
        await client.aclose()

    @pytest.mark.asyncio
    async def test_reuses_existing_client(self) -> None:
        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        c1 = await issuer._get_client()
        c2 = await issuer._get_client()
        assert c1 is c2
        await c1.aclose()


class TestIssueToken:
    @respx.mock
    @pytest.mark.asyncio
    async def test_success_returns_issued_token(self) -> None:
        raw_token = _make_jwt(sub="user-42", jti="jti-abc")
        respx.post(TOKEN_URL).mock(return_value=Response(200, json={"access_token": raw_token}))

        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            audience="api",
        )
        result = await issuer.issue_token(subject_token=SUBJECT_TOKEN, name="my-pat")

        assert result.raw_token == raw_token
        assert result.subject == "user-42"
        assert result.token_id == "jti-abc"
        assert result.expires_at > int(time.time())
        await issuer.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_success_without_audience(self) -> None:
        raw_token = _make_jwt()
        respx.post(TOKEN_URL).mock(return_value=Response(200, json={"access_token": raw_token}))

        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        result = await issuer.issue_token(subject_token=SUBJECT_TOKEN, name="test")
        assert result.raw_token == raw_token
        await issuer.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_non_200_raises_runtime_error(self) -> None:
        respx.post(TOKEN_URL).mock(return_value=Response(400, text="invalid_grant"))

        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        with pytest.raises(RuntimeError, match="Token exchange failed"):
            await issuer.issue_token(subject_token=SUBJECT_TOKEN, name="test")


class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_client(self) -> None:
        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        # Create a client first
        await issuer._get_client()
        assert issuer._client is not None

        await issuer.close()
        assert issuer._client is None

    @pytest.mark.asyncio
    async def test_close_without_client_is_noop(self) -> None:
        issuer = KeycloakTokenIssuer(
            token_url=TOKEN_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        assert issuer._client is None
        # Should not raise
        await issuer.close()
        assert issuer._client is None
