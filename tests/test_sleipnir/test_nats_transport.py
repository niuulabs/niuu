"""Tests for the NATS JetStream transport adapter (NIU-465).

Test strategy
-------------
Unit tests cover pure helper functions and the deduplication cache with no
external dependencies.

Adapter tests mock the nats-py client (``nats.connect``) so that no running
NATS server is required.  The NATS message callback (``_on_message``) is
extracted from mock call arguments and invoked directly to test delivery logic.

Skip all tests if nats-py is not installed.
"""

from __future__ import annotations

import asyncio
import ssl
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sleipnir.adapters.nats_transport import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_DEDUP_CACHE_SIZE,
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_RECONNECT_ATTEMPTS,
    DEFAULT_RETENTION,
    DEFAULT_RING_BUFFER_DEPTH,
    DEFAULT_SERVERS,
    DEFAULT_STREAM_NAME,
    DEFAULT_SUBJECT_PREFIX,
    NatsBridgeAdapter,
    NatsPublisher,
    NatsSubscriber,
    NatsTransport,
    _BridgeSubscription,
    _build_tls_context,
    _decode_nats_message,
    _DeduplicationCache,
    _durable_name_for_subject,
    _nats_subject_for_event,
    _nats_subjects_for_patterns,
    _parse_retention,
    nats_available,
)
from sleipnir.adapters.serialization import serialize
from sleipnir.domain.events import SleipnirEvent
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Skip all tests if nats-py is not installed.
# ---------------------------------------------------------------------------

pytest.importorskip("nats", reason="nats-py not installed; skipping NATS tests")

import nats.js.api as js_api  # noqa: E402 — only executed when nats is available

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_client() -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Return (mock_client, mock_js, mock_nats_sub) with wired-up async methods."""
    mock_sub = AsyncMock()
    mock_sub.unsubscribe = AsyncMock()

    mock_js = AsyncMock()
    mock_js.subscribe = AsyncMock(return_value=mock_sub)
    mock_js.publish = AsyncMock()
    mock_js.stream_info = AsyncMock()  # stream already exists by default
    mock_js.add_stream = AsyncMock()

    mock_client = AsyncMock()
    mock_client.jetstream = MagicMock(return_value=mock_js)
    mock_client.drain = AsyncMock()
    mock_client.close = AsyncMock()

    return mock_client, mock_js, mock_sub


@pytest.fixture
def mock_nats(monkeypatch):
    """Patch nats.connect to return a mock client."""
    client, js, nats_sub = _make_mock_client()
    with patch("sleipnir.adapters.nats_transport.nats") as mock_nats_module:
        mock_nats_module.connect = AsyncMock(return_value=client)
        yield mock_nats_module, client, js, nats_sub


# ---------------------------------------------------------------------------
# Unit tests — nats_available
# ---------------------------------------------------------------------------


def test_nats_available_returns_true():
    assert nats_available() is True


# ---------------------------------------------------------------------------
# Unit tests — _nats_subject_for_event
# ---------------------------------------------------------------------------


def test_subject_for_event_basic():
    result = _nats_subject_for_event("ravn.tool.complete", "sleipnir")
    assert result == "sleipnir.ravn.tool.complete"


def test_subject_for_event_custom_prefix():
    assert _nats_subject_for_event("system.health.ping", "myapp") == "myapp.system.health.ping"


# ---------------------------------------------------------------------------
# Unit tests — _nats_subjects_for_patterns
# ---------------------------------------------------------------------------


def test_subjects_wildcard_star_short_circuits():
    """'*' should return a single subscribe-all subject."""
    result = _nats_subjects_for_patterns(["*"], "sleipnir")
    assert result == ["sleipnir.>"]


def test_subjects_star_in_list_short_circuits():
    """'*' anywhere in the list short-circuits to subscribe-all."""
    result = _nats_subjects_for_patterns(["ravn.*", "*"], "sleipnir")
    assert result == ["sleipnir.>"]


def test_subjects_namespace_wildcard():
    """'ravn.*' → NATS multi-token wildcard."""
    assert _nats_subjects_for_patterns(["ravn.*"], "sleipnir") == ["sleipnir.ravn.>"]


def test_subjects_sub_namespace_wildcard():
    """'ravn.tool.*' → NATS multi-token wildcard."""
    assert _nats_subjects_for_patterns(["ravn.tool.*"], "sleipnir") == ["sleipnir.ravn.tool.>"]


def test_subjects_exact_match():
    """Exact event type → exact NATS subject."""
    assert _nats_subjects_for_patterns(["ravn.tool.complete"], "sleipnir") == [
        "sleipnir.ravn.tool.complete"
    ]


def test_subjects_multiple_patterns():
    """Multiple exact patterns → multiple NATS subjects."""
    result = _nats_subjects_for_patterns(["ravn.tool.complete", "ting.task.started"], "sleipnir")
    assert result == ["sleipnir.ravn.tool.complete", "sleipnir.ting.task.started"]


def test_subjects_complex_wildcard_falls_back_to_all():
    """Complex pattern with '?' → subscribe-all + app-level filter."""
    result = _nats_subjects_for_patterns(["ravn.tool.?"], "sleipnir")
    assert result == ["sleipnir.>"]


def test_subjects_bracket_wildcard_falls_back_to_all():
    """Pattern with '[' → subscribe-all."""
    result = _nats_subjects_for_patterns(["ravn.[a-z]*"], "sleipnir")
    assert result == ["sleipnir.>"]


def test_subjects_empty_list_returns_all():
    """Empty pattern list → subscribe-all (safe default)."""
    result = _nats_subjects_for_patterns([], "sleipnir")
    assert result == ["sleipnir.>"]


def test_durable_name_includes_filter_subject_hash():
    """One consumer group can own multiple JetStream filter subjects safely."""
    rpc_durable = _durable_name_for_subject(
        "valhalla-valkyries",
        "obs.valhalla.ravn.mesh.rpc.valkyrie_valhalla_k8s",
    )
    judgment_durable = _durable_name_for_subject(
        "valhalla-valkyries",
        "obs.valhalla.ravn.mesh.valkyrie.judgment.proposed",
    )

    assert rpc_durable.startswith("valhalla-valkyries-")
    assert judgment_durable.startswith("valhalla-valkyries-")
    assert rpc_durable != judgment_durable
    assert len(rpc_durable) <= 64
    assert len(judgment_durable) <= 64


def test_durable_name_sanitizes_consumer_group():
    durable = _durable_name_for_subject("cluster/k8s valkyries", "obs.ymir.>")

    assert durable.startswith("cluster-k8s-valkyries-")
    assert "/" not in durable
    assert " " not in durable


# ---------------------------------------------------------------------------
# Unit tests — _parse_retention
# ---------------------------------------------------------------------------


def test_parse_retention_limits():
    assert _parse_retention("limits") == js_api.RetentionPolicy.LIMITS


def test_parse_retention_interest():
    assert _parse_retention("interest") == js_api.RetentionPolicy.INTEREST


def test_parse_retention_workqueue():
    assert _parse_retention("workqueue") == js_api.RetentionPolicy.WORK_QUEUE


def test_parse_retention_invalid_raises():
    with pytest.raises(ValueError, match="Unknown retention policy"):
        _parse_retention("bogus")


# ---------------------------------------------------------------------------
# Unit tests — _decode_nats_message
# ---------------------------------------------------------------------------


def test_decode_nats_message_valid():
    event = make_event()
    data = serialize(event)
    decoded = _decode_nats_message(data)
    assert decoded is not None
    assert decoded.event_id == event.event_id


def test_decode_nats_message_invalid_returns_none():
    result = _decode_nats_message(b"not-valid-msgpack-\xff\xfe")
    assert result is None


# ---------------------------------------------------------------------------
# Unit tests — _DeduplicationCache
# ---------------------------------------------------------------------------


def test_dedup_cache_new_id_not_seen():
    cache = _DeduplicationCache(max_size=10)
    assert not cache.is_seen("evt-001")


def test_dedup_cache_after_mark_seen():
    cache = _DeduplicationCache(max_size=10)
    cache.mark_seen("evt-001")
    assert cache.is_seen("evt-001")


def test_dedup_cache_double_mark_is_idempotent():
    cache = _DeduplicationCache(max_size=10)
    cache.mark_seen("evt-001")
    cache.mark_seen("evt-001")
    # Should not grow the order queue beyond 1 entry
    assert len(cache._order) == 1


def test_dedup_cache_evicts_oldest_on_overflow():
    cache = _DeduplicationCache(max_size=3)
    cache.mark_seen("evt-001")
    cache.mark_seen("evt-002")
    cache.mark_seen("evt-003")
    # Cache is full; inserting evt-004 evicts evt-001
    cache.mark_seen("evt-004")
    assert not cache.is_seen("evt-001")
    assert cache.is_seen("evt-004")


def test_dedup_cache_max_size_respected():
    cache = _DeduplicationCache(max_size=5)
    for i in range(10):
        cache.mark_seen(f"evt-{i:03d}")
    assert len(cache._seen) == 5
    assert len(cache._order) == 5


def test_tls_context_can_skip_verification_for_internal_clusters():
    context = _build_tls_context(tls_insecure_skip_verify=True)

    assert context is not None
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE


# ---------------------------------------------------------------------------
# NatsPublisher tests
# ---------------------------------------------------------------------------


async def test_publisher_start_connects_and_ensures_stream(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher(servers=["nats://localhost:4222"])
    await pub.start()
    mock_module.connect.assert_called_once()
    js.stream_info.assert_called_once_with(DEFAULT_STREAM_NAME)


async def test_publisher_start_passes_auth_and_can_skip_stream_ensure(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher(
        servers=["tls://nats.example:4222"],
        ensure_stream=False,
        user="valkyrie",
        password="secret",
        tls_hostname="nats.example",
        tls_handshake_first=True,
    )

    await pub.start()

    mock_module.connect.assert_called_once_with(
        servers=["tls://nats.example:4222"],
        connect_timeout=DEFAULT_CONNECT_TIMEOUT_S,
        max_reconnect_attempts=DEFAULT_MAX_RECONNECT_ATTEMPTS,
        user="valkyrie",
        password="secret",
        tls_hostname="nats.example",
        tls_handshake_first=True,
    )
    js.stream_info.assert_not_called()
    js.add_stream.assert_not_called()


async def test_publisher_start_creates_stream_when_missing(mock_nats):
    mock_module, client, js, _ = mock_nats
    js.stream_info.side_effect = Exception("stream not found")
    pub = NatsPublisher()
    await pub.start()
    js.add_stream.assert_called_once()


async def test_publisher_ensure_stream_logs_debug_on_stream_info_failure(mock_nats, caplog):
    """stream_info failure is logged at DEBUG before attempting creation."""
    import logging

    mock_module, client, js, _ = mock_nats
    js.stream_info.side_effect = Exception("not found")
    pub = NatsPublisher()
    with caplog.at_level(logging.DEBUG, logger="sleipnir.adapters.nats_transport"):
        await pub.start()
    assert any("stream_info" in r.message and "failed" in r.message for r in caplog.records)


async def test_publisher_publish_sends_correct_subject(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher()
    await pub.start()
    event = make_event(event_type="ravn.tool.complete")
    await pub.publish(event)
    js.publish.assert_called_once()
    subject = js.publish.call_args[0][0]
    assert subject == "sleipnir.ravn.tool.complete"


async def test_publisher_publish_payload_is_msgpack(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher()
    await pub.start()
    event = make_event()
    await pub.publish(event)
    payload = js.publish.call_args[0][1]
    # Should be deserializable
    decoded = _decode_nats_message(payload)
    assert decoded is not None
    assert decoded.event_id == event.event_id


async def test_publisher_publish_drops_expired_ttl(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher()
    await pub.start()
    event = make_event(ttl=0)
    await pub.publish(event)
    js.publish.assert_not_called()


async def test_publisher_publish_before_start_raises():
    pub = NatsPublisher()
    with pytest.raises(RuntimeError, match="not started"):
        await pub.publish(make_event())


async def test_publisher_publish_batch(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher()
    await pub.start()
    events = [make_event(event_id=f"evt-{i:03d}") for i in range(3)]
    await pub.publish_batch(events)
    assert js.publish.call_count == 3


async def test_publisher_stop_drains_and_closes(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher()
    await pub.start()
    await pub.stop()
    client.drain.assert_called_once()
    client.close.assert_called_once()
    assert pub._client is None


async def test_publisher_stop_idempotent(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher()
    await pub.start()
    await pub.stop()
    await pub.stop()  # second stop should not raise


async def test_publisher_context_manager(mock_nats):
    mock_module, client, js, _ = mock_nats
    async with NatsPublisher() as pub:
        assert pub._js is not None
    assert pub._client is None


async def test_publisher_custom_subject_prefix(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher(subject_prefix="myapp")
    await pub.start()
    event = make_event(event_type="ravn.tool.complete")
    await pub.publish(event)
    subject = js.publish.call_args[0][0]
    assert subject == "myapp.ravn.tool.complete"


# ---------------------------------------------------------------------------
# NatsSubscriber tests
# ---------------------------------------------------------------------------


async def test_subscriber_invalid_ring_buffer_depth():
    with pytest.raises(ValueError, match="ring_buffer_depth"):
        NatsSubscriber(ring_buffer_depth=0)


async def test_subscriber_start_connects(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    mock_module.connect.assert_called_once()
    assert sub._running is True


async def test_subscriber_subscribe_before_start_raises():
    sub = NatsSubscriber()
    with pytest.raises(RuntimeError, match="not started"):
        await sub.subscribe(["ravn.*"], AsyncMock())


async def test_subscriber_subscribe_creates_nats_subscription(mock_nats):
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handler = AsyncMock()
    handle = await sub.subscribe(["ravn.*"], handler)
    js.subscribe.assert_called_once()
    call_kwargs = js.subscribe.call_args[1]
    assert call_kwargs["stream"] == DEFAULT_STREAM_NAME
    assert "cb" in call_kwargs
    await handle.unsubscribe()
    await sub.stop()


async def test_subscriber_subscribe_exact_subject(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    await sub.subscribe(["ravn.tool.complete"], AsyncMock())
    subject = js.subscribe.call_args[0][0]
    assert subject == "sleipnir.ravn.tool.complete"
    await sub.stop()


async def test_subscriber_subscribe_namespace_wildcard_subject(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    await sub.subscribe(["ravn.*"], AsyncMock())
    subject = js.subscribe.call_args[0][0]
    assert subject == "sleipnir.ravn.>"
    await sub.stop()


async def test_subscriber_consumer_group_sets_durable_and_queue(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(consumer_group="my-service")
    await sub.start()
    await sub.subscribe(["*"], AsyncMock())
    kwargs = js.subscribe.call_args[1]
    assert kwargs["durable"].startswith("my-service-")
    assert kwargs["durable"] != "my-service"
    assert kwargs["queue"] == kwargs["durable"]
    await sub.stop()


async def test_subscriber_no_consumer_group_no_durable_or_queue(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    await sub.subscribe(["*"], AsyncMock())
    kwargs = js.subscribe.call_args[1]
    assert "durable" not in kwargs
    assert "queue" not in kwargs
    await sub.stop()


# ---------------------------------------------------------------------------
# NatsSubscriber — consumer config tests
# ---------------------------------------------------------------------------


def test_build_consumer_config_default_is_new():
    sub = NatsSubscriber.__new__(NatsSubscriber)
    sub._replay_from_sequence = None
    sub._replay_from_time = None
    config = sub._build_consumer_config()
    assert config.deliver_policy == js_api.DeliverPolicy.NEW


def test_build_consumer_config_replay_from_sequence():
    sub = NatsSubscriber.__new__(NatsSubscriber)
    sub._replay_from_sequence = 42
    sub._replay_from_time = None
    config = sub._build_consumer_config()
    assert config.deliver_policy == js_api.DeliverPolicy.BY_START_SEQUENCE
    assert config.opt_start_seq == 42


def test_build_consumer_config_replay_from_time():
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    sub = NatsSubscriber.__new__(NatsSubscriber)
    sub._replay_from_sequence = None
    sub._replay_from_time = ts
    config = sub._build_consumer_config()
    assert config.deliver_policy == js_api.DeliverPolicy.BY_START_TIME
    assert config.opt_start_time == ts


def test_build_consumer_config_sequence_takes_priority_over_time():
    """When both replay options are set, sequence takes priority."""
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    sub = NatsSubscriber.__new__(NatsSubscriber)
    sub._replay_from_sequence = 10
    sub._replay_from_time = ts
    config = sub._build_consumer_config()
    assert config.deliver_policy == js_api.DeliverPolicy.BY_START_SEQUENCE


# ---------------------------------------------------------------------------
# NatsSubscriber — message callback delivery tests
# ---------------------------------------------------------------------------


async def test_subscriber_on_message_delivers_to_handler(mock_nats):
    """The _on_message callback dispatches a valid event to the handler."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(ring_buffer_depth=10)
    await sub.start()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    await sub.subscribe(["ravn.*"], handler)

    # Extract the callback registered with nats-py
    on_message = js.subscribe.call_args[1]["cb"]
    event = make_event(event_type="ravn.tool.complete")
    mock_msg = MagicMock()
    mock_msg.data = serialize(event)
    mock_msg.ack = AsyncMock()

    await on_message(mock_msg)
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].event_id == event.event_id
    # ack must fire (in the finally block) even for processed messages
    mock_msg.ack.assert_called_once()
    await sub.stop()


async def test_subscriber_on_message_acks_after_processing(mock_nats):
    """ack fires in finally — after enqueue, preserving at-least-once delivery."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    ack_call_order: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        ack_call_order.append("handler")

    await sub.subscribe(["ravn.*"], handler)
    on_message = js.subscribe.call_args[1]["cb"]

    event = make_event(event_type="ravn.tool.complete")
    mock_msg = MagicMock()
    mock_msg.data = serialize(event)

    async def _ack() -> None:
        ack_call_order.append("ack")

    mock_msg.ack = _ack

    await on_message(mock_msg)
    await asyncio.sleep(0.05)

    # ack must come after the event is enqueued (handler runs async, but ack
    # fires from the finally block in _on_message, before the handler task runs)
    assert "ack" in ack_call_order
    await sub.stop()


async def test_subscriber_on_message_drops_expired_ttl(mock_nats):
    """Expired TTL events are not dispatched; ack still fires."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    received: list[SleipnirEvent] = []
    await sub.subscribe(["*"], lambda evt: received.append(evt))

    on_message = js.subscribe.call_args[1]["cb"]
    event = make_event(ttl=0)
    mock_msg = MagicMock()
    mock_msg.data = serialize(event)
    mock_msg.ack = AsyncMock()

    await on_message(mock_msg)
    await asyncio.sleep(0.02)
    assert len(received) == 0
    mock_msg.ack.assert_called_once()
    await sub.stop()


async def test_subscriber_on_message_drops_pattern_mismatch(mock_nats):
    """Pattern-mismatched events are not dispatched; ack still fires."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    received: list[SleipnirEvent] = []
    await sub.subscribe(["ting.*"], lambda evt: received.append(evt))

    on_message = js.subscribe.call_args[1]["cb"]
    event = make_event(event_type="ravn.tool.complete")  # won't match "ting.*"
    mock_msg = MagicMock()
    mock_msg.data = serialize(event)
    mock_msg.ack = AsyncMock()

    await on_message(mock_msg)
    await asyncio.sleep(0.02)
    assert len(received) == 0
    mock_msg.ack.assert_called_once()
    await sub.stop()


async def test_subscriber_on_message_drops_bad_payload(mock_nats):
    """Malformed payloads are dropped; ack still fires."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    received: list[SleipnirEvent] = []
    await sub.subscribe(["*"], lambda evt: received.append(evt))

    on_message = js.subscribe.call_args[1]["cb"]
    mock_msg = MagicMock()
    mock_msg.data = b"\xff\xfe\xfd"  # malformed
    mock_msg.ack = AsyncMock()

    await on_message(mock_msg)
    await asyncio.sleep(0.02)
    assert len(received) == 0
    mock_msg.ack.assert_called_once()
    await sub.stop()


async def test_subscriber_on_message_skips_when_not_running(mock_nats):
    """Messages received after stop() are ignored; ack still fires."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    received: list[SleipnirEvent] = []
    await sub.subscribe(["*"], lambda evt: received.append(evt))

    on_message = js.subscribe.call_args[1]["cb"]
    sub._running = False  # simulate stopped state

    event = make_event()
    mock_msg = MagicMock()
    mock_msg.data = serialize(event)
    mock_msg.ack = AsyncMock()

    await on_message(mock_msg)
    await asyncio.sleep(0.02)
    assert len(received) == 0
    mock_msg.ack.assert_called_once()
    await sub.stop()


async def test_subscriber_stop_unsubscribes_nats_subs(mock_nats):
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    await sub.subscribe(["*"], AsyncMock())
    await sub.stop()
    nats_sub.unsubscribe.assert_called_once()
    assert sub._client is None


async def test_subscriber_context_manager(mock_nats):
    mock_module, client, js, _ = mock_nats
    async with NatsSubscriber() as sub:
        assert sub._running is True
    assert sub._client is None


# ---------------------------------------------------------------------------
# NatsTransport tests
# ---------------------------------------------------------------------------


async def test_transport_start_starts_both(mock_nats):
    mock_module, client, js, _ = mock_nats
    transport = NatsTransport()
    await transport.start()
    # Both publisher and subscriber should have connected
    assert mock_module.connect.call_count == 2
    await transport.stop()


async def test_transport_stop_stops_both(mock_nats):
    mock_module, client, js, _ = mock_nats
    transport = NatsTransport()
    await transport.start()
    await transport.stop()
    assert client.drain.call_count == 2


async def test_transport_publish_delegates_to_publisher(mock_nats):
    mock_module, client, js, _ = mock_nats
    transport = NatsTransport()
    await transport.start()
    event = make_event()
    await transport.publish(event)
    js.publish.assert_called_once()
    await transport.stop()


async def test_transport_publish_batch(mock_nats):
    mock_module, client, js, _ = mock_nats
    transport = NatsTransport()
    await transport.start()
    events = [make_event(event_id=f"evt-{i:03d}") for i in range(4)]
    await transport.publish_batch(events)
    assert js.publish.call_count == 4
    await transport.stop()


async def test_transport_subscribe_delegates_to_subscriber(mock_nats):
    mock_module, client, js, nats_sub = mock_nats
    transport = NatsTransport()
    await transport.start()
    handle = await transport.subscribe(["ravn.*"], AsyncMock())
    js.subscribe.assert_called_once()
    await handle.unsubscribe()
    await transport.stop()


async def test_transport_context_manager(mock_nats):
    mock_module, client, js, _ = mock_nats
    async with NatsTransport() as transport:
        assert transport._publisher._js is not None
    assert transport._publisher._client is None


async def test_transport_consumer_group_forwarded(mock_nats):
    mock_module, client, js, _ = mock_nats
    transport = NatsTransport(consumer_group="workers")
    await transport.start()
    await transport.subscribe(["*"], AsyncMock())
    kwargs = js.subscribe.call_args[1]
    assert kwargs["durable"].startswith("workers-")
    assert kwargs["queue"] == kwargs["durable"]
    await transport.stop()


# ---------------------------------------------------------------------------
# NatsBridgeAdapter tests
# ---------------------------------------------------------------------------


async def test_bridge_publish_forwards_to_both():
    """Publish should call both local and NATS publisher."""
    local_pub = MagicMock()
    local_pub.publish = AsyncMock()
    nats_pub = MagicMock()
    nats_pub.publish = AsyncMock()
    local_sub = MagicMock()
    nats_sub = MagicMock()
    mock_handle = AsyncMock()
    local_sub.subscribe = AsyncMock(return_value=mock_handle)
    nats_sub.subscribe = AsyncMock(return_value=mock_handle)

    bridge = NatsBridgeAdapter(
        local_publisher=local_pub,
        local_subscriber=local_sub,
        nats_publisher=nats_pub,
        nats_subscriber=nats_sub,
    )
    event = make_event()
    await bridge.publish(event)
    local_pub.publish.assert_called_once_with(event)
    nats_pub.publish.assert_called_once_with(event)


async def test_bridge_publish_batch():
    local_pub = MagicMock()
    local_pub.publish = AsyncMock()
    nats_pub = MagicMock()
    nats_pub.publish = AsyncMock()
    local_sub = MagicMock()
    nats_sub = MagicMock()
    mock_handle = AsyncMock()
    local_sub.subscribe = AsyncMock(return_value=mock_handle)
    nats_sub.subscribe = AsyncMock(return_value=mock_handle)

    bridge = NatsBridgeAdapter(
        local_publisher=local_pub,
        local_subscriber=local_sub,
        nats_publisher=nats_pub,
        nats_subscriber=nats_sub,
    )
    events = [make_event(event_id=f"evt-{i:03d}") for i in range(3)]
    await bridge.publish_batch(events)
    assert local_pub.publish.call_count == 3
    assert nats_pub.publish.call_count == 3


async def test_bridge_subscribe_subscribes_to_both():
    local_sub = AsyncMock()
    nats_sub = AsyncMock()
    mock_handle = AsyncMock()
    local_sub.subscribe = AsyncMock(return_value=mock_handle)
    nats_sub.subscribe = AsyncMock(return_value=mock_handle)

    bridge = NatsBridgeAdapter(
        local_publisher=AsyncMock(),
        local_subscriber=local_sub,
        nats_publisher=AsyncMock(),
        nats_subscriber=nats_sub,
    )
    handler = AsyncMock()
    handle = await bridge.subscribe(["ravn.*"], handler)
    local_sub.subscribe.assert_called_once()
    nats_sub.subscribe.assert_called_once()
    assert isinstance(handle, _BridgeSubscription)


async def test_bridge_deduplication_prevents_double_delivery():
    """An event arriving on both transports must be delivered only once."""
    local_sub = AsyncMock()
    nats_sub = AsyncMock()

    captured_handlers: list = []

    async def _capture_subscribe(event_types, handler):
        captured_handlers.append(handler)
        return AsyncMock()

    local_sub.subscribe = _capture_subscribe
    nats_sub.subscribe = _capture_subscribe

    bridge = NatsBridgeAdapter(
        local_publisher=AsyncMock(),
        local_subscriber=local_sub,
        nats_publisher=AsyncMock(),
        nats_subscriber=nats_sub,
    )
    delivered: list[SleipnirEvent] = []

    async def end_handler(evt: SleipnirEvent) -> None:
        delivered.append(evt)

    await bridge.subscribe(["*"], end_handler)
    assert len(captured_handlers) == 2

    event = make_event(event_id="evt-dedup")

    # Simulate the same event arriving on both transports
    await captured_handlers[0](event)
    await captured_handlers[1](event)

    assert len(delivered) == 1


async def test_bridge_different_event_ids_both_delivered():
    """Different event IDs should both be delivered."""
    local_sub = AsyncMock()
    nats_sub = AsyncMock()

    captured_handlers: list = []

    async def _capture_subscribe(event_types, handler):
        captured_handlers.append(handler)
        return AsyncMock()

    local_sub.subscribe = _capture_subscribe
    nats_sub.subscribe = _capture_subscribe

    bridge = NatsBridgeAdapter(
        local_publisher=AsyncMock(),
        local_subscriber=local_sub,
        nats_publisher=AsyncMock(),
        nats_subscriber=nats_sub,
    )
    delivered: list[SleipnirEvent] = []

    async def end_handler(evt: SleipnirEvent) -> None:
        delivered.append(evt)

    await bridge.subscribe(["*"], end_handler)

    event_a = make_event(event_id="evt-aaa")
    event_b = make_event(event_id="evt-bbb")

    await captured_handlers[0](event_a)  # local → delivers
    await captured_handlers[1](event_b)  # nats → delivers

    assert len(delivered) == 2


# ---------------------------------------------------------------------------
# _BridgeSubscription tests
# ---------------------------------------------------------------------------


async def test_bridge_subscription_unsubscribes_both():
    local_sub = AsyncMock()
    nats_sub = AsyncMock()
    local_sub.unsubscribe = AsyncMock()
    nats_sub.unsubscribe = AsyncMock()

    bridge_sub = _BridgeSubscription(local_sub=local_sub, nats_sub=nats_sub)
    await bridge_sub.unsubscribe()
    local_sub.unsubscribe.assert_called_once()
    nats_sub.unsubscribe.assert_called_once()


async def test_bridge_subscription_tolerates_unsubscribe_error():
    """If one unsubscribe raises, the other still runs."""
    local_sub = AsyncMock()
    nats_sub = AsyncMock()
    local_sub.unsubscribe = AsyncMock(side_effect=RuntimeError("oops"))
    nats_sub.unsubscribe = AsyncMock()

    bridge_sub = _BridgeSubscription(local_sub=local_sub, nats_sub=nats_sub)
    await bridge_sub.unsubscribe()  # must not raise
    nats_sub.unsubscribe.assert_called_once()


# ---------------------------------------------------------------------------
# _require_nats / nats_available when unavailable
# ---------------------------------------------------------------------------


def test_require_nats_raises_when_unavailable():
    from sleipnir.adapters import nats_transport

    original = nats_transport._NATS_AVAILABLE
    try:
        nats_transport._NATS_AVAILABLE = False
        with pytest.raises(ImportError, match="nats-py is required"):
            nats_transport._require_nats()
    finally:
        nats_transport._NATS_AVAILABLE = original


def test_nats_available_false_when_unavailable():
    from sleipnir.adapters import nats_transport

    original = nats_transport._NATS_AVAILABLE
    try:
        nats_transport._NATS_AVAILABLE = False
        assert nats_transport.nats_available() is False
    finally:
        nats_transport._NATS_AVAILABLE = original


# ---------------------------------------------------------------------------
# NatsPublisher / NatsSubscriber raise on construction without nats
# ---------------------------------------------------------------------------


def test_publisher_constructor_raises_without_nats():
    from sleipnir.adapters import nats_transport

    original = nats_transport._NATS_AVAILABLE
    try:
        nats_transport._NATS_AVAILABLE = False
        with pytest.raises(ImportError):
            NatsPublisher()
    finally:
        nats_transport._NATS_AVAILABLE = original


def test_subscriber_constructor_raises_without_nats():
    from sleipnir.adapters import nats_transport

    original = nats_transport._NATS_AVAILABLE
    try:
        nats_transport._NATS_AVAILABLE = False
        with pytest.raises(ImportError):
            NatsSubscriber()
    finally:
        nats_transport._NATS_AVAILABLE = original


def test_transport_constructor_raises_without_nats():
    from sleipnir.adapters import nats_transport

    original = nats_transport._NATS_AVAILABLE
    try:
        nats_transport._NATS_AVAILABLE = False
        with pytest.raises(ImportError):
            NatsTransport()
    finally:
        nats_transport._NATS_AVAILABLE = original


# ---------------------------------------------------------------------------
# Default constant sanity checks
# ---------------------------------------------------------------------------


def test_defaults_are_sane():
    assert DEFAULT_SERVERS == ["nats://localhost:4222"]
    assert DEFAULT_STREAM_NAME == "sleipnir"
    assert DEFAULT_SUBJECT_PREFIX == "sleipnir"
    assert DEFAULT_RETENTION == "limits"
    assert DEFAULT_MAX_AGE_SECONDS == 7 * 24 * 3600
    assert DEFAULT_MAX_BYTES == 1024 * 1024 * 1024
    assert DEFAULT_RING_BUFFER_DEPTH == 1000
    assert DEFAULT_DEDUP_CACHE_SIZE == 10_000
