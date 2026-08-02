"""Adapter-backed discovery for Observatory topology and event streams."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from observatory.contracts import ObservatorySnapshot
from observatory.entity_discovery import (
    DiscoveryAdapter,
    DiscoveryResult,
    topology_from_discovery,
)

logger = logging.getLogger(__name__)

#: Resolves the entity type ids currently registered.
RegistryTypeIdsProvider = Callable[[], Awaitable[Collection[str]]]


@dataclass(frozen=True)
class DiscoverySnapshot:
    """Materialized observatory snapshot."""

    topology: ObservatorySnapshot
    events: list[dict[str, str]]
    result: DiscoveryResult


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class ObservatoryDiscoveryService:
    """Materializes Observatory topology from configured discovery adapters."""

    def __init__(
        self,
        *,
        guild_url: str,
        ttl_seconds: float = 10.0,
        discovery_adapter: DiscoveryAdapter | None = None,
        registry_type_ids: RegistryTypeIdsProvider | None = None,
    ) -> None:
        self._guild_url = guild_url.rstrip("/")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._discovery_adapter = discovery_adapter
        self._registry_type_ids = registry_type_ids
        self._lock = asyncio.Lock()
        self._cached: dict[str, tuple[datetime, DiscoverySnapshot]] = {}
        self._refreshing = False
        self._background: set[asyncio.Task[None]] = set()

    @property
    def guild_url(self) -> str:
        """Return the configured Guild base URL."""
        return self._guild_url

    @property
    def base_url(self) -> str:
        """Backwards-compatible alias for callers that still read base_url."""
        return self._guild_url

    async def get_topology_snapshot(
        self,
        headers: Mapping[str, str] | None = None,
    ) -> ObservatorySnapshot:
        return deepcopy((await self._get_snapshot(headers=headers)).topology)

    async def get_events(
        self,
        headers: Mapping[str, str] | None = None,
    ) -> list[dict[str, str]]:
        return deepcopy((await self._get_snapshot(headers=headers)).events)

    async def get_discovery_result(
        self,
        headers: Mapping[str, str] | None = None,
    ) -> DiscoveryResult:
        """Return the cached unfiltered source result for principal-aware projections."""
        return deepcopy((await self._get_snapshot(headers=headers)).result)

    async def _get_snapshot(self, headers: Mapping[str, str] | None = None) -> DiscoverySnapshot:
        now = _utc_now()
        # Discovery is source truth fetched with adapter-owned credentials. Principal-aware
        # projections filter after this cache, so no restricted response is shared here.
        cache_key = "source"
        cached = self._cached.get(cache_key)
        if cached is not None:
            cached_at, cached_snapshot = cached
            if now - cached_at < self._ttl:
                return cached_snapshot

        # Stale-while-revalidate. Rebuilding costs seconds — it lists a whole
        # cluster and calls out to Bifrost, Ravn and Ting — and the caller that
        # happened to arrive on the expiry paid all of it. On ymir, the richest
        # source, that was ~7s against a Guild timeout, so the one Observatory
        # carrying residents, model routing and run state dropped out of the
        # estate every time its cache turned over, taking its meshes and edges
        # with it and making them look intermittent.
        #
        # A stale answer is the right one here: the snapshot describes an
        # estate that changes over minutes, and the alternative on offer is not
        # a fresher answer but no answer at all.
        # Any caller holding a stale entry is served from it. Gating this on
        # "am I the one who started the refresh" sent every *other* caller
        # during those seconds down to the lock below, where they waited out
        # the rebuild anyway — which is the whole cost this exists to avoid.
        if cached is not None:
            if not self._refreshing:
                self._refreshing = True
                task = asyncio.create_task(self._refresh(cache_key, headers=headers))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
            return cached[1]

        async with self._lock:
            now = _utc_now()
            cached = self._cached.get(cache_key)
            if cached is not None:
                cached_at, cached_snapshot = cached
                if now - cached_at < self._ttl:
                    return cached_snapshot

            snapshot = await self._discover(headers=headers)
            self._cached[cache_key] = (now, snapshot)
            return snapshot

    async def _refresh(self, cache_key: str, headers: Mapping[str, str] | None) -> None:
        """Rebuild the snapshot out of band, leaving the stale one readable."""
        try:
            async with self._lock:
                snapshot = await self._discover(headers=headers)
                self._cached[cache_key] = (_utc_now(), snapshot)
        except Exception as exc:  # a failed refresh must not poison the cache
            logger.warning(
                "Observatory snapshot refresh failed: %s", str(exc) or type(exc).__name__
            )
        finally:
            self._refreshing = False

    async def _discover(self, headers: Mapping[str, str] | None = None) -> DiscoverySnapshot:
        if self._discovery_adapter is None:
            result = DiscoveryResult(
                events=[
                    {
                        "id": "observatory:discovery:not-configured",
                        "type": "warning",
                        "service": "observatory",
                        "subject": "discovery",
                        "body": "No Observatory discovery adapters are configured",
                        "message": "No Observatory discovery adapters are configured",
                        "timestamp": _utc_now().isoformat().replace("+00:00", "Z"),
                    }
                ]
            )
        else:
            result = await self._discovery_adapter.discover()
        topology = topology_from_discovery(result, known_type_ids=await self._known_type_ids())
        events = topology.pop("events", [])
        return DiscoverySnapshot(topology=topology, events=events, result=result)

    async def _known_type_ids(self) -> Collection[str] | None:
        """Entity types from the live registry, which operators can edit.

        Falling back to ``None`` (the registry seed) keeps discovery working if
        the registry is unreachable — a degraded type map is better than an
        empty graph.
        """
        if self._registry_type_ids is None:
            return None
        try:
            return await self._registry_type_ids()
        except Exception:
            logger.warning("Registry type ids unavailable; using seed types", exc_info=True)
            return None
