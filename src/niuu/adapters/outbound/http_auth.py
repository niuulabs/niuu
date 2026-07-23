"""Reusable outbound HTTP auth adapters."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from pathlib import Path

import httpx

from niuu.ports.http_auth import HttpAuthPort


class NoAuthHeaderAdapter(HttpAuthPort):
    """Emit no auth headers."""

    def headers(self) -> dict[str, str]:
        return {}

    def invalidate(self) -> bool:
        return False


class StaticBearerTokenAuthAdapter(HttpAuthPort):
    """Emit a bearer token from an injected value or explicit external env var."""

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

    def invalidate(self) -> bool:
        return False


class WorkloadIdentityBearerTokenAuthAdapter(HttpAuthPort):
    """Exchange a projected workload identity token for a short-lived bearer JWT."""

    def __init__(
        self,
        *,
        base_url: str = "",
        exchange_url: str = "",
        token_file: str = "",
        token_file_env: str = "NIUU_WORKLOAD_IDENTITY_TOKEN_FILE",
        exchange_url_env: str = "NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL",
        audiences: Sequence[str] | None = None,
        scopes: Sequence[str] | None = None,
        timeout_seconds: float = 10.0,
        refresh_skew_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._exchange_url = exchange_url
        self._token_file = token_file
        self._token_file_env = token_file_env
        self._exchange_url_env = exchange_url_env
        self._audiences = list(audiences or ["volundr-api", "forge", "ting", "mimir", "guild"])
        # Requested build scopes: when set, the exchanged token is minted as a
        # least-privilege valkyrie_build token limited to these scopes.
        self._scopes = list(scopes or [])
        self._timeout_seconds = timeout_seconds
        self._refresh_skew_seconds = refresh_skew_seconds
        self._transport = transport
        self._token = ""
        self._expires_at = 0.0

    def headers(self) -> dict[str, str]:
        token = self._current_token()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def invalidate(self) -> bool:
        self._token = ""
        self._expires_at = 0.0
        return True

    def _current_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._expires_at:
            return self._token

        proof_path = self._proof_path()
        if proof_path is None:
            return ""
        proof = proof_path.read_text(encoding="utf-8").strip()
        if not proof:
            return ""

        exchange_url = self._resolved_exchange_url()
        if not exchange_url:
            return ""

        body: dict[str, object] = {"token": proof, "audiences": self._audiences}
        if self._scopes:
            body["scopes"] = self._scopes
        with httpx.Client(timeout=self._timeout_seconds, transport=self._transport) as client:
            response = client.post(exchange_url, json=body)
            response.raise_for_status()
            payload = response.json()

        token = str(payload.get("token") or "")
        if not token:
            raise RuntimeError("workload token exchange response did not include token")

        try:
            expires_in = float(payload.get("expires_in", 300))
        except (TypeError, ValueError):
            expires_in = 300.0
        self._token = token
        self._expires_at = now + max(0.0, expires_in - self._refresh_skew_seconds)
        return token

    def _proof_path(self) -> Path | None:
        configured = self._token_file or os.environ.get(self._token_file_env, "")
        if not configured:
            configured = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        path = Path(configured).expanduser()
        return path if path.exists() else None

    def _resolved_exchange_url(self) -> str:
        configured = self._exchange_url or os.environ.get(self._exchange_url_env, "")
        if configured:
            return configured.rstrip("/")
        if self._base_url:
            return f"{self._base_url}/api/v1/tokens/workload/exchange"
        return ""


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

    def invalidate(self) -> bool:
        self._token = ""
        self._expires_at = 0.0
        return True

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
