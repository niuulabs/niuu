"""Secret request SQL adapter with a mocked asyncpg boundary (no database)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from volundr.adapters.outbound.postgres_secret_requests import (
    CANCEL_OPEN_FOR_SESSION_SQL,
    CANCEL_SQL,
    DECLINE_SQL,
    DELETE_FOR_SESSION_SQL,
    DELIVER_SQL,
    FULFILL_SQL,
    INSERT_SQL,
    LIST_SQL,
    SELECT_ONE_SQL,
    SELECT_OPEN_SQL,
    PostgresSecretRequestRepository,
    row_to_request,
)
from volundr.domain.secret_requests import (
    SecretKeep,
    SecretRequest,
    SecretRequestError,
    SecretRequestStatus,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
SID = UUID("11111111-2222-3333-4444-555555555555")
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def row(**overrides) -> dict:
    base = {
        "id": uuid4(),
        "session_id": SID,
        "owner_id": "owner-1",
        "name": "GITLAB_TOKEN",
        "purpose": "push",
        "target": "gitlab.com",
        "status": "pending",
        "keep": None,
        "credential_name": None,
        "created_at": NOW,
        "resolved_at": None,
        "delivered_at": None,
    }
    return {**base, **overrides}


def request() -> SecretRequest:
    return row_to_request(row())


@pytest.fixture
def pool():
    mock = MagicMock()
    mock.fetchrow = AsyncMock(return_value=None)
    mock.fetch = AsyncMock(return_value=[])
    mock.execute = AsyncMock(return_value="DELETE 0")
    return mock


@pytest.fixture
def repo(pool) -> PostgresSecretRequestRepository:
    return PostgresSecretRequestRepository(pool)


class TestRows:
    def test_maps_a_row(self):
        mapped = row_to_request(
            row(status="fulfilled", keep="saved", credential_name="gitlab-token", resolved_at=NOW)
        )
        assert mapped.status is SecretRequestStatus.FULFILLED
        assert mapped.keep is SecretKeep.SAVED
        assert (mapped.credential_name, mapped.resolved_at) == ("gitlab-token", NOW)

    def test_a_pending_row_has_no_keep(self):
        assert row_to_request(row()).keep is None

    def test_the_table_has_no_column_for_the_secret(self):
        migration = (MIGRATIONS / "000105_session_secret_requests.up.sql").read_text()
        columns = {
            line.split()[0]
            for line in migration.splitlines()
            if line.startswith("    ") and not line.startswith("     ")
        }
        assert columns >= {"id", "session_id", "owner_id", "name", "status", "credential_name"}
        assert not columns & {"value", "secret", "data", "payload"}


class TestCreate:
    async def test_a_new_request_is_inserted(self, repo, pool):
        pool.fetchrow.return_value = row()
        original = request()

        created, is_new = await repo.create_or_get_open(original)

        assert is_new is True
        sql, *args = pool.fetchrow.await_args.args
        assert sql == INSERT_SQL
        assert "ON CONFLICT (session_id, name) WHERE status IN ('pending', 'fulfilled')" in sql
        assert args == [
            original.id,
            SID,
            "owner-1",
            "GITLAB_TOKEN",
            "push",
            "gitlab.com",
            "pending",
            original.created_at,
        ]
        assert created.name == "GITLAB_TOKEN"

    async def test_an_open_request_for_the_name_is_returned_instead(self, repo, pool):
        existing = row(status="fulfilled")
        pool.fetchrow.side_effect = [None, existing]

        found, is_new = await repo.create_or_get_open(request())

        assert is_new is False
        assert found.id == existing["id"]
        assert pool.fetchrow.await_args.args == (SELECT_OPEN_SQL, SID, "GITLAB_TOKEN")

    async def test_a_request_that_closed_during_the_lookup_is_an_error(self, repo, pool):
        pool.fetchrow.side_effect = [None, None]
        with pytest.raises(SecretRequestError, match="ask again"):
            await repo.create_or_get_open(request())

    async def test_text_postgres_cannot_hold_is_scrubbed(self, repo, pool):
        pool.fetchrow.return_value = row()
        dirty = SecretRequest(**{**request().__dict__, "purpose": "a\x00b", "target": None})

        await repo.create_or_get_open(dirty)

        args = pool.fetchrow.await_args.args
        assert "\x00" not in args[5]
        assert args[6] is None


class TestReads:
    async def test_get(self, repo, pool):
        pool.fetchrow.return_value = row()
        assert (await repo.get(uuid4())).name == "GITLAB_TOKEN"
        assert pool.fetchrow.await_args.args[0] == SELECT_ONE_SQL

    async def test_get_missing(self, repo):
        assert await repo.get(uuid4()) is None

    async def test_list_newest_first_with_and_without_a_status(self, repo, pool):
        pool.fetch.return_value = [row(), row(name="OTHER")]

        everything = await repo.list_for_session(SID)
        pending = await repo.list_for_session(SID, SecretRequestStatus.PENDING)

        assert [r.name for r in everything] == ["GITLAB_TOKEN", "OTHER"]
        assert "ORDER BY created_at DESC" in LIST_SQL
        assert pool.fetch.await_args_list[0].args == (LIST_SQL, SID, None)
        assert pool.fetch.await_args_list[1].args == (LIST_SQL, SID, "pending")
        assert len(pending) == 2


class TestTransitions:
    """Each is one conditional UPDATE; no row back means the request was not in that state."""

    async def test_fulfilled_only_from_pending(self, repo, pool):
        rid = uuid4()
        pool.fetchrow.return_value = row(
            id=rid, status="fulfilled", keep="session", credential_name="session-secret-x"
        )

        done = await repo.mark_fulfilled(
            rid, keep=SecretKeep.SESSION, credential_name="session-secret-x", at=NOW
        )

        assert done.status is SecretRequestStatus.FULFILLED
        assert pool.fetchrow.await_args.args == (
            FULFILL_SQL,
            rid,
            "session",
            "session-secret-x",
            NOW,
        )
        assert "status = 'pending'" in FULFILL_SQL

    async def test_declined_only_from_pending(self, repo, pool):
        rid = uuid4()
        pool.fetchrow.return_value = row(id=rid, status="declined")
        assert (await repo.mark_declined(rid, at=NOW)).status is SecretRequestStatus.DECLINED
        assert pool.fetchrow.await_args.args == (DECLINE_SQL, rid, NOW)
        assert "status = 'pending'" in DECLINE_SQL

    async def test_delivered_only_from_fulfilled(self, repo, pool):
        rid = uuid4()
        pool.fetchrow.return_value = row(id=rid, status="delivered", delivered_at=NOW)
        assert (await repo.mark_delivered(rid, at=NOW)).delivered_at == NOW
        assert pool.fetchrow.await_args.args == (DELIVER_SQL, rid, NOW)
        assert "status = 'fulfilled'" in DELIVER_SQL

    async def test_cancelled_from_pending_or_fulfilled(self, repo, pool):
        rid = uuid4()
        pool.fetchrow.return_value = row(id=rid, status="cancelled")
        assert (await repo.mark_cancelled(rid, at=NOW)).status is SecretRequestStatus.CANCELLED
        assert pool.fetchrow.await_args.args == (CANCEL_SQL, rid, NOW)
        assert "status IN ('pending', 'fulfilled')" in CANCEL_SQL

    async def test_a_lost_race_returns_none(self, repo):
        rid = uuid4()
        assert (
            await repo.mark_fulfilled(rid, keep=SecretKeep.SESSION, credential_name="c", at=NOW)
            is None
        )
        assert await repo.mark_declined(rid, at=NOW) is None
        assert await repo.mark_delivered(rid, at=NOW) is None
        assert await repo.mark_cancelled(rid, at=NOW) is None


class TestSessionEnd:
    async def test_cancels_every_open_request_and_returns_them(self, repo, pool):
        pool.fetch.return_value = [row(status="cancelled"), row(status="cancelled", name="B_KEY")]

        cancelled = await repo.cancel_open_for_session(SID, at=NOW)

        assert [r.name for r in cancelled] == ["GITLAB_TOKEN", "B_KEY"]
        assert pool.fetch.await_args.args == (CANCEL_OPEN_FOR_SESSION_SQL, SID, NOW)
        assert "status IN ('pending', 'fulfilled')" in CANCEL_OPEN_FOR_SESSION_SQL

    async def test_deletes_a_sessions_records_and_counts_them(self, repo, pool):
        pool.execute.return_value = "DELETE 3"
        assert await repo.delete_for_session(SID) == 3
        assert pool.execute.await_args.args == (DELETE_FOR_SESSION_SQL, SID)
