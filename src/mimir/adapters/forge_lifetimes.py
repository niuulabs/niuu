"""ForgeLayerLifetimes — contract C7 over HTTP, as the gateway's own service.

``POST {forge}/api/v1/forge/memory/ended`` with ``{"sessions": [...], "runs": [...]}``,
authenticated with the gateway's service credential (``memory.service_auth``: in
mini mode none; on a cluster a workload identity token). The answer must name
every session and run asked about, each ``active``, ``ended`` with ``ended_at``,
or ``gone``; anything else is unusable, and nothing is deleted on it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from mimir.ports.layer_lifetimes import (
    LayerLifetimes,
    LifeState,
    Lifetime,
    Lifetimes,
    LifetimesUnavailableError,
)
from niuu.ports.http_auth import HttpAuthPort

_PATH = "/api/v1/forge/memory/ended"


class ForgeLayerLifetimes(LayerLifetimes):
    """Args:
    base_url: Forge's base URL.
    timeout_seconds: per-request timeout.
    service_auth: the gateway's service credential.
    """

    def __init__(
        self, base_url: str, *, timeout_seconds: float, service_auth: HttpAuthPort
    ) -> None:
        if not base_url.strip():
            raise ValueError("memory.forge_base_url is empty: set it to Forge's base URL")
        if timeout_seconds <= 0:
            raise ValueError("memory.forge_timeout_seconds must be positive")
        self._base = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._auth = service_auth
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def ended(self, sessions: list[str], runs: list[str]) -> Lifetimes:
        headers = await asyncio.to_thread(self._auth.headers)
        try:
            response = await self._http().post(
                self._base + _PATH,
                json={"sessions": sessions, "runs": runs},
                headers={**headers, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise LifetimesUnavailableError(
                f"Forge could not be asked which sessions and runs ended at {self._base}: {exc}"
            ) from exc
        if response.status_code in (401, 403):
            self._auth.invalidate()
            raise LifetimesUnavailableError(
                f"Forge refused the gateway's service credential ({response.status_code}) on "
                f"{_PATH}; check memory.service_auth"
            )
        if response.status_code != 200:
            raise LifetimesUnavailableError(f"Forge answered {response.status_code} on {_PATH}")
        try:
            body = response.json()
        except ValueError as exc:
            raise LifetimesUnavailableError(f"Forge's answer on {_PATH} is not JSON") from exc
        if not isinstance(body, dict):
            raise LifetimesUnavailableError(f"Forge's answer on {_PATH} is not an object")
        return Lifetimes(
            sessions=_states(body.get("sessions"), sessions, "sessions"),
            runs=_states(body.get("runs"), runs, "runs"),
        )


def _states(raw: Any, asked: list[str], what: str) -> dict[str, Lifetime]:
    if not isinstance(raw, dict):
        raise LifetimesUnavailableError(f"Forge's answer has no {what} object")
    found: dict[str, Lifetime] = {}
    for key in asked:
        item = raw.get(key)
        if not isinstance(item, dict):
            raise LifetimesUnavailableError(f"Forge's answer does not name {what} {key}")
        try:
            state = LifeState(item.get("state"))
        except ValueError:
            raise LifetimesUnavailableError(
                f"Forge answered state {item.get('state')!r} for {what} {key}"
            ) from None
        ended_at = item.get("ended_at")
        moment = None
        if state is LifeState.ENDED:
            try:
                moment = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00"))
            except ValueError:
                raise LifetimesUnavailableError(
                    f"Forge answered ended without a valid ended_at for {what} {key}"
                ) from None
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
        found[key] = Lifetime(state=state, ended_at=moment)
    return found
