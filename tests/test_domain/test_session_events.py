"""Tests for the principal-scoped realtime session event stream."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from tests.conftest import InMemoryStatsRepository
from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter
from volundr.domain.models import EventType, Principal, RealtimeEvent, Stats
from volundr.domain.ports import EventBroadcaster, StatsRepository
from volundr.domain.services import SessionEventStream, SessionService, StatsService
from volundr.domain.services.session_events import EVENT_AUDIENCE

ALICE = Principal(user_id="alice", email="", tenant_id="t1", roles=["volundr:developer"])
BOB = Principal(user_id="bob", email="", tenant_id="t1", roles=["volundr:developer"])
T1_ADMIN = Principal(user_id="root", email="", tenant_id="t1", roles=["volundr:admin"])
T2_ADMIN = Principal(user_id="other-root", email="", tenant_id="t2", roles=["volundr:admin"])


def _event(event_type: EventType, **data) -> RealtimeEvent:
    return RealtimeEvent(type=event_type, data=data, timestamp=datetime.now(UTC))


def _alice_session_events() -> list[RealtimeEvent]:
    """One of every session-scoped event, all about alice's session in t1."""
    scope = {"owner_id": "alice", "tenant_id": "t1"}
    return [
        _event(EventType.SESSION_CREATED, id="alice-s", **scope),
        _event(EventType.SESSION_UPDATED, id="alice-s", **scope),
        _event(EventType.SESSION_ACTIVITY, session_id="alice-s", state="active", **scope),
        _event(EventType.SESSION_NEEDS_INPUT, session_id="alice-s", prompt="Which DB?", **scope),
        _event(EventType.CHRONICLE_EVENT, session_id="alice-s", **scope),
        _event(EventType.PR_CREATED, session_id="alice-s", pr_number=1, **scope),
        _event(EventType.SESSION_DELETED, id="alice-s", status="deleted", **scope),
    ]


class _FiniteBroadcaster(EventBroadcaster):
    def __init__(self, events: list[RealtimeEvent]) -> None:
        self._events = events

    async def publish(self, event: RealtimeEvent) -> None:
        self._events.append(event)

    async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
        for event in self._events:
            yield event


def _service(repository, pod_manager, *, authorization=None) -> SessionService:
    return SessionService(
        repository=repository,
        pod_manager=pod_manager,
        authorization=authorization,
    )


class _ScopedStatsRepository(StatsRepository):
    """Distinct figures per scope, so a test can tell whose figures it received."""

    ACTIVE = {("t1", "alice"): 1, ("t1", "bob"): 2, ("t1", None): 3, (None, None): 9}

    def __init__(self) -> None:
        self.scopes: list[tuple[str | None, str | None]] = []

    async def get_stats(self, *, tenant_id: str | None, owner_id: str | None) -> Stats:
        self.scopes.append((tenant_id, owner_id))
        active = self.ACTIVE[(tenant_id, owner_id)]
        return Stats(
            active_sessions=active,
            total_sessions=active * 10,
            tokens_today=active * 100,
            local_tokens=active * 40,
            cloud_tokens=active * 60,
            cost_today=Decimal("0.25") * active,
            sessions_today=active,
            sparklines={"sessionsToday": [0.0, float(active)]},
        )


def _stream(
    broadcaster: EventBroadcaster,
    sessions: SessionService,
    stats: StatsRepository | None = None,
) -> SessionEventStream:
    repository = stats if stats is not None else InMemoryStatsRepository()
    return SessionEventStream(broadcaster, sessions, StatsService(repository, sessions))


async def _received(stream: SessionEventStream, principal: Principal | None) -> list:
    return [event async for event in stream.subscribe(principal)]


@pytest.fixture
def sessions(repository, pod_manager) -> SessionService:
    return _service(repository, pod_manager, authorization=SimpleRoleAuthorizationAdapter())


class TestSessionEventStream:
    def test_every_event_type_has_an_audience(self):
        # A new EventType must be classified before the stream can carry it.
        assert set(EVENT_AUDIENCE) == set(EventType)

    async def test_non_owner_receives_nothing_about_another_users_session(self, sessions):
        stream = _stream(_FiniteBroadcaster(_alice_session_events()), sessions)
        assert await _received(stream, BOB) == []

    async def test_owner_receives_every_event_about_their_session(self, sessions):
        events = _alice_session_events()
        stream = _stream(_FiniteBroadcaster(list(events)), sessions)
        assert await _received(stream, ALICE) == events

    async def test_tenant_admin_sees_the_tenant_but_not_other_tenants(self, sessions):
        events = _alice_session_events()
        stream = _stream(_FiniteBroadcaster(list(events)), sessions)
        assert await _received(stream, T1_ADMIN) == events
        assert await _received(stream, T2_ADMIN) == []

    async def test_unowned_or_untenanted_sessions_never_widen_visibility(self, sessions):
        unowned = _event(EventType.SESSION_UPDATED, id="system", owner_id="", tenant_id="t1")
        untenanted = _event(EventType.SESSION_UPDATED, id="legacy", owner_id="alice", tenant_id="")
        stream = _stream(_FiniteBroadcaster([unowned, untenanted]), sessions)
        assert await _received(stream, ALICE) == []
        # Only a tenant admin sees its tenant's unowned sessions.
        assert await _received(stream, T1_ADMIN) == [unowned]

    async def test_heartbeat_reaches_every_subscriber(self, sessions):
        heartbeat = _event(EventType.HEARTBEAT)
        stream = _stream(_FiniteBroadcaster([heartbeat]), sessions)
        assert await _received(stream, BOB) == [heartbeat]

    async def test_read_state_hint_goes_only_to_its_reader(self, sessions):
        hint = _event(EventType.SESSION_READ_STATE, session_id="alice-s", owner_id="root")
        stream = _stream(_FiniteBroadcaster([hint]), sessions)
        assert await _received(stream, T1_ADMIN) == [hint]
        assert await _received(stream, ALICE) == []

    async def test_unattributed_events_reach_no_bounded_subscriber(
        self, sessions, repository, pod_manager
    ):
        merged = _event(EventType.PR_MERGED, pr_number=1, repo_url="https://git.test/r")
        stream = _stream(_FiniteBroadcaster([merged]), sessions)
        assert await _received(stream, T1_ADMIN) == []
        unscoped = _stream(_FiniteBroadcaster([merged]), _service(repository, pod_manager))
        assert await _received(unscoped, None) == [merged]

    async def test_without_identity_or_authorization_the_stream_is_unscoped(
        self, repository, pod_manager
    ):
        # Same as GET /sessions with no identity configured.
        events = _alice_session_events()
        stream = _stream(_FiniteBroadcaster(list(events)), _service(repository, pod_manager))
        assert await _received(stream, None) == events

    async def test_configured_authorization_refuses_an_anonymous_subscriber(self, sessions):
        stream = _stream(_FiniteBroadcaster(_alice_session_events()), sessions)
        with pytest.raises(PermissionError):
            stream.authorize(None)
        with pytest.raises(PermissionError):
            await _received(stream, None)

    async def test_session_event_without_scope_fields_fails_loudly(self, sessions):
        unscoped = _event(EventType.SESSION_UPDATED, id="alice-s")
        stream = _stream(_FiniteBroadcaster([unscoped]), sessions)
        with pytest.raises(ValueError, match="owner_id/tenant_id"):
            await _received(stream, ALICE)

    async def test_authorization_is_consulted_once_per_session(self, repository, pod_manager):
        authorization = SimpleRoleAuthorizationAdapter()
        authorization.is_allowed = AsyncMock(return_value=True)
        stream = _stream(
            _FiniteBroadcaster(_alice_session_events()),
            _service(repository, pod_manager, authorization=authorization),
        )
        assert len(await _received(stream, ALICE)) == len(_alice_session_events())
        authorization.is_allowed.assert_awaited_once()
        principal, action, resource = authorization.is_allowed.await_args.args
        assert (principal, action, resource.id) == (ALICE, "list", "alice-s")

    async def test_authorization_denial_hides_an_owned_session(self, repository, pod_manager):
        authorization = SimpleRoleAuthorizationAdapter()
        authorization.is_allowed = AsyncMock(return_value=False)
        stream = _stream(
            _FiniteBroadcaster(_alice_session_events()),
            _service(repository, pod_manager, authorization=authorization),
        )
        assert await _received(stream, ALICE) == []


class TestScopedStats:
    """A stats tick becomes figures over what the subscriber may list."""

    async def test_each_subscriber_receives_figures_for_its_own_bounds(self, sessions):
        # A publisher's own figures are never forwarded, only the subscriber's.
        tick = _event(EventType.STATS_UPDATED, active_sessions=1000)
        stats = _ScopedStatsRepository()
        stream = _stream(_FiniteBroadcaster([tick]), sessions, stats)

        [alice] = await _received(stream, ALICE)
        [bob] = await _received(stream, BOB)
        [admin] = await _received(stream, T1_ADMIN)

        assert (alice.data["active_sessions"], bob.data["active_sessions"]) == (1, 2)
        assert admin.data["active_sessions"] == 3
        assert stats.scopes == [("t1", "alice"), ("t1", "bob"), ("t1", None)]
        assert alice.type is EventType.STATS_UPDATED
        assert alice.timestamp == tick.timestamp

    async def test_payload_keeps_the_stats_updated_shape(self, sessions):
        stream = _stream(
            _FiniteBroadcaster([_event(EventType.STATS_UPDATED)]),
            sessions,
            _ScopedStatsRepository(),
        )
        [event] = await _received(stream, ALICE)
        assert event.data == {
            "active_sessions": 1,
            "total_sessions": 10,
            "sessions_today": 1,
            "tokens_today": 100,
            "local_tokens": 40,
            "cloud_tokens": 60,
            "cost_today": 0.25,
            "sparklines": {"sessionsToday": [0.0, 1.0]},
        }

    async def test_figures_are_computed_once_per_tick_for_each_scope(self, sessions):
        ticks = [_event(EventType.STATS_UPDATED)]
        stats = _ScopedStatsRepository()
        stream = _stream(_FiniteBroadcaster(ticks), sessions, stats)
        alice_in_another_tab = Principal(
            user_id="alice", email="", tenant_id="t1", roles=["volundr:developer"]
        )

        assert await _received(stream, ALICE) == await _received(stream, alice_in_another_tab)
        assert stats.scopes == [("t1", "alice")]

        ticks[:] = [_event(EventType.STATS_UPDATED)]
        await _received(stream, ALICE)
        assert stats.scopes == [("t1", "alice"), ("t1", "alice")]

    async def test_without_identity_or_authorization_figures_are_unbounded(
        self, repository, pod_manager
    ):
        stats = _ScopedStatsRepository()
        stream = _stream(
            _FiniteBroadcaster([_event(EventType.STATS_UPDATED)]),
            _service(repository, pod_manager),
            stats,
        )
        [event] = await _received(stream, None)
        assert event.data["active_sessions"] == 9
        assert stats.scopes == [(None, None)]

    async def test_a_stats_failure_fails_the_subscription(self, sessions):
        stats = _ScopedStatsRepository()
        stats.get_stats = AsyncMock(side_effect=ConnectionError("database unreachable"))
        stream = _stream(_FiniteBroadcaster([_event(EventType.STATS_UPDATED)]), sessions, stats)
        with pytest.raises(ConnectionError):
            await _received(stream, ALICE)


class TestVisibilityScope:
    def test_matches_list_sessions_bounds(self, sessions):
        assert sessions.visibility_scope(ALICE) == ("t1", "alice")
        assert sessions.visibility_scope(T1_ADMIN) == ("t1", None)

    def test_unbounded_only_without_authorization(self, sessions, repository, pod_manager):
        assert _service(repository, pod_manager).visibility_scope(None) == (None, None)
        with pytest.raises(PermissionError):
            sessions.visibility_scope(None)
