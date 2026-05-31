"""Tests for the RabbitMQ transport adapter (NIU-523).

Test strategy
-------------
All tests mock aio-pika so no external RabbitMQ broker is required.
The mock layer replaces ``aio_pika.connect_robust`` and all downstream
AMQP objects (channels, exchanges, queues, messages).

Coverage targets
----------------
- :func:`~sleipnir.adapters.rabbitmq._fnmatch_to_amqp` — routing key translation
- :class:`~sleipnir.adapters.rabbitmq.RabbitMQPublisher` — lifecycle, TTL, delivery mode
- :class:`~sleipnir.adapters.rabbitmq.RabbitMQSubscriber` — lifecycle, bindings, dispatch
- :class:`~sleipnir.adapters.rabbitmq.RabbitMQTransport` — combined pub+sub delegation
- Module-level helpers: ``rabbitmq_available``
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sleipnir.adapters.rabbitmq import (
    DEFAULT_AMQP_URL,
    DEFAULT_DEAD_LETTER_EXCHANGE,
    DEFAULT_DURABLE_THRESHOLD_URGENCY,
    DEFAULT_EXCHANGE_NAME,
    DEFAULT_PREFETCH_COUNT,
    DEFAULT_RING_BUFFER_DEPTH,
    RabbitMQPublisher,
    RabbitMQSubscriber,
    RabbitMQTransport,
    _decode_event,
    _encode_event,
    _fnmatch_to_amqp,
    rabbitmq_available,
)
from sleipnir.domain.events import SleipnirEvent
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Skip guard — tests require aio-pika
# ---------------------------------------------------------------------------

aio_pika = pytest.importorskip("aio_pika", reason="aio-pika not installed; skipping rabbitmq tests")


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_message(body: bytes) -> MagicMock:
    """Build a mock AMQP incoming message with the given body.

    Provides ``ack`` and ``nack`` as :class:`~unittest.mock.AsyncMock` so
    tests can assert which acknowledgement path was taken.
    """
    msg = MagicMock()
    msg.body = body
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    return msg


def _make_amqp_mocks():
    """Return a bundle of AsyncMock AMQP objects wired together.

    Returns a dict with keys: connection, channel, exchange, queue.
    """
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

    return {
        "connection": connection,
        "channel": channel,
        "exchange": exchange,
        "queue": queue,
    }


# ---------------------------------------------------------------------------
# Unit tests — _fnmatch_to_amqp
# ---------------------------------------------------------------------------


def test_fnmatch_to_amqp_star_wildcard():
    """'*' should produce '#' (subscribe to everything)."""
    assert _fnmatch_to_amqp("*") == "#"


def test_fnmatch_to_amqp_namespace_wildcard():
    """'ravn.*' → 'ravn.#' (all ravn events)."""
    assert _fnmatch_to_amqp("ravn.*") == "ravn.#"


def test_fnmatch_to_amqp_sub_namespace_wildcard():
    """'ravn.tool.*' → 'ravn.tool.#'."""
    assert _fnmatch_to_amqp("ravn.tool.*") == "ravn.tool.#"


def test_fnmatch_to_amqp_exact_match():
    """Exact event type passes through unchanged."""
    assert _fnmatch_to_amqp("ravn.tool.complete") == "ravn.tool.complete"


def test_fnmatch_to_amqp_question_mark_falls_back():
    """Pattern with '?' → '#' (unsupported by AMQP topic rules)."""
    assert _fnmatch_to_amqp("ravn.tool.?") == "#"


def test_fnmatch_to_amqp_bracket_falls_back():
    """Pattern with '[' → '#'."""
    assert _fnmatch_to_amqp("ravn.[abc]*") == "#"


def test_fnmatch_to_amqp_mid_star_falls_back():
    """Pattern with '*' not at the trailing position → '#'."""
    assert _fnmatch_to_amqp("ravn.*.complete") == "#"


def test_fnmatch_to_amqp_deep_namespace():
    """Three-level namespace wildcard is handled correctly."""
    assert _fnmatch_to_amqp("ting.saga.*") == "ting.saga.#"


# ---------------------------------------------------------------------------
# Unit tests — rabbitmq_available / ImportError guard
# ---------------------------------------------------------------------------


def test_rabbitmq_available_returns_true():
    assert rabbitmq_available() is True


def test_rabbitmq_available_false_when_not_installed():
    with patch("sleipnir.adapters.rabbitmq._AIO_PIKA_AVAILABLE", False):
        assert rabbitmq_available() is False


def test_import_error_when_aio_pika_not_available():
    """Publisher/Subscriber/Transport raise ImportError if aio-pika is absent."""
    with patch("sleipnir.adapters.rabbitmq._AIO_PIKA_AVAILABLE", False):
        with pytest.raises(ImportError, match="aio-pika"):
            RabbitMQPublisher()
        with pytest.raises(ImportError, match="aio-pika"):
            RabbitMQSubscriber()
        with pytest.raises(ImportError, match="aio-pika"):
            RabbitMQTransport()


# ---------------------------------------------------------------------------
# Unit tests — _encode_event / _decode_event
# ---------------------------------------------------------------------------


def test_encode_decode_round_trip():
    """JSON encode → decode round-trip preserves all fields."""
    event = make_event(event_id="json-rt", urgency=0.8, correlation_id="corr-1")
    raw = _encode_event(event)
    assert isinstance(raw, bytes)
    decoded = _decode_event(raw)
    assert decoded is not None
    assert decoded.event_id == "json-rt"
    assert decoded.urgency == pytest.approx(0.8)
    assert decoded.correlation_id == "corr-1"


def test_decode_event_returns_none_on_malformed(caplog):
    """_decode_event returns None and logs on malformed input."""
    with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.rabbitmq"):
        result = _decode_event(b"{not valid json}")
    assert result is None


def test_encode_event_produces_valid_json():
    """Encoded bytes parse as valid JSON with expected keys."""
    event = make_event()
    raw = _encode_event(event)
    data = json.loads(raw)
    assert data["event_type"] == event.event_type
    assert data["urgency"] == event.urgency


# ---------------------------------------------------------------------------
# Unit tests — RabbitMQPublisher lifecycle
# ---------------------------------------------------------------------------


async def test_publisher_start_declares_exchange():
    """start() declares the topic exchange."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]) as mock_connect:
        pub = RabbitMQPublisher(url="amqp://test/")
        await pub.start()

        mock_connect.assert_called_once_with("amqp://test/")
        mocks["channel"].declare_exchange.assert_called_once_with(
            DEFAULT_EXCHANGE_NAME,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        await pub.stop()


async def test_publisher_stop_closes_channel_and_connection():
    """stop() closes channel then connection."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        pub = RabbitMQPublisher()
        await pub.start()
        await pub.stop()

    mocks["channel"].close.assert_called_once()
    mocks["connection"].close.assert_called_once()
    assert pub._exchange is None
    assert pub._channel is None
    assert pub._connection is None


async def test_publisher_stop_idempotent():
    """stop() called multiple times does not raise."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        pub = RabbitMQPublisher()
        await pub.start()
        await pub.stop()
        await pub.stop()  # second call: no-op


async def test_publisher_raises_without_start():
    """publish() before start() raises RuntimeError."""
    pub = RabbitMQPublisher()
    with pytest.raises(RuntimeError, match="not started"):
        await pub.publish(make_event())


async def test_publisher_context_manager():
    """Async context manager calls start() and stop()."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        async with RabbitMQPublisher() as pub:
            assert pub._exchange is not None
        assert pub._exchange is None


# ---------------------------------------------------------------------------
# Unit tests — RabbitMQPublisher.publish delivery mode
# ---------------------------------------------------------------------------


async def test_publish_persistent_above_threshold():
    """Events with urgency > threshold use PERSISTENT delivery mode."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        with patch("aio_pika.Message") as mock_message_cls:
            mock_message_cls.return_value = MagicMock()
            pub = RabbitMQPublisher(durable_threshold_urgency=0.4)
            await pub.start()
            event = make_event(event_id="pers", urgency=0.5)
            await pub.publish(event)
            await pub.stop()

    mock_message_cls.assert_called_once()
    _, kwargs = mock_message_cls.call_args
    assert kwargs["delivery_mode"] == aio_pika.DeliveryMode.PERSISTENT


async def test_publish_transient_at_threshold():
    """Events with urgency == threshold use NOT_PERSISTENT delivery mode."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        with patch("aio_pika.Message") as mock_message_cls:
            mock_message_cls.return_value = MagicMock()
            pub = RabbitMQPublisher(durable_threshold_urgency=0.4)
            await pub.start()
            event = make_event(urgency=0.4)
            await pub.publish(event)
            await pub.stop()

    _, kwargs = mock_message_cls.call_args
    assert kwargs["delivery_mode"] == aio_pika.DeliveryMode.NOT_PERSISTENT


async def test_publish_transient_below_threshold():
    """Events with urgency < threshold use NOT_PERSISTENT delivery mode."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        with patch("aio_pika.Message") as mock_message_cls:
            mock_message_cls.return_value = MagicMock()
            pub = RabbitMQPublisher(durable_threshold_urgency=0.4)
            await pub.start()
            event = make_event(urgency=0.2)
            await pub.publish(event)
            await pub.stop()

    _, kwargs = mock_message_cls.call_args
    assert kwargs["delivery_mode"] == aio_pika.DeliveryMode.NOT_PERSISTENT


# ---------------------------------------------------------------------------
# Unit tests — RabbitMQPublisher routing key & TTL
# ---------------------------------------------------------------------------


async def test_publish_uses_event_type_as_routing_key():
    """publish() uses event.event_type as the AMQP routing key."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        pub = RabbitMQPublisher()
        await pub.start()
        event = make_event(event_type="ravn.tool.complete")
        await pub.publish(event)
        await pub.stop()

    mocks["exchange"].publish.assert_called_once()
    _, kwargs = mocks["exchange"].publish.call_args
    assert kwargs["routing_key"] == "ravn.tool.complete"


async def test_publish_drops_ttl_zero_event(caplog):
    """Events with ttl=0 are silently dropped before publishing."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        pub = RabbitMQPublisher()
        await pub.start()
        await pub.publish(make_event(ttl=0))
        await pub.stop()

    mocks["exchange"].publish.assert_not_called()


async def test_publish_batch_publishes_all_events():
    """publish_batch() publishes every event in the batch."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        pub = RabbitMQPublisher()
        await pub.start()
        batch = [make_event(event_id=f"b{i}") for i in range(5)]
        await pub.publish_batch(batch)
        await pub.stop()

    assert mocks["exchange"].publish.call_count == 5


async def test_publish_message_content_type_is_json():
    """Published messages have content_type='application/json'."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        with patch("aio_pika.Message") as mock_message_cls:
            mock_message_cls.return_value = MagicMock()
            pub = RabbitMQPublisher()
            await pub.start()
            await pub.publish(make_event())
            await pub.stop()

    _, kwargs = mock_message_cls.call_args
    assert kwargs["content_type"] == "application/json"


# ---------------------------------------------------------------------------
# Unit tests — RabbitMQSubscriber lifecycle
# ---------------------------------------------------------------------------


async def test_subscriber_start_declares_exchanges_and_queue():
    """start() declares DLX, main exchange, and subscriber queue.

    Consumption is deferred until first subscribe() call so that
    pre-existing messages are not consumed before handlers are registered.
    """
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber(service_id="ravn:test")
        await sub.start()
        # consume is deferred — not called until subscribe()
        mocks["queue"].consume.assert_not_called()
        await sub.subscribe(["ravn.*"], lambda e: None)
        mocks["queue"].consume.assert_called_once()
        await sub.stop()

    declare_calls = mocks["channel"].declare_exchange.call_args_list
    exchange_names = [c[0][0] for c in declare_calls]
    assert DEFAULT_DEAD_LETTER_EXCHANGE in exchange_names
    assert DEFAULT_EXCHANGE_NAME in exchange_names

    mocks["channel"].declare_queue.assert_called_once()


async def test_subscriber_named_queue_is_durable():
    """Named service_id produces a durable queue."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber(service_id="ravn:agent")
        await sub.start()
        await sub.stop()

    _, kwargs = mocks["channel"].declare_queue.call_args
    assert kwargs["durable"] is True
    assert kwargs["auto_delete"] is False


async def test_subscriber_anonymous_queue_is_auto_delete():
    """No service_id produces an auto-delete, non-durable queue."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.stop()

    _, kwargs = mocks["channel"].declare_queue.call_args
    assert kwargs["durable"] is False
    assert kwargs["auto_delete"] is True


async def test_subscriber_queue_has_dead_letter_argument():
    """Subscriber queue is declared with x-dead-letter-exchange argument."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.stop()

    _, kwargs = mocks["channel"].declare_queue.call_args
    assert kwargs["arguments"]["x-dead-letter-exchange"] == DEFAULT_DEAD_LETTER_EXCHANGE


async def test_subscriber_sets_prefetch_count():
    """start() calls set_qos with the configured prefetch_count."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber(prefetch_count=5)
        await sub.start()
        await sub.stop()

    mocks["channel"].set_qos.assert_called_once_with(prefetch_count=5)


async def test_subscriber_stop_cancels_consumer():
    """stop() cancels the AMQP consumer and closes connection."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        # Must subscribe to trigger consume before stop can cancel it.
        await sub.subscribe(["ravn.*"], lambda e: None)
        consumer_tag = sub._consumer_tag
        await sub.stop()

    mocks["queue"].cancel.assert_called_once_with(consumer_tag)
    mocks["channel"].close.assert_called_once()
    mocks["connection"].close.assert_called_once()
    assert sub._connection is None
    assert sub._channel is None
    assert sub._queue is None


async def test_subscriber_stop_idempotent():
    """stop() called multiple times does not raise."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.stop()
        await sub.stop()


async def test_subscriber_context_manager():
    """Async context manager calls start() and stop()."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        async with RabbitMQSubscriber() as sub:
            assert sub._queue is not None
        assert sub._queue is None


async def test_subscriber_rejects_zero_ring_buffer_depth():
    with pytest.raises(ValueError, match="ring_buffer_depth"):
        RabbitMQSubscriber(ring_buffer_depth=0)


async def test_subscriber_raises_without_start():
    """subscribe() before start() raises RuntimeError."""
    sub = RabbitMQSubscriber()

    async def handler(_: SleipnirEvent) -> None:
        pass

    with pytest.raises(RuntimeError, match="not started"):
        await sub.subscribe(["*"], handler)


# ---------------------------------------------------------------------------
# Unit tests — RabbitMQSubscriber.subscribe bindings
# ---------------------------------------------------------------------------


async def test_subscribe_binds_amqp_routing_key():
    """subscribe() binds the queue to the exchange with translated routing key."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()

        async def handler(_: SleipnirEvent) -> None:
            pass

        handle = await sub.subscribe(["ravn.*"], handler)
        await sub.stop()
        await handle.unsubscribe()

    mocks["queue"].bind.assert_called_once_with(mocks["exchange"], routing_key="ravn.#")


async def test_subscribe_deduplicates_amqp_bindings():
    """Two subscribe() calls with the same translated key bind only once."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()

        async def handler(_: SleipnirEvent) -> None:
            pass

        h1 = await sub.subscribe(["ravn.*"], handler)
        h2 = await sub.subscribe(["ravn.*"], handler)  # same AMQP key
        await sub.stop()
        await h1.unsubscribe()
        await h2.unsubscribe()

    # Should only bind once despite two subscribe() calls
    assert mocks["queue"].bind.call_count == 1


async def test_subscribe_multiple_patterns_each_binds():
    """subscribe() with multiple patterns creates one binding per unique AMQP key."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()

        async def handler(_: SleipnirEvent) -> None:
            pass

        handle = await sub.subscribe(["ravn.*", "ting.*"], handler)
        await sub.stop()
        await handle.unsubscribe()

    bound_keys = {c[1]["routing_key"] for c in mocks["queue"].bind.call_args_list}
    assert "ravn.#" in bound_keys
    assert "ting.#" in bound_keys


async def test_subscribe_star_binds_hash():
    """'*' pattern produces '#' AMQP binding."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()

        async def handler(_: SleipnirEvent) -> None:
            pass

        handle = await sub.subscribe(["*"], handler)
        await sub.stop()
        await handle.unsubscribe()

    _, kwargs = mocks["queue"].bind.call_args
    assert kwargs["routing_key"] == "#"


# ---------------------------------------------------------------------------
# Unit tests — RabbitMQSubscriber._on_message dispatch
# ---------------------------------------------------------------------------


async def test_on_message_dispatches_to_matching_handler():
    """A matching subscription receives the event and the message is acked."""
    mocks = _make_amqp_mocks()
    received: list[SleipnirEvent] = []
    done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        done.set()

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.subscribe(["ravn.*"], handler)

        event = make_event(event_id="msg-1", event_type="ravn.tool.complete")
        msg = _make_mock_message(_encode_event(event))
        await sub._on_message(msg)

        await asyncio.wait_for(done.wait(), timeout=2.0)
        await sub.stop()

    assert len(received) == 1
    assert received[0].event_id == "msg-1"
    msg.ack.assert_called_once()
    msg.nack.assert_not_called()


async def test_on_message_does_not_dispatch_to_non_matching_handler():
    """A non-matching subscription does not receive the event; message is acked."""
    mocks = _make_amqp_mocks()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        # Subscribe to ting.* only
        await sub.subscribe(["ting.*"], handler)

        event = make_event(event_type="ravn.tool.complete")
        msg = _make_mock_message(_encode_event(event))
        await sub._on_message(msg)

        # Allow any async delivery
        await asyncio.sleep(0.05)
        await sub.stop()

    assert received == []
    msg.ack.assert_called_once()
    msg.nack.assert_not_called()


async def test_on_message_drops_expired_ttl():
    """TTL=0 events are not dispatched but the message is acked (valid, just stale)."""
    mocks = _make_amqp_mocks()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.subscribe(["*"], handler)

        event = make_event(ttl=0)
        msg = _make_mock_message(_encode_event(event))
        await sub._on_message(msg)

        await asyncio.sleep(0.05)
        await sub.stop()

    assert received == []
    # TTL-expired messages are acked (they are valid, just stale) — not nacked.
    msg.ack.assert_called_once()
    msg.nack.assert_not_called()


async def test_on_message_handles_malformed_json(caplog):
    """Malformed JSON body is nacked without requeue so DLX receives it."""
    mocks = _make_amqp_mocks()

    async def handler(_: SleipnirEvent) -> None:
        pass

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.subscribe(["*"], handler)

        msg = _make_mock_message(b"{bad json")
        with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.rabbitmq"):
            await sub._on_message(msg)

        await sub.stop()

    msg.nack.assert_called_once_with(requeue=False)
    msg.ack.assert_not_called()


async def test_on_message_dispatches_to_multiple_subscribers():
    """All active subscriptions independently receive the same event."""
    mocks = _make_amqp_mocks()
    bucket_a: list[str] = []
    bucket_b: list[str] = []
    done_a = asyncio.Event()
    done_b = asyncio.Event()

    async def handler_a(evt: SleipnirEvent) -> None:
        bucket_a.append(evt.event_id)
        done_a.set()

    async def handler_b(evt: SleipnirEvent) -> None:
        bucket_b.append(evt.event_id)
        done_b.set()

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.subscribe(["*"], handler_a)
        await sub.subscribe(["*"], handler_b)

        event = make_event(event_id="multi-sub")
        msg = _make_mock_message(_encode_event(event))
        await sub._on_message(msg)

        await asyncio.wait_for(asyncio.gather(done_a.wait(), done_b.wait()), timeout=2.0)
        await sub.stop()

    assert bucket_a == ["multi-sub"]
    assert bucket_b == ["multi-sub"]
    msg.ack.assert_called_once()
    msg.nack.assert_not_called()


async def test_on_message_inactive_subscription_not_dispatched():
    """An unsubscribed handler does not receive events."""
    mocks = _make_amqp_mocks()
    received: list[str] = []
    first_done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)
        first_done.set()

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        handle = await sub.subscribe(["*"], handler)

        # First message: delivered
        event1 = make_event(event_id="before-unsub")
        await sub._on_message(_make_mock_message(_encode_event(event1)))
        await asyncio.wait_for(first_done.wait(), timeout=2.0)

        # Unsubscribe and send second message
        await handle.unsubscribe()
        event2 = make_event(event_id="after-unsub")
        await sub._on_message(_make_mock_message(_encode_event(event2)))
        await asyncio.sleep(0.05)
        await sub.stop()

    assert "before-unsub" in received
    assert "after-unsub" not in received


async def test_flush_waits_for_event_processing():
    """flush() blocks until all queued events are processed."""
    mocks = _make_amqp_mocks()
    processed: list[str] = []

    async def slow_handler(evt: SleipnirEvent) -> None:
        await asyncio.sleep(0.02)
        processed.append(evt.event_id)

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.subscribe(["*"], slow_handler)

        event = make_event(event_id="flush-me")
        await sub._on_message(_make_mock_message(_encode_event(event)))
        await sub.flush()
        await sub.stop()

    assert "flush-me" in processed


# ---------------------------------------------------------------------------
# Unit tests — ring buffer overflow
# ---------------------------------------------------------------------------


async def test_ring_buffer_overflow_drops_oldest_and_warns(caplog):
    """When the ring buffer is full, the oldest event is dropped with a warning."""
    mocks = _make_amqp_mocks()
    released = asyncio.Event()

    async def blocking_handler(_: SleipnirEvent) -> None:
        await released.wait()

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber(ring_buffer_depth=2)
        await sub.start()
        await sub.subscribe(["*"], blocking_handler)

        # Send more events than the ring buffer can hold.
        with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.rabbitmq"):
            for i in range(10):
                event = make_event(event_id=f"overflow-{i}")
                await sub._on_message(_make_mock_message(_encode_event(event)))

        overflow_warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
        assert len(overflow_warnings) >= 1

        released.set()
        await sub.stop()


# ---------------------------------------------------------------------------
# Unit tests — crashing handler isolation
# ---------------------------------------------------------------------------


async def test_crashing_handler_does_not_affect_sibling():
    """A handler that raises must not prevent sibling subscriptions from receiving."""
    mocks = _make_amqp_mocks()
    received_by_stable: list[str] = []
    n = 3
    all_stable_done = asyncio.Event()

    async def crashing_handler(_: SleipnirEvent) -> None:
        raise RuntimeError("Simulated handler crash")

    async def stable_handler(evt: SleipnirEvent) -> None:
        received_by_stable.append(evt.event_id)
        if len(received_by_stable) >= n:
            all_stable_done.set()

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber()
        await sub.start()
        await sub.subscribe(["*"], crashing_handler)
        await sub.subscribe(["*"], stable_handler)

        for i in range(n):
            event = make_event(event_id=f"crash-{i}")
            await sub._on_message(_make_mock_message(_encode_event(event)))

        await asyncio.wait_for(all_stable_done.wait(), timeout=2.0)
        await sub.stop()

    assert len(received_by_stable) == n


# ---------------------------------------------------------------------------
# Unit tests — RabbitMQTransport
# ---------------------------------------------------------------------------


async def test_transport_start_starts_both_pub_and_sub():
    """RabbitMQTransport.start() calls start on both publisher and subscriber."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        transport = RabbitMQTransport()
        await transport.start()
        assert transport._publisher._exchange is not None
        assert transport._subscriber._queue is not None
        await transport.stop()


async def test_transport_stop_order():
    """stop() calls subscriber.stop() before publisher.stop()."""
    mocks = _make_amqp_mocks()
    stop_order: list[str] = []

    async def track_sub_stop(*_):
        stop_order.append("sub")

    async def track_pub_stop(*_):
        stop_order.append("pub")

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        transport = RabbitMQTransport()
        await transport.start()
        transport._subscriber.stop = track_sub_stop
        transport._publisher.stop = track_pub_stop
        await transport.stop()

    assert stop_order == ["sub", "pub"]


async def test_transport_publish_delegates_to_publisher():
    """publish() delegates to the internal RabbitMQPublisher."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        transport = RabbitMQTransport()
        await transport.start()
        event = make_event(event_id="transport-pub")
        await transport.publish(event)
        await transport.stop()

    mocks["exchange"].publish.assert_called_once()


async def test_transport_publish_batch_publishes_all():
    """publish_batch() delivers all events via the internal publisher."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        transport = RabbitMQTransport()
        await transport.start()
        batch = [make_event(event_id=f"t{i}") for i in range(4)]
        await transport.publish_batch(batch)
        await transport.stop()

    assert mocks["exchange"].publish.call_count == 4


async def test_transport_subscribe_delegates_to_subscriber():
    """subscribe() delegates to the internal RabbitMQSubscriber."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        transport = RabbitMQTransport()
        await transport.start()

        async def handler(_: SleipnirEvent) -> None:
            pass

        handle = await transport.subscribe(["ravn.*"], handler)
        await transport.stop()
        await handle.unsubscribe()

    mocks["queue"].bind.assert_called_once()


async def test_transport_flush_delegates_to_subscriber():
    """flush() delegates to the internal RabbitMQSubscriber."""
    mocks = _make_amqp_mocks()
    processed: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        processed.append(evt.event_id)

    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        transport = RabbitMQTransport()
        await transport.start()
        await transport.subscribe(["*"], handler)

        event = make_event(event_id="flush-transport")
        msg = _make_mock_message(_encode_event(event))
        await transport._subscriber._on_message(msg)

        await transport.flush()
        await transport.stop()

    assert "flush-transport" in processed


async def test_transport_context_manager():
    """Async context manager starts and stops cleanly."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        async with RabbitMQTransport() as transport:
            assert transport._publisher._exchange is not None
        assert transport._publisher._exchange is None


# ---------------------------------------------------------------------------
# Unit tests — default constants
# ---------------------------------------------------------------------------


def test_default_constants_are_sensible():
    """Sanity check on module-level defaults."""
    assert DEFAULT_AMQP_URL.startswith("amqp://")
    assert "sleipnir" in DEFAULT_EXCHANGE_NAME
    assert "dead_letter" in DEFAULT_DEAD_LETTER_EXCHANGE
    assert DEFAULT_PREFETCH_COUNT == 1
    assert 0.0 < DEFAULT_DURABLE_THRESHOLD_URGENCY < 1.0
    assert DEFAULT_RING_BUFFER_DEPTH > 0


# ---------------------------------------------------------------------------
# Unit tests — custom exchange name
# ---------------------------------------------------------------------------


async def test_custom_exchange_name_is_used():
    """A custom exchange_name is used when declaring the exchange."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        pub = RabbitMQPublisher(exchange_name="my.custom.exchange")
        await pub.start()
        await pub.stop()

    declare_call_names = [c[0][0] for c in mocks["channel"].declare_exchange.call_args_list]
    assert "my.custom.exchange" in declare_call_names


async def test_custom_dead_letter_exchange_name():
    """A custom dead_letter_exchange is used when declaring the DLX."""
    mocks = _make_amqp_mocks()
    with patch("aio_pika.connect_robust", return_value=mocks["connection"]):
        sub = RabbitMQSubscriber(dead_letter_exchange="my.dlx")
        await sub.start()
        await sub.stop()

    declare_call_names = [c[0][0] for c in mocks["channel"].declare_exchange.call_args_list]
    assert "my.dlx" in declare_call_names

    _, kwargs = mocks["channel"].declare_queue.call_args
    assert kwargs["arguments"]["x-dead-letter-exchange"] == "my.dlx"
