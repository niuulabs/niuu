"""Discover wardens from another Ravn HTTP endpoint."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ravn.warden.models import WardenSpec


class HttpRavnWardenDiscoveryAdapter:
    """Fetch WardenSpec summaries from a remote Ravn API."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 5.0,
        headers: dict[str, str] | None = None,
        auth_header_env: str = "",
        exclude_ids: list[str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._headers = headers or {}
        self._auth_header_env = auth_header_env
        self._exclude_ids = set(exclude_ids or [])

    async def list_wardens(self) -> list[WardenSpec]:
        """Return wardens from the remote Ravn API."""
        headers = dict(self._headers)
        if self._auth_header_env:
            token = os.environ.get(self._auth_header_env, "").strip()
            if token:
                headers.setdefault("Authorization", token)

        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(self._wardens_url(), headers=headers)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, list):
            return []

        wardens: list[WardenSpec] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            warden = self._parse_warden(item)
            if warden is None or warden.id in self._exclude_ids:
                continue
            wardens.append(warden)
        return wardens

    def _wardens_url(self) -> str:
        if self._base_url.endswith("/api/v1/ravn"):
            return f"{self._base_url}/wardens"
        if self._base_url.endswith("/ravn"):
            return f"{self._base_url}/wardens"
        return f"{self._base_url}/api/v1/ravn/wardens"

    def _parse_warden(self, item: dict[str, Any]) -> WardenSpec | None:
        try:
            return WardenSpec.model_validate(item)
        except ValueError:
            return None
