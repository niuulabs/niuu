"""Minimal async JSON HTTP client boundary for tool-build backends.

A tiny protocol so the Forge/Ting backends are unit-testable with a fake
client while the real implementation authenticates with projected workload
identity inside the cluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

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
    """Authenticated JSON GET/POST used by the build backends.

    ``headers`` are merged over the client's own (auth) headers — protocol
    surfaces like A2A require per-call headers such as ``A2A-Version``.
    """

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        raise NotImplementedError

    async def post(
        self,
        url: str,
        json_body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        raise NotImplementedError


def client_from_workload_identity(
    *,
    base_url: str,
    external_token: str = "",
    external_token_env: str = "",
    workload_token_file: str = "",
    workload_exchange_url: str = "",
    workload_audiences: list[str] | None = None,
    workload_scopes: list[str] | None = None,
    timeout_seconds: float = 30.0,
    transport: httpx.BaseTransport | None = None,
    allowed_origins: list[str] | None = None,
) -> HttpxJsonClient:
    """Build the real client for dynamic tool-build adapters.

    In-cluster Ravn/Valkyrie calls use projected workload identity by default.
    ``external_token_env`` is intentionally explicit for non-cluster callers
    that still need to bring an already-issued bearer token.

    ``workload_scopes`` requests a least-privilege valkyrie_build token at the
    exchange — a build backend passes exactly the scope its launch endpoint
    enforces, so a leaked build token cannot do anything else.
    """
    if external_token:
        return HttpxJsonClient(
            auth=StaticBearerTokenAuthAdapter(token=external_token),
            timeout_seconds=timeout_seconds,
            allowed_origins=allowed_origins or [base_url],
        )
    if external_token_env:
        return HttpxJsonClient(
            auth=StaticBearerTokenAuthAdapter(token_env=external_token_env),
            timeout_seconds=timeout_seconds,
            allowed_origins=allowed_origins or [base_url],
        )
    return HttpxJsonClient(
        auth=WorkloadIdentityBearerTokenAuthAdapter(
            base_url=base_url,
            token_file=workload_token_file,
            exchange_url=workload_exchange_url,
            audiences=workload_audiences,
            scopes=workload_scopes,
            timeout_seconds=timeout_seconds,
            transport=transport,
        ),
        timeout_seconds=timeout_seconds,
        allowed_origins=allowed_origins or [base_url],
    )


class HttpxJsonClient:
    """httpx-backed :class:`AsyncJsonHttpClient` with pluggable bearer auth."""

    def __init__(
        self,
        *,
        auth: HttpAuthPort | None = None,
        timeout_seconds: float = 30.0,
        allowed_origins: list[str] | None = None,
    ) -> None:
        self._auth = auth
        self._timeout = timeout_seconds
        self._allowed_origins = frozenset(
            normalize_http_origin(origin) for origin in (allowed_origins or [])
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._auth is not None:
            headers.update(self._auth.headers())
        return headers

    async def _resolve_headers(self) -> dict[str, str]:
        """Resolve auth headers off the event loop.

        ``HttpAuthPort.headers()`` is sync by contract and the workload-identity
        adapter performs a blocking token exchange on cache miss — run it in a
        worker thread so a refresh never stalls every other coroutine.
        """
        import asyncio  # noqa: PLC0415

        return await asyncio.to_thread(self._headers)

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        import httpx  # noqa: PLC0415

        self._assert_allowed_origin(url)
        merged = await self._resolve_headers()
        if headers:
            merged.update(headers)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=merged)
            return HttpResponse(status_code=resp.status_code, body=_safe_json(resp))

    async def post(
        self,
        url: str,
        json_body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        import httpx  # noqa: PLC0415

        self._assert_allowed_origin(url)
        merged = await self._resolve_headers()
        if headers:
            merged.update(headers)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=merged, json=json_body)
            return HttpResponse(status_code=resp.status_code, body=_safe_json(resp))

    def _assert_allowed_origin(self, url: str) -> None:
        if not self._allowed_origins:
            return
        try:
            origin = normalize_http_origin(url)
        except ValueError as exc:
            raise ValueError("HTTP client target must be an absolute http(s) URL") from exc
        if origin not in self._allowed_origins:
            raise ValueError(f"refusing authenticated request to untrusted origin {origin}")


def normalize_http_origin(url: str) -> str:
    """Return a canonical HTTP origin, rejecting credentials and malformed URLs."""
    try:
        parsed = urlsplit(str(url or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid HTTP URL") from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https and include a host")
    if parsed.username or parsed.password:
        raise ValueError("URL must not embed credentials")
    default_port = 80 if scheme == "http" else 443
    host = parsed.hostname.casefold()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def _safe_json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 — non-JSON error bodies become text
        return getattr(resp, "text", "")
