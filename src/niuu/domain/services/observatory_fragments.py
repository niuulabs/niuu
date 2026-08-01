"""Accepting and ageing pushed topology fragments.

The inbox holds one fragment per source. Aggregation reads them back alongside
a health verdict, so a source that has stopped publishing is reported as
`stale` with a last-seen time instead of disappearing from the graph — a dead
Spark should read "last seen 4m ago", not look like it never existed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from niuu.domain.observatory import (
    ObservatoryFragment,
    StoredFragment,
    TopologySourceHealth,
)
from niuu.ports.observatory_fragments import ObservatoryFragmentRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ObservatoryFragmentInboxService:
    """Accepts pushed fragments and reports how fresh each source is."""

    def __init__(
        self,
        repository: ObservatoryFragmentRepository,
        *,
        ttl_seconds: float,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    async def accept(self, source_id: str, fragment: ObservatoryFragment) -> StoredFragment:
        """Record *source_id*'s current view.

        The arrival time is stamped here rather than taken from the payload:
        a source with a skewed clock, or one replaying an old fragment, must
        not be able to present itself as fresher than it is.
        """
        stored = await self._repository.put(source_id, fragment, received_at=self._clock())
        logger.debug(
            "Accepted topology fragment from %s (%d nodes, %d edges)",
            source_id,
            len(fragment.nodes),
            len(fragment.edges),
        )
        return stored

    async def forget(self, source_id: str) -> bool:
        """Drop a source that is being decommissioned."""
        return await self._repository.delete(source_id)

    async def current(self) -> list[tuple[StoredFragment, TopologySourceHealth]]:
        """Every pushed fragment with its freshness verdict."""
        now = self._clock()
        return [
            (stored, self._health(stored, now))
            for stored in await self._repository.list_fragments()
        ]

    def _health(self, stored: StoredFragment, now: datetime) -> TopologySourceHealth:
        meta = stored.fragment.meta
        received = stored.received_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        stale = now - received > self._ttl
        return TopologySourceHealth(
            source_id=stored.source_id,
            source_kind=meta.source_kind if meta else "",
            source_name=meta.source_name if meta else "",
            transport="push",
            status="stale" if stale else "healthy",
            cluster_id=meta.cluster_id if meta else "",
            realm_id=meta.realm_id if meta else "",
            revision=meta.revision if meta else "",
            node_count=len(stored.fragment.nodes),
            last_seen=received.isoformat().replace("+00:00", "Z"),
            message=(
                f"No fragment published for {int((now - received).total_seconds())}s"
                if stale
                else ""
            ),
        )
