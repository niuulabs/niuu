"""Tests for the InMemoryEventBroadcaster adapter."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from volundr.adapters.outbound.broadcaster import InMemoryEventBroadcaster
from volundr.domain.models import (
    CommitSummary,
    EventType,
    FileSummary,
    GitSource,
    RealtimeEvent,
    Session,
    SessionStatus,
    Stats,
    TimelineEvent,
    TimelineEventType,
    TimelineResponse,
)


@pytest.fixture
def broadcaster() -> InMemoryEventBroadcaster:
    """Create a broadcaster for testing."""
    return InMemoryEventBroadcaster(max_queue_size=10)


@pytest.fixture
def sample_session() -> Session:
    """Create a sample session for testing."""
    return Session(
        id=uuid4(),
        name="Test Session",
        model="claude-sonnet-4-20250514",
        source=GitSource(repo="https://github.com/test/repo", branch="main"),
        status=SessionStatus.RUNNING,
    )


@pytest.fixture
def sample_stats() -> Stats:
    """Create sample stats for testing."""
    return Stats(
        active_sessions=5,
        total_sessions=10,
        tokens_today=1000,
        local_tokens=200,
        cloud_tokens=800,
        cost_today=Decimal("0.50"),
    )


class TestInMemoryEventBroadcaster:
    """Tests for InMemoryEventBroadcaster."""

    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self, broadcaster: InMemoryEventBroadcaster):
        """Test that published events are received by subscribers."""
        event = RealtimeEvent(
            type=EventType.HEARTBEAT,
            data={},
            timestamp=datetime.now(UTC),
        )

        received_events: list[RealtimeEvent] = []

        async def collect_events():
            async for e in broadcaster.subscribe():
                received_events.append(e)
                break  # Only collect one event

        # Start subscriber task
        subscriber_task = asyncio.create_task(collect_events())

        # Give subscriber time to register
        await asyncio.sleep(0.01)

        # Publish event
        await broadcaster.publish(event)

        # Wait for subscriber to receive
        await asyncio.wait_for(subscriber_task, timeout=1.0)

        assert len(received_events) == 1
        assert received_events[0].type == EventType.HEARTBEAT

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, broadcaster: InMemoryEventBroadcaster):
        """Test that multiple subscribers receive the same event."""
        event = RealtimeEvent(
            type=EventType.HEARTBEAT,
            data={"test": "data"},
            timestamp=datetime.now(UTC),
        )

        received_1: list[RealtimeEvent] = []
        received_2: list[RealtimeEvent] = []

        async def collect_events(storage: list):
            async for e in broadcaster.subscribe():
                storage.append(e)
                break

        # Start two subscribers
        task1 = asyncio.create_task(collect_events(received_1))
        task2 = asyncio.create_task(collect_events(received_2))

        await asyncio.sleep(0.01)

        # Verify subscriber count
        assert broadcaster.subscriber_count == 2

        # Publish event
        await broadcaster.publish(event)

        # Wait for both subscribers
        await asyncio.wait_for(asyncio.gather(task1, task2), timeout=1.0)

        assert len(received_1) == 1
        assert len(received_2) == 1
        assert received_1[0].type == EventType.HEARTBEAT
        assert received_2[0].type == EventType.HEARTBEAT

    @pytest.mark.asyncio
    async def test_subscriber_cleanup_on_cancel(self, broadcaster: InMemoryEventBroadcaster):
        """Test that subscribers are cleaned up when cancelled."""

        async def long_running_subscriber():
            async for _ in broadcaster.subscribe():
                pass

        task = asyncio.create_task(long_running_subscriber())
        await asyncio.sleep(0.01)

        assert broadcaster.subscriber_count == 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected: task was intentionally cancelled

        # Give time for cleanup
        await asyncio.sleep(0.01)

        assert broadcaster.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_publish_session_created(
        self,
        broadcaster: InMemoryEventBroadcaster,
        sample_session: Session,
    ):
        """Test publishing session created event."""
        received: list[RealtimeEvent] = []

        async def collect():
            async for e in broadcaster.subscribe():
                received.append(e)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        await broadcaster.publish_session_created(sample_session)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].type == EventType.SESSION_CREATED
        assert received[0].data["id"] == str(sample_session.id)
        assert received[0].data["name"] == sample_session.name
        assert received[0].data["status"] == sample_session.status.value

    @pytest.mark.asyncio
    async def test_publish_session_updated(
        self,
        broadcaster: InMemoryEventBroadcaster,
        sample_session: Session,
    ):
        """Test publishing session updated event."""
        received: list[RealtimeEvent] = []

        async def collect():
            async for e in broadcaster.subscribe():
                received.append(e)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        await broadcaster.publish_session_updated(sample_session)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].type == EventType.SESSION_UPDATED
        assert received[0].data["id"] == str(sample_session.id)

    @pytest.mark.asyncio
    async def test_publish_session_deleted(self, broadcaster: InMemoryEventBroadcaster):
        """Test publishing session deleted event."""
        session_id = uuid4()
        received: list[RealtimeEvent] = []

        async def collect():
            async for e in broadcaster.subscribe():
                received.append(e)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        await broadcaster.publish_session_deleted(session_id)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].type == EventType.SESSION_DELETED
        assert received[0].data["id"] == str(session_id)

    @pytest.mark.asyncio
    async def test_publish_stats(
        self,
        broadcaster: InMemoryEventBroadcaster,
        sample_stats: Stats,
    ):
        """Test publishing stats update event."""
        received: list[RealtimeEvent] = []

        async def collect():
            async for e in broadcaster.subscribe():
                received.append(e)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        await broadcaster.publish_stats(sample_stats)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].type == EventType.STATS_UPDATED
        assert received[0].data["active_sessions"] == sample_stats.active_sessions
        assert received[0].data["total_sessions"] == sample_stats.total_sessions
        assert received[0].data["tokens_today"] == sample_stats.tokens_today

    @pytest.mark.asyncio
    async def test_publish_heartbeat(self, broadcaster: InMemoryEventBroadcaster):
        """Test publishing heartbeat event."""
        received: list[RealtimeEvent] = []

        async def collect():
            async for e in broadcaster.subscribe():
                received.append(e)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        await broadcaster.publish_heartbeat()

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].type == EventType.HEARTBEAT
        assert received[0].data == {}

    @pytest.mark.asyncio
    async def test_queue_overflow_drops_old_events(self):
        """Test that queue overflow drops oldest events."""
        broadcaster = InMemoryEventBroadcaster(max_queue_size=2)

        received: list[RealtimeEvent] = []

        async def collect():
            async for e in broadcaster.subscribe():
                received.append(e)
                if len(received) == 2:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        # Publish 3 events but queue size is 2
        for i in range(3):
            event = RealtimeEvent(
                type=EventType.HEARTBEAT,
                data={"index": i},
                timestamp=datetime.now(UTC),
            )
            await broadcaster.publish(event)

        await asyncio.wait_for(task, timeout=1.0)

        # Should only receive the last 2 events (oldest dropped)
        assert len(received) == 2
        assert received[0].data["index"] == 1
        assert received[1].data["index"] == 2

    @pytest.mark.asyncio
    async def test_create_session_event(
        self,
        broadcaster: InMemoryEventBroadcaster,
        sample_session: Session,
    ):
        """Test creating a session event."""
        event = broadcaster.create_session_event(EventType.SESSION_CREATED, sample_session)

        assert event.type == EventType.SESSION_CREATED
        assert event.data["id"] == str(sample_session.id)
        assert event.data["name"] == sample_session.name
        assert event.data["model"] == sample_session.model
        assert event.data["repo"] == sample_session.repo
        assert event.data["branch"] == sample_session.branch
        assert event.data["status"] == sample_session.status.value

    @pytest.mark.asyncio
    async def test_create_stats_event(
        self,
        broadcaster: InMemoryEventBroadcaster,
        sample_stats: Stats,
    ):
        """Test creating a stats event."""
        event = broadcaster.create_stats_event(sample_stats)

        assert event.type == EventType.STATS_UPDATED
        assert event.data["active_sessions"] == sample_stats.active_sessions
        assert event.data["cost_today"] == float(sample_stats.cost_today)

    @pytest.mark.asyncio
    async def test_create_heartbeat_event(self, broadcaster: InMemoryEventBroadcaster):
        """Test creating a heartbeat event."""
        event = broadcaster.create_heartbeat_event()

        assert event.type == EventType.HEARTBEAT
        assert event.data == {}
        assert event.timestamp is not None

    @pytest.mark.asyncio
    async def test_no_subscribers_publish_still_works(self, broadcaster: InMemoryEventBroadcaster):
        """Test that publishing with no subscribers doesn't error."""
        assert broadcaster.subscriber_count == 0

        # Should not raise
        await broadcaster.publish_heartbeat()

    @pytest.mark.asyncio
    async def test_dead_queue_removal(self, broadcaster: InMemoryEventBroadcaster):
        """Test that broken queues are detected and removed during publish."""
        # Create a mock queue that raises on put_nowait
        dead_queue: asyncio.Queue[RealtimeEvent] = MagicMock(spec=asyncio.Queue)
        dead_queue.full.return_value = False
        dead_queue.put_nowait.side_effect = RuntimeError("broken queue")

        # Inject the dead queue directly into subscribers
        broadcaster._subscribers.add(dead_queue)
        assert broadcaster.subscriber_count == 1

        event = RealtimeEvent(
            type=EventType.HEARTBEAT,
            data={},
            timestamp=datetime.now(UTC),
        )

        # Publish should not raise, but should remove the dead queue
        await broadcaster.publish(event)

        assert broadcaster.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_queue_empty_race_condition(self, broadcaster: InMemoryEventBroadcaster):
        """Test QueueEmpty race between full() check and get_nowait()."""
        # Create a mock queue where full() returns True but get_nowait raises QueueEmpty
        race_queue: asyncio.Queue[RealtimeEvent] = MagicMock(spec=asyncio.Queue)
        race_queue.full.return_value = True
        race_queue.get_nowait.side_effect = asyncio.QueueEmpty()
        race_queue.put_nowait.return_value = None

        broadcaster._subscribers.add(race_queue)

        event = RealtimeEvent(
            type=EventType.HEARTBEAT,
            data={},
            timestamp=datetime.now(UTC),
        )

        # Should handle the QueueEmpty gracefully and still put the event
        await broadcaster.publish(event)

        race_queue.get_nowait.assert_called_once()
        race_queue.put_nowait.assert_called_once_with(event)
        # Queue should still be a subscriber (not removed)
        assert broadcaster.subscriber_count == 1

    @pytest.mark.asyncio
    async def test_publish_chronicle_event_all_fields(self, broadcaster: InMemoryEventBroadcaster):
        """Test publish_chronicle_event with all optional fields populated."""
        session_id = uuid4()
        chronicle_id = uuid4()
        event_id = uuid4()

        timeline_event = TimelineEvent(
            id=event_id,
            chronicle_id=chronicle_id,
            session_id=session_id,
            t=42,
            type=TimelineEventType.FILE,
            label="src/main.py",
            tokens=150,
            action="modified",
            ins=10,
            del_=3,
            hash="abc1234",
            exit_code=0,
        )

        timeline = TimelineResponse(
            events=[timeline_event],
            files=[
                FileSummary(path="src/main.py", status="mod", ins=10, del_=3),
                FileSummary(path="src/utils.py", status="new", ins=25, del_=0),
            ],
            commits=[
                CommitSummary(hash="abc1234", msg="fix bug", time="14:35"),
            ],
            token_burn=[0, 50, 100, 150],
        )

        received: list[RealtimeEvent] = []

        async def collect():
            async for e in broadcaster.subscribe():
                received.append(e)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        await broadcaster.publish_chronicle_event(session_id, timeline_event, timeline)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        evt = received[0]
        assert evt.type == EventType.CHRONICLE_EVENT
        assert evt.data["session_id"] == str(session_id)

        # Verify event data includes all optional fields
        event_data = evt.data["event"]
        assert event_data["t"] == 42
        assert event_data["type"] == "file"
        assert event_data["label"] == "src/main.py"
        assert event_data["tokens"] == 150
        assert event_data["action"] == "modified"
        assert event_data["ins"] == 10
        assert event_data["del"] == 3
        assert event_data["hash"] == "abc1234"
        assert event_data["exit"] == 0

        # Verify files
        assert len(evt.data["files"]) == 2
        assert evt.data["files"][0] == {
            "path": "src/main.py",
            "status": "mod",
            "ins": 10,
            "del": 3,
        }
        assert evt.data["files"][1] == {
            "path": "src/utils.py",
            "status": "new",
            "ins": 25,
            "del": 0,
        }

        # Verify commits
        assert len(evt.data["commits"]) == 1
        assert evt.data["commits"][0] == {
            "hash": "abc1234",
            "msg": "fix bug",
            "time": "14:35",
        }

        # Verify token_burn
        assert evt.data["token_burn"] == [0, 50, 100, 150]

    @pytest.mark.asyncio
    async def test_publish_chronicle_event_minimal_fields(
        self, broadcaster: InMemoryEventBroadcaster
    ):
        """Test publish_chronicle_event with only required fields (no optional)."""
        session_id = uuid4()

        timeline_event = TimelineEvent(
            id=uuid4(),
            chronicle_id=uuid4(),
            session_id=session_id,
            t=0,
            type=TimelineEventType.SESSION,
            label="session started",
        )

        timeline = TimelineResponse(
            events=[timeline_event],
            files=[],
            commits=[],
            token_burn=[],
        )

        received: list[RealtimeEvent] = []

        async def collect():
            async for e in broadcaster.subscribe():
                received.append(e)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)

        await broadcaster.publish_chronicle_event(session_id, timeline_event, timeline)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        evt = received[0]
        assert evt.type == EventType.CHRONICLE_EVENT

        # Verify event data has only required fields (no optional keys)
        event_data = evt.data["event"]
        assert event_data["t"] == 0
        assert event_data["type"] == "session"
        assert event_data["label"] == "session started"
        assert "tokens" not in event_data
        assert "action" not in event_data
        assert "ins" not in event_data
        assert "del" not in event_data
        assert "hash" not in event_data
        assert "exit" not in event_data

        # Verify empty lists
        assert evt.data["files"] == []
        assert evt.data["commits"] == []
        assert evt.data["token_burn"] == []


# ---------------------------------------------------------------------------
# Sleipnir forwarding tests
# ---------------------------------------------------------------------------


class TestInMemoryEventBroadcasterSleipnirForwarding:
    """Tests for the optional Sleipnir publisher integration."""

    @pytest.mark.asyncio
    async def test_no_sleipnir_publisher_no_forward(self):
        """When no publisher is configured, no Sleipnir call is made."""
        publisher = AsyncMock()
        b = InMemoryEventBroadcaster(max_queue_size=10)
        event = RealtimeEvent(
            type=EventType.SESSION_CREATED,
            data={"id": str(uuid4())},
            timestamp=datetime.now(UTC),
        )

        await b.publish(event)

        publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_sleipnir_publisher_called_for_session_created(self):
        """SESSION_CREATED events are forwarded to Sleipnir."""
        publisher = AsyncMock()
        publisher.publish = AsyncMock()
        b = InMemoryEventBroadcaster(max_queue_size=10, sleipnir_publisher=publisher)
        event = RealtimeEvent(
            type=EventType.SESSION_CREATED,
            data={"id": str(uuid4())},
            timestamp=datetime.now(UTC),
        )

        await b.publish(event)

        publisher.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_sleipnir_not_called_for_heartbeat(self):
        """HEARTBEAT events are not forwarded (not in the mapping)."""
        publisher = AsyncMock()
        publisher.publish = AsyncMock()
        b = InMemoryEventBroadcaster(max_queue_size=10, sleipnir_publisher=publisher)
        event = RealtimeEvent(
            type=EventType.HEARTBEAT,
            data={},
            timestamp=datetime.now(UTC),
        )

        await b.publish(event)

        publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_sleipnir_publish_failure_is_silent(self):
        """A Sleipnir publish error does not propagate to callers."""
        publisher = AsyncMock()
        publisher.publish = AsyncMock(side_effect=RuntimeError("bus down"))
        b = InMemoryEventBroadcaster(max_queue_size=10, sleipnir_publisher=publisher)
        event = RealtimeEvent(
            type=EventType.SESSION_CREATED,
            data={},
            timestamp=datetime.now(UTC),
        )

        # Should not raise
        await b.publish(event)

    @pytest.mark.asyncio
    async def test_sleipnir_type_map_built_lazily(self):
        """The Sleipnir type map is None until first publish."""
        publisher = AsyncMock()
        publisher.publish = AsyncMock()
        b = InMemoryEventBroadcaster(max_queue_size=10, sleipnir_publisher=publisher)

        assert b._sleipnir_type_map is None

        event = RealtimeEvent(
            type=EventType.SESSION_CREATED,
            data={},
            timestamp=datetime.now(UTC),
        )
        await b.publish(event)

        assert b._sleipnir_type_map is not None

    @pytest.mark.asyncio
    async def test_sleipnir_forwarded_event_uses_correct_type(self):
        """Forwarded SleipnirEvent uses the mapped event type."""
        from sleipnir.domain import registry
        from sleipnir.domain.events import SleipnirEvent

        publisher = AsyncMock()
        publisher.publish = AsyncMock()
        b = InMemoryEventBroadcaster(max_queue_size=10, sleipnir_publisher=publisher)
        event = RealtimeEvent(
            type=EventType.SESSION_CREATED,
            data={"id": "sess-1"},
            timestamp=datetime.now(UTC),
        )

        await b.publish(event)

        arg: SleipnirEvent = publisher.publish.call_args[0][0]
        assert arg.event_type == registry.VOLUNDR_SESSION_CREATED

    @pytest.mark.asyncio
    async def test_custom_source_used_in_sleipnir_event(self):
        """The sleipnir_source string is used in the published event."""
        from sleipnir.domain.events import SleipnirEvent

        publisher = AsyncMock()
        publisher.publish = AsyncMock()
        b = InMemoryEventBroadcaster(
            max_queue_size=10,
            sleipnir_publisher=publisher,
            sleipnir_source="volundr:staging",
        )
        event = RealtimeEvent(
            type=EventType.SESSION_CREATED,
            data={},
            timestamp=datetime.now(UTC),
        )

        await b.publish(event)

        arg: SleipnirEvent = publisher.publish.call_args[0][0]
        assert arg.source == "volundr:staging"
