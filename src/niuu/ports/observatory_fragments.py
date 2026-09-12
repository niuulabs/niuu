"""Port for the Observatory topology fragment push inbox.

Sources that cannot be reached from the aggregator — a resident on a
bare-metal Spark, a Docker container behind NAT — publish their own partial
view of the topology here on a heartbeat. Storage is keyed on the source, so a
heartbeat is idempotent: it replaces that source's previous view rather than
appending to it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from niuu.domain.observatory import ObservatoryFragment, StoredFragment


class ObservatoryFragmentRepository(ABC):
    """Stores the most recent fragment published by each source."""

    @abstractmethod
    async def put(
        self,
        source_id: str,
        fragment: ObservatoryFragment,
        *,
        received_at: datetime,
        owner_id: str = "",
        tenant_id: str = "",
    ) -> StoredFragment:
        """Replace *source_id*'s fragment and return what was stored."""

    @abstractmethod
    async def list_fragments(self) -> list[StoredFragment]:
        """Return every source's most recent fragment.

        Expired fragments are returned too. Whether a source is stale is a
        judgement about time that belongs to the caller, and a source that has
        stopped reporting should be visible as stale rather than vanish.
        """

    @abstractmethod
    async def delete(
        self, source_id: str, *, owner_id: str | None = None, tenant_id: str | None = None
    ) -> bool:
        """Forget *source_id*. Returns False when it was not present."""

    async def get(self, source_id: str) -> StoredFragment | None:
        """Return one source; adapters may implement an indexed lookup."""
        return next((f for f in await self.list_fragments() if f.source_id == source_id), None)
