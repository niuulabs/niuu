"""PostgreSQL adapter for the Observatory fragment push inbox."""

from __future__ import annotations

import json
from datetime import datetime

import asyncpg

from identity.ports import AuthorizationDeniedError
from niuu.domain.observatory import ObservatoryFragment, StoredFragment
from niuu.ports.observatory_fragments import ObservatoryFragmentRepository


class PostgresObservatoryFragmentRepository(ObservatoryFragmentRepository):
    """Stores each source's most recent fragment in PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def put(
        self,
        source_id: str,
        fragment: ObservatoryFragment,
        *,
        received_at: datetime,
        owner_id: str = "",
        tenant_id: str = "",
    ) -> StoredFragment:
        """Replace *source_id*'s fragment.

        An upsert rather than an insert: a heartbeat says "this is my current
        state", so re-publishing must not accumulate rows.
        """
        meta = fragment.meta
        row = await self._pool.fetchrow(
            """
            INSERT INTO observatory_fragments (
                source_id, source_kind, source_name, realm_id, cluster_id,
                host_id, revision, payload, received_at, owner_id, tenant_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (source_id) DO UPDATE SET
                source_kind = EXCLUDED.source_kind,
                source_name = EXCLUDED.source_name,
                realm_id = EXCLUDED.realm_id,
                cluster_id = EXCLUDED.cluster_id,
                host_id = EXCLUDED.host_id,
                revision = EXCLUDED.revision,
                payload = EXCLUDED.payload,
                received_at = EXCLUDED.received_at
            WHERE observatory_fragments.owner_id = EXCLUDED.owner_id
              AND observatory_fragments.tenant_id = EXCLUDED.tenant_id
            RETURNING source_id, payload, received_at, owner_id, tenant_id
            """,
            source_id,
            meta.source_kind if meta else "",
            meta.source_name if meta else "",
            meta.realm_id if meta else "",
            meta.cluster_id if meta else "",
            meta.host_id if meta else "",
            meta.revision if meta else "",
            json.dumps(fragment.model_dump(by_alias=True, mode="json")),
            received_at,
            owner_id,
            tenant_id,
        )
        if row is None:
            raise AuthorizationDeniedError("Topology source ownership conflict")
        return self._row_to_fragment(row)

    async def get(self, source_id: str) -> StoredFragment | None:
        row = await self._pool.fetchrow(
            """SELECT source_id, payload, received_at, owner_id, tenant_id
               FROM observatory_fragments WHERE source_id = $1""",
            source_id,
        )
        return self._row_to_fragment(row) if row is not None else None

    async def list_fragments(self) -> list[StoredFragment]:
        rows = await self._pool.fetch(
            """
            SELECT source_id, payload, received_at, owner_id, tenant_id
            FROM observatory_fragments
            ORDER BY source_id
            """
        )
        return [self._row_to_fragment(row) for row in rows]

    async def delete(
        self, source_id: str, *, owner_id: str | None = None, tenant_id: str | None = None
    ) -> bool:
        result = await self._pool.execute(
            """DELETE FROM observatory_fragments WHERE source_id = $1
               AND ($2::text IS NULL OR owner_id = $2)
               AND ($3::text IS NULL OR tenant_id = $3)""",
            source_id,
            owner_id,
            tenant_id,
        )
        return not str(result).endswith(" 0")

    @staticmethod
    def _row_to_fragment(row: asyncpg.Record) -> StoredFragment:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return StoredFragment(
            owner_id=row["owner_id"],
            tenant_id=row["tenant_id"],
            source_id=row["source_id"],
            fragment=ObservatoryFragment.model_validate(payload),
            received_at=row["received_at"],
        )
