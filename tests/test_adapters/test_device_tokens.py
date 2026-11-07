"""The push gateway's per-token state in PostgreSQL (``device_tokens``), with a mocked pool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.push_device_state import (
    DELETE_CONNECTION_SQL,
    LOCK_TOKEN_SQL,
    REPLACE_ACTIVITY_TOKEN_SQL,
    TAKE_OVER_SQL,
    UPSERT_SQL,
    DeviceStateRow,
    PostgresDeviceStateStore,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _row(**overrides) -> DeviceStateRow:
    values = {
        "id": uuid4(),
        "owner_id": "user-1",
        "platform": "ios",
        "app_bundle_id": "com.niuu.lexi.chat",
        "kind": "alert",
        "environment": "production",
        "activity_id": None,
        "connection_id": "conn-1",
        "credential_name": "push-device-abc",
        "token_sha256": "f" * 64,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return DeviceStateRow(**values)


def _record(row: DeviceStateRow, **overrides) -> dict:
    record = {**row.__dict__, "last_push_at": None, "token": None}
    record.update(overrides)
    return record


@pytest.fixture
def conn():
    connection = MagicMock()
    connection.fetch = AsyncMock(return_value=[])
    connection.fetchrow = AsyncMock()
    connection.execute = AsyncMock()

    @asynccontextmanager
    async def transaction():
        yield

    connection.transaction = transaction
    return connection


@pytest.fixture
def pool(conn):
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock()

    @asynccontextmanager
    async def acquire():
        yield conn

    pool.acquire = acquire
    return pool


@pytest.fixture
def store(pool) -> PostgresDeviceStateStore:
    return PostgresDeviceStateStore(pool)


class TestRegister:
    async def test_takes_the_token_over_and_upserts_by_credential(self, store, conn):
        row = _row()
        taken = _row(owner_id="user-2", credential_name="push-device-old", connection_id="c2")
        conn.fetch.return_value = [_record(taken)]
        conn.fetchrow.return_value = _record(row)

        stored, removed = await store.register(row)

        # Registrations of one token take turns, inside the registration's transaction.
        conn.execute.assert_awaited_once_with(LOCK_TOKEN_SQL, row.token_sha256, "alert")
        conn.fetch.assert_awaited_once_with(
            TAKE_OVER_SQL, row.token_sha256, "alert", row.credential_name
        )
        assert conn.fetchrow.await_args.args[0] == UPSERT_SQL
        # The row never carries a token: only its sha256.
        columns = UPSERT_SQL.split("(", 1)[1].split(")", 1)[0]
        assert "token" not in [column.strip() for column in columns.split(",")]
        assert stored == row
        assert removed == [taken]

    async def test_a_live_activity_token_replaces_the_earlier_one_of_its_activity(
        self, store, conn
    ):
        row = _row(kind="live_activity", activity_id="act-1", credential_name="n-la")
        earlier = _row(kind="live_activity", activity_id="act-1", credential_name="n-la-old")
        conn.fetch.side_effect = [[], [_record(earlier)]]
        conn.fetchrow.return_value = _record(row)

        _, removed = await store.register(row)

        assert conn.fetch.await_args_list[1].args == (
            REPLACE_ACTIVITY_TOKEN_SQL,
            "conn-1",
            "live_activity",
            "act-1",
            "n-la",
        )
        assert removed == [earlier]

    async def test_a_row_still_holding_its_token_is_taken_over_but_not_returned(self, store, conn):
        row = _row()
        unmoved = _record(_row(), credential_name=None, connection_id=None, token="tok")
        conn.fetch.return_value = [unmoved]
        conn.fetchrow.return_value = _record(row)

        _, removed = await store.register(row)

        assert removed == []


class TestReads:
    async def test_get_and_owner_rows(self, store, pool):
        row = _row()
        pool.fetchrow.return_value = _record(row)
        pool.fetch.return_value = [_record(row)]

        assert await store.get("push-device-abc") == row
        assert await store.get_by_id(row.id) == row
        assert await store.owner_rows("user-1") == [row]
        assert await store.owner_rows_with_token("user-1", "f" * 64) == [row]

    async def test_get_of_a_missing_credential_is_none(self, store, pool):
        assert await store.get("nope") is None
        assert await store.get_by_id(uuid4()) is None

    async def test_holder_ignores_a_row_without_a_credential(self, store, pool):
        pool.fetchrow.return_value = _record(_row(), credential_name=None)
        assert await store.holder("f" * 64, "alert", "push-device-abc") is None
        pool.fetchrow.return_value = _record(_row(credential_name="other"))
        assert (await store.holder("f" * 64, "alert", "push-device-abc")).credential_name == "other"

    async def test_naive_timestamps_read_as_utc(self, store, pool):
        naive = datetime(2026, 11, 3, 12, 0)
        pool.fetchrow.return_value = _record(_row(), created_at=naive, updated_at=naive)

        row = await store.get("push-device-abc")

        assert row.created_at.tzinfo is UTC

    async def test_counts_per_environment_and_kind(self, store, pool):
        pool.fetch.return_value = [
            {"environment": "production", "kind": "alert", "n": 2},
            {"environment": "sandbox", "kind": "widget", "n": 1},
        ]

        assert await store.count_by_environment() == {
            "production": {"alert": 2},
            "sandbox": {"widget": 1},
        }

    async def test_stored_tokens_are_the_rows_still_holding_one(self, store, pool):
        record = _record(_row(), credential_name=None, connection_id=None, token="old")
        pool.fetch.return_value = [record]

        [stored] = await store.stored_tokens()

        assert stored.token == "old" and stored.kind == "alert"


class TestRemovalAndThrottles:
    async def test_delete_connection_returns_the_rows_it_removed(self, store, pool):
        row = _row()
        pool.fetch.return_value = [_record(row)]

        assert await store.delete_connection("conn-1") == [row]
        pool.fetch.assert_awaited_with(DELETE_CONNECTION_SQL, "conn-1")

    async def test_delete_and_sha_update(self, store, pool):
        row = _row()
        pool.fetch.return_value = [_record(row)]

        assert await store.delete(row.id) == [row]
        await store.set_token_sha(row.id, "e" * 64)
        pool.execute.assert_awaited_with(
            "UPDATE device_tokens SET token_sha256 = $2 WHERE id = $1", row.id, "e" * 64
        )

    async def test_a_claim_held_back_marks_the_push_owed(self, store, pool):
        row_id = uuid4()
        pool.fetchval.return_value = None

        assert not await store.claim_push(row_id, min_interval_seconds=120)
        pool.execute.assert_awaited_once()

        pool.fetchval.return_value = row_id
        assert await store.claim_push(row_id, min_interval_seconds=120)

    async def test_release_and_owed_pushes(self, store, pool):
        row = _row(kind="widget")
        pool.fetch.return_value = [_record(row)]

        await store.release_push(row.id)
        assert await store.claim_owed_pushes("widget", min_interval_seconds=0) == [row]
