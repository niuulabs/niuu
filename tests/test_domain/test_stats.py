"""Tests for StatsService."""

from decimal import Decimal

import pytest

from tests.conftest import InMemoryStatsRepository
from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter
from volundr.domain.models import Principal, Stats
from volundr.domain.services import SessionService, StatsService

DEVELOPER = Principal(user_id="alice", email="", tenant_id="t1", roles=["volundr:developer"])
ADMIN = Principal(user_id="root", email="", tenant_id="t1", roles=["volundr:admin"])


@pytest.fixture
def stats_repo() -> InMemoryStatsRepository:
    """Create a stats repository with sample data."""
    return InMemoryStatsRepository(
        active_sessions=3,
        total_sessions=10,
        tokens_today=50000,
        local_tokens=20000,
        cloud_tokens=30000,
        cost_today=Decimal("1.50"),
    )


@pytest.fixture
def sessions(repository, pod_manager) -> SessionService:
    """A session service without authorization, i.e. no identity configured."""
    return SessionService(repository=repository, pod_manager=pod_manager)


@pytest.fixture
def authorized_sessions(repository, pod_manager) -> SessionService:
    return SessionService(
        repository=repository,
        pod_manager=pod_manager,
        authorization=SimpleRoleAuthorizationAdapter(),
    )


@pytest.fixture
def stats_service(stats_repo: InMemoryStatsRepository, sessions: SessionService) -> StatsService:
    """Create a stats service with test repository."""
    return StatsService(stats_repo, sessions)


class TestStatsService:
    """Tests for StatsService."""

    async def test_get_stats_returns_stats(self, stats_service: StatsService) -> None:
        """Test that get_stats returns Stats object."""
        stats = await stats_service.get_stats(None)

        assert isinstance(stats, Stats)
        assert stats.active_sessions == 3
        assert stats.total_sessions == 10
        assert stats.tokens_today == 50000
        assert stats.local_tokens == 20000
        assert stats.cloud_tokens == 30000
        assert stats.cost_today == Decimal("1.50")

    async def test_get_stats_with_zero_values(self, sessions: SessionService) -> None:
        """Test that get_stats handles zero values."""
        repo = InMemoryStatsRepository()
        service = StatsService(repo, sessions)

        stats = await service.get_stats(None)

        assert stats.active_sessions == 0
        assert stats.total_sessions == 0
        assert stats.tokens_today == 0
        assert stats.local_tokens == 0
        assert stats.cloud_tokens == 0
        assert stats.cost_today == Decimal("0")

    async def test_get_stats_with_updated_values(
        self, stats_repo: InMemoryStatsRepository, sessions: SessionService
    ) -> None:
        """Test that get_stats reflects updated values."""
        service = StatsService(stats_repo, sessions)

        # Update stats
        stats_repo.set_stats(active_sessions=5, tokens_today=100000)

        stats = await service.get_stats(None)

        assert stats.active_sessions == 5
        assert stats.tokens_today == 100000
        # Other values should remain unchanged
        assert stats.total_sessions == 10
        assert stats.local_tokens == 20000


class TestStatsScope:
    """Stats are bounded exactly like ``SessionService.list_sessions``."""

    async def test_a_developer_counts_only_its_own_sessions_in_its_tenant(
        self, stats_repo: InMemoryStatsRepository, authorized_sessions: SessionService
    ) -> None:
        await StatsService(stats_repo, authorized_sessions).get_stats(DEVELOPER)
        assert stats_repo.scopes == [("t1", "alice")]

    async def test_a_tenant_admin_counts_its_whole_tenant_only(
        self, stats_repo: InMemoryStatsRepository, authorized_sessions: SessionService
    ) -> None:
        await StatsService(stats_repo, authorized_sessions).get_stats(ADMIN)
        assert stats_repo.scopes == [("t1", None)]

    async def test_without_identity_or_authorization_stats_are_unbounded(
        self, stats_service: StatsService, stats_repo: InMemoryStatsRepository
    ) -> None:
        await stats_service.get_stats(None)
        assert stats_repo.scopes == [(None, None)]

    async def test_configured_authorization_refuses_an_anonymous_caller(
        self, stats_repo: InMemoryStatsRepository, authorized_sessions: SessionService
    ) -> None:
        with pytest.raises(PermissionError):
            await StatsService(stats_repo, authorized_sessions).get_stats(None)
        assert stats_repo.scopes == []
