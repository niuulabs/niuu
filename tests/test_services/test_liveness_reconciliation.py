"""Tests for SessionService.reconcile_liveness (G6 — dead-broker detection)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.conftest import InMemorySessionRepository
from volundr.domain.models import (
    GitSource,
    Session,
    SessionActivityState,
    SessionStatus,
)
from volundr.domain.services.session import SessionService


def _session(status: SessionStatus, last_active: datetime) -> Session:
    return Session(
        id=uuid4(),
        name="s",
        model="claude-opus-4-8",
        source=GitSource(type="git", repo_url="https://example.com/r.git", branch="main"),
        status=status,
        chat_endpoint="ws://host:8080/s/x/session",
        code_endpoint="file:///x",
        pod_name="local-x",
        created_at=last_active,
        last_active=last_active,
    )


@pytest.fixture
def repo() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def service(repo, pod_manager, broadcaster) -> SessionService:
    return SessionService(
        repository=repo,
        pod_manager=pod_manager,
        broadcaster=broadcaster,
        validate_repos=False,
    )


class TestReconcileLiveness:
    async def test_marks_stale_running_session_stopped_and_clears_endpoint(self, service, repo):
        old = datetime.now(UTC) - timedelta(seconds=3600)
        stale = _session(SessionStatus.RUNNING, old)
        await repo.create(stale)

        count = await service.reconcile_liveness(stale_after_seconds=600)

        assert count == 1
        updated = await repo.get(stale.id)
        assert updated.status == SessionStatus.STOPPED
        assert updated.chat_endpoint is None
        assert updated.code_endpoint is None
        assert "liveness" in (updated.error or "")

    async def test_keeps_recently_active_running_session(self, service, repo):
        fresh = _session(SessionStatus.RUNNING, datetime.now(UTC))
        await repo.create(fresh)

        count = await service.reconcile_liveness(stale_after_seconds=600)

        assert count == 0
        assert (await repo.get(fresh.id)).status == SessionStatus.RUNNING

    async def test_ignores_non_running_sessions(self, service, repo):
        old = datetime.now(UTC) - timedelta(seconds=3600)
        for st in (SessionStatus.STARTING, SessionStatus.PROVISIONING, SessionStatus.STOPPED):
            await repo.create(_session(st, old))

        count = await service.reconcile_liveness(stale_after_seconds=600)

        assert count == 0

    async def test_returns_count_across_multiple_stale(self, service, repo):
        old = datetime.now(UTC) - timedelta(seconds=3600)
        for _ in range(3):
            await repo.create(_session(SessionStatus.RUNNING, old))

        count = await service.reconcile_liveness(stale_after_seconds=600)

        assert count == 3


class TestActivityRefreshesLastActive:
    async def test_update_activity_bumps_last_active(self, service, repo):
        old = datetime.now(UTC) - timedelta(seconds=3600)
        session = _session(SessionStatus.RUNNING, old)
        await repo.create(session)

        before = datetime.now(UTC)
        updated = await service.update_activity(
            session.id, SessionActivityState.ACTIVE, metadata={}
        )

        assert updated.last_active >= before
        # and a heartbeat keeps it out of the stale set
        assert await service.reconcile_liveness(stale_after_seconds=600) == 0
