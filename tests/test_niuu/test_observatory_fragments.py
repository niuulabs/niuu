"""Tests for the Observatory topology fragment push inbox.

Sources that cannot be reached — a resident on a bare-metal Spark, a Docker
container behind NAT — publish here instead of being polled. The properties
that matter: publishing is idempotent, arrival time is the inbox's to decide,
and a source that goes quiet becomes visible as stale rather than vanishing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from niuu.adapters.memory_observatory_fragments import InMemoryObservatoryFragmentRepository
from niuu.domain.observatory import FragmentMeta, ObservatoryFragment, TopologyNode
from niuu.domain.services.observatory_fragments import ObservatoryFragmentInboxService

START = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


class _Clock:
    """Hand-advanced clock, so staleness is tested without sleeping."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _fragment(*, source_id: str = "spark-1", nodes: int = 1) -> ObservatoryFragment:
    return ObservatoryFragment(
        nodes=[
            TopologyNode(
                id=f"{source_id}-node-{index}",
                type_id="ravn_long",
                label=f"resident-{index}",
                host_id="saehrimnir",
                realm_id="sparks",
            )
            for index in range(nodes)
        ],
        meta=FragmentMeta(
            source_id=source_id,
            source_kind="resident",
            source_name=source_id,
            realm_id="sparks",
            host_id="saehrimnir",
            revision="rev-1",
        ),
    )


def _inbox(clock: _Clock, *, ttl_seconds: float = 180.0) -> ObservatoryFragmentInboxService:
    return ObservatoryFragmentInboxService(
        InMemoryObservatoryFragmentRepository(),
        ttl_seconds=ttl_seconds,
        clock=clock,
    )


class TestAccepting:
    @pytest.mark.asyncio
    async def test_a_republished_fragment_replaces_rather_than_accumulates(self) -> None:
        """A heartbeat says "this is my current state", so a retry is harmless."""
        clock = _Clock()
        inbox = _inbox(clock)

        await inbox.accept("spark-1", _fragment(nodes=3))
        await inbox.accept("spark-1", _fragment(nodes=1))

        current = await inbox.current()
        assert len(current) == 1
        stored, health = current[0]
        assert len(stored.fragment.nodes) == 1
        assert health.node_count == 1

    @pytest.mark.asyncio
    async def test_sources_do_not_overwrite_each_other(self) -> None:
        clock = _Clock()
        inbox = _inbox(clock)

        await inbox.accept("spark-1", _fragment(source_id="spark-1"))
        await inbox.accept("spark-2", _fragment(source_id="spark-2"))

        assert {health.source_id for _stored, health in await inbox.current()} == {
            "spark-1",
            "spark-2",
        }

    @pytest.mark.asyncio
    async def test_arrival_time_comes_from_the_inbox_not_the_publisher(self) -> None:
        """A source with a skewed clock must not be able to look fresher."""
        clock = _Clock()
        inbox = _inbox(clock)

        stored = await inbox.accept("spark-1", _fragment())

        assert stored.received_at == START

    @pytest.mark.asyncio
    async def test_forget_drops_a_decommissioned_source(self) -> None:
        clock = _Clock()
        inbox = _inbox(clock)
        await inbox.accept("spark-1", _fragment())

        assert await inbox.forget("spark-1") is True
        assert await inbox.current() == []

    @pytest.mark.asyncio
    async def test_forgetting_an_unknown_source_reports_that(self) -> None:
        assert await _inbox(_Clock()).forget("never-seen") is False


class TestFreshness:
    @pytest.mark.asyncio
    async def test_a_fresh_source_is_healthy(self) -> None:
        clock = _Clock()
        inbox = _inbox(clock, ttl_seconds=180.0)
        await inbox.accept("spark-1", _fragment())

        clock.advance(60)
        _stored, health = (await inbox.current())[0]

        assert health.status == "healthy"
        assert health.transport == "push"

    @pytest.mark.asyncio
    async def test_a_quiet_source_goes_stale_without_disappearing(self) -> None:
        """A dead Spark should read "last seen 4m ago", not look like it never
        existed."""
        clock = _Clock()
        inbox = _inbox(clock, ttl_seconds=180.0)
        await inbox.accept("spark-1", _fragment())

        clock.advance(240)
        current = await inbox.current()

        assert len(current) == 1
        _stored, health = current[0]
        assert health.status == "stale"
        assert health.last_seen == "2026-08-01T12:00:00Z"
        assert "240s" in health.message

    @pytest.mark.asyncio
    async def test_republishing_restores_freshness(self) -> None:
        clock = _Clock()
        inbox = _inbox(clock, ttl_seconds=180.0)
        await inbox.accept("spark-1", _fragment())

        clock.advance(240)
        await inbox.accept("spark-1", _fragment())
        _stored, health = (await inbox.current())[0]

        assert health.status == "healthy"

    @pytest.mark.asyncio
    async def test_health_carries_the_sources_own_placement(self) -> None:
        clock = _Clock()
        inbox = _inbox(clock)
        await inbox.accept("spark-1", _fragment())

        _stored, health = (await inbox.current())[0]

        assert health.realm_id == "sparks"
        assert health.source_kind == "resident"
        assert health.revision == "rev-1"

    @pytest.mark.asyncio
    async def test_a_fragment_without_meta_is_still_tracked(self) -> None:
        """Meta is optional on the contract, so the inbox must not require it."""
        clock = _Clock()
        inbox = _inbox(clock)

        await inbox.accept("anonymous", ObservatoryFragment())
        _stored, health = (await inbox.current())[0]

        assert health.source_id == "anonymous"
        assert health.source_kind == ""


class TestInMemoryRepository:
    @pytest.mark.asyncio
    async def test_lists_sources_in_a_stable_order(self) -> None:
        repository = InMemoryObservatoryFragmentRepository()
        for source_id in ("spark-2", "spark-1", "spark-3"):
            await repository.put(source_id, ObservatoryFragment(), received_at=START)

        assert [item.source_id for item in await repository.list_fragments()] == [
            "spark-1",
            "spark-2",
            "spark-3",
        ]
