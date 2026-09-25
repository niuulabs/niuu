"""Exercise the real SQL adapter with a mocked pool; no database container."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from volundr.adapters.outbound.postgres_session_participants import (
    PostgresSessionParticipantRepository,
)
from volundr.domain.session_participants import ParticipantRole


def _row(**overrides) -> dict:
    now = datetime.now(UTC)
    row = {
        "session_id": overrides.get("session_id", uuid4()),
        "user_id": "invitee",
        "tenant_id": "acme",
        "role": "observer",
        "status": "invited",
        "invited_by": "owner",
        "created_at": now,
        "updated_at": now,
        "expires_at": None,
    }
    row.update(overrides)
    return row


def adapter():
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    return PostgresSessionParticipantRepository(pool), pool


async def test_invite_upserts_and_always_resets_status_to_invited():
    repo, pool = adapter()
    sid = uuid4()
    pool.fetchrow.return_value = _row(session_id=sid)
    result = await repo.invite(sid, "invitee", "acme", ParticipantRole.OBSERVER, "owner", None)
    assert result.status.value == "invited"
    sql, *params = pool.fetchrow.call_args.args
    assert "ON CONFLICT (session_id, user_id) DO UPDATE" in sql
    assert params == [sid, "invitee", "acme", "observer", "owner", None]


async def test_accept_only_matches_invited_rows():
    repo, pool = adapter()
    sid = uuid4()
    pool.fetchrow.return_value = _row(session_id=sid, status="active")
    result = await repo.accept(sid, "invitee")
    assert result is not None and result.status.value == "active"
    sql = pool.fetchrow.call_args.args[0]
    assert "status = 'invited'" in sql


async def test_accept_returns_none_when_nothing_matched():
    repo, pool = adapter()
    pool.fetchrow.return_value = None
    assert await repo.accept(uuid4(), "invitee") is None


async def test_revoke_matches_any_status():
    repo, pool = adapter()
    sid = uuid4()
    pool.fetchrow.return_value = _row(session_id=sid, status="revoked")
    result = await repo.revoke(sid, "invitee")
    assert result is not None and result.status.value == "revoked"


async def test_revoke_returns_none_when_no_grant_exists():
    repo, pool = adapter()
    pool.fetchrow.return_value = None
    assert await repo.revoke(uuid4(), "nobody") is None


async def test_get_returns_none_when_absent():
    repo, pool = adapter()
    pool.fetchrow.return_value = None
    assert await repo.get(uuid4(), "nobody") is None


async def test_list_for_session_returns_every_status():
    repo, pool = adapter()
    sid = uuid4()
    pool.fetch.return_value = [_row(session_id=sid, status="revoked"), _row(session_id=sid)]
    results = await repo.list_for_session(sid)
    assert len(results) == 2
    sql = pool.fetch.call_args.args[0]
    assert "status" not in sql.split("WHERE")[1]


async def test_list_active_for_session_filters_status_in_sql():
    repo, pool = adapter()
    sid = uuid4()
    pool.fetch.return_value = [_row(session_id=sid, status="active")]
    results = await repo.list_active_for_session(sid)
    assert len(results) == 1
    sql = pool.fetch.call_args.args[0]
    assert "status = 'active'" in sql
    assert pool.fetch.call_args.args[1] == sid


async def test_list_active_for_user_queries_by_user_id():
    repo, pool = adapter()
    pool.fetch.return_value = [_row(user_id="someone", status="active")]
    results = await repo.list_active_for_user("someone")
    assert len(results) == 1 and results[0].user_id == "someone"
    sql = pool.fetch.call_args.args[0]
    assert "user_id = $1" in sql
    assert pool.fetch.call_args.args[1] == "someone"
