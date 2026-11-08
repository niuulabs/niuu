"""PostgreSQL adapter for mesh peer-id claims. Raw asyncpg SQL.

``claim`` is one conditional upsert (see ``migrations/000115_mesh_peer_claims.up.sql``): two
holders racing for a peer id cannot both win, because the loser's ``DO UPDATE ... WHERE``
matches no row and returns nothing. The comparison with ``expires_at`` uses the clock this
adapter is given, never the database's, so the time that stamps a claim is the time that
expires it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from volundr.domain.mesh_peer_claims import MeshPeerClaims, PeerClaim, PeerIdClaimedError

CLAIM_SQL = """INSERT INTO mesh_peer_claims
    (peer_id, holder, launch_id, mesh_realm, expires_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (peer_id) DO UPDATE
SET holder = EXCLUDED.holder,
    launch_id = EXCLUDED.launch_id,
    mesh_realm = EXCLUDED.mesh_realm,
    expires_at = EXCLUDED.expires_at,
    updated_at = EXCLUDED.updated_at
WHERE mesh_peer_claims.holder = EXCLUDED.holder
   OR mesh_peer_claims.expires_at <= EXCLUDED.updated_at
RETURNING peer_id"""

RELEASE_SQL = """DELETE FROM mesh_peer_claims
WHERE holder = $1 AND launch_id = $2
RETURNING peer_id, holder, launch_id, mesh_realm"""


class PostgresMeshPeerClaims(MeshPeerClaims):
    def __init__(self, pool: Any, *, clock: Callable[[], datetime] | None = None) -> None:
        self._pool = pool
        self._clock = clock or (lambda: datetime.now(UTC))

    async def claim(
        self,
        *,
        peer_id: str,
        holder: str,
        launch_id: str,
        mesh_realm: str,
        expires_at: datetime,
    ) -> None:
        row = await self._pool.fetchrow(
            CLAIM_SQL, peer_id, holder, launch_id, mesh_realm, expires_at, self._clock()
        )
        if row is None:
            raise PeerIdClaimedError(peer_id)

    async def release(self, *, holder: str, launch_id: str) -> list[PeerClaim]:
        rows = await self._pool.fetch(RELEASE_SQL, holder, launch_id)
        return [
            PeerClaim(
                peer_id=row["peer_id"],
                holder=row["holder"],
                launch_id=row["launch_id"],
                mesh_realm=row["mesh_realm"],
            )
            for row in rows
        ]
