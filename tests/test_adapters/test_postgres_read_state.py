"""Exercise the real SQL adapter with a mocked infrastructure boundary; no database container."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.pg_session_event_log import _IMPORT_INSERT_SQL, _INSERT_SQL
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.domain.session_read_state import SessionReadStateChange, SessionReadStateConflictError


def adapter(marker=None, latest=20):
    pool, conn = MagicMock(), MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.fetchrow = AsyncMock(
        side_effect=[
            {"latest_final_seq": latest, "latest_final_turn_id": "final", "latest_final_at": None},
            marker,
        ]
    )
    conn.execute = AsyncMock()
    pool.fetch = AsyncMock()
    return PostgresSessionRepository(pool), pool, conn


def change(state="read", revision=0, through=10):
    return SessionReadStateChange(state=state, expected_revision=revision, through_seq=through)


async def test_new_reply_is_not_cleared_by_read_of_old_reply():
    repo, _, conn = adapter(latest=20)
    sid = uuid4()
    result = await repo.change_read_state(sid, "reader-a", change(through=10))
    assert result.is_unread and result.read_through_seq == 10
    assert result.revision == 1 and result.latest_output_seq == 20
    assert "FOR UPDATE" in conn.fetchrow.call_args_list[0].args[0]
    assert conn.execute.call_args.args[1:] == (sid, "reader-a", 10, False, 1)


async def test_manual_unread_survives_stale_automatic_read():
    repo, _, conn = adapter({"revision": 2, "read_through_seq": 20, "manually_unread": True})
    with pytest.raises(SessionReadStateConflictError):
        await repo.change_read_state(uuid4(), "reader-a", change(revision=1, through=20))
    conn.execute.assert_not_called()


async def test_read_cursor_never_moves_backwards():
    repo, _, _ = adapter({"revision": 3, "read_through_seq": 20, "manually_unread": True})
    result = await repo.change_read_state(uuid4(), "reader-a", change(revision=3, through=10))
    assert result.read_through_seq == 20 and not result.is_unread


async def test_mark_unread_changes_no_execution_or_activity_fields():
    repo, _, conn = adapter({"revision": 3, "read_through_seq": 20, "manually_unread": False})
    result = await repo.change_read_state(uuid4(), "reader-a", change("unread", 3, 20))
    assert result.manually_unread and result.is_unread and result.revision == 4
    sql = conn.execute.call_args.args[0]
    assert "UPDATE sessions" not in sql and "last_active" not in sql


async def test_future_cursor_is_rejected_without_write():
    repo, _, conn = adapter()
    with pytest.raises(ValueError, match="future"):
        await repo.change_read_state(uuid4(), "reader-a", change(through=21))
    conn.execute.assert_not_called()


async def test_deleted_session_is_not_recreated():
    repo, _, conn = adapter()
    conn.fetchrow.side_effect = [None]
    with pytest.raises(ValueError, match="no longer exists"):
        await repo.change_read_state(uuid4(), "reader-a", change())
    conn.execute.assert_not_called()


async def test_listing_is_one_bulk_query_scoped_to_reader():
    repo, pool, _ = adapter()
    ids = [uuid4(), uuid4()]
    pool.fetch.return_value = [
        {
            "id": id,
            "latest_output_seq": 10,
            "read_through_seq": 0,
            "manually_unread": False,
            "revision": 0,
        }
        for id in ids
    ]
    result = await repo.get_read_states(ids, "reader-b")
    assert all(result[id].is_unread for id in ids)
    assert pool.fetch.call_count == 1 and pool.fetch.call_args.args[1:] == (ids, "reader-b")
    assert "r.user_id = $2" in pool.fetch.call_args.args[0]
    pool.fetch.reset_mock()
    assert await repo.get_read_states([], "reader-b") == {}
    pool.fetch.assert_not_called()


def test_projection_uses_only_new_inserted_final_events_not_imports_or_retries():
    assert "ON CONFLICT (session_id, seq) DO NOTHING" in _INSERT_SQL
    assert "FROM inserted i" in _INSERT_SQL and "i.seq > s.latest_final_seq" in _INSERT_SQL
    assert "final_output" in _INSERT_SQL and "'public'" in _INSERT_SQL
    assert "jsonb_typeof" in _INSERT_SQL
    assert "latest_final_seq" not in _IMPORT_INSERT_SQL
