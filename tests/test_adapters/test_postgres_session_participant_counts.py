"""The batched people count behind ``people_count``: one query for any number of sessions."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.adapters.outbound.postgres_session_participants import (
    PostgresSessionParticipantRepository,
)
from volundr.domain.models import GitSource, Session
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import ParticipantRole


def adapter():
    pool = MagicMock()
    pool.fetch = AsyncMock()
    return PostgresSessionParticipantRepository(pool), pool


async def test_counts_active_unexpired_grants_for_every_session_in_one_query():
    repo, pool = adapter()
    first, second = uuid4(), uuid4()
    now = datetime.now(UTC)
    pool.fetch.return_value = [
        {"session_id": first, "granted": 3},
        {"session_id": second, "granted": 1},
    ]

    counts = await repo.count_active_for_sessions([first, second, uuid4()], now)

    assert counts == {first: 3, second: 1}
    pool.fetch.assert_awaited_once()
    sql, ids, at = pool.fetch.call_args.args
    assert "session_id = ANY($1::uuid[])" in sql
    assert "status = 'active'" in sql
    assert "expires_at IS NULL OR expires_at > $2" in sql
    assert "GROUP BY session_id" in sql
    assert at == now
    assert len(ids) == 3


async def test_a_session_with_no_active_grant_has_no_entry():
    repo, pool = adapter()
    pool.fetch.return_value = []

    assert await repo.count_active_for_sessions([uuid4()], datetime.now(UTC)) == {}


class TestPeopleCounts:
    """``SessionParticipantService.people_counts``: owner plus active grants, one read."""

    def _service(self):
        grants = InMemorySessionParticipantRepository()
        grants.count_active_for_sessions = AsyncMock(wraps=grants.count_active_for_sessions)
        users = AsyncMock()
        service = SessionParticipantService(
            grants, SessionService(InMemorySessionRepository(), MockPodManager()), users
        )
        return service, grants

    def _session(self) -> Session:
        return Session(
            name="s",
            model="m",
            owner_id="ada",
            tenant_id="acme",
            source=GitSource(repo="https://github.com/acme/repo", branch="main"),
        )

    async def test_every_id_is_answered_in_a_single_repository_read(self):
        service, grants = self._service()
        shared, alone = self._session(), self._session()
        await grants.invite(shared.id, "bo", "acme", ParticipantRole.VIEWER, "ada", None)
        await grants.accept(shared.id, "bo")
        await grants.invite(shared.id, "cy", "acme", ParticipantRole.VIEWER, "ada", None)
        await grants.invite(shared.id, "di", "acme", ParticipantRole.APPROVER, "ada", None)
        await grants.accept(shared.id, "di")
        await grants.invite(alone.id, "ed", "acme", ParticipantRole.VIEWER, "ada", None)
        await grants.revoke(alone.id, "ed")

        counts = await service.people_counts([shared.id, alone.id])

        # The owner plus the two accepted grants; an open invitation and a revoked one add none.
        assert counts == {shared.id: 3, alone.id: 1}
        grants.count_active_for_sessions.assert_awaited_once()

    async def test_no_sessions_asks_nothing(self):
        service, grants = self._service()

        assert await service.people_counts([]) == {}
        grants.count_active_for_sessions.assert_not_awaited()

    async def test_an_expired_grant_is_not_a_person(self):
        service, grants = self._service()
        session = self._session()
        await grants.invite(
            session.id,
            "bo",
            "acme",
            ParticipantRole.VIEWER,
            "ada",
            datetime(2020, 1, 1, tzinfo=UTC),
        )
        await grants.accept(session.id, "bo")

        assert await service.people_counts([session.id]) == {session.id: 1}
