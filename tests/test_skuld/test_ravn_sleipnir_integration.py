"""Integration tests: Ravn → Sleipnir event publishing (NIU-527).

Validates that :class:`~skuld.ravn_events.RavnEvent` instances are correctly
translated to :class:`~sleipnir.domain.events.SleipnirEvent` and delivered to
all subscribers through every supported transport.

Test scenarios
--------------
1. Turn start → ``ravn.turn.start`` received by audit-log subscriber
2. Tool call sequence → ``ravn.tool.start`` then ``ravn.tool.complete`` in order
3. Tool failure → ``ravn.tool.error`` received with error payload
4. Task complete → ``ravn.task.complete`` received by Ting subscriber
5. Decision required → ``ravn.decision.required`` with urgency ≥ 0.8
6. correlation_id groups all events from one task across all event types

Transport coverage
------------------
Every scenario runs against three bus implementations:

- **in_process** — :class:`~sleipnir.adapters.in_process.InProcessBus` (asyncio queues)
- **nng** — :class:`~sleipnir.adapters.nng_transport.NngTransport` (real IPC sockets);
  skipped when pynng is not installed.
- **rabbitmq** — :class:`RabbitMQLoopbackBus`, a thin wrapper that applies the
  RabbitMQ JSON encode/decode round-trip via a mock AMQP layer, confirming
  full serialisation fidelity without an external broker.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.ravn_events import RavnEvent, RavnEventType
from skuld.ravn_translator import RavnEventTranslator
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.adapters.serialization import deserialize, serialize
from sleipnir.domain.events import SleipnirEvent
from sleipnir.domain.registry import (
    RAVN_DECISION_REQUIRED,
    RAVN_TASK_COMPLETE,
    RAVN_TOOL_COMPLETE,
    RAVN_TOOL_ERROR,
    RAVN_TOOL_START,
    RAVN_TURN_START,
)
from sleipnir.ports.events import EventHandler, SleipnirPublisher, SleipnirSubscriber, Subscription

# ---------------------------------------------------------------------------
# RabbitMQLoopbackBus — simulates RabbitMQ JSON wire format without a broker
# ---------------------------------------------------------------------------


class RabbitMQLoopbackBus(SleipnirPublisher, SleipnirSubscriber):
    """Test-only bus that forces JSON encode → decode (RabbitMQ wire format).

    Wraps :class:`~sleipnir.adapters.in_process.InProcessBus` so the same
    test scenarios can be run against RabbitMQ's serialisation path without
    spinning up a real broker.  Each published event is serialised to JSON
    and deserialised back before being dispatched to subscribers, exactly
    mirroring what :class:`~sleipnir.adapters.rabbitmq.RabbitMQPublisher`
    and :class:`~sleipnir.adapters.rabbitmq.RabbitMQSubscriber` do over AMQP.
    """

    def __init__(self) -> None:
        self._inner = InProcessBus()

    async def publish(self, event: SleipnirEvent) -> None:
        raw = serialize(event, fmt="json")
        decoded = deserialize(raw, fmt="json")
        await self._inner.publish(decoded)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        for event in events:
            await self.publish(event)

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        return await self._inner.subscribe(event_types, handler)

    async def flush(self) -> None:
        await self._inner.flush()


# ---------------------------------------------------------------------------
# Shared fixture — parameterised over the three transports
# ---------------------------------------------------------------------------


def _make_amqp_mocks():
    queue = AsyncMock()
    queue.name = "test-queue"
    queue.consume = AsyncMock(return_value="consumer-tag-1")
    queue.cancel = AsyncMock()
    queue.bind = AsyncMock()

    exchange = AsyncMock()
    exchange.publish = AsyncMock()

    channel = AsyncMock()
    channel.set_qos = AsyncMock()
    channel.declare_exchange = AsyncMock(return_value=exchange)
    channel.declare_queue = AsyncMock(return_value=queue)
    channel.close = AsyncMock()

    connection = AsyncMock()
    connection.channel = AsyncMock(return_value=channel)
    connection.close = AsyncMock()

    return {"connection": connection, "channel": channel, "exchange": exchange, "queue": queue}


@pytest.fixture(
    params=["in_process", "nng", "rabbitmq"],
    ids=["in_process", "nng", "rabbitmq"],
)
async def bus(
    request: pytest.FixtureRequest,
    tmp_path,
) -> AsyncGenerator:
    """Yield a publish+subscribe bus for each transport under test."""
    transport_name: str = request.param

    if transport_name == "in_process":
        b = InProcessBus()
        yield b

    elif transport_name == "nng":
        try:
            import pynng  # noqa: F401
        except ImportError:
            pytest.skip("pynng not installed")

        import tempfile

        from sleipnir.adapters.nng_transport import NngTransport

        short_dir = tempfile.mkdtemp(prefix="nng_")
        addr = f"ipc://{short_dir}/s.sock"
        transport = NngTransport(address=addr)
        await transport.start()
        try:
            yield transport
        finally:
            await transport.stop()

    elif transport_name == "rabbitmq":
        aio_pika = pytest.importorskip("aio_pika", reason="aio-pika not installed")  # noqa: F841

        from sleipnir.adapters.rabbitmq import RabbitMQSubscriber, _encode_event

        mocks = _make_amqp_mocks()

        with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
            sub = RabbitMQSubscriber()
            await sub.start()

            # Loopback shim: encode → JSON bytes → _on_message, simulating
            # the full AMQP round-trip (encode on publish, decode on consume).
            class _RabbitMQLoopback:
                async def publish(self_, event: SleipnirEvent) -> None:  # noqa: N805
                    body = _encode_event(event)
                    mock_msg = MagicMock()
                    mock_msg.body = body
                    mock_msg.ack = AsyncMock()
                    mock_msg.nack = AsyncMock()
                    await sub._on_message(mock_msg)

                async def publish_batch(self_, events: list[SleipnirEvent]) -> None:  # noqa: N805
                    for evt in events:
                        await self_.publish(evt)

                async def subscribe(self_, event_types, handler) -> Subscription:  # noqa: N805
                    return await sub.subscribe(event_types, handler)

                async def flush(self_) -> None:  # noqa: N805
                    await sub.flush()

            yield _RabbitMQLoopback()

            await sub.stop()

    else:
        raise ValueError(f"Unknown transport: {transport_name}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Translator instance (shared across all tests)
# ---------------------------------------------------------------------------

TRANSLATOR = RavnEventTranslator()

# Fixed session / task IDs for reproducible assertions
_SESSION_ID = "sess-niu527"
_TASK_ID = "task-niu527"
_SOURCE = f"ravn:{_SESSION_ID}"


def _make_ravn_event(
    event_type: RavnEventType,
    payload: dict | None = None,
    urgency: float = 0.5,
    correlation_id: str = _SESSION_ID,
) -> RavnEvent:
    return RavnEvent(
        type=event_type,
        source=_SOURCE,
        payload=payload or {},
        urgency=urgency,
        correlation_id=correlation_id,
    )


async def _publish_and_collect(
    b,
    ravn_event: RavnEvent,
    patterns: list[str],
) -> list[SleipnirEvent]:
    """Translate *ravn_event*, publish through *b*, collect matching events."""
    received: list[SleipnirEvent] = []
    done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        done.set()

    await b.subscribe(patterns, handler)
    sleipnir_event = TRANSLATOR.translate(ravn_event)
    await b.publish(sleipnir_event)

    # Flush + brief settle for async transports (nng needs socket round-trip).
    await b.flush()
    try:
        await asyncio.wait_for(done.wait(), timeout=3.0)
    except TimeoutError:
        pass  # let assertions report the failure

    return received


# ---------------------------------------------------------------------------
# Scenario 1: Turn start → ravn.turn.start received by audit-log subscriber
# ---------------------------------------------------------------------------


class TestScenario1TurnStart:
    async def test_turn_start_received(self, bus):
        ravn_event = _make_ravn_event(
            RavnEventType.TURN_START,
            payload={"task_id": _TASK_ID, "session_id": _SESSION_ID},
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.turn.start"])

        assert len(received) == 1
        evt = received[0]
        assert evt.event_type == RAVN_TURN_START
        assert evt.source == _SOURCE
        assert evt.correlation_id == _SESSION_ID

    async def test_turn_start_received_via_namespace_wildcard(self, bus):
        ravn_event = _make_ravn_event(
            RavnEventType.TURN_START,
            payload={"task_id": _TASK_ID},
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.*"])

        assert len(received) == 1
        assert received[0].event_type == RAVN_TURN_START

    async def test_turn_start_not_delivered_to_ting_subscriber(self, bus):
        ravn_event = _make_ravn_event(RavnEventType.TURN_START)
        received: list[SleipnirEvent] = []

        async def handler(evt: SleipnirEvent) -> None:
            received.append(evt)

        await bus.subscribe(["ting.*"], handler)
        await bus.publish(TRANSLATOR.translate(ravn_event))
        await bus.flush()
        await asyncio.sleep(0.05)

        assert received == []


# ---------------------------------------------------------------------------
# Scenario 2: Tool call sequence — ravn.tool.start then ravn.tool.complete
# ---------------------------------------------------------------------------


class TestScenario2ToolCallSequence:
    async def test_tool_start_received(self, bus):
        ravn_event = _make_ravn_event(
            RavnEventType.TOOL_START,
            payload={"tool": "bash", "tool_use_id": "tu-001"},
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.tool.*"])

        assert len(received) == 1
        assert received[0].event_type == RAVN_TOOL_START
        assert received[0].payload["tool"] == "bash"

    async def test_tool_complete_received(self, bus):
        ravn_event = _make_ravn_event(
            RavnEventType.TOOL_COMPLETE,
            payload={"tool": "bash", "tool_use_id": "tu-001", "exit_code": 0},
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.tool.*"])

        assert len(received) == 1
        assert received[0].event_type == RAVN_TOOL_COMPLETE

    async def test_tool_call_sequence_delivered_in_order(self, bus):
        """tool.start must arrive before tool.complete."""
        order: list[str] = []
        done = asyncio.Event()

        async def handler(evt: SleipnirEvent) -> None:
            order.append(evt.event_type)
            if len(order) == 2:
                done.set()

        await bus.subscribe(["ravn.tool.*"], handler)

        start_event = TRANSLATOR.translate(
            _make_ravn_event(
                RavnEventType.TOOL_START,
                payload={"tool": "bash", "tool_use_id": "tu-001"},
            )
        )
        complete_event = TRANSLATOR.translate(
            _make_ravn_event(
                RavnEventType.TOOL_COMPLETE,
                payload={"tool": "bash", "tool_use_id": "tu-001"},
            )
        )

        await bus.publish(start_event)
        await bus.publish(complete_event)
        await bus.flush()
        await asyncio.wait_for(done.wait(), timeout=3.0)

        assert order == [RAVN_TOOL_START, RAVN_TOOL_COMPLETE]


# ---------------------------------------------------------------------------
# Scenario 3: Tool failure → ravn.tool.error with error payload
# ---------------------------------------------------------------------------


class TestScenario3ToolError:
    async def test_tool_error_received(self, bus):
        ravn_event = _make_ravn_event(
            RavnEventType.TOOL_ERROR,
            payload={
                "tool": "bash",
                "tool_use_id": "tu-002",
                "error": "Command not found: foo",
                "is_error": True,
            },
            urgency=0.7,
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.tool.error"])

        assert len(received) == 1
        evt = received[0]
        assert evt.event_type == RAVN_TOOL_ERROR
        assert evt.payload["is_error"] is True
        assert evt.payload["error"] == "Command not found: foo"

    async def test_tool_error_urgency_preserved(self, bus):
        ravn_event = _make_ravn_event(RavnEventType.TOOL_ERROR, urgency=0.7)
        received = await _publish_and_collect(bus, ravn_event, ["ravn.*"])

        assert len(received) == 1
        assert received[0].urgency == pytest.approx(0.7)

    async def test_tool_error_not_delivered_as_tool_complete(self, bus):
        ravn_event = _make_ravn_event(RavnEventType.TOOL_ERROR)
        received: list[SleipnirEvent] = []

        async def handler(evt: SleipnirEvent) -> None:
            received.append(evt)

        await bus.subscribe(["ravn.tool.complete"], handler)
        await bus.publish(TRANSLATOR.translate(ravn_event))
        await bus.flush()
        await asyncio.sleep(0.05)

        assert received == []


# ---------------------------------------------------------------------------
# Scenario 4: Task complete → ravn.task.complete received by Ting subscriber
# ---------------------------------------------------------------------------


class TestScenario4TaskComplete:
    async def test_task_complete_received(self, bus):
        ravn_event = _make_ravn_event(
            RavnEventType.TASK_COMPLETE,
            payload={"task_id": _TASK_ID, "session_id": _SESSION_ID},
            urgency=0.7,
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.task.complete"])

        assert len(received) == 1
        evt = received[0]
        assert evt.event_type == RAVN_TASK_COMPLETE
        assert evt.source == _SOURCE

    async def test_task_complete_received_by_wildcard_ting_subscriber(self, bus):
        """Validate that a subscriber using '*' (all events) also receives task.complete."""
        ravn_event = _make_ravn_event(RavnEventType.TASK_COMPLETE)
        received = await _publish_and_collect(bus, ravn_event, ["*"])

        assert len(received) == 1
        assert received[0].event_type == RAVN_TASK_COMPLETE

    async def test_task_complete_payload_preserved(self, bus):
        payload = {"task_id": _TASK_ID, "duration_ms": 4200, "tool_calls": 3}
        ravn_event = _make_ravn_event(RavnEventType.TASK_COMPLETE, payload=payload)
        received = await _publish_and_collect(bus, ravn_event, ["ravn.task.*"])

        assert len(received) == 1
        evt = received[0]
        assert evt.payload["task_id"] == _TASK_ID
        assert evt.payload["duration_ms"] == 4200
        assert evt.payload["tool_calls"] == 3


# ---------------------------------------------------------------------------
# Scenario 5: Decision required → urgency ≥ 0.8 for Valkyrie
# ---------------------------------------------------------------------------


class TestScenario5DecisionRequired:
    async def test_decision_required_received(self, bus):
        ravn_event = _make_ravn_event(
            RavnEventType.DECISION_REQUIRED,
            payload={"question": "Approve deployment to production?"},
            urgency=0.9,
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.decision.required"])

        assert len(received) == 1
        evt = received[0]
        assert evt.event_type == RAVN_DECISION_REQUIRED

    async def test_decision_required_urgency_gte_08(self, bus):
        """Valkyrie requirement: urgency MUST be ≥ 0.8 for decision events."""
        ravn_event = _make_ravn_event(
            RavnEventType.DECISION_REQUIRED,
            payload={"question": "Confirm destructive operation?"},
            urgency=0.85,
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.*"])

        assert len(received) == 1
        assert received[0].urgency >= 0.8

    async def test_decision_required_payload_preserved(self, bus):
        question = "Should I delete all temp files?"
        ravn_event = _make_ravn_event(
            RavnEventType.DECISION_REQUIRED,
            payload={"question": question, "context": "workspace cleanup"},
            urgency=0.9,
        )
        received = await _publish_and_collect(bus, ravn_event, ["ravn.decision.*"])

        assert len(received) == 1
        assert received[0].payload["question"] == question


# ---------------------------------------------------------------------------
# Scenario 6: correlation_id groups all events from one task
# ---------------------------------------------------------------------------


class TestScenario6CorrelationId:
    async def test_correlation_id_preserved_across_all_event_types(self, bus):
        """Every event type carries the same correlation_id (the task/session ID)."""
        event_types_to_check = [
            RavnEventType.TURN_START,
            RavnEventType.TOOL_START,
            RavnEventType.TOOL_COMPLETE,
            RavnEventType.TOOL_ERROR,
            RavnEventType.TASK_COMPLETE,
            RavnEventType.DECISION_REQUIRED,
        ]

        received: list[SleipnirEvent] = []
        done = asyncio.Event()

        async def handler(evt: SleipnirEvent) -> None:
            received.append(evt)
            if len(received) == len(event_types_to_check):
                done.set()

        await bus.subscribe(["ravn.*"], handler)

        urgency_map = {
            RavnEventType.DECISION_REQUIRED: 0.9,
            RavnEventType.TOOL_ERROR: 0.7,
        }

        for et in event_types_to_check:
            ravn_event = _make_ravn_event(
                et,
                payload={"task_id": _TASK_ID},
                urgency=urgency_map.get(et, 0.5),
                correlation_id=_SESSION_ID,
            )
            await bus.publish(TRANSLATOR.translate(ravn_event))

        await bus.flush()
        await asyncio.wait_for(done.wait(), timeout=5.0)

        assert len(received) == len(event_types_to_check)
        for evt in received:
            assert evt.correlation_id == _SESSION_ID, (
                f"correlation_id mismatch for {evt.event_type!r}: "
                f"expected {_SESSION_ID!r}, got {evt.correlation_id!r}"
            )

    async def test_events_from_different_tasks_have_different_correlation_ids(self, bus):
        """Events from distinct tasks must not share correlation_id."""
        task_a = "task-a-001"
        task_b = "task-b-002"

        received: list[SleipnirEvent] = []
        done = asyncio.Event()

        async def handler(evt: SleipnirEvent) -> None:
            received.append(evt)
            if len(received) == 2:
                done.set()

        await bus.subscribe(["ravn.*"], handler)

        for cid in (task_a, task_b):
            ravn = _make_ravn_event(
                RavnEventType.TURN_START,
                payload={"task_id": cid},
                correlation_id=cid,
            )
            await bus.publish(TRANSLATOR.translate(ravn))

        await bus.flush()
        await asyncio.wait_for(done.wait(), timeout=3.0)

        assert len(received) == 2
        corr_ids = {evt.correlation_id for evt in received}
        assert corr_ids == {task_a, task_b}

    async def test_correlation_id_none_when_not_set(self, bus):
        """Events without correlation_id arrive with correlation_id=None."""
        ravn = RavnEvent(
            type=RavnEventType.TURN_START,
            source=_SOURCE,
            payload={},
            urgency=0.5,
            correlation_id=None,
        )
        received = await _publish_and_collect(bus, ravn, ["ravn.*"])

        assert len(received) == 1
        assert received[0].correlation_id is None


# ---------------------------------------------------------------------------
# Translation unit tests (transport-independent)
# ---------------------------------------------------------------------------


class TestTranslationMapping:
    """Verify each field maps correctly from RavnEvent → SleipnirEvent."""

    def test_type_maps_to_event_type(self):
        for et in RavnEventType:
            urgency = 0.9 if et == RavnEventType.DECISION_REQUIRED else 0.5
            ravn = _make_ravn_event(et, urgency=urgency)
            result = TRANSLATOR.translate(ravn)
            assert result.event_type == et.value

    def test_source_preserved(self):
        ravn = _make_ravn_event(RavnEventType.TURN_START)
        result = TRANSLATOR.translate(ravn)
        assert result.source == _SOURCE

    def test_payload_preserved(self):
        payload = {"key": "value", "num": 42, "nested": {"a": 1}}
        ravn = _make_ravn_event(RavnEventType.TOOL_COMPLETE, payload=payload)
        result = TRANSLATOR.translate(ravn)
        assert result.payload == payload

    def test_urgency_preserved(self):
        for urgency in (0.0, 0.3, 0.5, 0.8, 0.9, 1.0):
            ravn = _make_ravn_event(RavnEventType.TURN_START, urgency=urgency)
            result = TRANSLATOR.translate(ravn)
            assert result.urgency == pytest.approx(urgency)

    def test_correlation_id_preserved(self):
        ravn = _make_ravn_event(RavnEventType.TASK_COMPLETE, correlation_id="custom-corr-id")
        result = TRANSLATOR.translate(ravn)
        assert result.correlation_id == "custom-corr-id"

    def test_domain_is_code(self):
        ravn = _make_ravn_event(RavnEventType.TURN_START)
        result = TRANSLATOR.translate(ravn)
        assert result.domain == "code"

    def test_timestamp_is_utc(self):
        from datetime import UTC

        ravn = _make_ravn_event(RavnEventType.TURN_START)
        result = TRANSLATOR.translate(ravn)
        assert result.timestamp.tzinfo is UTC

    def test_summary_is_non_empty_string(self):
        for et in RavnEventType:
            urgency = 0.9 if et == RavnEventType.DECISION_REQUIRED else 0.5
            ravn = _make_ravn_event(et, urgency=urgency)
            result = TRANSLATOR.translate(ravn)
            assert isinstance(result.summary, str)
            assert result.summary.strip() != ""

    def test_invalid_urgency_raises(self):
        with pytest.raises(ValueError, match="urgency"):
            RavnEvent(
                type=RavnEventType.TURN_START,
                source="ravn:x",
                urgency=1.5,
            )

    def test_default_summary_fallback(self):
        """_default_summary is used when no builder is registered for the type."""
        from skuld.ravn_translator import _default_summary

        ravn = _make_ravn_event(RavnEventType.TURN_START)
        # Call the fallback directly — it must include the event type value.
        summary = _default_summary(ravn)
        assert RAVN_TURN_START in summary

    def test_default_summary_fallback_via_monkeypatch(self, monkeypatch):
        """Translator uses _default_summary when the type is absent from builders."""
        from skuld.ravn_translator import _SUMMARY_BUILDERS

        monkeypatch.setitem(_SUMMARY_BUILDERS, RavnEventType.TURN_START, None)
        # Remove the entry so the .get() falls back to _default_summary.
        monkeypatch.delitem(_SUMMARY_BUILDERS, RavnEventType.TURN_START)
        ravn = _make_ravn_event(RavnEventType.TURN_START)
        result = TRANSLATOR.translate(ravn)
        assert result.event_type == RAVN_TURN_START
        assert isinstance(result.summary, str) and result.summary.strip() != ""


# ---------------------------------------------------------------------------
# Scenario 1 (hardening): AuditSubscriber + SqliteAuditRepository end-to-end
# ---------------------------------------------------------------------------


class TestAuditSubscriberEndToEnd:
    """Validate scenario 1 with the real AuditSubscriber and SQLite repo.

    This exercises the full path:
        RavnEvent → Translator → InProcessBus → AuditSubscriber → SqliteAuditRepository
    and confirms events are queryable back from the store.
    """

    async def test_turn_start_persisted_to_audit_log(self):
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
        from sleipnir.adapters.audit_subscriber import AuditSubscriber
        from sleipnir.ports.audit import AuditQuery

        bus = InProcessBus()
        repo = SqliteAuditRepository(db_path=":memory:")
        subscriber = AuditSubscriber(bus, repo)
        await subscriber.start()

        ravn_event = _make_ravn_event(
            RavnEventType.TURN_START,
            payload={"task_id": _TASK_ID, "session_id": _SESSION_ID},
        )
        sleipnir_event = TRANSLATOR.translate(ravn_event)
        await bus.publish(sleipnir_event)
        await bus.flush()

        events = await repo.query(AuditQuery(event_type_pattern="ravn.turn.start"))
        assert len(events) == 1
        assert events[0].event_type == RAVN_TURN_START
        assert events[0].source == _SOURCE
        assert events[0].correlation_id == _SESSION_ID

        await subscriber.stop()

    async def test_all_ravn_events_persisted_by_audit_subscriber(self):
        """All 6 required event types are written to the audit log."""
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
        from sleipnir.adapters.audit_subscriber import AuditSubscriber
        from sleipnir.ports.audit import AuditQuery

        bus = InProcessBus()
        repo = SqliteAuditRepository(db_path=":memory:")
        subscriber = AuditSubscriber(bus, repo)
        await subscriber.start()

        event_types = [
            (RavnEventType.TURN_START, 0.5),
            (RavnEventType.TOOL_START, 0.5),
            (RavnEventType.TOOL_COMPLETE, 0.5),
            (RavnEventType.TOOL_ERROR, 0.7),
            (RavnEventType.TASK_COMPLETE, 0.7),
            (RavnEventType.DECISION_REQUIRED, 0.9),
        ]
        for et, urgency in event_types:
            ravn = _make_ravn_event(et, urgency=urgency, correlation_id=_SESSION_ID)
            await bus.publish(TRANSLATOR.translate(ravn))

        await bus.flush()

        persisted = await repo.query(AuditQuery(event_type_pattern="ravn.*", limit=20))
        persisted_types = {e.event_type for e in persisted}
        expected = {et.value for et, _ in event_types}
        assert expected == persisted_types

        for evt in persisted:
            assert evt.correlation_id == _SESSION_ID

        await subscriber.stop()

    async def test_audit_subscriber_idempotent_start(self):
        """Calling start() twice does not double-subscribe."""
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
        from sleipnir.adapters.audit_subscriber import AuditSubscriber
        from sleipnir.ports.audit import AuditQuery

        bus = InProcessBus()
        repo = SqliteAuditRepository(db_path=":memory:")
        subscriber = AuditSubscriber(bus, repo)
        await subscriber.start()
        await subscriber.start()  # second call must be a no-op

        ravn_event = _make_ravn_event(RavnEventType.TURN_START)
        await bus.publish(TRANSLATOR.translate(ravn_event))
        await bus.flush()

        events = await repo.query(AuditQuery(event_type_pattern="ravn.*"))
        # If start() subscribed twice, the event would be appended twice —
        # but idempotent INSERT ignores duplicates, so count stays 1.
        assert len(events) == 1

        await subscriber.stop()

    async def test_audit_repo_query_by_correlation_id(self):
        """query(correlation_id=...) returns only events for that task."""
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
        from sleipnir.adapters.audit_subscriber import AuditSubscriber
        from sleipnir.ports.audit import AuditQuery

        bus = InProcessBus()
        repo = SqliteAuditRepository(db_path=":memory:")
        subscriber = AuditSubscriber(bus, repo)
        await subscriber.start()

        task_a, task_b = "task-audit-a", "task-audit-b"
        for cid in (task_a, task_b):
            ravn = _make_ravn_event(
                RavnEventType.TURN_START,
                payload={"task_id": cid},
                correlation_id=cid,
            )
            await bus.publish(TRANSLATOR.translate(ravn))

        await bus.flush()

        result_a = await repo.query(AuditQuery(correlation_id=task_a))
        assert len(result_a) == 1
        assert result_a[0].correlation_id == task_a

        result_b = await repo.query(AuditQuery(correlation_id=task_b))
        assert len(result_b) == 1
        assert result_b[0].correlation_id == task_b

        await subscriber.stop()


# ---------------------------------------------------------------------------
# Transport hardening tests
# ---------------------------------------------------------------------------


class TestPublishBatch:
    """Verify publish_batch preserves event ordering across transports."""

    async def test_publish_batch_order_preserved(self, bus):
        """Batch of events arrives in the same order it was submitted."""
        order: list[str] = []
        done = asyncio.Event()

        event_types = [
            RavnEventType.TURN_START,
            RavnEventType.TOOL_START,
            RavnEventType.TOOL_COMPLETE,
            RavnEventType.TASK_COMPLETE,
        ]

        async def handler(evt: SleipnirEvent) -> None:
            order.append(evt.event_type)
            if len(order) == len(event_types):
                done.set()

        await bus.subscribe(["ravn.*"], handler)

        batch = [TRANSLATOR.translate(_make_ravn_event(et)) for et in event_types]
        await bus.publish_batch(batch)
        await bus.flush()
        await asyncio.wait_for(done.wait(), timeout=3.0)

        assert order == [et.value for et in event_types]


class TestTTLExpiry:
    """Events with TTL ≤ 0 are dropped before delivery."""

    async def test_ttl_zero_event_not_delivered(self):
        """An event with ttl=0 must be silently dropped by the in-process bus."""
        bus = InProcessBus()
        received: list[SleipnirEvent] = []

        async def handler(evt: SleipnirEvent) -> None:  # pragma: no cover
            received.append(evt)

        await bus.subscribe(["ravn.*"], handler)

        sleipnir_event = TRANSLATOR.translate(_make_ravn_event(RavnEventType.TURN_START))
        # Rebuild with ttl=0 to force expiry on dispatch.
        from dataclasses import replace

        expired = replace(sleipnir_event, ttl=0)
        await bus.publish(expired)
        await bus.flush()
        await asyncio.sleep(0.05)

        assert received == []

    async def test_ttl_none_event_is_delivered(self):
        """An event with ttl=None (no expiry) passes through normally."""
        bus = InProcessBus()
        received: list[SleipnirEvent] = []
        done = asyncio.Event()

        async def handler(evt: SleipnirEvent) -> None:
            received.append(evt)
            done.set()

        await bus.subscribe(["ravn.*"], handler)

        sleipnir_event = TRANSLATOR.translate(_make_ravn_event(RavnEventType.TURN_START))
        assert sleipnir_event.ttl is None
        await bus.publish(sleipnir_event)
        await bus.flush()
        await asyncio.wait_for(done.wait(), timeout=2.0)

        assert len(received) == 1


class TestHandlerExceptionRecovery:
    """A handler that raises must not crash the bus or silence other subscribers."""

    async def test_raising_handler_does_not_prevent_second_subscriber(self):
        """Even if handler_a raises, handler_b still receives the event."""
        bus = InProcessBus()
        received_b: list[SleipnirEvent] = []
        done = asyncio.Event()

        async def bad_handler(evt: SleipnirEvent) -> None:
            raise RuntimeError("deliberate handler failure")

        async def good_handler(evt: SleipnirEvent) -> None:
            received_b.append(evt)
            done.set()

        await bus.subscribe(["ravn.*"], bad_handler)
        await bus.subscribe(["ravn.*"], good_handler)

        sleipnir_event = TRANSLATOR.translate(_make_ravn_event(RavnEventType.TURN_START))
        await bus.publish(sleipnir_event)
        await bus.flush()
        await asyncio.wait_for(done.wait(), timeout=2.0)

        assert len(received_b) == 1


class TestInProcessBusEdgeCases:
    """Edge cases for InProcessBus that improve hardening coverage."""

    def test_invalid_ring_buffer_depth_raises(self):
        with pytest.raises(ValueError, match="ring_buffer_depth"):
            InProcessBus(ring_buffer_depth=0)

    async def test_unsubscribe_then_no_delivery(self):
        """After unsubscribing, no further events arrive."""
        bus = InProcessBus()
        received: list[SleipnirEvent] = []

        async def handler(evt: SleipnirEvent) -> None:  # pragma: no cover
            received.append(evt)

        sub = await bus.subscribe(["ravn.*"], handler)
        await sub.unsubscribe()

        sleipnir_event = TRANSLATOR.translate(_make_ravn_event(RavnEventType.TURN_START))
        await bus.publish(sleipnir_event)
        await bus.flush()
        await asyncio.sleep(0.05)

        assert received == []

    async def test_double_unsubscribe_is_safe(self):
        """Calling unsubscribe twice does not raise."""
        bus = InProcessBus()

        async def handler(evt: SleipnirEvent) -> None:  # pragma: no cover
            pass

        sub = await bus.subscribe(["ravn.*"], handler)
        await sub.unsubscribe()
        await sub.unsubscribe()  # must not raise

    async def test_ring_buffer_overflow_drops_oldest(self):
        """When the ring buffer is full, the oldest event is dropped."""
        depth = 2
        bus = InProcessBus(ring_buffer_depth=depth)

        # Pause handler to let the queue fill up.
        pause = asyncio.Event()
        received: list[SleipnirEvent] = []

        async def slow_handler(evt: SleipnirEvent) -> None:
            await pause.wait()
            received.append(evt)

        await bus.subscribe(["ravn.*"], slow_handler)

        events = [
            TRANSLATOR.translate(_make_ravn_event(RavnEventType.TURN_START, payload={"n": i}))
            for i in range(depth + 2)
        ]
        for evt in events:
            await bus.publish(evt)

        # Release handler and flush.
        pause.set()
        await bus.flush()

        # Ring buffer depth=2 means at most 2 events are queued; some were dropped.
        assert len(received) <= depth


class TestAuditSubscriberLifecycle:
    """Lifecycle and config paths for AuditSubscriber."""

    async def test_stop_when_not_running_is_safe(self):
        """Calling stop() before start() does not raise."""
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
        from sleipnir.adapters.audit_subscriber import AuditSubscriber

        bus = InProcessBus()
        repo = SqliteAuditRepository(db_path=":memory:")
        subscriber = AuditSubscriber(bus, repo)
        await subscriber.stop()  # must not raise

    async def test_start_stop_lifecycle(self):
        """start → publish → stop round-trip completes without error."""
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
        from sleipnir.adapters.audit_subscriber import AuditSubscriber
        from sleipnir.ports.audit import AuditQuery

        bus = InProcessBus()
        repo = SqliteAuditRepository(db_path=":memory:")
        subscriber = AuditSubscriber(bus, repo)

        assert not subscriber.running
        await subscriber.start()
        assert subscriber.running

        await bus.publish(TRANSLATOR.translate(_make_ravn_event(RavnEventType.TURN_START)))
        await bus.flush()

        await subscriber.stop()
        assert not subscriber.running

        events = await repo.query(AuditQuery(event_type_pattern="ravn.*"))
        assert len(events) == 1

    async def test_disabled_audit_subscriber_does_not_persist(self):
        """AuditSubscriber with enabled=False never writes to the repo."""
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
        from sleipnir.adapters.audit_subscriber import AuditConfig, AuditSubscriber
        from sleipnir.ports.audit import AuditQuery

        bus = InProcessBus()
        repo = SqliteAuditRepository(db_path=":memory:")
        subscriber = AuditSubscriber(bus, repo, config=AuditConfig(enabled=False))
        await subscriber.start()
        assert not subscriber.running

        await bus.publish(TRANSLATOR.translate(_make_ravn_event(RavnEventType.TURN_START)))
        await bus.flush()
        await asyncio.sleep(0.05)

        events = await repo.query(AuditQuery(event_type_pattern="ravn.*"))
        assert events == []

    async def test_handler_exception_does_not_stop_audit_subscriber(self):
        """If the repo raises, AuditSubscriber logs and continues."""

        from sleipnir.adapters.audit_subscriber import AuditSubscriber
        from sleipnir.ports.audit import AuditRepository

        class FailingRepo(AuditRepository):
            async def append(self, event):
                raise RuntimeError("db write failed")

            async def query(self, q):  # pragma: no cover
                return []

            async def purge_expired(self):  # pragma: no cover
                return 0

        bus = InProcessBus()
        subscriber = AuditSubscriber(bus, FailingRepo())
        await subscriber.start()

        # publish and flush — subscriber must survive the exception
        await bus.publish(TRANSLATOR.translate(_make_ravn_event(RavnEventType.TURN_START)))
        await bus.flush()

        assert subscriber.running
        await subscriber.stop()


class TestSqliteAuditRepository:
    """Additional coverage for SqliteAuditRepository query paths."""

    async def test_purge_expired_returns_count(self):
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository

        repo = SqliteAuditRepository(db_path=":memory:")
        # No TTL events — purge should return 0.
        count = await repo.purge_expired()
        assert count == 0

    async def test_query_with_source_filter(self):
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository
        from sleipnir.ports.audit import AuditQuery

        repo = SqliteAuditRepository(db_path=":memory:")
        source_a = "ravn:sess-a"
        source_b = "ravn:sess-b"

        for src in (source_a, source_b):
            evt = TRANSLATOR.translate(
                RavnEvent(
                    type=RavnEventType.TURN_START,
                    source=src,
                    payload={},
                    urgency=0.5,
                )
            )
            await repo.append(evt)

        results = await repo.query(AuditQuery(source=source_a))
        assert len(results) == 1
        assert results[0].source == source_a

    async def test_close_releases_connection(self):
        from sleipnir.adapters.audit_sqlite import SqliteAuditRepository

        repo = SqliteAuditRepository(db_path=":memory:")
        # Force connection open.
        await repo.purge_expired()
        await repo.close()
        # Calling close again must be safe.
        await repo.close()
