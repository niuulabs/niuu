"""Guild-backed discovery for Observatory topology and event streams."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from niuu.ports.http_auth import HttpAuthPort
from observatory.contracts import ObservatorySnapshot

JsonFetcher = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class DiscoverySnapshot:
    """Materialized observatory snapshot."""

    topology: ObservatorySnapshot
    events: list[dict[str, str]]


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class ObservatoryDiscoveryService:
    """Reads Observatory discovery data from the Guild snapshot endpoint."""

    def __init__(
        self,
        *,
        guild_url: str,
        auth: HttpAuthPort,
        ttl_seconds: float = 10.0,
        timeout_seconds: float = 4.0,
        transport: httpx.AsyncBaseTransport | None = None,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self._guild_url = guild_url.rstrip("/")
        self._auth = auth
        self._ttl = timedelta(seconds=ttl_seconds)
        self._timeout = timeout_seconds
        self._transport = transport
        self._fetch_json = fetch_json
        self._lock = asyncio.Lock()
        self._cached_at: datetime | None = None
        self._cached_snapshot: DiscoverySnapshot | None = None

    @property
    def guild_url(self) -> str:
        """Return the configured Guild base URL."""
        return self._guild_url

    @property
    def base_url(self) -> str:
        """Backwards-compatible alias for callers that still read base_url."""
        return self._guild_url

    async def get_topology_snapshot(self) -> ObservatorySnapshot:
        return deepcopy((await self._get_snapshot()).topology)

    async def get_events(self) -> list[dict[str, str]]:
        return deepcopy((await self._get_snapshot()).events)

    async def _get_snapshot(self) -> DiscoverySnapshot:
        now = _utc_now()
        if (
            self._cached_snapshot is not None
            and self._cached_at is not None
            and now - self._cached_at < self._ttl
        ):
            return self._cached_snapshot

        async with self._lock:
            now = _utc_now()
            if (
                self._cached_snapshot is not None
                and self._cached_at is not None
                and now - self._cached_at < self._ttl
            ):
                return self._cached_snapshot

            snapshot = await self._discover()
            self._cached_snapshot = snapshot
            self._cached_at = now
            return snapshot

    async def _discover(self) -> DiscoverySnapshot:
        if self._fetch_json is not None:
            payload = await self._safe_fetch(
                self._fetch_json,
                "/api/v1/niuu/observatory/snapshot",
            )
        else:
            async with httpx.AsyncClient(
                base_url=self._guild_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:

                async def client_fetch(path: str) -> Any:
                    response = await client.get(path, headers=self._auth.headers())
                    response.raise_for_status()
                    return response.json()

                payload = await self._safe_fetch(client_fetch, "/api/v1/niuu/observatory/snapshot")

        if not isinstance(payload, dict):
            payload = {}
        events = payload.get("events")
        topology: ObservatorySnapshot = {
            key: value for key, value in payload.items() if key != "events"
        }
        if not isinstance(events, list):
            events = []
        if "nodes" not in topology or not isinstance(topology.get("nodes"), list):
            topology.setdefault("nodes", [])
        if "edges" not in topology or not isinstance(topology.get("edges"), list):
            topology.setdefault("edges", [])
        if "layoutHints" in topology and not isinstance(topology.get("layoutHints"), dict):
            topology.pop("layoutHints", None)
        if "timestamp" not in topology:
            topology["timestamp"] = _utc_now().isoformat().replace("+00:00", "Z")
        return DiscoverySnapshot(topology=topology, events=events)

    async def _safe_fetch(self, fetch: JsonFetcher, path: str) -> Any:
        try:
            return await fetch(path)
        except Exception:
            return {
                "timestamp": _utc_now().isoformat().replace("+00:00", "Z"),
                "nodes": [
                    {
                        "id": "service:observatory",
                        "typeId": "service",
                        "label": "Observatory",
                        "parentId": None,
                        "status": "failed",
                        "svcType": "observatory",
                    }
                ],
                "edges": [],
                "events": [
                    {
                        "id": "observatory:guild:unreachable",
                        "level": "warning",
                        "service": "observatory",
                        "message": "Guild discovery endpoint unavailable",
                        "timestamp": _utc_now().isoformat().replace("+00:00", "Z"),
                    }
                ],
            }
