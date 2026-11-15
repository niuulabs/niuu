"""Extended tests for cli.auth.oidc — callback handler, exchange, and login flow."""

from __future__ import annotations

import io
from pathlib import Path
from threading import Event
from unittest.mock import patch

import httpx
import pytest
import respx

from cli.auth.credentials import CredentialStore, StoredTokens
from cli.auth.oidc import (
    LoginExpiredError,
    OIDCClient,
    _make_callback_handler,
)


@pytest.fixture(autouse=True)
def _set_credential_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIUU_CREDENTIAL_KEY", "test-secret-key-for-ci")


class TestCallbackHandler:
    """Tests for the _make_callback_handler HTTP handler."""

    def _make_request(self, handler_cls, path: str) -> None:
        """Simulate a GET request to the handler."""
        # Create a minimal mock request object
        handler = handler_cls.__new__(handler_cls)
        handler.path = path
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.requestline = f"GET {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.client_address = ("127.0.0.1", 12345)

        responses: list[tuple] = []

        def mock_send_response(code):
            responses.append(("status", code))

        def mock_send_header(key, val):
            responses.append(("header", key, val))

        def mock_end_headers():
            responses.append(("end",))

        handler.send_response = mock_send_response
        handler.send_header = mock_send_header
        handler.end_headers = mock_end_headers

        handler.do_GET()
        return responses

    def test_valid_code_callback(self) -> None:
        event = Event()
        result: dict[str, str] = {}
        state = "expected-state-123"
        handler_cls = _make_callback_handler(event, result, state)

        self._make_request(handler_cls, f"/callback?code=auth-code-456&state={state}")

        assert event.is_set()
        assert result["code"] == "auth-code-456"
        assert "error" not in result

    def test_state_mismatch(self) -> None:
        event = Event()
        result: dict[str, str] = {}
        handler_cls = _make_callback_handler(event, result, "expected-state")

        self._make_request(handler_cls, "/callback?code=abc&state=wrong-state")

        assert event.is_set()
        assert result["error"] == "state_mismatch"

    def test_error_callback(self) -> None:
        event = Event()
        result: dict[str, str] = {}
        state = "my-state"
        handler_cls = _make_callback_handler(event, result, state)

        self._make_request(
            handler_cls,
            f"/callback?error=access_denied&error_description=User+denied&state={state}",
        )

        assert event.is_set()
        assert result["error"] == "access_denied"
        assert result["error_description"] == "User denied"

    def test_log_message_suppressed(self) -> None:
        event = Event()
        result: dict[str, str] = {}
        handler_cls = _make_callback_handler(event, result, "s")
        handler = handler_cls.__new__(handler_cls)
        # Should not raise
        handler.log_message("test %s", "msg")


class TestOIDCClientExchangeCode:
    """Tests for OIDCClient._exchange_code()."""

    @pytest.mark.asyncio
    async def test_exchange_code_returns_stored_tokens(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        client = OIDCClient(
            issuer="https://idp.example.com",
            client_id="cli",
            credential_store=store,
        )

        with respx.mock:
            respx.post("https://idp.example.com/token").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "access-123",
                        "refresh_token": "refresh-456",
                        "id_token": "id-789",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            )
            tokens = await client._exchange_code(
                token_endpoint="https://idp.example.com/token",
                code="auth-code",
                redirect_uri="http://127.0.0.1:8888/callback",
                verifier="test-verifier",
            )

        assert tokens.access_token == "access-123"
        assert tokens.refresh_token == "refresh-456"
        assert tokens.id_token == "id-789"
        assert tokens.token_type == "Bearer"
        assert tokens.expires_at > 0

    @pytest.mark.asyncio
    async def test_exchange_code_without_expires_in(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        client = OIDCClient(
            issuer="https://idp.example.com",
            client_id="cli",
            credential_store=store,
        )

        with respx.mock:
            respx.post("https://idp.example.com/token").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "access-123",
                    },
                )
            )
            tokens = await client._exchange_code(
                token_endpoint="https://idp.example.com/token",
                code="auth-code",
                redirect_uri="http://127.0.0.1:8888/callback",
                verifier="test-verifier",
            )

        assert tokens.expires_at == 0.0
        assert tokens.refresh_token == ""
        assert tokens.id_token == ""


class TestOIDCClientLogin:
    """Tests for the full OIDCClient.login() flow."""

    @pytest.mark.asyncio
    async def test_login_timeout(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        client = OIDCClient(
            issuer="https://idp.example.com",
            client_id="cli",
            credential_store=store,
        )
        client._discovery = {
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
        }

        with (
            patch("cli.auth.oidc.webbrowser.open"),
            patch("cli.auth.oidc.CALLBACK_TIMEOUT_SECONDS", 0.01),
            pytest.raises(TimeoutError, match="callback not received"),
        ):
            await client.login()

    @pytest.mark.asyncio
    async def test_login_error_from_idp(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        client = OIDCClient(
            issuer="https://idp.example.com",
            client_id="cli",
            credential_store=store,
        )
        client._discovery = {
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
        }

        def fake_browser_open(url):
            """Simulate browser callback with an error."""
            import http.client
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            state = params["state"][0]
            redirect = params["redirect_uri"][0]
            redirect_parsed = urlparse(redirect)

            conn = http.client.HTTPConnection(redirect_parsed.hostname, redirect_parsed.port)
            conn.request(
                "GET",
                f"/callback?error=access_denied&error_description=User+denied&state={state}",
            )
            conn.getresponse()
            conn.close()

        with (
            patch("cli.auth.oidc.webbrowser.open", side_effect=fake_browser_open),
            pytest.raises(RuntimeError, match="OIDC error"),
        ):
            await client.login()

    @pytest.mark.asyncio
    async def test_login_success(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        client = OIDCClient(
            issuer="https://idp.example.com",
            client_id="cli",
            credential_store=store,
        )
        client._discovery = {
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
        }

        def fake_browser_open(url):
            """Simulate browser callback with a code."""
            import http.client
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            state = params["state"][0]
            redirect = params["redirect_uri"][0]
            redirect_parsed = urlparse(redirect)

            conn = http.client.HTTPConnection(redirect_parsed.hostname, redirect_parsed.port)
            conn.request(
                "GET",
                f"/callback?code=auth-code-success&state={state}",
            )
            conn.getresponse()
            conn.close()

        with (
            patch("cli.auth.oidc.webbrowser.open", side_effect=fake_browser_open),
            respx.mock,
        ):
            respx.post("https://idp.example.com/token").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "id_token": "new-id",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            )
            tokens = await client.login()

        assert tokens.access_token == "new-access"
        # Should be persisted
        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "new-access"


class TestOIDCClientRefreshEdgeCases:
    """Additional edge cases for OIDCClient.refresh()."""

    @pytest.mark.asyncio
    async def test_refresh_no_refresh_token(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        store.store(StoredTokens(access_token="old", refresh_token="", issuer="x"))
        client = OIDCClient(issuer="x", client_id="cli", credential_store=store)
        with pytest.raises(LoginExpiredError, match="niuu login"):
            await client.refresh()

    @pytest.mark.asyncio
    async def test_refresh_preserves_old_refresh_token_if_not_returned(
        self, tmp_path: Path
    ) -> None:
        issuer = "https://idp.example.com"
        store = CredentialStore(path=tmp_path / "creds")
        store.store(
            StoredTokens(
                access_token="old-access",
                refresh_token="original-refresh",
                id_token="original-id",
                issuer=issuer,
            )
        )
        client = OIDCClient(issuer=issuer, client_id="cli", credential_store=store)
        client._discovery = {
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
        }

        with respx.mock:
            respx.post(f"{issuer}/token").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "access_token": "new-access",
                        # No refresh_token or id_token in response
                    },
                )
            )
            new_token = await client.refresh()

        assert new_token == "new-access"
        loaded = store.load()
        assert loaded is not None
        assert loaded.refresh_token == "original-refresh"
        assert loaded.id_token == "original-id"


class TestOIDCClientWhoamiEdgeCases:
    """Additional edge cases for OIDCClient.whoami()."""

    def test_whoami_with_invalid_jwt(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        store.store(StoredTokens(access_token="x", id_token="not-a-jwt"))
        client = OIDCClient(issuer="", client_id="", credential_store=store)
        assert client.whoami() is None
