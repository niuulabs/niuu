"""In-memory adapter for the Observatory fragment push inbox.

Used where the platform runs without a database (mini mode) and in tests.
Fragments are lost on restart, which is acceptable: a live source republishes
on its next heartbeat, and one that does not is exactly the source an operator
should see reported as stale.
"""

from __future__ import annotations

from datetime import datetime

from identity.ports import AuthorizationDeniedError
from niuu.domain.observatory import ObservatoryFragment, StoredFragment
from niuu.ports.observatory_fragments import ObservatoryFragmentRepository


class InMemoryObservatoryFragmentRepository(ObservatoryFragmentRepository):
    """Holds each source's most recent fragment in process memory."""

    def __init__(self) -> None:
        self._fragments: dict[str, StoredFragment] = {}

    async def put(
        self,
        source_id: str,
        fragment: ObservatoryFragment,
        *,
        received_at: datetime,
        owner_id: str = "",
        tenant_id: str = "",
    ) -> StoredFragment:
        existing = self._fragments.get(source_id)
        if existing is not None and (existing.owner_id, existing.tenant_id) != (
            owner_id,
            tenant_id,
        ):
            raise AuthorizationDeniedError("Topology source ownership conflict")
        stored = StoredFragment(
            owner_id=owner_id,
            tenant_id=tenant_id,
            source_id=source_id,
            fragment=fragment,
            received_at=received_at,
        )
        self._fragments[source_id] = stored
        return stored

    async def list_fragments(self) -> list[StoredFragment]:
        return [self._fragments[key] for key in sorted(self._fragments)]

    async def delete(
        self, source_id: str, *, owner_id: str | None = None, tenant_id: str | None = None
    ) -> bool:
        existing = self._fragments.get(source_id)
        if (
            existing is None
            or (owner_id is not None and existing.owner_id != owner_id)
            or (tenant_id is not None and existing.tenant_id != tenant_id)
        ):
            return False
        return self._fragments.pop(source_id, None) is not None
