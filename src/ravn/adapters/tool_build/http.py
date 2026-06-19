"""Minimal async JSON HTTP client boundary for tool-build backends.

A tiny protocol so the Forge/Ting backends are unit-testable with a fake
client while the real implementation authenticates with projected workload
identity inside the cluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from niuu.adapters.outbound.http_auth import (
    StaticBearerTokenAuthAdapter,
    WorkloadIdentityBearerTokenAuthAdapter,
)
from niuu.ports.http_auth import HttpAuthPort


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Any


class AsyncJsonHttpClient(Protocol):
    """Authenticated JSON GET/POST used by the build backends."""

    async def get(self, url: str) -> HttpResponse:
        raise NotImplementedError

    async def post(self, url: str, json_body: dict[str, Any]) -> HttpResponse:
        raise NotImplementedError


def client_from_workload_identity(
    *,
    base_url: str,
    external_token_env: str = "",
    workload_token_file: str = "",
    workload_exchange_url: str = "",
    workload_audiences: list[str] | None = None,
    timeout_seconds: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> HttpxJsonClient:
    """Build the real client for dynamic tool-build adapters.

    In-cluster Ravn/Valkyrie calls use projected workload identity by default.
    ``external_token_env`` is intentionally explicit for non-cluster callers
    that still need to bring an already-issued bearer token.
    """
    if external_token_env:
        return HttpxJsonClient(
            auth=StaticBearerTokenAuthAdapter(token_env=external_token_env),
            timeout_seconds=timeout_seconds,
        )
    return HttpxJsonClient(
        auth=WorkloadIdentityBearerTokenAuthAdapter(
            base_url=base_url,
            token_file=workload_token_file,
            exchange_url=workload_exchange_url,
            audiences=workload_audiences,
            timeout_seconds=timeout_seconds,
            transport=transport,
        ),
        timeout_seconds=timeout_seconds,
    )


class HttpxJsonClient:
    """httpx-backed :class:`AsyncJsonHttpClient` with pluggable bearer auth."""

    def __init__(
        self,
        *,
        auth: HttpAuthPort | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._auth = auth
        self._timeout = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._auth is not None:
            headers.update(self._auth.headers())
        return headers

    async def get(self, url: str) -> HttpResponse:
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=self._headers())
            return HttpResponse(status_code=resp.status_code, body=_safe_json(resp))

    async def post(self, url: str, json_body: dict[str, Any]) -> HttpResponse:
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=self._headers(), json=json_body)
            return HttpResponse(status_code=resp.status_code, body=_safe_json(resp))


def _safe_json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 — non-JSON error bodies become text
        return getattr(resp, "text", "")
