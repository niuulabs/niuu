"""Tests for cli.auth.oidc — OIDC/PKCE authentication client."""

from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

import httpx
import pytest
import respx

from cli.auth.credentials import CredentialStore, StoredTokens
from cli.auth.oidc import (
    DEFAULT_SCOPES,
    LoginExpiredError,
    OIDCClient,
    TokenRefreshError,
    decode_id_token,
    fresh_access_token,
    generate_pkce_pair,
)

ISSUER = "https://idp.example.com"
DISCOVERY = {
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "revocation_endpoint": f"{ISSUER}/revoke",
}


def _stored(store: CredentialStore, **overrides: object) -> None:
    fields: dict[str, object] = {
        "access_token": "old-access",
        "refresh_token": "refresh-tok",
        "issuer": ISSUER,
        "client_id": "cli",
    }
    fields.update(overrides)
    store.store(StoredTokens(**fields))  # type: ignore[arg-type]


def _client(store: CredentialStore) -> OIDCClient:
    client = OIDCClient(issuer=ISSUER, client_id="cli", credential_store=store)
    client._discovery = dict(DISCOVERY)
    return client


@pytest.fixture(autouse=True)
def _set_credential_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIUU_CREDENTIAL_KEY", "test-secret-key-for-ci")


class TestPKCE:
    def test_verifier_length(self) -> None:
        verifier, _ = generate_pkce_pair()
        assert len(verifier) > 40

    def test_challenge_is_s256(self) -> None:
        verifier, challenge = generate_pkce_pair()
        expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
        assert challenge == expected

    def test_unique_pairs(self) -> None:
        pair1 = generate_pkce_pair()
        pair2 = generate_pkce_pair()
        assert pair1[0] != pair2[0]


class TestDecodeIdToken:
    def test_decodes_jwt_without_verification(self) -> None:
        import jwt as pyjwt

        payload = {"sub": "user-1", "name": "Test User", "email": "test@example.com"}
        token = pyjwt.encode(payload, "a-test-secret-long-enough-for-hs256", algorithm="HS256")
        claims = decode_id_token(token)
        assert claims["sub"] == "user-1"
        assert claims["name"] == "Test User"


class TestOIDCClientDiscover:
    async def test_fetches_discovery_document(self) -> None:
        issuer = "https://idp.example.com"
        discovery = {
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "issuer": issuer,
        }
        with respx.mock:
            respx.get(f"{issuer}/.well-known/openid-configuration").mock(
                return_value=httpx.Response(200, json=discovery)
            )
            client = OIDCClient(issuer=issuer, client_id="test")
            result = await client.discover()
        assert result["authorization_endpoint"] == f"{issuer}/authorize"

    async def test_caches_discovery(self) -> None:
        issuer = "https://idp.example.com"
        discovery = {"authorization_endpoint": "x", "token_endpoint": "y"}
        with respx.mock:
            route = respx.get(f"{issuer}/.well-known/openid-configuration").mock(
                return_value=httpx.Response(200, json=discovery)
            )
            client = OIDCClient(issuer=issuer, client_id="test")
            await client.discover()
            await client.discover()
        assert route.call_count == 1


class TestOIDCClientRefresh:
    async def test_refresh_exchanges_token(self, tmp_path: Path) -> None:
        issuer = "https://idp.example.com"
        store = CredentialStore(path=tmp_path / "creds")
        store.store(
            StoredTokens(
                access_token="old-access",
                refresh_token="refresh-tok",
                issuer=issuer,
            )
        )

        client = OIDCClient(
            issuer=issuer,
            client_id="cli",
            credential_store=store,
        )
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
                        "refresh_token": "new-refresh",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            )
            new_token = await client.refresh()

        assert new_token == "new-access"
        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "new-access"
        assert loaded.refresh_token == "new-refresh"

    async def test_refresh_without_a_login_raises(self, tmp_path: Path) -> None:
        client = _client(CredentialStore(path=tmp_path / "creds"))
        with pytest.raises(LoginExpiredError, match="niuu login"):
            await client.refresh()

    async def test_a_refused_refresh_names_the_issuers_reason(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store)
        with respx.mock:
            respx.post(f"{ISSUER}/token").mock(
                return_value=httpx.Response(
                    400,
                    json={"error": "invalid_grant", "error_description": "Token is not active"},
                )
            )
            with pytest.raises(LoginExpiredError, match="invalid_grant: Token is not active"):
                await _client(store).refresh()
        assert store.load().access_token == "old-access"  # type: ignore[union-attr]

    async def test_a_server_error_is_not_called_an_expired_login(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store)
        with respx.mock:
            respx.post(f"{ISSUER}/token").mock(return_value=httpx.Response(503, text="down"))
            with pytest.raises(TokenRefreshError, match="HTTP 503: down"):
                await _client(store).refresh()

    async def test_an_unreachable_issuer_raises(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store)
        with respx.mock:
            respx.post(f"{ISSUER}/token").mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(TokenRefreshError, match="Could not reach"):
                await _client(store).refresh()


class TestLoginScopes:
    def test_the_default_login_asks_for_an_offline_refresh_token(self) -> None:
        assert "offline_access" in DEFAULT_SCOPES.split()
        assert OIDCClient(issuer=ISSUER, client_id="cli")._scopes == DEFAULT_SCOPES


class TestFreshAccessToken:
    async def test_nobody_logged_in(self, tmp_path: Path) -> None:
        assert await fresh_access_token(CredentialStore(path=tmp_path / "creds")) is None

    async def test_an_unexpired_token_is_used_as_is(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store, expires_at=time.time() + 300)
        with respx.mock:
            assert await fresh_access_token(store) == "old-access"

    async def test_a_token_without_expiry_is_used_as_is(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store)
        assert await fresh_access_token(store) == "old-access"

    async def test_an_expired_token_is_refreshed_first(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store, expires_at=time.time() - 1)
        with respx.mock:
            respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
                return_value=httpx.Response(200, json=DISCOVERY)
            )
            token = respx.post(f"{ISSUER}/token").mock(
                return_value=httpx.Response(200, json={"access_token": "new", "expires_in": 300})
            )
            assert await fresh_access_token(store) == "new"
        assert b"client_id=cli" in token.calls[0].request.content
        assert store.load().access_token == "new"  # type: ignore[union-attr]

    async def test_an_expired_token_that_cannot_refresh_raises(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store, refresh_token="", expires_at=time.time() - 1)
        with pytest.raises(LoginExpiredError, match="no refresh token"):
            await fresh_access_token(store)


class TestOIDCClientWhoami:
    def test_whoami_returns_claims(self, tmp_path: Path) -> None:
        import jwt as pyjwt

        id_token = pyjwt.encode(
            {"sub": "u1", "name": "Alice", "email": "alice@example.com"},
            "a-test-secret-long-enough-for-hs256",
            algorithm="HS256",
        )
        store = CredentialStore(path=tmp_path / "creds")
        store.store(StoredTokens(access_token="x", id_token=id_token))

        client = OIDCClient(issuer="", client_id="", credential_store=store)
        claims = client.whoami()
        assert claims is not None
        assert claims["name"] == "Alice"

    def test_whoami_returns_none_when_not_logged_in(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        client = OIDCClient(issuer="", client_id="", credential_store=store)
        assert client.whoami() is None


class TestOIDCClientLogout:
    async def test_logout_revokes_the_refresh_token_then_clears(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store)
        with respx.mock:
            revoke = respx.post(f"{ISSUER}/revoke").mock(return_value=httpx.Response(200))
            await _client(store).logout()
        body = revoke.calls[0].request.content
        assert b"token=refresh-tok" in body
        assert b"token_type_hint=refresh_token" in body
        assert b"client_id=cli" in body
        assert store.load() is None

    async def test_a_failed_revocation_raises_and_still_clears(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store)
        with respx.mock:
            respx.post(f"{ISSUER}/revoke").mock(
                return_value=httpx.Response(400, json={"error": "invalid_client"})
            )
            with pytest.raises(TokenRefreshError, match="did not revoke the login"):
                await _client(store).logout()
        assert store.load() is None

    async def test_an_issuer_without_revocation_raises(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store)
        client = _client(store)
        del client._discovery["revocation_endpoint"]
        with pytest.raises(TokenRefreshError, match="no revocation_endpoint"):
            await client.logout()
        assert store.load() is None

    async def test_an_unreachable_revocation_endpoint_raises(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store)
        with respx.mock:
            respx.post(f"{ISSUER}/revoke").mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(TokenRefreshError, match="Could not reach"):
                await _client(store).logout()

    async def test_a_login_without_refresh_token_is_only_cleared(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        _stored(store, refresh_token="")
        with respx.mock:
            await _client(store).logout()
        assert store.load() is None


class TestOIDCClientLoadAccessToken:
    def test_load_access_token(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        store.store(StoredTokens(access_token="my-token"))
        client = OIDCClient(issuer="", client_id="", credential_store=store)
        assert client.load_access_token() == "my-token"

    def test_load_access_token_none(self, tmp_path: Path) -> None:
        store = CredentialStore(path=tmp_path / "creds")
        client = OIDCClient(issuer="", client_id="", credential_store=store)
        assert client.load_access_token() is None
