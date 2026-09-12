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

from identity.models import Principal, Resource
from identity.ports import AuthorizationDeniedError, AuthorizationPort
from niuu.domain.observatory import (
    ObservatoryFragment,
    StoredFragment,
    TopologySourceHealth,
)
from niuu.ports.observatory_fragments import ObservatoryFragmentRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _loggable(value: str) -> str:
    """Flatten a caller-supplied value before it reaches the log.

    `source_id` is a URL path segment, so without this a publisher could put
    newlines in it and forge whole log entries.
    """
    return value.replace("\r", "\\r").replace("\n", "\\n")[:200]


class ObservatoryFragmentInboxService:
    """Accepts pushed fragments and reports how fresh each source is."""

    def __init__(
        self,
        repository: ObservatoryFragmentRepository,
        *,
        ttl_seconds: float,
        authorization: AuthorizationPort,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._authorization = authorization
        self._repository = repository
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    async def accept(
        self, source_id: str, fragment: ObservatoryFragment, *, principal: Principal
    ) -> StoredFragment:
        """Record *source_id*'s current view.

        The arrival time is stamped here rather than taken from the payload:
        a source with a skewed clock, or one replaying an old fragment, must
        not be able to present itself as fresher than it is.
        """
        existing = await self._repository.get(source_id)
        owner = existing.owner_id if existing else principal.user_id
        tenant = existing.tenant_id if existing else principal.tenant_id
        resource = Resource("topology_source", source_id, {"owner_id": owner, "tenant_id": tenant})
        if not await self._authorization.is_allowed(
            principal, "update" if existing else "create", resource
        ):
            raise AuthorizationDeniedError("Topology source operation denied")
        stored = await self._repository.put(
            source_id, fragment, received_at=self._clock(), owner_id=owner, tenant_id=tenant
        )
        logger.debug(
            "Accepted topology fragment from %s (%d nodes, %d edges)",
            _loggable(source_id),
            len(fragment.nodes),
            len(fragment.edges),
        )
        return stored

    async def forget(self, source_id: str, *, principal: Principal) -> bool:
        """Drop a source that is being decommissioned."""
        existing = await self._repository.get(source_id)
        if existing is None:
            return False
        resource = Resource(
            "topology_source",
            source_id,
            {"owner_id": existing.owner_id, "tenant_id": existing.tenant_id},
        )
        if not await self._authorization.is_allowed(principal, "delete", resource):
            raise AuthorizationDeniedError("Topology source operation denied")
        return await self._repository.delete(
            source_id, owner_id=existing.owner_id, tenant_id=existing.tenant_id
        )

    async def current(
        self, *, principal: Principal
    ) -> list[tuple[StoredFragment, TopologySourceHealth]]:
        """Every pushed fragment with its freshness verdict."""
        now = self._clock()
        stored = await self._repository.list_fragments()
        allowed = await self._authorization.filter_allowed(
            principal,
            "read",
            [
                Resource(
                    "topology_source",
                    f.source_id,
                    {"owner_id": f.owner_id, "tenant_id": f.tenant_id},
                )
                for f in stored
            ],
        )
        ids = {r.id for r in allowed}
        return [(f, self._health(f, now)) for f in stored if f.source_id in ids]

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
