"""The Postgres store behind the ODIN review relay (pool mocked, no database)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from volundr.adapters.outbound.postgres_odin_review_relay import (
    PostgresOdinReviewRelayStateStore,
)

T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock()
    return pool


@pytest.fixture
def store(pool) -> PostgresOdinReviewRelayStateStore:
    return PostgresOdinReviewRelayStateStore(pool)


async def test_begin_owner_fixes_the_watermark_once_and_reads_it_back(store, pool):
    pool.fetchrow.return_value = {"watermark_at": T0, "digest_recorded_at": None}

    state = await store.begin_owner("owner-a", T0)

    insert_sql = pool.execute.call_args[0][0]
    assert "INSERT INTO odin_review_relay_owners" in insert_sql
    assert "ON CONFLICT (owner_id) DO NOTHING" in insert_sql
    assert pool.execute.call_args[0][1:] == ("owner-a", T0)
    assert pool.fetchrow.call_args[0][1] == "owner-a"
    assert (state.watermark_at, state.digest_recorded) == (T0, False)


async def test_begin_owner_reports_an_announced_digest(store, pool):
    pool.fetchrow.return_value = {"watermark_at": T0, "digest_recorded_at": T0}

    assert (await store.begin_owner("owner-a", T0)).digest_recorded is True


async def test_begin_owner_fails_loudly_if_the_row_is_gone(store, pool):
    pool.fetchrow.return_value = None

    with pytest.raises(RuntimeError, match="vanished"):
        await store.begin_owner("owner-a", T0)


async def test_mark_digest_recorded_only_touches_an_unannounced_owner(store, pool):
    await store.mark_digest_recorded("owner-a")

    sql = pool.execute.call_args[0][0]
    assert "SET digest_recorded_at = now()" in sql and "digest_recorded_at IS NULL" in sql
    assert pool.execute.call_args[0][1] == "owner-a"


async def test_recorded_among_asks_only_about_the_given_ids(store, pool):
    pool.fetch.return_value = [{"review_id": "r1"}]

    assert await store.recorded_among("owner-a", {"r2", "r1"}) == {"r1"}

    assert pool.fetch.call_args[0][1:] == ("owner-a", ["r1", "r2"])


async def test_recorded_among_nothing_does_not_query(store, pool):
    assert await store.recorded_among("owner-a", set()) == set()
    pool.fetch.assert_not_called()


async def test_mark_recorded_is_idempotent(store, pool):
    await store.mark_recorded("owner-a", "r1")

    sql = pool.execute.call_args[0][0]
    assert "INSERT INTO odin_review_relay_items" in sql
    assert "ON CONFLICT (owner_id, review_id) DO NOTHING" in sql
    assert pool.execute.call_args[0][1:] == ("owner-a", "r1")


async def test_open_ids_are_the_unretired_items(store, pool):
    pool.fetch.return_value = [{"review_id": "r1"}, {"review_id": "r3"}]

    assert await store.open_ids("owner-a") == {"r1", "r3"}

    assert "retired_at IS NULL" in pool.fetch.call_args[0][0]


async def test_mark_retired_sets_the_time_once(store, pool):
    await store.mark_retired("owner-a", "r1")

    sql = pool.execute.call_args[0][0]
    assert "SET retired_at = now()" in sql and "retired_at IS NULL" in sql
    assert pool.execute.call_args[0][1:] == ("owner-a", "r1")
