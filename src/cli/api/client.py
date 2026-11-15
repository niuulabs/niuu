"""Base async HTTP client with Bearer token auth and automatic 401 retry."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx

REQUEST_TIMEOUT_SECONDS = 30.0


class APIClient:
    """Async httpx wrapper with Bearer auth and transparent token refresh.

    On a 401 response the client calls ``refresh_token_fn`` (if provided),
    replaces the cached access token and retries the request **once**. A refresh that
    fails raises (``cli.auth.oidc.LoginError``) instead of handing back the 401.
    """

    def __init__(
        self,
        base_url: str,
        access_token: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        refresh_token_fn: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._timeout = timeout
        self._refresh_token_fn = refresh_token_fn

    @property
    def base_url(self) -> str:
        return self._base_url

    def set_token(self, token: str) -> None:
        self._access_token = token

    def _headers(self) -> dict[str, str]:
        if self._access_token:
            return {"Authorization": f"Bearer {self._access_token}"}
        return {}

    async def _maybe_refresh(self) -> bool:
        """Refresh the access token; False when this client has no way to refresh."""
        if self._refresh_token_fn is None:
            return False
        self._access_token = await self._refresh_token_fn()
        return True

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Send an HTTP request, retrying once on 401 after token refresh."""
        url = f"{self._base_url}{path}"
        effective_timeout = timeout or self._timeout
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                params=params,
            )
            if resp.status_code == 401 and await self._maybe_refresh():
                resp = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json,
                    params=params,
                )
            return resp

    async def get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        return await self.request("GET", path, params=params, timeout=timeout)

    async def post(
        self,
        path: str,
        *,
        json: Any | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        return await self.request("POST", path, json=json, timeout=timeout)

    async def delete(
        self,
        path: str,
        *,
        timeout: float | None = None,
    ) -> httpx.Response:
        return await self.request("DELETE", path, timeout=timeout)

    async def stream_sse(self, path: str) -> AsyncGenerator[tuple[str, str], None]:
        """Yield ``(event_type, data)`` tuples from an SSE endpoint.

        On a 401 the client attempts a single token refresh and reconnects.
        SSE keepalive comments (lines starting with ``:``) are silently dropped.
        """
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.send(
                client.build_request("GET", url, headers=self._headers()),
                stream=True,
            )
            if resp.status_code == 401 and await self._maybe_refresh():
                await resp.aclose()
                resp = await client.send(
                    client.build_request("GET", url, headers=self._headers()),
                    stream=True,
                )
            resp.raise_for_status()
            try:
                event_type = ""
                async for line in resp.aiter_lines():
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_type = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data = line[len("data:") :].strip()
                        yield event_type, data
                        event_type = ""
                    elif line == "":
                        event_type = ""
            finally:
                await resp.aclose()
