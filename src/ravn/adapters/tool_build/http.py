"""Minimal async JSON HTTP client seam for tool-build backends.

A tiny protocol so the Forge/Ting backends are unit-testable with a fake
client (no live services, no docker) while the real implementation wraps
httpx with the same PAT bearer-token pattern ravn already uses to reach Ting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Any


class AsyncJsonHttpClient(Protocol):
    """Bearer-authenticated JSON GET/POST used by the build backends."""

    async def get(self, url: str) -> HttpResponse: ...

    async def post(self, url: str, json_body: dict[str, Any]) -> HttpResponse: ...


def client_from_pat_env(pat_env: str) -> HttpxJsonClient:
    """Build the real client, bearer-authenticated from an env-var-named PAT.

    Backend constructors take plain YAML kwargs (dynamic-adapter rule), so
    config names the env var and the token resolves here at construction
    time — it is never stored in config.
    """
    import os  # noqa: PLC0415

    token = os.environ.get(pat_env, "") if pat_env else ""
    return HttpxJsonClient(token=token)


class HttpxJsonClient:
    """httpx-backed :class:`AsyncJsonHttpClient` with PAT bearer auth."""

    def __init__(self, *, token: str = "", timeout_seconds: float = 30.0) -> None:
        self._token = token
        self._timeout = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
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
