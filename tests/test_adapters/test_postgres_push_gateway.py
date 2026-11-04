"""The push gateway's PostgreSQL store, against a mocked pool (SQL shape and mapping)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.support.push_fakes import MINI, THOR, guild_notification
from volundr.adapters.outbound.postgres_push_gateway import (
    STATE_ID,
    PostgresPushDeliveryRepository,
)
from volundr.domain.push_ports import PushDelivery, PushDeliveryStatus

NOW = datetime(2026, 10, 2, 6, 40, 14, tzinfo=UTC)


class _Context:
    def __init__(self, value=None) -> None:
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc) -> bool:
        return False


@pytest.fixture
def conn():
    connection = MagicMock()
    connection.fetchval = AsyncMock()
    connection.execute = AsyncMock()
    connection.transaction = MagicMock(return_value=_Context())
    return connection


@pytest.fixture
def pool(conn):
    mock = MagicMock()
    mock.execute = AsyncMock()
    mock.fetch = AsyncMock()
    mock.fetchval = AsyncMock()
    mock.acquire = MagicMock(return_value=_Context(conn))
    return mock


@pytest.fixture
def repository(pool) -> PostgresPushDeliveryRepository:
    return PostgresPushDeliveryRepository(pool, max_detail_chars=40)


def delivery(notification_id: str = "n-1", attempts: int = 1) -> PushDelivery:
    return PushDelivery(
        notification=guild_notification(notification_id), attempts=attempts, created_at=NOW
    )


class TestPositions:
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            (None, {}),
            (json.dumps({THOR: 323, MINI: 7}), {THOR: 323, MINI: 7}),
            ({THOR: 323, "bad": "x", "null": None}, {THOR: 323}),
            ("[1, 2]", {}),
        ],
    )
    async def test_load(self, repository, pool, stored, expected):
        pool.fetchval.return_value = stored
        assert await repository.load_positions() == expected
        assert pool.fetchval.call_args[0][1] == STATE_ID

    async def test_save_merges_per_node(self, repository, pool):
        await repository.save_positions({THOR: 5})
        sql, state_id, positions = pool.execute.call_args[0]
        assert "forge_push_gateway_state.positions || EXCLUDED.positions" in sql
        assert state_id == STATE_ID and json.loads(positions) == {THOR: 5}

    async def test_save_nothing_writes_nothing(self, repository, pool):
        await repository.save_positions({})
        pool.execute.assert_not_called()


class TestRecord:
    async def test_inserts_new_rows_and_advances_positions_in_one_transaction(
        self, repository, conn
    ):
        new, seen = guild_notification("n-1", seq=4), guild_notification("n-0", seq=3)
        conn.fetchval.side_effect = ["n-1", None]

        fresh = await repository.record([new, seen], {THOR: 4})

        assert fresh == [new]
        conn.transaction.assert_called_once()
        sql, instance_id, notification_id, owner_id, notified_at, payload = (
            conn.fetchval.call_args_list[0][0]
        )
        assert "ON CONFLICT (instance_id, notification_id) DO NOTHING" in sql
        assert (instance_id, notification_id, owner_id) == (THOR, "n-1", "dev-user")
        assert notified_at == new.created_at
        assert json.loads(payload)["title"] == "Build finished"
        saved = conn.execute.call_args[0]
        assert saved[1] == STATE_ID and json.loads(saved[2]) == {THOR: 4}

    async def test_without_positions_only_rows_are_written(self, repository, conn):
        conn.fetchval.return_value = "n-1"
        await repository.record([guild_notification("n-1")], {})
        conn.execute.assert_not_called()


class TestClaim:
    async def test_claims_due_rows_with_a_lease_in_order(self, repository, pool):
        first, second = guild_notification("n-1", seq=1), guild_notification("n-2", seq=2)
        pool.fetch.return_value = [
            {"payload": json.dumps(second.stored()), "attempts": 1, "created_at": NOW},
            {"payload": first.stored(), "attempts": 2, "created_at": NOW},
        ]

        claimed = await repository.claim_due(limit=20, lease_seconds=60)

        sql, limit, lease = pool.fetch.call_args[0]
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "status = 'pending' AND next_attempt_at <= now()" in sql
        assert "attempts = d.attempts + 1" in sql
        assert (limit, lease) == (20, 60.0)
        assert [(item.notification.id, item.attempts) for item in claimed] == [
            ("n-1", 2),
            ("n-2", 1),
        ]
        assert claimed[0].notification == first

    @pytest.mark.parametrize(("limit", "lease"), [(0, 60), (5, 0)])
    async def test_rejects_a_bad_claim(self, repository, limit, lease):
        with pytest.raises(ValueError):
            await repository.claim_due(limit=limit, lease_seconds=lease)


class TestSettle:
    async def test_settle_is_fenced_by_attempts(self, repository, pool):
        pool.execute.return_value = "UPDATE 1"

        assert await repository.settle(delivery(attempts=2), PushDeliveryStatus.SENT, "ok") is True

        sql, *args = pool.execute.call_args[0]
        assert "attempts = $3 AND status = 'pending'" in sql
        assert "settled_at = now()" in sql
        assert args == [THOR, "n-1", 2, "sent", "ok"]

    async def test_a_lost_claim_settles_nothing(self, repository, pool):
        pool.execute.return_value = "UPDATE 0"
        assert await repository.settle(delivery(), PushDeliveryStatus.FAILED, "x") is False

    async def test_detail_is_bounded_and_scrubbed(self, repository, pool):
        pool.execute.return_value = "UPDATE 1"
        await repository.settle(delivery(), PushDeliveryStatus.FAILED, "a\x00" + "b" * 100)
        detail = pool.execute.call_args[0][5]
        assert len(detail) == 40 and "\x00" not in detail

    async def test_pending_is_not_a_settled_state(self, repository):
        with pytest.raises(ValueError, match="settled as"):
            await repository.settle(delivery(), PushDeliveryStatus.PENDING, "x")

    async def test_retry_moves_the_next_attempt(self, repository, pool):
        pool.execute.side_effect = ["UPDATE 1", "UPDATE 0"]

        assert await repository.retry(delivery(), detail="APNs: HTTP 503", retry_at=NOW) is True
        assert await repository.retry(delivery(), detail="APNs: HTTP 503", retry_at=NOW) is False

        sql, *args = pool.execute.call_args[0]
        assert "SET next_attempt_at = $4, detail = $5" in sql
        assert args == [THOR, "n-1", 1, NOW, "APNs: HTTP 503"]


class TestCountsAndPruning:
    @pytest.mark.parametrize(("value", "expected"), [(3, 3), (None, 0)])
    async def test_count_sent_since(self, repository, pool, value, expected):
        pool.fetchval.return_value = value
        assert await repository.count_sent_since("dev-user", NOW) == expected
        sql, owner, since = pool.fetchval.call_args[0]
        assert "status = 'sent' AND settled_at >= $2" in sql
        assert (owner, since) == ("dev-user", NOW)

    async def test_prune_reports_how_many(self, repository, pool):
        pool.execute.return_value = "DELETE 12"
        assert await repository.prune_settled(NOW) == 12
        sql, before = pool.execute.call_args[0]
        assert "status <> 'pending' AND settled_at < $1" in sql
        assert before == NOW


def test_detail_limit_is_checked():
    with pytest.raises(ValueError, match="max_detail_chars"):
        PostgresPushDeliveryRepository(MagicMock(), max_detail_chars=0)
