"""Tests for OAuth2Provider adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from volundr.adapters.outbound.oauth2_provider import OAuth2Provider
from volundr.domain.models import OAuthSpec

_HTTPX_CLIENT = "volundr.adapters.outbound.oauth2_provider.httpx.AsyncClient"


def _make_spec(**overrides) -> OAuthSpec:
    defaults = {
        "authorize_url": "https://auth.example.com/authorize",
        "token_url": "https://auth.example.com/token",
        "revoke_url": "",
        "scopes": (),
        "token_field_mapping": {},
        "extra_authorize_params": {},
        "extra_token_params": {},
    }
    defaults.update(overrides)
    return OAuthSpec(**defaults)


def _make_provider(spec: OAuthSpec | None = None, **kwargs) -> OAuth2Provider:
    if spec is None:
        spec = _make_spec()
    return OAuth2Provider(
        spec=spec,
        client_id=kwargs.get("client_id", "test-client-id"),
        client_secret=kwargs.get("client_secret", "test-client-secret"),
    )


class TestAuthorizationUrl:
    def test_builds_url_with_required_params(self):
        provider = _make_provider()

        url = provider.authorization_url(
            state="abc123",
            redirect_uri="https://app.test/callback",
        )

        assert "https://auth.example.com/authorize?" in url
        assert "client_id=test-client-id" in url
        assert "redirect_uri=https" in url
        assert "response_type=code" in url
        assert "state=abc123" in url

    def test_includes_scopes(self):
        spec = _make_spec(scopes=("read", "write", "admin"))
        provider = _make_provider(spec=spec)

        url = provider.authorization_url(state="s1", redirect_uri="https://app/cb")

        assert "scope=read+write+admin" in url

    def test_includes_extra_authorize_params(self):
        spec = _make_spec(
            extra_authorize_params={"prompt": "consent", "access_type": "offline"},
        )
        provider = _make_provider(spec=spec)

        url = provider.authorization_url(state="s1", redirect_uri="https://app/cb")

        assert "prompt=consent" in url
        assert "access_type=offline" in url

    def test_no_scope_when_empty(self):
        provider = _make_provider()

        url = provider.authorization_url(state="s1", redirect_uri="https://app/cb")

        assert "scope=" not in url


class TestExchangeCode:
    async def test_exchanges_code_for_token(self):
        spec = _make_spec(token_field_mapping={"api_key": "access_token"})
        provider = _make_provider(spec=spec)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "tok-abc",
            "token_type": "bearer",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(_HTTPX_CLIENT, return_value=mock_client):
            result = await provider.exchange_code(
                "auth-code-123",
                "https://app/cb",
            )

        assert result == {"api_key": "tok-abc"}
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[0][0] == "https://auth.example.com/token"
        post_data = call_kwargs[1]["data"]
        assert post_data["grant_type"] == "authorization_code"
        assert post_data["code"] == "auth-code-123"
        assert post_data["client_id"] == "test-client-id"
        assert post_data["client_secret"] == "test-client-secret"

    async def test_applies_token_field_mapping(self):
        spec = _make_spec(
            token_field_mapping={
                "api_key": "access_token",
                "refresh": "refresh_token",
            },
        )
        provider = _make_provider(spec=spec)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "at-123",
            "refresh_token": "rt-456",
            "token_type": "bearer",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(_HTTPX_CLIENT, return_value=mock_client):
            result = await provider.exchange_code("code", "https://app/cb")

        assert result == {"api_key": "at-123", "refresh": "rt-456"}

    async def test_falls_back_to_access_token_when_no_mapping_matches(self):
        spec = _make_spec(token_field_mapping={})
        provider = _make_provider(spec=spec)

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "fallback-tok"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(_HTTPX_CLIENT, return_value=mock_client):
            result = await provider.exchange_code("code", "https://app/cb")

        assert result == {"access_token": "fallback-tok"}

    async def test_includes_extra_token_params(self):
        spec = _make_spec(
            extra_token_params={"audience": "https://api.example.com"},
        )
        provider = _make_provider(spec=spec)

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(_HTTPX_CLIENT, return_value=mock_client):
            await provider.exchange_code("code", "https://app/cb")

        post_data = mock_client.post.call_args[1]["data"]
        assert post_data["audience"] == "https://api.example.com"

    async def test_json_token_endpoint_keeps_refresh_and_expiry(self):
        spec = _make_spec(token_request_format="json")
        provider = _make_provider(spec=spec)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "at-123",
            "refresh_token": "rt-456",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(_HTTPX_CLIENT, return_value=mock_client):
            result = await provider.exchange_code("code", "https://app/cb")

        assert result["access_token"] == "at-123"
        assert result["refresh_token"] == "rt-456"
        assert result["expires_at"]
        assert "json" in mock_client.post.call_args.kwargs
        assert "data" not in mock_client.post.call_args.kwargs


class TestRevokeToken:
    async def test_no_op_when_revoke_url_empty(self):
        spec = _make_spec(revoke_url="")
        provider = _make_provider(spec=spec)

        with patch(_HTTPX_CLIENT) as mock_cls:
            await provider.revoke_token("some-token")

        mock_cls.assert_not_called()

    async def test_calls_revoke_url(self):
        spec = _make_spec(revoke_url="https://auth.example.com/revoke")
        provider = _make_provider(spec=spec)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(_HTTPX_CLIENT, return_value=mock_client):
            await provider.revoke_token("tok-to-revoke")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://auth.example.com/revoke"
        assert call_args[1]["data"] == {"token": "tok-to-revoke"}

    async def test_revoke_swallows_exceptions(self):
        spec = _make_spec(revoke_url="https://auth.example.com/revoke")
        provider = _make_provider(spec=spec)

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("network error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(_HTTPX_CLIENT, return_value=mock_client):
            await provider.revoke_token("tok")  # should not raise


class TestGenerateState:
    def test_returns_string(self):
        state = OAuth2Provider.generate_state()

        assert isinstance(state, str)
        assert len(state) > 0

    def test_returns_unique_values(self):
        states = {OAuth2Provider.generate_state() for _ in range(10)}

        assert len(states) == 10
