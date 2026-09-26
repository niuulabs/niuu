"""Tests for chronicle timeline service methods."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.conftest import (
    InMemoryChronicleRepository,
    InMemorySessionRepository,
    InMemoryTimelineRepository,
    MockEventBroadcaster,
    MockPodManager,
)
from volundr.domain.models import (
    GitSource,
    TimelineEvent,
    TimelineEventType,
)
from volundr.domain.services import ChronicleService, SessionService


@pytest.fixture
def session_service(
    repository: InMemorySessionRepository, pod_manager: MockPodManager
) -> SessionService:
    return SessionService(repository, pod_manager)


@pytest.fixture
def chronicle_svc(
    chronicle_repository: InMemoryChronicleRepository,
    session_service: SessionService,
    timeline_repository: InMemoryTimelineRepository,
    broadcaster: MockEventBroadcaster,
) -> ChronicleService:
    return ChronicleService(
        chronicle_repository,
        session_service,
        broadcaster=broadcaster,
        timeline_repository=timeline_repository,
    )


@pytest.fixture
def chronicle_svc_no_timeline(
    chronicle_repository: InMemoryChronicleRepository,
    session_service: SessionService,
) -> ChronicleService:
    return ChronicleService(chronicle_repository, session_service)


def _make_event(
    chronicle_id,
    session_id,
    t,
    event_type=TimelineEventType.MESSAGE,
    label="test",
    **kwargs,
) -> TimelineEvent:
    return TimelineEvent(
        id=uuid4(),
        chronicle_id=chronicle_id,
        session_id=session_id,
        t=t,
        type=event_type,
        label=label,
        created_at=datetime.now(UTC),
        **kwargs,
    )


class TestGetTimeline:
    """Tests for ChronicleService.get_timeline."""

    async def test_returns_none_when_no_timeline_repo(
        self,
        chronicle_svc_no_timeline: ChronicleService,
    ):
        """Returns None when timeline repository is not configured."""
        result = await chronicle_svc_no_timeline.get_timeline(uuid4(), principal=None)
        assert result is None

    async def test_returns_none_when_no_chronicle(self, chronicle_svc: ChronicleService):
        """Returns None when no chronicle exists for session."""
        result = await chronicle_svc.get_timeline(uuid4(), principal=None)
        assert result is None

    async def test_returns_empty_timeline(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
    ):
        """Returns empty timeline when chronicle exists but has no events."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await chronicle_svc.create_chronicle(session.id, principal=None)

        timeline = await chronicle_svc.get_timeline(session.id, principal=None)

        assert timeline is not None
        assert timeline.events == []
        assert timeline.files == []
        assert timeline.commits == []
        assert timeline.token_burn == []

    async def test_returns_events_ordered_by_t(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """Events are ordered by elapsed time."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        ev3 = _make_event(chronicle.id, session.id, t=30, label="third")
        ev1 = _make_event(chronicle.id, session.id, t=0, label="first")
        ev2 = _make_event(chronicle.id, session.id, t=10, label="second")
        await timeline_repository.add_event(ev3)
        await timeline_repository.add_event(ev1)
        await timeline_repository.add_event(ev2)

        timeline = await chronicle_svc.get_timeline(session.id, principal=None)

        assert timeline is not None
        assert [e.t for e in timeline.events] == [0, 10, 30]
        assert [e.label for e in timeline.events] == ["first", "second", "third"]


class TestAddTimelineEvent:
    """Tests for ChronicleService.add_timeline_event."""

    async def test_stores_event(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """Event is persisted in the timeline repository."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        stored = await chronicle_svc.add_timeline_event(
            session.id, principal=None, t=5, type=TimelineEventType.MESSAGE, label="test"
        )

        assert stored.t == 5
        assert (stored.chronicle_id, stored.session_id) == (chronicle.id, session.id)
        events = await timeline_repository.get_events(chronicle.id)
        assert len(events) == 1

    async def test_first_event_creates_the_chronicle(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        chronicle_repository: InMemoryChronicleRepository,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """A session without a chronicle gets one, attributed like the session."""
        from volundr.domain.models import Principal

        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            principal=Principal(user_id="alice", email="", tenant_id="t1", roles=[]),
        )

        stored = await chronicle_svc.add_timeline_event(
            session.id,
            principal=None,
            t=3,
            type=TimelineEventType.FILE,
            label="main.py",
            action="modified",
            ins=4,
            del_=1,
        )

        chronicle = await chronicle_repository.get_by_session(session.id)
        assert chronicle is not None
        assert (chronicle.owner_id, chronicle.tenant_id) == ("alice", "t1")
        assert stored.chronicle_id == chronicle.id
        (event,) = await timeline_repository.get_events(chronicle.id)
        assert (event.action, event.ins, event.del_) == ("modified", 4, 1)

    async def test_publishes_sse_event(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        broadcaster: MockEventBroadcaster,
    ):
        """Adding an event publishes a chronicle_event via SSE."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        await chronicle_svc.create_chronicle(session.id, principal=None)

        await chronicle_svc.add_timeline_event(
            session.id, principal=None, t=10, type=TimelineEventType.MESSAGE, label="test"
        )

        chronicle_events = [e for e in broadcaster.events if e.type.value == "chronicle_event"]
        assert len(chronicle_events) == 1
        assert chronicle_events[0].data["session_id"] == str(session.id)

    async def test_sse_event_carries_session_owner_and_tenant(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        broadcaster: MockEventBroadcaster,
    ):
        """The stream scopes chronicle events by the session's owner and tenant."""
        from volundr.domain.models import Principal

        session = await session_service.create_session(
            name="Scoped",
            model="sonnet",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
            principal=Principal(user_id="alice", email="", tenant_id="t1", roles=[]),
        )
        await chronicle_svc.create_chronicle(session.id, principal=None)

        await chronicle_svc.add_timeline_event(
            session.id, principal=None, t=0, type=TimelineEventType.MESSAGE, label="test"
        )

        (published,) = [e for e in broadcaster.events if e.type.value == "chronicle_event"]
        assert (published.data["owner_id"], published.data["tenant_id"]) == ("alice", "t1")

    async def test_unknown_session_is_refused_before_storing(
        self,
        chronicle_svc: ChronicleService,
        chronicle_repository: InMemoryChronicleRepository,
    ):
        """An event for a session with no history is not persisted, nor a chronicle made."""
        from volundr.domain.services import SessionNotFoundError

        session_id = uuid4()
        with pytest.raises(SessionNotFoundError):
            await chronicle_svc.add_timeline_event(
                session_id, principal=None, t=0, type=TimelineEventType.MESSAGE, label="x"
            )
        assert await chronicle_repository.get_by_session(session_id) is None

    async def test_deleted_session_is_refused_before_storing(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        repository: InMemorySessionRepository,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """History outlives its session, but an event that cannot be scoped for the
        stream is not persisted either."""
        from volundr.domain.services import SessionNotFoundError

        session = await session_service.create_session(
            name="Gone",
            model="sonnet",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)
        await repository.delete(session.id)

        with pytest.raises(SessionNotFoundError):
            await chronicle_svc.add_timeline_event(
                session.id, principal=None, t=0, type=TimelineEventType.MESSAGE, label="x"
            )
        assert await timeline_repository.get_events(chronicle.id) == []

    async def test_raises_when_no_timeline_repo(
        self,
        chronicle_svc_no_timeline: ChronicleService,
    ):
        """Raises RuntimeError when timeline repository is not configured."""
        with pytest.raises(RuntimeError, match="Timeline repository not configured"):
            await chronicle_svc_no_timeline.add_timeline_event(
                uuid4(), principal=None, t=0, type=TimelineEventType.MESSAGE, label="x"
            )


class TestAggregateFiles:
    """Tests for file aggregation from timeline events."""

    async def test_aggregates_file_events(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """File events are deduplicated and aggregated."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        await timeline_repository.add_event(
            _make_event(
                chronicle.id,
                session.id,
                t=5,
                event_type=TimelineEventType.FILE,
                label="src/main.py",
                action="modified",
                ins=10,
                del_=3,
            )
        )
        await timeline_repository.add_event(
            _make_event(
                chronicle.id,
                session.id,
                t=15,
                event_type=TimelineEventType.FILE,
                label="src/main.py",
                action="modified",
                ins=5,
                del_=2,
            )
        )
        await timeline_repository.add_event(
            _make_event(
                chronicle.id,
                session.id,
                t=20,
                event_type=TimelineEventType.FILE,
                label="src/new.py",
                action="created",
                ins=50,
                del_=0,
            )
        )

        timeline = await chronicle_svc.get_timeline(session.id, principal=None)

        assert timeline is not None
        assert len(timeline.files) == 2

        main_file = next(f for f in timeline.files if f.path == "src/main.py")
        assert main_file.status == "mod"
        assert main_file.ins == 15
        assert main_file.del_ == 5

        new_file = next(f for f in timeline.files if f.path == "src/new.py")
        assert new_file.status == "new"
        assert new_file.ins == 50


class TestAggregateCommits:
    """Tests for commit aggregation from timeline events."""

    async def test_aggregates_git_events(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """Git events are listed as commits, newest first."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        await timeline_repository.add_event(
            _make_event(
                chronicle.id,
                session.id,
                t=30,
                event_type=TimelineEventType.GIT,
                label="fix(thermal): null check",
                hash="a1d3e47ff",
            )
        )
        await timeline_repository.add_event(
            _make_event(
                chronicle.id,
                session.id,
                t=60,
                event_type=TimelineEventType.GIT,
                label="feat(thermal): windup prevention",
                hash="f8c2b19aa",
            )
        )

        timeline = await chronicle_svc.get_timeline(session.id, principal=None)

        assert timeline is not None
        assert len(timeline.commits) == 2
        # Newest first
        assert timeline.commits[0].hash == "f8c2b19"
        assert timeline.commits[0].msg == "feat(thermal): windup prevention"
        assert timeline.commits[1].hash == "a1d3e47"


class TestAggregateTokenBurn:
    """Tests for token_burn aggregation from timeline events."""

    async def test_buckets_tokens_by_5_min(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """Message tokens are bucketed into 5-minute intervals."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        # Bucket 0: 0-299s
        await timeline_repository.add_event(_make_event(chronicle.id, session.id, t=10, tokens=100))
        await timeline_repository.add_event(
            _make_event(chronicle.id, session.id, t=200, tokens=200)
        )
        # Bucket 1: 300-599s
        await timeline_repository.add_event(
            _make_event(chronicle.id, session.id, t=350, tokens=500)
        )

        timeline = await chronicle_svc.get_timeline(session.id, principal=None)

        assert timeline is not None
        assert len(timeline.token_burn) == 2
        assert timeline.token_burn[0] == 300
        assert timeline.token_burn[1] == 500

    async def test_empty_token_burn_for_no_messages(
        self,
        chronicle_svc: ChronicleService,
        session_service: SessionService,
        timeline_repository: InMemoryTimelineRepository,
    ):
        """No message events results in empty token_burn."""
        session = await session_service.create_session(
            name="Test",
            model="sonnet",
            source=GitSource(
                repo="https://github.com/org/repo",
                branch="main",
            ),
        )
        chronicle = await chronicle_svc.create_chronicle(session.id, principal=None)

        # Only a non-message event
        await timeline_repository.add_event(
            _make_event(
                chronicle.id,
                session.id,
                t=10,
                event_type=TimelineEventType.SESSION,
                label="Session started",
            )
        )

        timeline = await chronicle_svc.get_timeline(session.id, principal=None)

        assert timeline is not None
        # token_burn has buckets but all zeros
        assert all(b == 0 for b in timeline.token_burn)
