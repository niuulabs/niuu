"""HTTP client for the platform APIs, shared by the tracker bridge and the platform tools.

``platform_client`` builds an ``httpx.AsyncClient`` against the platform base URL,
authenticated with an explicit access token or, when none is configured, with a
bearer exchanged for the session's projected workload identity token.
``PlatformAuthMixin`` gives a tool the same auth kwargs and a ``_client()`` factory.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from niuu.domain.tool import ToolResult

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_TIMEOUT = 30.0
DEFAULT_WORKLOAD_TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
DEFAULT_WORKLOAD_AUDIENCES = ["volundr-api", "forge", "ting", "mimir", "guild"]


async def platform_client(
    base_url: str,
    timeout: float,
    access_token: str = "",
    *,
    workload_token_file: str = DEFAULT_WORKLOAD_TOKEN_FILE,
    exchange_url: str = "",
    audiences: list[str] | None = None,
) -> httpx.AsyncClient:
    headers: dict[str, str] = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    else:
        token = await exchange_workload_token(
            base_url,
            timeout,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, headers=headers)


async def exchange_workload_token(
    base_url: str,
    timeout: float,
    *,
    workload_token_file: str,
    exchange_url: str,
    audiences: list[str] | None,
) -> str:
    token_path = Path(workload_token_file).expanduser()
    if not token_path.exists():
        return ""
    proof = token_path.read_text(encoding="utf-8").strip()
    if not proof:
        return ""
    url = exchange_url.rstrip("/") or f"{base_url.rstrip('/')}/api/v1/tokens/workload/exchange"
    body = {"token": proof, "audiences": audiences or DEFAULT_WORKLOAD_AUDIENCES}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=body)
        response.raise_for_status()
        payload = response.json()
    token = str(payload.get("token") or "")
    if not token:
        raise RuntimeError("workload token exchange response did not include token")
    return token


class PlatformAuthMixin:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        access_token: str = "",
        workload_token_file: str = DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        # Every platform tool takes the same auth kwargs verbatim; the shared
        # constructor stops each new tool from re-typing the boilerplate.
        self._set_platform_auth(
            base_url=base_url,
            timeout=timeout,
            access_token=access_token,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )

    def _set_platform_auth(
        self,
        *,
        base_url: str,
        timeout: float,
        access_token: str,
        workload_token_file: str = DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._access_token = access_token
        self._workload_token_file = workload_token_file
        self._exchange_url = exchange_url
        self._audiences = audiences or DEFAULT_WORKLOAD_AUDIENCES

    async def _client(self) -> httpx.AsyncClient:
        return await platform_client(
            self._base_url,
            self._timeout,
            self._access_token,
            workload_token_file=self._workload_token_file,
            exchange_url=self._exchange_url,
            audiences=self._audiences,
        )


def tool_ok(data: object) -> ToolResult:
    return ToolResult(tool_call_id="", content=json.dumps(data, default=str))


def tool_err(message: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=message, is_error=True)


def http_failure(exc: Exception) -> str:
    """Describe a failed call, including the server's own explanation.

    The bare ``raise_for_status`` text names only the status and URL; the
    server's ``detail`` is what says whether the address, the credentials or
    the host was wrong, and an agent that cannot see it guesses.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return str(exc)
    try:
        payload = exc.response.json()
    except ValueError:
        return str(exc)
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if not detail:
        return str(exc)
    return f"{exc} — {detail}"
