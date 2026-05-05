"""Tests for RabbitMQEventSink adapter.

Since aio-pika is an optional dependency, these tests mock the AMQP
connection and verify correct message routing, serialization, and
lifecycle management.
"""

import pytest

pytest.importorskip("aio_pika", reason="aio-pika not installed")

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from volundr.domain.models import SessionEvent, SessionEventType


def _make_event(**overrides) -> SessionEvent:
    defaults = {
        "id": uuid4(),
        "session_id": uuid4(),
        "event_type": SessionEventType.MESSAGE_ASSISTANT,
        "timestamp": datetime.now(UTC),
        "data": {"content_preview": "hello"},
        "sequence": 0,
        "tokens_in": 100,
        "tokens_out": 50,
        "cost": Decimal("0.003"),
        "model": "claude-sonnet-4-20250514",
    }
    defaults.update(overrides)
    return SessionEvent(**defaults)


def _make_sink():
    """Create RabbitMQEventSink with mocked internals (no real connection)."""
    from volundr.adapters.outbound.rabbitmq_event_sink import RabbitMQEventSink

    sink = RabbitMQEventSink(
        url="amqp://guest:guest@localhost:5672/",
        exchange_name="test.events",
        exchange_type="topic",
    )
    # Manually set internal state as if connect() succeeded
    mock_exchange = AsyncMock()
    sink._exchange = mock_exchange
    sink._connection = AsyncMock()
    sink._channel = AsyncMock()
    sink._healthy = True

    return sink, mock_exchange


class TestRabbitMQSinkProperties:
    """Tests for sink properties."""

    def test_sink_name(self):
        sink, _ = _make_sink()
        assert sink.sink_name == "rabbitmq"

    def test_healthy_after_connect(self):
        sink, _ = _make_sink()
        assert sink.healthy is True

    def test_unhealthy_before_connect(self):
        from volundr.adapters.outbound.rabbitmq_event_sink import RabbitMQEventSink

        sink = RabbitMQEventSink(url="amqp://localhost/")
        assert sink.healthy is False


class TestRabbitMQConnect:
    """Tests for connection lifecycle."""

    async def test_connect_declares_exchange(self):
        from volundr.adapters.outbound.rabbitmq_event_sink import RabbitMQEventSink

        mock_connection = AsyncMock()
        mock_channel = AsyncMock()
        mock_exchange = AsyncMock()
        mock_connection.channel.return_value = mock_channel
        mock_channel.declare_exchange.return_value = mock_exchange

        with patch("aio_pika.connect_robust", return_value=mock_connection):
            sink = RabbitMQEventSink(
                url="amqp://localhost/",
                exchange_name="my.exchange",
                exchange_type="topic",
            )
            await sink.connect()

        assert sink.healthy is True
        mock_channel.declare_exchange.assert_called_once()
        call_args = mock_channel.declare_exchange.call_args
        assert call_args[0][0] == "my.exchange"

    async def test_close_disconnects(self):
        sink, _ = _make_sink()
        mock_conn = sink._connection

        await sink.close()

        assert sink.healthy is False
        mock_conn.close.assert_called_once()
        assert sink._connection is None
        assert sink._channel is None
        assert sink._exchange is None


class TestRabbitMQEmit:
    """Tests for event emission."""

    async def test_emit_publishes_message(self):
        sink, exchange = _make_sink()
        event = _make_event()

        await sink.emit(event)

        exchange.publish.assert_called_once()
        call_args = exchange.publish.call_args
        assert call_args[1]["routing_key"] == "session.message_assistant"

    async def test_emit_routing_key_matches_event_type(self):
        sink, exchange = _make_sink()

        for event_type in [
            SessionEventType.FILE_MODIFIED,
            SessionEventType.GIT_COMMIT,
            SessionEventType.TERMINAL_COMMAND,
            SessionEventType.SESSION_START,
        ]:
            exchange.reset_mock()
            event = _make_event(event_type=event_type)
            await sink.emit(event)
            routing_key = exchange.publish.call_args[1]["routing_key"]
            assert routing_key == f"session.{event_type.value}"

    async def test_emit_skips_when_not_connected(self):
        from volundr.adapters.outbound.rabbitmq_event_sink import RabbitMQEventSink

        sink = RabbitMQEventSink(url="amqp://localhost/")
        event = _make_event()

        # Should not raise, just log warning
        await sink.emit(event)

    async def test_emit_batch_publishes_all(self):
        sink, exchange = _make_sink()
        events = [_make_event(sequence=i) for i in range(3)]

        await sink.emit_batch(events)

        assert exchange.publish.call_count == 3

    async def test_emit_batch_skips_when_not_connected(self):
        from volundr.adapters.outbound.rabbitmq_event_sink import RabbitMQEventSink

        sink = RabbitMQEventSink(url="amqp://localhost/")
        events = [_make_event(sequence=i) for i in range(3)]

        # Should not raise
        await sink.emit_batch(events)


class TestRabbitMQSerialization:
    """Tests for message serialization."""

    def test_serialize_produces_valid_json(self):
        from volundr.adapters.outbound.rabbitmq_event_sink import RabbitMQEventSink

        event = _make_event()
        body = RabbitMQEventSink._serialize(event)

        parsed = json.loads(body)
        assert parsed["event_type"] == "message_assistant"
        assert parsed["session_id"] == str(event.session_id)
        assert parsed["tokens_in"] == 100
        assert parsed["tokens_out"] == 50
        assert parsed["cost"] == 0.003
        assert parsed["model"] == "claude-sonnet-4-20250514"

    def test_serialize_handles_none_cost(self):
        from volundr.adapters.outbound.rabbitmq_event_sink import RabbitMQEventSink

        event = _make_event(cost=None, tokens_in=None, tokens_out=None)
        body = RabbitMQEventSink._serialize(event)

        parsed = json.loads(body)
        assert parsed["cost"] is None
        assert parsed["tokens_in"] is None
        assert parsed["tokens_out"] is None

    def test_serialize_includes_data_payload(self):
        from volundr.adapters.outbound.rabbitmq_event_sink import RabbitMQEventSink

        event = _make_event(data={"path": "/src/main.py", "insertions": 10, "deletions": 5})
        body = RabbitMQEventSink._serialize(event)

        parsed = json.loads(body)
        assert parsed["data"]["path"] == "/src/main.py"
        assert parsed["data"]["insertions"] == 10


class TestRabbitMQFlush:
    """Tests for flush (no-op for AMQP)."""

    async def test_flush_is_noop(self):
        sink, _ = _make_sink()
        # Should not raise
        await sink.flush()
