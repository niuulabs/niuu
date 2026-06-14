"""Reusable outbound HTTP auth adapters."""

from __future__ import annotations

import os
import time

import httpx

from niuu.ports.http_auth import HttpAuthPort


class NoAuthHeaderAdapter(HttpAuthPort):
    """Emit no auth headers."""

    def headers(self) -> dict[str, str]:
        return {}


class StaticBearerTokenAuthAdapter(HttpAuthPort):
    """Emit a bearer token from an injected value or environment variable."""

    def __init__(
        self,
        *,
        token: str = "",
        token_env: str = "",
    ) -> None:
        self._token = token
        self._token_env = token_env

    def headers(self) -> dict[str, str]:
        token = self._token
        if not token and self._token_env:
            token = os.environ.get(self._token_env, "")
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}


class ClientCredentialsBearerTokenAuthAdapter(HttpAuthPort):
    """Mint and cache a bearer token with the OAuth2 client credentials grant."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str = "",
        client_secret_env: str = "",
        audience: str = "",
        scope: str = "",
        timeout_seconds: float = 10.0,
        refresh_skew_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._client_secret_env = client_secret_env
        self._audience = audience
        self._scope = scope
        self._timeout_seconds = timeout_seconds
        self._refresh_skew_seconds = refresh_skew_seconds
        self._transport = transport
        self._token = ""
        self._expires_at = 0.0

    def headers(self) -> dict[str, str]:
        token = self._current_token()
        return {"Authorization": f"Bearer {token}"}

    def _current_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._expires_at:
            return self._token

        client_secret = self._client_secret
        if not client_secret and self._client_secret_env:
            client_secret = os.environ.get(self._client_secret_env, "")
        if not client_secret:
            raise RuntimeError("OAuth client credentials auth requires a client secret")

        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": client_secret,
        }
        if self._audience:
            data["audience"] = self._audience
        if self._scope:
            data["scope"] = self._scope

        with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
            response = client.post(self._token_url, data=data)
            response.raise_for_status()
            payload = response.json()

        token = str(payload.get("access_token") or "")
        if not token:
            raise RuntimeError("OAuth client credentials response did not include access_token")

        try:
            expires_in = float(payload.get("expires_in", 300))
        except (TypeError, ValueError):
            expires_in = 300.0
        self._token = token
        self._expires_at = now + max(0.0, expires_in - self._refresh_skew_seconds)
        return token
