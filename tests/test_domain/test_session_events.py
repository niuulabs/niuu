"""Tests for the principal-scoped realtime session event stream."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.authorization import SimpleRoleAuthorizationAdapter
from volundr.domain.models import EventType, Principal, RealtimeEvent
from volundr.domain.ports import EventBroadcaster
from volundr.domain.services import SessionEventStream, SessionService
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
        stream = SessionEventStream(_FiniteBroadcaster(_alice_session_events()), sessions)
        assert await _received(stream, BOB) == []

    async def test_owner_receives_every_event_about_their_session(self, sessions):
        events = _alice_session_events()
        stream = SessionEventStream(_FiniteBroadcaster(list(events)), sessions)
        assert await _received(stream, ALICE) == events

    async def test_tenant_admin_sees_the_tenant_but_not_other_tenants(self, sessions):
        events = _alice_session_events()
        stream = SessionEventStream(_FiniteBroadcaster(list(events)), sessions)
        assert await _received(stream, T1_ADMIN) == events
        assert await _received(stream, T2_ADMIN) == []

    async def test_unowned_or_untenanted_sessions_never_widen_visibility(self, sessions):
        unowned = _event(EventType.SESSION_UPDATED, id="system", owner_id="", tenant_id="t1")
        untenanted = _event(EventType.SESSION_UPDATED, id="legacy", owner_id="alice", tenant_id="")
        stream = SessionEventStream(_FiniteBroadcaster([unowned, untenanted]), sessions)
        assert await _received(stream, ALICE) == []
        # Only a tenant admin sees its tenant's unowned sessions.
        assert await _received(stream, T1_ADMIN) == [unowned]

    async def test_deployment_wide_signals_reach_every_subscriber(self, sessions):
        heartbeat = _event(EventType.HEARTBEAT)
        stats = _event(EventType.STATS_UPDATED, active_sessions=3)
        stream = SessionEventStream(_FiniteBroadcaster([heartbeat, stats]), sessions)
        assert await _received(stream, BOB) == [heartbeat, stats]

    async def test_read_state_hint_goes_only_to_its_reader(self, sessions):
        hint = _event(EventType.SESSION_READ_STATE, session_id="alice-s", owner_id="root")
        stream = SessionEventStream(_FiniteBroadcaster([hint]), sessions)
        assert await _received(stream, T1_ADMIN) == [hint]
        assert await _received(stream, ALICE) == []

    async def test_unattributed_events_reach_no_bounded_subscriber(
        self, sessions, repository, pod_manager
    ):
        merged = _event(EventType.PR_MERGED, pr_number=1, repo_url="https://git.test/r")
        stream = SessionEventStream(_FiniteBroadcaster([merged]), sessions)
        assert await _received(stream, T1_ADMIN) == []
        unscoped = SessionEventStream(
            _FiniteBroadcaster([merged]), _service(repository, pod_manager)
        )
        assert await _received(unscoped, None) == [merged]

    async def test_without_identity_or_authorization_the_stream_is_unscoped(
        self, repository, pod_manager
    ):
        # Same as GET /sessions with no identity configured.
        events = _alice_session_events()
        stream = SessionEventStream(
            _FiniteBroadcaster(list(events)), _service(repository, pod_manager)
        )
        assert await _received(stream, None) == events

    async def test_configured_authorization_refuses_an_anonymous_subscriber(self, sessions):
        stream = SessionEventStream(_FiniteBroadcaster(_alice_session_events()), sessions)
        with pytest.raises(PermissionError):
            stream.authorize(None)
        with pytest.raises(PermissionError):
            await _received(stream, None)

    async def test_session_event_without_scope_fields_fails_loudly(self, sessions):
        unscoped = _event(EventType.SESSION_UPDATED, id="alice-s")
        stream = SessionEventStream(_FiniteBroadcaster([unscoped]), sessions)
        with pytest.raises(ValueError, match="owner_id/tenant_id"):
            await _received(stream, ALICE)

    async def test_authorization_is_consulted_once_per_session(self, repository, pod_manager):
        authorization = SimpleRoleAuthorizationAdapter()
        authorization.is_allowed = AsyncMock(return_value=True)
        stream = SessionEventStream(
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
        stream = SessionEventStream(
            _FiniteBroadcaster(_alice_session_events()),
            _service(repository, pod_manager, authorization=authorization),
        )
        assert await _received(stream, ALICE) == []


class TestVisibilityScope:
    def test_matches_list_sessions_bounds(self, sessions):
        assert sessions.visibility_scope(ALICE) == ("t1", "alice")
        assert sessions.visibility_scope(T1_ADMIN) == ("t1", None)

    def test_unbounded_only_without_authorization(self, sessions, repository, pod_manager):
        assert _service(repository, pod_manager).visibility_scope(None) == (None, None)
        with pytest.raises(PermissionError):
            sessions.visibility_scope(None)
