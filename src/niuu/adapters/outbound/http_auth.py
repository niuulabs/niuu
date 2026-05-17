"""Reusable outbound HTTP auth adapters."""

from __future__ import annotations

import os

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
