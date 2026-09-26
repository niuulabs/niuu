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
import base64
import logging
import ssl
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest

from sleipnir.adapters.nats_transport import (
    DEFAULT_ACK_PROGRESS_INTERVAL_S,
    DEFAULT_ACK_WAIT_S,
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_CONSUMER_CHECK_FAILURE_THRESHOLD,
    DEFAULT_CONSUMER_HEALTH_CHECK_INTERVAL_S,
    DEFAULT_CONSUMER_HEALTH_CHECK_JITTER_S,
    DEFAULT_CONSUMER_RECOVERY_BACKOFF_S,
    DEFAULT_DEDUP_CACHE_SIZE,
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DELIVER,
    DEFAULT_MAX_RECONNECT_ATTEMPTS,
    DEFAULT_NAK_BACKOFF_S,
    DEFAULT_RETENTION,
    DEFAULT_RING_BUFFER_DEPTH,
    DEFAULT_SERVERS,
    DEFAULT_STREAM_NAME,
    DEFAULT_SUBJECT_PREFIX,
    NatsBridgeAdapter,
    NatsCorePublisher,
    NatsPublisher,
    NatsSubscriber,
    NatsTransport,
    _BridgeSubscription,
    _build_tls_context,
    _connect_options,
    _ConsumerWatch,
    _decode_nats_message,
    _DeduplicationCache,
    _durable_name_for_subject,
    _HttpConnectNatsClient,
    _HttpConnectTcpTransport,
    _nats_subject_for_event,
    _nats_subjects_for_patterns,
    _parse_retention,
    _sandbox_proxy_url,
    nats_available,
)
from sleipnir.adapters.serialization import deserialize, serialize
from sleipnir.domain.events import SleipnirEvent
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Skip all tests if nats-py is not installed.
# ---------------------------------------------------------------------------

pytest.importorskip("nats", reason="nats-py not installed; skipping NATS tests")

import nats.js.api as js_api  # noqa: E402 — only executed when nats is available
import nats.js.errors as js_errors  # noqa: E402 — only executed when nats is available

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
    mock_client.publish = AsyncMock()
    mock_client.flush = AsyncMock()
    mock_client.subscribe = AsyncMock(return_value=mock_sub)
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


def test_sandbox_proxy_uses_operating_system_all_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "http://10.200.0.1:3128")

    assert _sandbox_proxy_url() == "http://10.200.0.1:3128"


def test_explicit_sandbox_proxy_overrides_operating_system_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example:3128")

    assert _sandbox_proxy_url("http://10.200.0.1:3128") == "http://10.200.0.1:3128"


@pytest.mark.asyncio
async def test_http_connect_transport_tunnels_nats_connection():
    request = b""

    async def proxy(reader, writer):
        nonlocal request
        request = await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\nINFO {}\r\n")
        await writer.drain()
        await reader.read()
        writer.close()

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transport = _HttpConnectTcpTransport(f"http://127.0.0.1:{port}")
    try:
        await transport.connect(urlparse("nats://nats.internal:4222"), 1024, 2)
        assert await transport.readline() == b"INFO {}\r\n"
        assert request.startswith(b"CONNECT nats.internal:4222 HTTP/1.1\r\n")
    finally:
        transport.close()
        await transport.wait_closed()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_connect_client_does_not_replace_unconnected_transport():
    client = _HttpConnectNatsClient("http://127.0.0.1:3128")
    client.options["connect_timeout"] = 2
    server = MagicMock(uri=urlparse("nats://nats.internal:4222"))

    with patch.object(_HttpConnectTcpTransport, "connect", new_callable=AsyncMock) as connect:
        await client._connect_to_server(server)

    assert isinstance(client._transport, _HttpConnectTcpTransport)
    connect.assert_awaited_once()


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


def test_tls_context_loads_inline_ca_bundle():
    context = MagicMock(spec=ssl.SSLContext)
    with patch("sleipnir.adapters.nats_transport.ssl.create_default_context", return_value=context):
        result = _build_tls_context(tls_ca_pem="certificate-pem")

    assert result is context
    context.load_verify_locations.assert_called_once_with(cadata="certificate-pem")


def test_nats_file_paths_expand_home_directory():
    with patch("sleipnir.adapters.nats_transport.ssl.create_default_context") as create:
        _build_tls_context(tls_ca_file="~/.ravn/nats-ca.crt")
    create.assert_called_once_with(cafile=str(Path.home() / ".ravn/nats-ca.crt"))

    options = _connect_options(nkeys_seed_file="~/.ravn/consumer.nk")
    assert options["nkeys_seed"] == str(Path.home() / ".ravn/consumer.nk")


def test_tls_context_can_accept_legacy_private_ca_without_disabling_verification():
    context = _build_tls_context(tls_legacy_ca=True)

    assert context is not None
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.verify_flags & getattr(ssl, "VERIFY_X509_STRICT", 0) == 0


# ---------------------------------------------------------------------------
# NatsPublisher tests
# ---------------------------------------------------------------------------


async def test_publisher_start_connects_and_ensures_stream(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher(servers=["nats://localhost:4222"])
    await pub.start()
    mock_module.connect.assert_called_once()
    client.jetstream.assert_called_once_with()
    js.stream_info.assert_called_once_with(DEFAULT_STREAM_NAME)


async def test_publisher_start_uses_configured_jetstream_domain(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher(
        servers=["nats://localhost:4222"],
        jetstream_domain="ymir",
        ensure_stream=False,
    )

    await pub.start()

    client.jetstream.assert_called_once_with(domain="ymir")
    js.stream_info.assert_not_called()


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


async def test_publisher_publish_sends_explicit_additional_subjects(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsPublisher(subject_prefix="obs.ymir")
    await pub.start()
    event = make_event(
        event_type="flock.learning.proposed",
        payload={
            "learning_id": "resident:ymir:inspect-proof",
            "additional_nats_subjects": [
                "flock.k8s.ymir.flock.learning.proposed",
                "obs.ymir.flock.learning.proposed",
            ],
        },
    )

    await pub.publish(event)

    subjects = [call.args[0] for call in js.publish.call_args_list]
    assert subjects == [
        "obs.ymir.flock.learning.proposed",
        "flock.k8s.ymir.flock.learning.proposed",
    ]
    payloads = [call.args[1] for call in js.publish.call_args_list]
    assert all(_decode_nats_message(payload).event_id == event.event_id for payload in payloads)


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
# NatsCorePublisher tests
# ---------------------------------------------------------------------------


async def test_core_publisher_publish_sends_core_subject(mock_nats):
    mock_module, client, js, _ = mock_nats
    pub = NatsCorePublisher(subject_prefix="obs.cmd.valhalla")

    await pub.start()
    event = make_event(event_type="learning.adoption.recorded")
    await pub.publish(event)

    client.jetstream.assert_not_called()
    client.publish.assert_called_once()
    subject, payload = client.publish.call_args.args
    assert subject == "obs.cmd.valhalla.learning.adoption.recorded"
    assert _decode_nats_message(payload).event_id == event.event_id
    client.flush.assert_called_once()
    await pub.stop()


async def test_core_publisher_publish_before_start_raises():
    pub = NatsCorePublisher()

    with pytest.raises(RuntimeError, match="not started"):
        await pub.publish(make_event())


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
    client.jetstream.assert_called_once_with()
    assert sub._running is True


async def test_subscriber_start_uses_configured_jetstream_domain(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(jetstream_domain="ymir", ensure_stream=False)

    await sub.start()

    client.jetstream.assert_called_once_with(domain="ymir")
    js.stream_info.assert_not_called()
    await sub.stop()


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


async def test_subscriber_adds_extra_stream_subjects(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(
        subject_prefix="obs.valhalla",
        stream_name="obs-valhalla-events",
        extra_subscriptions=[
            {"subject": "flock.k8s.>", "stream_name": "flock-k8s-events"},
        ],
    )
    await sub.start()
    await sub.subscribe(["flock.learning.proposed"], AsyncMock())

    calls = js.subscribe.call_args_list
    assert [call.args[0] for call in calls] == [
        "obs.valhalla.flock.learning.proposed",
        "flock.k8s.>",
    ]
    assert [call.kwargs["stream"] for call in calls] == [
        "obs-valhalla-events",
        "flock-k8s-events",
    ]
    await sub.stop()


async def test_subscriber_scopes_extra_stream_subjects_to_event_types(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(
        subject_prefix="obs.valhalla",
        stream_name="obs-valhalla-events",
        extra_subscriptions=[
            {
                "subject": "flock.k8s.>",
                "stream_name": "flock-k8s-events",
                "event_types": ["flock.learning.*", "learning.adoption.recorded"],
            },
        ],
    )
    await sub.start()

    await sub.subscribe(["signal.kubernetes.event"], AsyncMock())
    assert [call.args[0] for call in js.subscribe.call_args_list] == [
        "obs.valhalla.signal.kubernetes.event"
    ]

    js.subscribe.reset_mock()
    await sub.subscribe(["flock.learning.proposed"], AsyncMock())
    assert [call.args[0] for call in js.subscribe.call_args_list] == [
        "obs.valhalla.flock.learning.proposed",
        "flock.k8s.>",
    ]
    assert [call.kwargs["stream"] for call in js.subscribe.call_args_list] == [
        "obs-valhalla-events",
        "flock-k8s-events",
    ]
    await sub.stop()


async def test_subscriber_adds_core_subscriptions(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(
        subject_prefix="obs.valhalla",
        stream_name="obs-valhalla-events",
        core_subscriptions=[{"subject": "obs.cmd.valhalla.>"}],
    )
    await sub.start()
    await sub.subscribe(["learning.adoption.recorded"], AsyncMock())

    js.subscribe.assert_called_once()
    client.subscribe.assert_called_once()
    assert client.subscribe.call_args.args[0] == "obs.cmd.valhalla.>"
    await sub.stop()


async def test_subscriber_core_stream_uses_live_core_nats_only(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(
        subject_prefix="obs.workshop",
        stream_name="core",
    )
    await sub.start()
    await sub.subscribe(["*"], AsyncMock())

    js.subscribe.assert_not_called()
    client.subscribe.assert_called_once()
    assert client.subscribe.call_args.args[0] == "obs.workshop.>"
    await sub.stop()


async def test_subscriber_disables_nats_py_auto_ack(mock_nats):
    """nats-py acks when the callback returns unless manual_ack is set.

    The callback only queues the message, so auto-ack would acknowledge it
    before the handler runs: exactly the at-most-once bug this guards.
    """
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(consumer_group="my-service")
    await sub.start()
    await sub.subscribe(["ravn.*"], AsyncMock())
    assert js.subscribe.call_args.kwargs["manual_ack"] is True
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
    config = NatsSubscriber()._build_consumer_config()
    assert config.deliver_policy == js_api.DeliverPolicy.NEW


def test_build_consumer_config_replay_from_sequence():
    config = NatsSubscriber(replay_from_sequence=42)._build_consumer_config()
    assert config.deliver_policy == js_api.DeliverPolicy.BY_START_SEQUENCE
    assert config.opt_start_seq == 42


def test_build_consumer_config_replay_from_time():
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    config = NatsSubscriber(replay_from_time=ts)._build_consumer_config()
    assert config.deliver_policy == js_api.DeliverPolicy.BY_START_TIME
    assert config.opt_start_time == ts


def test_build_consumer_config_sequence_takes_priority_over_time():
    """When both replay options are set, sequence takes priority."""
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    config = NatsSubscriber(replay_from_sequence=10, replay_from_time=ts)._build_consumer_config()
    assert config.deliver_policy == js_api.DeliverPolicy.BY_START_SEQUENCE


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"replay_from_sequence": 3},
        {"replay_from_time": datetime(2026, 1, 1, tzinfo=UTC)},
    ],
)
def test_build_consumer_config_sets_explicit_ack_and_redelivery_limits(kwargs):
    sub = NatsSubscriber(
        max_deliver=4,
        ack_wait_s=12.0,
        ack_progress_interval_s=3.0,
        ring_buffer_depth=64,
        **kwargs,
    )
    config = sub._build_consumer_config()
    assert config.ack_policy == js_api.AckPolicy.EXPLICIT
    assert config.ack_wait == 12.0
    assert config.max_deliver == 4
    assert config.max_ack_pending == 64, "max_ack_pending defaults to the queue depth"


def test_build_consumer_config_uses_explicit_max_ack_pending():
    config = NatsSubscriber(max_ack_pending=10)._build_consumer_config()
    assert config.max_ack_pending == 10


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_deliver": 0}, "max_deliver"),
        ({"ack_wait_s": 0}, "ack_wait_s"),
        ({"ack_wait_s": 5.0, "ack_progress_interval_s": 5.0}, "ack_progress_interval_s"),
        ({"ack_progress_interval_s": 0}, "ack_progress_interval_s"),
        ({"max_ack_pending": 0}, "max_ack_pending"),
        ({"nak_backoff_s": []}, "nak_backoff_s"),
        ({"nak_backoff_s": [1.0, -1.0]}, "nak_backoff_s"),
        ({"consumer_health_check_interval_s": 0}, "consumer_health_check_interval_s"),
        ({"consumer_health_check_interval_s": -1.0}, "consumer_health_check_interval_s"),
        ({"consumer_recovery_backoff_s": []}, "consumer_recovery_backoff_s"),
        ({"consumer_recovery_backoff_s": [1.0, -1.0]}, "consumer_recovery_backoff_s"),
        ({"consumer_check_failure_threshold": 0}, "consumer_check_failure_threshold"),
        ({"consumer_health_check_jitter_s": -1.0}, "consumer_health_check_jitter_s"),
    ],
)
def test_subscriber_rejects_invalid_delivery_settings(kwargs, match):
    with pytest.raises(ValueError, match=match):
        NatsSubscriber(**kwargs)


# ---------------------------------------------------------------------------
# NatsSubscriber — message callback delivery tests
# ---------------------------------------------------------------------------


class _FakeMsg:
    """A JetStream message double that records every acknowledgement it is sent."""

    def __init__(
        self,
        data: bytes,
        subject: str = "sleipnir.test.event",
        *,
        num_delivered: int = 1,
        stream_seq: int = 7,
        log: list[str] | None = None,
    ) -> None:
        self.data = data
        self.subject = subject
        self.metadata = SimpleNamespace(
            num_delivered=num_delivered,
            sequence=SimpleNamespace(stream=stream_seq, consumer=num_delivered),
        )
        self.calls: list[str] = []
        self.nak_delays: list[float | None] = []
        self.errors: dict[str, Exception] = {}
        self._log = log

    def _record(self, op: str) -> None:
        if op in self.errors:
            raise self.errors[op]
        self.calls.append(op)
        if self._log is not None:
            self._log.append(op)

    async def ack(self) -> None:
        self._record("ack")

    async def nak(self, delay: float | None = None) -> None:
        self._record("nak")
        self.nak_delays.append(delay)

    async def term(self) -> None:
        self._record("term")

    async def in_progress(self) -> None:
        self._record("in_progress")


def _event_bytes(n: int = 0, event_type: str = "test.event") -> bytes:
    return serialize(make_event(event_id=f"evt-{n}", event_type=event_type, payload={"n": n}))


async def _drain(sub: NatsSubscriber) -> None:
    """Wait until every queued delivery has been handled and settled."""
    for subscription in list(sub._subscriptions):
        await asyncio.wait_for(subscription._queue.join(), timeout=2.0)


async def test_subscriber_on_message_delivers_to_handler(mock_nats):
    """The _on_message callback dispatches a valid event to the handler, then acks."""
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
    msg = _FakeMsg(serialize(event), subject="sleipnir.ravn.tool.complete")

    await on_message(msg)
    await _drain(sub)

    assert [evt.event_id for evt in received] == [event.event_id]
    assert msg.calls == ["ack"]
    await sub.stop()


async def test_subscriber_on_message_acks_after_processing(mock_nats):
    """The ack is sent only after the handler has returned — never on receipt."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    order: list[str] = []
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        order.append("handler-start")
        handler_started.set()
        await release_handler.wait()
        order.append("handler-done")

    await sub.subscribe(["ravn.*"], handler)
    on_message = js.subscribe.call_args[1]["cb"]
    msg = _FakeMsg(serialize(make_event(event_type="ravn.tool.complete")), log=order)

    await on_message(msg)
    await asyncio.wait_for(handler_started.wait(), timeout=1.0)
    assert msg.calls == [], "the message must not be acked while its handler is running"

    release_handler.set()
    await _drain(sub)

    assert order == ["handler-start", "handler-done", "ack"]
    await sub.stop()


async def test_subscriber_naks_with_backoff_when_handler_raises(mock_nats):
    """A failing handler gets its message nak'd with the configured delay, never acked."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(nak_backoff_s=[2.0, 7.0])
    await sub.start()

    async def handler(evt: SleipnirEvent) -> None:
        raise RuntimeError("downstream unavailable")

    await sub.subscribe(["test.*"], handler)
    on_message = js.subscribe.call_args[1]["cb"]
    attempts = [_FakeMsg(_event_bytes(), num_delivered=n) for n in (1, 2, 3)]

    for msg in attempts:
        await on_message(msg)
    await _drain(sub)

    assert [msg.calls for msg in attempts] == [["nak"], ["nak"], ["nak"]]
    # The last backoff entry repeats for attempts beyond the list.
    assert [msg.nak_delays for msg in attempts] == [[2.0], [7.0], [7.0]]
    assert sub.stats()["handler_failures"] == 3
    js.publish.assert_not_called()
    await sub.stop()


async def test_subscriber_redelivery_is_acked_once_handler_succeeds(mock_nats):
    """The redelivery that follows a nak reaches the handler again and is then acked."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handled: list[str] = []

    async def flaky_handler(evt: SleipnirEvent) -> None:
        handled.append(evt.event_id)
        if len(handled) == 1:
            raise RuntimeError("transient")

    await sub.subscribe(["test.*"], flaky_handler)
    on_message = js.subscribe.call_args[1]["cb"]
    first = _FakeMsg(_event_bytes(), num_delivered=1)
    redelivery = _FakeMsg(_event_bytes(), num_delivered=2)

    await on_message(first)
    await _drain(sub)
    await on_message(redelivery)
    await _drain(sub)

    assert handled == ["evt-0", "evt-0"]
    assert first.calls == ["nak"]
    assert redelivery.calls == ["ack"]
    await sub.stop()


async def test_subscriber_dead_letters_after_max_deliver(mock_nats):
    """The final failed delivery is published to the DLQ and terminated, not nak'd."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(max_deliver=3)
    await sub.start()

    async def handler(evt: SleipnirEvent) -> None:
        raise ValueError("poison payload")

    await sub.subscribe(["test.*"], handler)
    on_message = js.subscribe.call_args[1]["cb"]
    js.publish.reset_mock()
    msg = _FakeMsg(_event_bytes(), num_delivered=3, stream_seq=42)

    await on_message(msg)
    await _drain(sub)

    assert msg.calls == ["term"]
    dlq_subject, dlq_payload = js.publish.call_args[0]
    assert dlq_subject == "sleipnir.system.dlq.message"
    record = deserialize(dlq_payload)
    assert record.event_type == "system.dlq.message"
    assert record.payload["deliveries"] == 3
    assert record.payload["stream_sequence"] == 42
    assert "poison payload" in record.payload["reason"]
    original = deserialize(base64.b64decode(record.payload["raw_base64"]))
    assert original.event_id == "evt-0"
    assert sub.stats()["dlq_published"] == 1
    await sub.stop()


async def test_subscriber_keeps_exhausted_message_when_dlq_publish_fails(mock_nats):
    """A message that could not be dead-lettered is nak'd again, never acked or terminated."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(max_deliver=1, nak_backoff_s=[4.0])
    await sub.start()

    async def handler(evt: SleipnirEvent) -> None:
        raise ValueError("poison payload")

    await sub.subscribe(["test.*"], handler)
    on_message = js.subscribe.call_args[1]["cb"]
    js.publish.side_effect = RuntimeError("broker down")
    msg = _FakeMsg(_event_bytes(), num_delivered=1)

    await on_message(msg)
    await _drain(sub)

    assert msg.calls == ["nak"]
    assert msg.nak_delays == [4.0]
    assert sub.stats()["dlq_publish_failures"] == 1
    assert sub.stats()["dlq_published"] == 0
    await sub.stop()


async def test_failing_dlq_record_is_terminated_without_another_dlq_record(mock_nats):
    """Dead-lettering a DLQ record would loop, so an exhausted one is only terminated."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(max_deliver=1)
    await sub.start()

    async def handler(evt: SleipnirEvent) -> None:
        raise RuntimeError("cannot archive")

    await sub.subscribe(["system.dlq.message"], handler)
    on_message = js.subscribe.call_args[1]["cb"]
    js.publish.reset_mock()
    msg = _FakeMsg(
        _event_bytes(event_type="system.dlq.message"),
        subject="sleipnir.system.dlq.message",
    )

    await on_message(msg)
    await _drain(sub)

    assert msg.calls == ["term"]
    js.publish.assert_not_called()
    await sub.stop()


async def test_subscriber_on_message_drops_expired_ttl(mock_nats):
    """Expired TTL events are not dispatched; they are acked as deliberately skipped."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    received: list[SleipnirEvent] = []
    await sub.subscribe(["*"], lambda evt: received.append(evt))

    on_message = js.subscribe.call_args[1]["cb"]
    msg = _FakeMsg(serialize(make_event(ttl=0)))

    await on_message(msg)
    await asyncio.sleep(0.02)
    assert len(received) == 0
    assert msg.calls == ["ack"]
    await sub.stop()


async def test_subscriber_on_message_drops_pattern_mismatch(mock_nats):
    """Pattern-mismatched events are not dispatched; they are acked as not ours."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    received: list[SleipnirEvent] = []
    await sub.subscribe(["ting.*"], lambda evt: received.append(evt))

    on_message = js.subscribe.call_args[1]["cb"]
    event = make_event(event_type="ravn.tool.complete")  # won't match "ting.*"
    msg = _FakeMsg(serialize(event))

    await on_message(msg)
    await asyncio.sleep(0.02)
    assert len(received) == 0
    assert msg.calls == ["ack"]
    await sub.stop()


async def test_subscriber_on_message_dead_letters_bad_payload(mock_nats):
    """Malformed payloads are dead-lettered and terminated; the handler never sees them."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    received: list[SleipnirEvent] = []
    await sub.subscribe(["*"], lambda evt: received.append(evt))

    on_message = js.subscribe.call_args[1]["cb"]
    msg = _FakeMsg(b"\xff\xfe\xfd")  # malformed

    await on_message(msg)
    await asyncio.sleep(0.02)
    assert len(received) == 0
    assert msg.calls == ["term"]
    assert sub.stats()["dlq_published"] == 1
    await sub.stop()


async def test_subscriber_on_message_skips_when_not_running(mock_nats):
    """Messages received after stop() are left unacked so JetStream redelivers them."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    received: list[SleipnirEvent] = []
    await sub.subscribe(["*"], lambda evt: received.append(evt))

    on_message = js.subscribe.call_args[1]["cb"]
    sub._running = False  # simulate stopped state

    msg = _FakeMsg(serialize(make_event()))

    await on_message(msg)
    await asyncio.sleep(0.02)
    assert len(received) == 0
    assert msg.calls == []
    await sub.stop()


async def test_full_queue_blocks_the_callback_instead_of_dropping(mock_nats):
    """Backpressure: a full queue blocks the NATS callback and withholds acks."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(ring_buffer_depth=1)
    await sub.start()
    gate = asyncio.Event()
    handled: list[int] = []

    async def slow_handler(evt: SleipnirEvent) -> None:
        await gate.wait()
        handled.append(evt.payload["n"])

    handle = await sub.subscribe(["test.*"], slow_handler)
    on_message = js.subscribe.call_args[1]["cb"]
    msgs = [_FakeMsg(_event_bytes(n)) for n in range(3)]

    await on_message(msgs[0])
    while not handle._queue.empty():  # the consumer takes msg 0 into the handler
        await asyncio.sleep(0)
    await on_message(msgs[1])  # fills the depth-1 queue
    blocked = asyncio.create_task(on_message(msgs[2]))
    await asyncio.sleep(0.05)

    assert not blocked.done(), "a full queue must block the callback, not drop an event"
    assert [msg.calls for msg in msgs] == [[], [], []]

    gate.set()
    await asyncio.wait_for(blocked, timeout=1.0)
    await _drain(sub)

    assert handled == [0, 1, 2]
    assert [msg.calls for msg in msgs] == [["ack"], ["ack"], ["ack"]]
    await sub.stop()


async def test_unsettled_messages_get_in_progress_pings(mock_nats):
    """Queued and in-handler messages are kept alive; settled ones are not pinged."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(ack_wait_s=1.0, ack_progress_interval_s=0.02)
    await sub.start()
    gate = asyncio.Event()

    async def slow_handler(evt: SleipnirEvent) -> None:
        await gate.wait()

    await sub.subscribe(["test.*"], slow_handler)
    on_message = js.subscribe.call_args[1]["cb"]
    in_handler = _FakeMsg(_event_bytes(0))
    queued = _FakeMsg(_event_bytes(1))

    await on_message(in_handler)
    await on_message(queued)
    await asyncio.sleep(0.1)

    assert in_handler.calls.count("in_progress") >= 2
    assert queued.calls.count("in_progress") >= 2
    assert "ack" not in in_handler.calls + queued.calls

    gate.set()
    await _drain(sub)
    pings = in_handler.calls.count("in_progress")
    await asyncio.sleep(0.1)

    assert in_handler.calls.count("in_progress") == pings
    assert in_handler.calls[-1] == "ack"
    assert queued.calls[-1] == "ack"
    await sub.stop()


async def test_unsubscribe_releases_unsettled_messages(mock_nats):
    """Unsubscribing stops the NATS subscriptions and naks what was not handled."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handler_started = asyncio.Event()

    async def stuck_handler(evt: SleipnirEvent) -> None:
        handler_started.set()
        await asyncio.Event().wait()

    handle = await sub.subscribe(["test.*"], stuck_handler)
    on_message = js.subscribe.call_args[1]["cb"]
    in_handler = _FakeMsg(_event_bytes(0))
    queued = _FakeMsg(_event_bytes(1))
    await on_message(in_handler)
    await asyncio.wait_for(handler_started.wait(), timeout=1.0)
    await on_message(queued)

    await handle.unsubscribe()

    nats_sub.unsubscribe.assert_awaited_once()
    assert in_handler.calls == ["nak"]
    assert queued.calls == ["nak"]
    assert in_handler.nak_delays == [None], "released messages are redelivered immediately"
    assert sub._subscriptions == []

    late = _FakeMsg(_event_bytes(2))
    await on_message(late)
    assert late.calls == [], "an inactive subscription leaves late messages for redelivery"
    await sub.stop()


async def test_stop_releases_in_flight_message(mock_nats):
    """stop() hands the message a handler was still working on back to JetStream."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handler_started = asyncio.Event()

    async def stuck_handler(evt: SleipnirEvent) -> None:
        handler_started.set()
        await asyncio.Event().wait()

    await sub.subscribe(["test.*"], stuck_handler)
    on_message = js.subscribe.call_args[1]["cb"]
    msg = _FakeMsg(_event_bytes())
    await on_message(msg)
    await asyncio.wait_for(handler_started.wait(), timeout=1.0)

    await sub.stop()

    assert msg.calls == ["nak"]
    client.drain.assert_awaited_once()


async def test_settle_failure_is_counted_and_logged(mock_nats, caplog):
    """A nak that cannot be sent is counted; JetStream redelivers after ack_wait."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber()
    await sub.start()

    async def handler(evt: SleipnirEvent) -> None:
        raise RuntimeError("boom")

    await sub.subscribe(["test.*"], handler)
    on_message = js.subscribe.call_args[1]["cb"]
    msg = _FakeMsg(_event_bytes())
    msg.errors["nak"] = RuntimeError("connection closed")

    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.nats_transport"):
        await on_message(msg)
        await _drain(sub)

    assert sub.stats()["nak_failures"] == 1
    assert any("nak failed" in record.message for record in caplog.records)
    await sub.stop()


async def test_core_subscription_handler_failure_is_not_redelivered(mock_nats, caplog):
    """Core NATS has no acks: a failing handler is logged and counted (at-most-once)."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(subject_prefix="obs.workshop", stream_name="core")
    await sub.start()

    async def handler(evt: SleipnirEvent) -> None:
        raise RuntimeError("boom")

    await sub.subscribe(["*"], handler)
    on_message = client.subscribe.call_args.kwargs["cb"]

    with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.nats_transport"):
        await on_message(SimpleNamespace(data=serialize(make_event())))
        await _drain(sub)

    assert sub.stats()["handler_failures"] == 1
    assert any("at-most-once" in record.message for record in caplog.records)
    await sub.stop()


async def test_subscribe_failure_tears_down_the_partial_subscription(mock_nats):
    mock_module, client, js, _ = mock_nats
    js.subscribe.side_effect = RuntimeError("consumer create denied")
    sub = NatsSubscriber()
    await sub.start()

    with pytest.raises(RuntimeError, match="consumer create denied"):
        await sub.subscribe(["ravn.*"], AsyncMock())

    assert sub._subscriptions == []
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


async def test_transport_forwards_delivery_settings_to_subscriber(mock_nats):
    transport = NatsTransport(
        max_deliver=3,
        ack_wait_s=9.0,
        ack_progress_interval_s=2.0,
        max_ack_pending=7,
        nak_backoff_s=[0.5],
    )
    subscriber = transport._subscriber
    assert subscriber._max_deliver == 3
    assert subscriber._ack_wait_s == 9.0
    assert subscriber._ack_progress_interval_s == 2.0
    assert subscriber._max_ack_pending == 7
    assert subscriber._nak_backoff_s == [0.5]


async def test_transport_jetstream_domain_forwarded_to_publisher_and_subscriber(mock_nats):
    mock_module, client, js, _ = mock_nats
    transport = NatsTransport(jetstream_domain="ymir", ensure_stream=False)

    await transport.start()

    assert client.jetstream.call_count == 2
    assert client.jetstream.call_args_list[0].kwargs == {"domain": "ymir"}
    assert client.jetstream.call_args_list[1].kwargs == {"domain": "ymir"}
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


async def test_bridge_failed_handling_does_not_suppress_redelivery():
    """Only a successful handling marks an event seen; the retry must reach the handler."""
    captured_handlers: list = []

    async def _capture_subscribe(event_types, handler):
        captured_handlers.append(handler)
        return AsyncMock()

    local_sub = AsyncMock()
    nats_sub = AsyncMock()
    local_sub.subscribe = _capture_subscribe
    nats_sub.subscribe = _capture_subscribe
    bridge = NatsBridgeAdapter(
        local_publisher=AsyncMock(),
        local_subscriber=local_sub,
        nats_publisher=AsyncMock(),
        nats_subscriber=nats_sub,
    )
    attempts: list[str] = []

    async def flaky_handler(evt: SleipnirEvent) -> None:
        attempts.append(evt.event_id)
        if len(attempts) == 1:
            raise RuntimeError("transient")

    await bridge.subscribe(["*"], flaky_handler)
    event = make_event(event_id="evt-retry")

    with pytest.raises(RuntimeError, match="transient"):
        await captured_handlers[1](event)  # NATS copy fails → transport naks it
    await captured_handlers[1](event)  # the redelivery is handled
    await captured_handlers[0](event)  # the local copy is now a duplicate

    assert attempts == ["evt-retry", "evt-retry"]


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
    assert DEFAULT_MAX_DELIVER == 5
    assert DEFAULT_ACK_WAIT_S == 30.0
    assert 0 < DEFAULT_ACK_PROGRESS_INTERVAL_S < DEFAULT_ACK_WAIT_S
    assert DEFAULT_NAK_BACKOFF_S == (1.0, 5.0, 30.0, 60.0)


async def test_each_js_subscription_gets_a_fresh_consumer_config(mock_nats):
    """Sharing one ConsumerConfig across push subscriptions makes nats-py
    stamp the same deliver_subject onto every consumer, so each message fans
    out to every callback (observed as N duplicate deliveries for N subjects).
    Every JetStream subscription must receive its own config instance."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handler = AsyncMock()
    await sub.subscribe(
        ["learning.promoted", "flock.learning.proposed", "flock.learning.adopted"],
        handler,
    )
    configs = [call.kwargs["config"] for call in js.subscribe.call_args_list]
    assert len(configs) == 3
    assert len({id(config) for config in configs}) == 3
    await sub.stop()


# ---------------------------------------------------------------------------
# NIU-1042: error-path hardening — every failure is observable
# ---------------------------------------------------------------------------


async def _subscribed_callback(sub: NatsSubscriber, js, patterns: list[str]):
    handler = AsyncMock()
    await sub.subscribe(patterns, handler)
    return js.subscribe.call_args_list[0].kwargs["cb"]


async def test_ack_failure_is_counted_and_logged(mock_nats, caplog) -> None:
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    callback = await _subscribed_callback(sub, js, ["test.*"])

    msg = _FakeMsg(_event_bytes())
    msg.errors["ack"] = RuntimeError("ack broke")
    with caplog.at_level("WARNING"):
        await callback(msg)
        await _drain(sub)
    assert sub.stats()["ack_failures"] == 1
    assert any("ack failed" in record.message for record in caplog.records)
    await sub.stop()


async def test_decode_failure_publishes_dlq_record(mock_nats) -> None:
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    callback = await _subscribed_callback(sub, js, ["test.*"])
    js.publish.reset_mock()

    msg = _FakeMsg(b"\x00not-msgpack-garbage", stream_seq=11)
    await callback(msg)

    stats = sub.stats()
    assert stats["decode_failures"] == 1
    assert stats["dlq_published"] == 1
    assert msg.calls == ["term"], "a dead-lettered message is terminated, not acked"

    dlq_subject, dlq_payload = js.publish.call_args[0]
    assert dlq_subject == "sleipnir.system.dlq.message"
    record = deserialize(dlq_payload)
    assert record.event_type == "system.dlq.message"
    assert record.payload["original_subject"] == "sleipnir.test.event"
    assert record.payload["stream_sequence"] == 11
    assert base64.b64decode(record.payload["raw_base64"]) == b"\x00not-msgpack-garbage"
    await sub.stop()


async def test_poison_dlq_record_is_not_dead_lettered_again(mock_nats) -> None:
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    callback = await _subscribed_callback(sub, js, ["*"])
    js.publish.reset_mock()

    msg = _FakeMsg(b"garbage", subject="sleipnir.system.dlq.message")
    await callback(msg)

    stats = sub.stats()
    assert stats["decode_failures"] == 1
    assert stats["dlq_published"] == 0
    assert msg.calls == ["term"]
    js.publish.assert_not_called()
    await sub.stop()


async def test_dlq_publish_failure_leaves_message_for_redelivery(mock_nats) -> None:
    """If the DLQ record cannot be published, the message is nak'd — never acked."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(nak_backoff_s=[3.0])
    await sub.start()
    callback = await _subscribed_callback(sub, js, ["test.*"])
    js.publish.side_effect = RuntimeError("broker down")

    msg = _FakeMsg(b"garbage")
    await callback(msg)

    stats = sub.stats()
    assert stats["dlq_publish_failures"] == 1
    assert stats["dlq_published"] == 0
    assert msg.calls == ["nak"]
    assert msg.nak_delays == [3.0]
    await sub.stop()


async def test_ensure_stream_failure_raises_at_startup(mock_nats) -> None:
    mock_module, client, js, nats_sub = mock_nats
    js.stream_info.side_effect = RuntimeError("no such stream")
    js.add_stream.side_effect = RuntimeError("permission denied")
    sub = NatsSubscriber()
    with pytest.raises(RuntimeError, match="could not be created"):
        await sub.start()


async def test_insecure_tls_logs_prominent_warning(caplog) -> None:
    from sleipnir.adapters.nats_transport import _build_tls_context

    with caplog.at_level("WARNING"):
        context = _build_tls_context(tls_insecure_skip_verify=True)
    assert context is not None
    assert any("DISABLED" in record.message for record in caplog.records)


async def test_publish_timeout_raises_with_subject(mock_nats) -> None:
    import asyncio as _asyncio

    mock_module, client, js, nats_sub = mock_nats

    async def _hang(*args, **kwargs):
        await _asyncio.sleep(30)

    js.publish.side_effect = _hang
    pub = NatsPublisher(publish_timeout_s=0.05)
    await pub.start()

    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from sleipnir.domain.events import SleipnirEvent as _Event

    event = _Event(
        event_type="test.event",
        source="t",
        payload={},
        summary="t",
        urgency=0.1,
        domain="infrastructure",
        timestamp=_dt.now(_UTC),
    )
    with pytest.raises(TimeoutError, match="sleipnir.test.event"):
        await pub.publish(event)


# ---------------------------------------------------------------------------
# Consumer/stream recovery (fix/sleipnir-nats-consumer-recovery)
#
# A NATS JetStream store can be reset (streams recreated empty) without the
# process restarting.  nats-py's push-subscription heartbeats do not surface
# a missed-heartbeat signal to the application for non-ordered consumers, so
# recovery is driven by polling ``PushSubscription.consumer_info()`` — the
# same JetStream API call nats-py itself uses to bind to a durable.
# ---------------------------------------------------------------------------


async def test_subscribe_registers_a_consumer_watch(mock_nats):
    """Each JetStream push subscription gets a watch so it can be recovered."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["ravn.tool.complete"], AsyncMock())

    assert len(handle.consumer_watches) == 1
    watch = handle.consumer_watches[0]
    assert isinstance(watch, _ConsumerWatch)
    assert watch.subject == "sleipnir.ravn.tool.complete"
    assert watch.stream_name == DEFAULT_STREAM_NAME
    assert watch.nats_sub is nats_sub
    await sub.stop()


async def test_core_subscription_is_not_watched(mock_nats):
    """Core NATS subscriptions have no consumer to lose, so they are not watched."""
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(subject_prefix="obs.workshop", stream_name="core")
    await sub.start()
    handle = await sub.subscribe(["*"], AsyncMock())

    assert handle.consumer_watches == []
    await sub.stop()


async def test_consumer_recovery_defaults():
    sub = NatsSubscriber()
    assert sub._consumer_health_check_interval_s == DEFAULT_CONSUMER_HEALTH_CHECK_INTERVAL_S
    assert sub._consumer_recovery_backoff_s == list(DEFAULT_CONSUMER_RECOVERY_BACKOFF_S)


async def test_health_check_ignores_transient_errors(mock_nats, caplog):
    """A timeout or connectivity blip is logged and left alone — never recreated."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]
    nats_sub.consumer_info = AsyncMock(side_effect=TimeoutError("slow"))

    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.nats_transport"):
        await sub._check_consumer_watch(handle, watch)

    assert sub.stats()["consumer_lost"] == 0
    js.subscribe.assert_called_once()
    assert watch.nats_sub is nats_sub
    assert any("transient" in record.message for record in caplog.records)
    await sub.stop()


async def test_health_check_skips_when_subscription_inactive(mock_nats):
    """An unsubscribed subscription is never recovered."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]
    await handle.unsubscribe()
    nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())

    await sub._check_consumer_watch(handle, watch)

    assert sub.stats()["consumer_lost"] == 0
    await sub.stop()


async def test_health_check_recreates_deleted_consumer(mock_nats, caplog):
    """A confirmed 404 recreates the consumer with the same subject and stream."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]

    nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())
    new_nats_sub = AsyncMock()
    new_nats_sub.unsubscribe = AsyncMock()
    js.subscribe = AsyncMock(return_value=new_nats_sub)

    with caplog.at_level(logging.INFO, logger="sleipnir.adapters.nats_transport"):
        await sub._check_consumer_watch(handle, watch)

    assert sub.stats()["consumer_lost"] == 1
    assert sub.stats()["consumer_recovered"] == 1
    assert watch.nats_sub is new_nats_sub
    assert new_nats_sub in handle.nats_subs
    assert nats_sub not in handle.nats_subs
    nats_sub.unsubscribe.assert_awaited_once()
    assert js.subscribe.call_args.kwargs["stream"] == watch.stream_name
    assert any(
        record.levelname == "ERROR" and "is missing" in record.message for record in caplog.records
    )
    assert any(
        record.levelname == "INFO" and "recovered" in record.message for record in caplog.records
    )
    await sub.stop()


async def test_recovery_retries_with_backoff_while_stream_absent(mock_nats, caplog):
    """A stream still absent on the first recreation attempt is retried, not given up on."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(consumer_recovery_backoff_s=[0.01])
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]

    nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())
    recovered_sub = AsyncMock()
    recovered_sub.unsubscribe = AsyncMock()
    js.subscribe = AsyncMock(side_effect=[RuntimeError("stream not found"), recovered_sub])

    with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.nats_transport"):
        await sub._check_consumer_watch(handle, watch)

    assert sub.stats()["consumer_recovery_failures"] == 1
    assert sub.stats()["consumer_recovered"] == 1
    assert watch.nats_sub is recovered_sub
    assert any("retrying in" in record.message for record in caplog.records)
    await sub.stop()


async def test_watchdog_recovers_lost_consumer_in_the_background(mock_nats):
    """The periodic watchdog task itself detects and recovers loss — not just the helper."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(consumer_health_check_interval_s=0.02, consumer_health_check_jitter_s=0.0)
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())

    nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())
    recovered_sub = AsyncMock()
    recovered_sub.unsubscribe = AsyncMock()
    recovered_sub.consumer_info = AsyncMock()
    js.subscribe = AsyncMock(return_value=recovered_sub)

    for _ in range(100):
        await asyncio.sleep(0.02)
        if sub.stats()["consumer_recovered"] >= 1:
            break

    assert sub.stats()["consumer_recovered"] == 1
    assert handle.consumer_watches[0].nats_sub is recovered_sub
    await sub.stop()


async def test_unsubscribe_cancels_the_watchdog_task(mock_nats):
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watchdog_task = handle._watchdog_task

    await handle.unsubscribe()

    assert watchdog_task.cancelled() or watchdog_task.done()
    assert handle.consumer_watches == []
    await sub.stop()


async def test_transport_forwards_consumer_recovery_settings_to_subscriber(mock_nats):
    transport = NatsTransport(
        consumer_health_check_interval_s=5.0,
        consumer_recovery_backoff_s=[2.0],
    )
    subscriber = transport._subscriber
    assert subscriber._consumer_health_check_interval_s == 5.0
    assert subscriber._consumer_recovery_backoff_s == [2.0]


# ---------------------------------------------------------------------------
# Consumer recovery — PR #1060 review follow-ups
#
# 1. Deliver-subject stability: a recreated durable must give every replica
#    the same inbox, and a health check must detect a replica still bound to
#    a stale one and rebind it (queue-group replicas going deaf silently).
# 2. Recovery must never race unsubscribe(): the watchdog is stopped before
#    nats_subs/consumer_watches are torn down, and a create that outlives an
#    unsubscribe is abandoned instead of leaking a live, never-acking sub.
# ---------------------------------------------------------------------------


async def test_recreated_durable_uses_a_deterministic_deliver_subject(mock_nats):
    """Every replica's create-or-bind must land on the same inbox for a durable."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(consumer_group="workers")
    await sub.start()
    await sub.subscribe(["test.*"], AsyncMock())

    kwargs = js.subscribe.call_args.kwargs
    durable = kwargs["durable"]
    config = kwargs["config"]
    assert config.deliver_subject == sub._deliver_subject_for_durable(durable)
    assert config.deliver_group == durable
    await sub.stop()


async def test_deliver_subject_mismatch_triggers_a_rebind(mock_nats, caplog):
    """Another replica recreating the durable elsewhere must be detected and rebound.

    This is the two-replica scenario: replica B's watch still points at its
    old push subscription, but the server now reports a different deliver
    subject (as if replica A had recreated the durable).  Without comparing
    deliver_subject/deliver_group, a mere "the durable exists" 200 hides that
    replica B stopped receiving anything.
    """
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(consumer_group="workers")
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]
    assert watch.durable is not None

    foreign_info = SimpleNamespace(
        config=SimpleNamespace(
            deliver_subject="_INBOX.some-other-replica", deliver_group=watch.durable
        )
    )
    nats_sub.consumer_info = AsyncMock(return_value=foreign_info)
    rebound_sub = AsyncMock()
    rebound_sub.unsubscribe = AsyncMock()
    js.subscribe = AsyncMock(return_value=rebound_sub)

    with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.nats_transport"):
        await sub._check_consumer_watch(handle, watch)

    assert watch.nats_sub is rebound_sub
    assert sub.stats()["consumer_recovered"] == 1
    assert any("recreated elsewhere" in record.message for record in caplog.records)
    await sub.stop()


async def test_matching_deliver_subject_is_left_alone(mock_nats):
    """A healthy, correctly-bound durable must not be recreated on every poll."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(consumer_group="workers")
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]

    healthy_info = SimpleNamespace(
        config=SimpleNamespace(
            deliver_subject=sub._deliver_subject_for_durable(watch.durable),
            deliver_group=watch.durable,
        )
    )
    nats_sub.consumer_info = AsyncMock(return_value=healthy_info)
    js.subscribe.reset_mock()

    await sub._check_consumer_watch(handle, watch)

    js.subscribe.assert_not_called()
    assert sub.stats()["consumer_recovered"] == 0
    await sub.stop()


async def test_ephemeral_subscription_skips_the_deliver_subject_check(mock_nats):
    """No consumer_group means no durable to share — nothing to compare against."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]
    assert watch.durable is None

    nats_sub.consumer_info = AsyncMock(
        return_value=SimpleNamespace(
            config=SimpleNamespace(deliver_subject="anything", deliver_group=None)
        )
    )
    js.subscribe.reset_mock()

    await sub._check_consumer_watch(handle, watch)

    js.subscribe.assert_not_called()
    assert sub.stats()["consumer_recovered"] == 0
    await sub.stop()


async def test_unsubscribe_cancels_in_flight_watchdog_recovery_cleanly(mock_nats):
    """unsubscribe() must fully stop the watchdog before nats_subs is touched.

    The watchdog is cancelled (and awaited) first specifically so a recovery
    stuck inside the recreate call is torn down before teardown runs, instead
    of resuming afterwards and re-appending a subscription nothing then
    cleans up.
    """
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(consumer_health_check_interval_s=0.01, consumer_health_check_jitter_s=0.0)
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())

    nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())
    create_started = asyncio.Event()

    async def _hang_subscribe(*args, **kwargs):
        create_started.set()
        await asyncio.Event().wait()  # never resolves; must be cancelled by unsubscribe()

    js.subscribe = AsyncMock(side_effect=_hang_subscribe)

    await asyncio.wait_for(create_started.wait(), timeout=1.0)
    await asyncio.wait_for(handle.unsubscribe(), timeout=1.0)

    assert handle.nats_subs == []
    assert handle.consumer_watches == []
    await sub.stop()


async def test_recovery_abandons_a_new_subscription_if_unsubscribed_meanwhile(mock_nats):
    """A create that outlives a concurrent unsubscribe() must not be adopted.

    ``_recover_consumer`` rechecks ``sub.active``/``self._running`` itself
    right after the create call returns, as a second line of defense beyond
    cancelling the watchdog: asyncio does not guarantee a pending cancel is
    delivered before an await that is about to complete anyway.
    """
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]

    create_started = asyncio.Event()
    release_create = asyncio.Event()
    recovered_sub = AsyncMock()
    recovered_sub.unsubscribe = AsyncMock()

    async def _slow_subscribe(*args, **kwargs):
        create_started.set()
        await release_create.wait()
        return recovered_sub

    js.subscribe = AsyncMock(side_effect=_slow_subscribe)

    recover_task = asyncio.create_task(sub._recover_consumer(handle, watch, reason="test"))
    await asyncio.wait_for(create_started.wait(), timeout=1.0)

    # Simulate teardown finishing while the create call above is still
    # in flight (a plain unsubscribe() here would itself cancel the task
    # this recovery runs in when driven by the real watchdog; calling it
    # directly isolates the second, independent guard).
    original_nats_sub = watch.nats_sub
    handle._active = False
    release_create.set()
    await asyncio.wait_for(recover_task, timeout=1.0)

    # The abandoned recreate is unsubscribed and never adopted — the original
    # (still-registered) subscription is untouched, not replaced by it.
    assert recovered_sub not in handle.nats_subs
    assert watch.nats_sub is original_nats_sub
    recovered_sub.unsubscribe.assert_awaited_once()
    await sub.stop()


async def test_unsubscribe_survives_nats_subs_mutation_during_teardown(mock_nats):
    """Teardown iterates a copy of nats_subs, so a reentrant mutation cannot corrupt it."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())

    extra = AsyncMock()
    extra.unsubscribe = AsyncMock()

    async def _mutate_then_unsubscribe():
        handle.nats_subs.append(extra)

    nats_sub.unsubscribe = AsyncMock(side_effect=_mutate_then_unsubscribe)

    await handle.unsubscribe()

    assert handle.nats_subs == []
    await sub.stop()


async def test_recovery_replays_from_the_earlier_of_healthy_or_delivered(mock_nats):
    """Recreate must not reuse the startup deliver policy (NEW would skip the gap)."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]
    watch.last_healthy_at = datetime(2020, 1, 1, tzinfo=UTC)
    watch.last_delivered_at = datetime(2020, 1, 2, tzinfo=UTC)

    nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())
    recovered_sub = AsyncMock()
    recovered_sub.unsubscribe = AsyncMock()
    js.subscribe = AsyncMock(return_value=recovered_sub)

    await sub._check_consumer_watch(handle, watch)

    config = js.subscribe.call_args.kwargs["config"]
    assert config.deliver_policy == js_api.DeliverPolicy.BY_START_TIME
    assert config.opt_start_time == datetime(2020, 1, 1, tzinfo=UTC)
    await sub.stop()


async def test_recovery_reensures_its_own_stream_before_recreating(mock_nats):
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(ensure_stream=True)
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]

    nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())
    js.stream_info.reset_mock()
    recovered_sub = AsyncMock()
    recovered_sub.unsubscribe = AsyncMock()
    js.subscribe = AsyncMock(return_value=recovered_sub)

    await sub._check_consumer_watch(handle, watch)

    js.stream_info.assert_called_once_with(DEFAULT_STREAM_NAME)
    await sub.stop()


async def test_recovery_does_not_reensure_a_foreign_stream(mock_nats):
    """An extra_subscriptions stream is owned elsewhere; guessing its config would be wrong."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(
        subject_prefix="obs.valhalla",
        stream_name="obs-valhalla-events",
        extra_subscriptions=[{"subject": "flock.k8s.>", "stream_name": "flock-k8s-events"}],
    )
    await sub.start()
    handle = await sub.subscribe(["flock.learning.proposed"], AsyncMock())
    foreign_watch = next(w for w in handle.consumer_watches if w.stream_name == "flock-k8s-events")

    foreign_nats_sub = foreign_watch.nats_sub
    foreign_nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())
    js.stream_info.reset_mock()
    recovered_sub = AsyncMock()
    recovered_sub.unsubscribe = AsyncMock()
    js.subscribe = AsyncMock(return_value=recovered_sub)

    await sub._check_consumer_watch(handle, foreign_watch)

    js.stream_info.assert_not_called()
    await sub.stop()


async def test_old_subscription_cleanup_failure_is_logged_not_swallowed(mock_nats, caplog):
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]

    nats_sub.consumer_info = AsyncMock(side_effect=js_errors.NotFoundError())
    nats_sub.unsubscribe = AsyncMock(side_effect=RuntimeError("already gone"))
    recovered_sub = AsyncMock()
    js.subscribe = AsyncMock(return_value=recovered_sub)

    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.nats_transport"):
        await sub._check_consumer_watch(handle, watch)

    assert any(
        "NATS unsubscribe failed" in record.message
        and "replaced push subscription" in record.message
        for record in caplog.records
    )
    await sub.stop()


async def test_health_check_escalates_to_error_after_the_configured_threshold(mock_nats, caplog):
    """A blind watchdog (every check failing) must become visible, not just counted."""
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber(consumer_check_failure_threshold=2)
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]
    nats_sub.consumer_info = AsyncMock(side_effect=TimeoutError("slow"))

    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.nats_transport"):
        await sub._check_consumer_watch(handle, watch)
        await sub._check_consumer_watch(handle, watch)

    assert sub.stats()["consumer_check_failures"] == 2
    levels = [
        record.levelname
        for record in caplog.records
        if "consumer health check failed" in record.message
    ]
    assert levels == ["WARNING", "ERROR"]
    await sub.stop()


async def test_healthy_check_resets_consecutive_failures(mock_nats):
    mock_module, client, js, nats_sub = mock_nats
    sub = NatsSubscriber()
    await sub.start()
    handle = await sub.subscribe(["test.*"], AsyncMock())
    watch = handle.consumer_watches[0]
    watch.consecutive_check_failures = 5

    nats_sub.consumer_info = AsyncMock(
        return_value=SimpleNamespace(
            config=SimpleNamespace(deliver_subject=None, deliver_group=None)
        )
    )

    await sub._check_consumer_watch(handle, watch)

    assert watch.consecutive_check_failures == 0
    await sub.stop()


async def test_watch_consumers_applies_the_configured_jitter(mock_nats, monkeypatch):
    mock_module, client, js, nats_sub = mock_nats
    calls: list[tuple[float, float]] = []

    def _tracking_uniform(a: float, b: float) -> float:
        calls.append((a, b))
        return 0.0

    monkeypatch.setattr("sleipnir.adapters.nats_transport.random.uniform", _tracking_uniform)

    # The watchdog is started as a side effect of subscribe(), so the
    # subscriber's own configured jitter must already be in effect by then —
    # patch random.uniform before subscribing rather than racing a
    # separately-created watchdog task against the real one.
    sub = NatsSubscriber(consumer_health_check_interval_s=0.01, consumer_health_check_jitter_s=2.5)
    await sub.start()
    await sub.subscribe(["test.*"], AsyncMock())
    await asyncio.sleep(0.05)

    assert calls
    assert calls[0] == (0, 2.5)
    await sub.stop()


async def test_core_only_subscription_never_starts_a_watchdog_task(mock_nats):
    mock_module, client, js, _ = mock_nats
    sub = NatsSubscriber(subject_prefix="obs.workshop", stream_name="core")
    await sub.start()
    handle = await sub.subscribe(["*"], AsyncMock())

    assert handle._watchdog_task is None
    await sub.stop()


async def test_transport_forwards_consumer_check_settings_to_subscriber(mock_nats):
    transport = NatsTransport(
        consumer_check_failure_threshold=7,
        consumer_health_check_jitter_s=1.5,
    )
    subscriber = transport._subscriber
    assert subscriber._consumer_check_failure_threshold == 7
    assert subscriber._consumer_health_check_jitter_s == 1.5


def test_consumer_check_defaults():
    sub = NatsSubscriber()
    assert sub._consumer_check_failure_threshold == DEFAULT_CONSUMER_CHECK_FAILURE_THRESHOLD
    assert sub._consumer_health_check_jitter_s == DEFAULT_CONSUMER_HEALTH_CHECK_JITTER_S
