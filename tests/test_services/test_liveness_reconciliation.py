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


def test_liveness_reaper_disabled_by_default():
    """Brokers only report activity on state changes today, so the reaper
    would falsely reap quiet-but-alive sessions — it must be opt-in."""
    from volundr.config import SessionLivenessConfig

    assert SessionLivenessConfig().enabled is False


def test_pod_status_reconcile_enabled_by_default():
    """The pod-status-authoritative reconcile is safe (no false reap) so it is on."""
    from volundr.config import SessionLivenessConfig

    cfg = SessionLivenessConfig()
    assert cfg.reconcile_enabled is True
    assert cfg.reconcile_interval_seconds >= 5


class TestReconcileActiveSessions:
    """INV-9: a dead pod + RUNNING row => periodic reconcile flips status,
    clears the endpoint, and stamps a queryable liveness error — and an
    idle-but-alive session is never false-reaped (pod-status authoritative)."""

    async def test_dead_pod_running_row_is_flipped_and_endpoint_cleared(
        self, service, repo, pod_manager
    ):
        session = _session(SessionStatus.RUNNING, datetime.now(UTC))
        await repo.create(session)

        async def dead(_session):
            return SessionStatus.STOPPED

        pod_manager.status = dead  # type: ignore[method-assign]

        count = await service.reconcile_active_sessions()

        assert count == 1
        updated = await repo.get(session.id)
        assert updated.status == SessionStatus.STOPPED
        assert updated.chat_endpoint is None
        assert updated.code_endpoint is None
        assert (updated.error or "").startswith("liveness:")

    async def test_failed_pod_sets_failed_with_liveness_error(self, service, repo, pod_manager):
        session = _session(SessionStatus.RUNNING, datetime.now(UTC))
        await repo.create(session)

        async def failed(_session):
            return SessionStatus.FAILED

        pod_manager.status = failed  # type: ignore[method-assign]

        count = await service.reconcile_active_sessions()

        assert count == 1
        updated = await repo.get(session.id)
        assert updated.status == SessionStatus.FAILED
        assert updated.chat_endpoint is None
        assert (updated.error or "").startswith("liveness:")

    async def test_idle_but_alive_session_is_not_false_reaped(self, service, repo, pod_manager):
        # The pod manager (authority) still reports RUNNING even though the row
        # has been quiet for an hour — the reconcile leaves it untouched.
        old = datetime.now(UTC) - timedelta(seconds=3600)
        session = _session(SessionStatus.RUNNING, old)
        await repo.create(session)

        async def alive(_session):
            return SessionStatus.RUNNING

        pod_manager.status = alive  # type: ignore[method-assign]

        count = await service.reconcile_active_sessions()

        assert count == 0
        updated = await repo.get(session.id)
        assert updated.status == SessionStatus.RUNNING
        assert updated.chat_endpoint is not None

    async def test_mark_session_dead_reconciles_single_row(self, service, repo, pod_manager):
        session = _session(SessionStatus.RUNNING, datetime.now(UTC))
        await repo.create(session)

        async def dead(_session):
            return SessionStatus.STOPPED

        pod_manager.status = dead  # type: ignore[method-assign]

        result = await service.mark_session_dead(session.id)

        assert result is not None
        assert result.status == SessionStatus.STOPPED
        assert result.chat_endpoint is None
        assert (result.error or "").startswith("liveness:")

    async def test_mark_session_dead_leaves_live_session_untouched(
        self, service, repo, pod_manager
    ):
        session = _session(SessionStatus.RUNNING, datetime.now(UTC))
        await repo.create(session)

        async def alive(_session):
            return SessionStatus.RUNNING

        pod_manager.status = alive  # type: ignore[method-assign]

        result = await service.mark_session_dead(session.id)

        assert result is not None
        assert result.status == SessionStatus.RUNNING
        assert result.chat_endpoint is not None

    async def test_liveness_error_cleared_by_activity_heartbeat(self, service, repo, pod_manager):
        # A reconciled-then-relaunched session must lose the stale liveness marker
        # when the broker reports activity again.
        session = _session(SessionStatus.RUNNING, datetime.now(UTC))
        await repo.create(session)

        async def dead(_session):
            return SessionStatus.STOPPED

        pod_manager.status = dead  # type: ignore[method-assign]
        await service.reconcile_active_sessions()
        # Simulate a fresh broker by restoring RUNNING status before heartbeat.
        reconciled = await repo.get(session.id)
        revived = reconciled.model_copy(update={"status": SessionStatus.RUNNING})
        await repo.update(revived)

        updated = await service.update_activity(
            session.id, SessionActivityState.ACTIVE, metadata={}
        )

        assert updated.error is None
