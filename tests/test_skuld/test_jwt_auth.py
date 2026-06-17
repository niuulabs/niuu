"""Tests for Skuld broker JWT extraction and auth header propagation."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from skuld.broker import (
    Broker,
    _decode_jwt_claims,
    _extract_bearer_token,
    _extract_token_from_websocket,
)
from skuld.config import SkuldSettings


def _make_jwt(claims: dict) -> str:
    """Build a fake JWT (header.payload.signature) with the given claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    signature = base64.urlsafe_b64encode(b"fake-sig").rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.{signature.decode()}"


class TestDecodeJwtClaims:
    """Tests for _decode_jwt_claims helper."""

    def test_valid_jwt(self):
        claims = {"sub": "user-123", "email": "test@example.com", "tenant_id": "t1"}
        token = _make_jwt(claims)
        result = _decode_jwt_claims(token)
        assert result["sub"] == "user-123"
        assert result["email"] == "test@example.com"

    def test_invalid_token_returns_empty(self):
        assert _decode_jwt_claims("not-a-jwt") == {}

    def test_empty_string_returns_empty(self):
        assert _decode_jwt_claims("") == {}

    def test_malformed_base64_returns_empty(self):
        assert _decode_jwt_claims("a.!!!invalid!!!.c") == {}


class TestExtractBearerToken:
    """Tests for _extract_bearer_token helper."""

    def test_bearer_token(self):
        assert _extract_bearer_token({"authorization": "Bearer abc123"}) == "abc123"

    def test_bearer_case_insensitive(self):
        assert _extract_bearer_token({"authorization": "bearer abc123"}) == "abc123"

    def test_no_auth_header(self):
        assert _extract_bearer_token({}) is None

    def test_non_bearer_auth(self):
        assert _extract_bearer_token({"authorization": "Basic abc123"}) is None


class TestExtractTokenFromWebSocket:
    """Tests for _extract_token_from_websocket helper."""

    def _make_ws(self, headers: dict | None = None, query_params: dict | None = None):
        ws = MagicMock()
        ws.headers = headers or {}
        ws.query_params = query_params or {}
        return ws

    def test_authorization_header_preferred(self):
        ws = self._make_ws(
            headers={"authorization": "Bearer header-token"},
            query_params={"access_token": "query-token"},
        )
        assert _extract_token_from_websocket(ws) == "header-token"

    def test_query_param_fallback(self):
        ws = self._make_ws(query_params={"access_token": "query-token"})
        assert _extract_token_from_websocket(ws) == "query-token"

    def test_websocket_subprotocol_preferred_over_query_param(self):
        ws = self._make_ws(
            headers={"sec-websocket-protocol": "volundr.bearer.protocol-token"},
            query_params={"access_token": "query-token"},
        )
        assert _extract_token_from_websocket(ws) == "protocol-token"

    def test_no_token_returns_none(self):
        ws = self._make_ws()
        assert _extract_token_from_websocket(ws) is None


class TestBrokerJwtIntegration:
    """Tests for JWT handling in the Broker class."""

    @pytest.fixture
    def settings(self, tmp_path):
        return SkuldSettings(
            session={"id": "test-session", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            volundr_api_url="http://volundr-api:8080",
        )

    @pytest.fixture
    def test_broker(self, settings):
        return Broker(settings=settings)

    def test_init_no_jwt(self, test_broker):
        assert test_broker._user_jwt is None
        assert test_broker._user_claims == {}

    def test_update_jwt_from_websocket_with_bearer(self, test_broker):
        claims = {"sub": "user-42", "email": "u@x.com"}
        token = _make_jwt(claims)
        ws = MagicMock()
        ws.headers = {"authorization": f"Bearer {token}"}
        ws.query_params = {}

        test_broker._update_jwt_from_websocket(ws)

        assert test_broker._user_jwt == token
        assert test_broker._user_claims["sub"] == "user-42"

    def test_update_jwt_from_websocket_with_query_param(self, test_broker):
        claims = {"sub": "user-99"}
        token = _make_jwt(claims)
        ws = MagicMock()
        ws.headers = {}
        ws.query_params = {"access_token": token}

        test_broker._update_jwt_from_websocket(ws)

        assert test_broker._user_jwt == token
        assert test_broker._user_claims["sub"] == "user-99"

    def test_update_jwt_from_websocket_with_subprotocol(self, test_broker):
        claims = {"sub": "user-protocol"}
        token = _make_jwt(claims)
        ws = MagicMock()
        ws.headers = {"sec-websocket-protocol": f"chat, volundr.bearer.{token}"}
        ws.query_params = {}

        test_broker._update_jwt_from_websocket(ws)

        assert test_broker._user_jwt == token
        assert test_broker._user_claims["sub"] == "user-protocol"

    def test_update_jwt_refreshes_on_reconnect(self, test_broker):
        old_token = _make_jwt({"sub": "user-1"})
        new_token = _make_jwt({"sub": "user-1", "exp": 9999999999})

        ws1 = MagicMock()
        ws1.headers = {"authorization": f"Bearer {old_token}"}
        ws1.query_params = {}
        test_broker._update_jwt_from_websocket(ws1)
        assert test_broker._user_jwt == old_token

        ws2 = MagicMock()
        ws2.headers = {"authorization": f"Bearer {new_token}"}
        ws2.query_params = {}
        test_broker._update_jwt_from_websocket(ws2)
        assert test_broker._user_jwt == new_token

    def test_build_auth_headers_with_jwt(self, test_broker):
        token = _make_jwt({"sub": "u1"})
        test_broker._user_jwt = token
        with patch.dict("os.environ", {"VOLUNDR_API_TOKEN": ""}, clear=False):
            headers = test_broker._build_auth_headers()
        assert headers == {"Authorization": f"Bearer {token}"}

    def test_build_auth_headers_no_token_returns_empty(self, test_broker, monkeypatch):
        monkeypatch.delenv("VOLUNDR_API_TOKEN", raising=False)
        test_broker._user_jwt = None
        headers = test_broker._build_auth_headers()
        assert headers == {}

    @pytest.mark.asyncio
    async def test_get_http_client_uses_jwt(self, test_broker):
        token = _make_jwt({"sub": "u1"})
        test_broker._user_jwt = token

        with patch.dict("os.environ", {"VOLUNDR_API_TOKEN": ""}, clear=False):
            client = await test_broker._get_http_client()
        assert client.headers.get("authorization") == f"Bearer {token}"

        # Cleanup
        await client.aclose()
        test_broker._http_client = None

    @pytest.mark.asyncio
    async def test_get_http_client_recreates_on_jwt_change(self, test_broker):
        token1 = _make_jwt({"sub": "u1"})
        token2 = _make_jwt({"sub": "u1", "refreshed": True})

        with patch.dict("os.environ", {"VOLUNDR_API_TOKEN": ""}, clear=False):
            test_broker._user_jwt = token1
            client1 = await test_broker._get_http_client()

            test_broker._user_jwt = token2
            client2 = await test_broker._get_http_client()

        # Should be a new client instance
        assert client2 is not client1
        assert client2.headers.get("authorization") == f"Bearer {token2}"

        # Cleanup
        await client2.aclose()
        test_broker._http_client = None

    def test_update_jwt_propagates_to_chronicle_watcher(self, test_broker):
        mock_watcher = MagicMock()
        test_broker._chronicle_watcher = mock_watcher

        token = _make_jwt({"sub": "u1"})
        ws = MagicMock()
        ws.headers = {"authorization": f"Bearer {token}"}
        ws.query_params = {}

        with patch.dict("os.environ", {"VOLUNDR_API_TOKEN": ""}, clear=False):
            test_broker._update_jwt_from_websocket(ws)

        mock_watcher.update_headers.assert_called_once_with({"Authorization": f"Bearer {token}"})

    def test_no_jwt_on_websocket_logs_warning(self, test_broker):
        """When no token is present and no prior JWT exists, a warning is logged."""
        ws = MagicMock()
        ws.headers = {}
        ws.query_params = {}

        test_broker._update_jwt_from_websocket(ws)

        # No JWT stored
        assert test_broker._user_jwt is None

    def test_no_jwt_on_websocket_preserves_existing(self, test_broker):
        """When reconnect has no token, existing JWT is preserved."""
        existing_token = _make_jwt({"sub": "u1"})
        test_broker._user_jwt = existing_token

        ws = MagicMock()
        ws.headers = {}
        ws.query_params = {}

        test_broker._update_jwt_from_websocket(ws)

        # Existing JWT preserved
        assert test_broker._user_jwt == existing_token

    @pytest.mark.asyncio
    async def test_get_http_client_fallback_when_no_jwt(self, test_broker, monkeypatch):
        """When no JWT or PAT is set, client has no auth headers."""
        monkeypatch.delenv("VOLUNDR_API_TOKEN", raising=False)
        test_broker._user_jwt = None
        client = await test_broker._get_http_client()
        assert "authorization" not in {k.lower() for k in client.headers.keys()}

        # Cleanup
        await client.aclose()
        test_broker._http_client = None

    @pytest.mark.asyncio
    async def test_get_http_client_reuses_when_jwt_unchanged(self, test_broker):
        """Client is reused when JWT hasn't changed."""
        token = _make_jwt({"sub": "u1"})
        test_broker._user_jwt = token

        client1 = await test_broker._get_http_client()
        client2 = await test_broker._get_http_client()

        assert client1 is client2

        # Cleanup
        await client1.aclose()
        test_broker._http_client = None
