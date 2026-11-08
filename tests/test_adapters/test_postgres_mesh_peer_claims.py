"""The mesh peer-id claim table's SQL, against a mocked asyncpg pool (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.postgres_mesh_peer_claims import (
    CLAIM_SQL,
    RELEASE_SQL,
    PostgresMeshPeerClaims,
)
from volundr.domain.mesh_peer_claims import PeerClaim, PeerIdClaimedError

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _claims(pool: AsyncMock) -> PostgresMeshPeerClaims:
    return PostgresMeshPeerClaims(pool, clock=lambda: NOW)


async def test_claim_is_one_conditional_upsert_stamped_with_the_adapters_clock() -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = {"peer_id": "peer-a"}
    expires = NOW + timedelta(minutes=15)

    await _claims(pool).claim(
        peer_id="peer-a", holder="session:s1", launch_id="l1", mesh_realm="r", expires_at=expires
    )

    pool.fetchrow.assert_awaited_once_with(
        CLAIM_SQL, "peer-a", "session:s1", "l1", "r", expires, NOW
    )
    # Held by the same holder, or expired: the only two ways an existing row is replaced.
    assert "ON CONFLICT (peer_id) DO UPDATE" in CLAIM_SQL
    assert "mesh_peer_claims.holder = EXCLUDED.holder" in CLAIM_SQL
    assert "mesh_peer_claims.expires_at <= EXCLUDED.updated_at" in CLAIM_SQL


async def test_a_claim_held_by_another_live_holder_raises_naming_the_peer_id() -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = None

    with pytest.raises(PeerIdClaimedError, match="peer-a") as raised:
        await _claims(pool).claim(
            peer_id="peer-a", holder="session:s2", launch_id="l", mesh_realm="", expires_at=NOW
        )

    assert raised.value.peer_id == "peer-a"


async def test_release_deletes_the_holders_launch_and_returns_what_it_deleted() -> None:
    pool = AsyncMock()
    pool.fetch.return_value = [
        {"peer_id": "peer-a", "holder": "session:s1", "launch_id": "l1", "mesh_realm": "r"},
        {"peer_id": "peer-b", "holder": "session:s1", "launch_id": "l1", "mesh_realm": ""},
    ]

    released = await _claims(pool).release(holder="session:s1", launch_id="l1")

    pool.fetch.assert_awaited_once_with(RELEASE_SQL, "session:s1", "l1")
    assert released == [
        PeerClaim(peer_id="peer-a", holder="session:s1", launch_id="l1", mesh_realm="r"),
        PeerClaim(peer_id="peer-b", holder="session:s1", launch_id="l1", mesh_realm=""),
    ]


async def test_releasing_a_launch_the_holder_moved_past_deletes_nothing() -> None:
    pool = AsyncMock()
    pool.fetch.return_value = []

    assert await _claims(pool).release(holder="session:s1", launch_id="old") == []


async def test_the_default_clock_is_utc_now() -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = {"peer_id": "p"}

    await PostgresMeshPeerClaims(pool).claim(
        peer_id="p", holder="h", launch_id="l", mesh_realm="", expires_at=NOW
    )

    stamped = pool.fetchrow.await_args.args[-1]
    assert stamped.tzinfo is not None and abs(stamped - datetime.now(UTC)) < timedelta(seconds=5)
