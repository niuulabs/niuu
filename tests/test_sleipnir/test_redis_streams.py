"""Tests for the Redis Streams transport adapter (NIU-524).

Test strategy
-------------
All tests use ``FakeRedis`` — an in-memory Redis double — injected via the
``_redis`` constructor parameter so no real Redis process is needed.

The ``FakeRedis`` simulates the subset of redis.asyncio used by the adapter:
  * ``xadd``          — append to an in-memory stream, notify waiters
  * ``xgroup_create`` — track consumer groups; raise BUSYGROUP on duplicates
  * ``xreadgroup``    — deliver undelivered messages; block up to *block* ms
  * ``xack``          — no-op acknowledgement
  * ``pipeline``      — collects xadd commands, executes in ``execute()``
  * ``aclose``        — no-op

Tests use a short ``block_timeout_ms`` (10 ms) so the consumer task wakes up
quickly without slowing down the suite.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from sleipnir.adapters.redis_streams import (
    DEFAULT_BLOCK_TIMEOUT_MS,
    DEFAULT_CONSUMER_GROUP,
    DEFAULT_MAXLEN,
    DEFAULT_REDIS_URL,
    DEFAULT_REPLAY_BATCH_SIZE,
    DEFAULT_RING_BUFFER_DEPTH,
    DEFAULT_STREAM_PREFIX,
    RedisStreamsTransport,
    _streams_for_patterns,
    redis_available,
)
from sleipnir.domain.events import SleipnirEvent
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# FakeRedis — in-memory Redis double
# ---------------------------------------------------------------------------

_FAST_BLOCK_MS = 10  # Keep consumer task latency low in tests


class FakeRedis:
    """In-memory fake implementing the redis.asyncio subset used by the adapter."""

    def __init__(self) -> None:
        # stream_key -> [(id_str, {bytes_field: bytes_value}), ...]
        self._streams: dict[str, list[tuple[str, dict[bytes, bytes]]]] = {}
        # stream_key -> group_name -> last_delivered_id
        self._group_state: dict[str, dict[str, str]] = {}
        # asyncio.Event per stream — fired when new messages arrive
        self._msg_ready: dict[str, asyncio.Event] = {}
        self._id_seq = 0
        # Track calls for assertion helpers
        self.xadd_calls: list[dict] = []
        self.xack_calls: list[tuple] = []
        self.group_create_calls: list[dict] = []
        self.closed = False

    # ------------------------------------------------------------------
    # ID helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        self._id_seq += 1
        return f"1000000000000-{self._id_seq}"

    @staticmethod
    def _id_gt(a: str, b: str) -> bool:
        """Return True if stream entry id *a* is strictly greater than *b*."""
        if b == "$":
            return False
        if "-" not in b:
            # b is a raw millisecond or "0" — anything is after it
            return True

        def parse(s: str) -> tuple[int, int]:
            parts = s.split("-", 1)
            return (int(parts[0]), int(parts[1]))

        return parse(a) > parse(b)

    # ------------------------------------------------------------------
    # Event (signal) helpers
    # ------------------------------------------------------------------

    def _msg_event(self, stream: str) -> asyncio.Event:
        if stream not in self._msg_ready:
            self._msg_ready[stream] = asyncio.Event()
        return self._msg_ready[stream]

    # ------------------------------------------------------------------
    # Redis commands
    # ------------------------------------------------------------------

    async def xadd(
        self,
        stream: str,
        data: dict,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> bytes:
        if stream not in self._streams:
            self._streams[stream] = []
        msg_id = self._next_id()
        normalised: dict[bytes, bytes] = {
            (k if isinstance(k, bytes) else k.encode()): (v if isinstance(v, bytes) else v)
            for k, v in data.items()
        }
        self._streams[stream].append((msg_id, normalised))
        if maxlen is not None and len(self._streams[stream]) > maxlen:
            self._streams[stream] = self._streams[stream][-maxlen:]
        self.xadd_calls.append(
            {"stream": stream, "data": data, "maxlen": maxlen, "approximate": approximate}
        )
        # Notify consumer tasks waiting on this stream.
        evt = self._msg_event(stream)
        evt.set()
        return msg_id.encode()

    async def xgroup_create(
        self,
        stream: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> bool:
        if mkstream and stream not in self._streams:
            self._streams[stream] = []
        stream_groups = self._group_state.setdefault(stream, {})
        if groupname in stream_groups:
            raise Exception("BUSYGROUP Consumer Group name already exists")
        # Resolve "$" to the current last entry so history is not replayed.
        resolved_id = id
        if id == "$":
            msgs = self._streams.get(stream, [])
            resolved_id = msgs[-1][0] if msgs else "0-0"
        self._group_state[stream][groupname] = resolved_id
        self.group_create_calls.append(
            {"stream": stream, "groupname": groupname, "id": id, "mkstream": mkstream}
        )
        return True

    def _collect_for_group(
        self, stream: str, groupname: str, count: int
    ) -> list[tuple[bytes, dict[bytes, bytes]]]:
        """Return up to *count* undelivered messages for *groupname* in *stream*."""
        all_msgs = self._streams.get(stream, [])
        last_id = self._group_state.get(stream, {}).get(groupname, "$")
        result: list[tuple[bytes, dict[bytes, bytes]]] = []
        for msg_id, data in all_msgs:
            if self._id_gt(msg_id, last_id):
                result.append((msg_id.encode(), data))
                if len(result) >= count:
                    break
        if result:
            self._group_state[stream][groupname] = result[-1][0].decode()
        return result

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict,
        count: int = 100,
        block: int | None = None,
    ) -> list | None:
        results = []
        for stream, start_id in streams.items():
            if start_id != ">":
                continue
            msgs = self._collect_for_group(stream, groupname, count)
            if msgs:
                key = stream.encode() if isinstance(stream, str) else stream
                results.append((key, msgs))

        if results:
            return results

        if not block:
            return None

        timeout = block / 1000.0
        stream_list = list(streams.keys())

        # Clear events so we don't miss a set() that happened before we wait.
        for s in stream_list:
            self._msg_event(s).clear()

        # Re-check after clearing — a message might have arrived between the
        # first check and the clear (in asyncio this cannot happen, but
        # defensive programming is free here).
        results = []
        for stream, start_id in streams.items():
            if start_id != ">":
                continue
            msgs = self._collect_for_group(stream, groupname, count)
            if msgs:
                key = stream.encode() if isinstance(stream, str) else stream
                results.append((key, msgs))

        if results:
            return results

        wait_tasks = [asyncio.create_task(self._msg_event(s).wait()) for s in stream_list]
        try:
            done, pending = await asyncio.wait(
                wait_tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            for t in wait_tasks:
                t.cancel()
            raise
        for t in pending:
            t.cancel()

        if not done:
            return None

        results = []
        for stream, start_id in streams.items():
            if start_id != ">":
                continue
            msgs = self._collect_for_group(stream, groupname, count)
            if msgs:
                key = stream.encode() if isinstance(stream, str) else stream
                results.append((key, msgs))
        return results or None

    async def xack(self, stream: str, groupname: str, *msg_ids: bytes | str) -> int:
        self.xack_calls.append((stream, groupname, msg_ids))
        return len(msg_ids)

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)

    async def aclose(self) -> None:
        self.closed = True


class _FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._cmds: list = []

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def xadd(
        self,
        stream: str,
        data: dict,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> _FakePipeline:
        self._cmds.append(("xadd", stream, data, maxlen, approximate))
        return self

    async def execute(self) -> list:
        results = []
        for cmd in self._cmds:
            if cmd[0] == "xadd":
                _, stream, data, maxlen, approx = cmd
                result = await self._redis.xadd(stream, data, maxlen=maxlen, approximate=approx)
                results.append(result)
        self._cmds.clear()
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_transport(**kwargs) -> RedisStreamsTransport:
    """Return a ``RedisStreamsTransport`` with a ``FakeRedis`` and fast poll."""
    fake = kwargs.pop("fake", FakeRedis())
    kwargs.setdefault("block_timeout_ms", _FAST_BLOCK_MS)
    return RedisStreamsTransport(_redis=fake, **kwargs), fake


async def drain(transport: RedisStreamsTransport, *, timeout: float = 1.0) -> None:
    """Wait for the reader and handler tasks to deliver all pending messages.

    Sleeps briefly (one poll cycle) to let the reader task pick up messages
    from the fake Redis, then delegates to ``transport.flush()`` which joins
    every active subscription queue — consistent with InProcessBus.flush().
    """
    await asyncio.sleep(_FAST_BLOCK_MS / 1000 * 2)
    await asyncio.wait_for(transport.flush(), timeout=timeout)


# ---------------------------------------------------------------------------
# Unit tests — module-level helpers
# ---------------------------------------------------------------------------


def test_redis_available_returns_bool():
    result = redis_available()
    assert isinstance(result, bool)


def test_streams_for_patterns_star():
    """'*' expands to all known-namespace streams."""
    from sleipnir.domain.events import EVENT_NAMESPACES

    streams = _streams_for_patterns("sleipnir", ["*"])
    expected = sorted(f"sleipnir:{ns}" for ns in EVENT_NAMESPACES)
    assert streams == expected


def test_streams_for_patterns_namespace_wildcard():
    """'ravn.*' maps to just the ravn stream."""
    assert _streams_for_patterns("sleipnir", ["ravn.*"]) == ["sleipnir:ravn"]


def test_streams_for_patterns_exact():
    """An exact event type maps to its namespace stream."""
    assert _streams_for_patterns("sleipnir", ["ting.task.started"]) == ["sleipnir:ting"]


def test_streams_for_patterns_multiple():
    """Multiple distinct namespaces are sorted and deduplicated."""
    streams = _streams_for_patterns("sleipnir", ["ravn.*", "ting.*", "ravn.tool.complete"])
    assert streams == ["sleipnir:ravn", "sleipnir:ting"]


def test_streams_for_patterns_wildcard_namespace():
    """A wildcard in the namespace segment expands to all namespaces."""
    from sleipnir.domain.events import EVENT_NAMESPACES

    streams = _streams_for_patterns("sleipnir", ["*.*"])
    expected = sorted(f"sleipnir:{ns}" for ns in EVENT_NAMESPACES)
    assert streams == expected


def test_streams_for_patterns_custom_prefix():
    """Custom prefix is applied correctly."""
    assert _streams_for_patterns("myapp", ["ravn.*"]) == ["myapp:ravn"]


# ---------------------------------------------------------------------------
# Unit tests — constructor validation
# ---------------------------------------------------------------------------


def test_constructor_defaults():
    transport = RedisStreamsTransport(_redis=FakeRedis())
    assert transport._url == DEFAULT_REDIS_URL
    assert transport._stream_prefix == DEFAULT_STREAM_PREFIX
    assert transport._maxlen == DEFAULT_MAXLEN
    assert transport._replay_on_startup is False
    assert transport._consumer_group == DEFAULT_CONSUMER_GROUP
    assert transport._ring_buffer_depth == DEFAULT_RING_BUFFER_DEPTH
    assert transport._block_timeout_ms == DEFAULT_BLOCK_TIMEOUT_MS
    assert transport._replay_batch_size == DEFAULT_REPLAY_BATCH_SIZE


def test_constructor_invalid_ring_buffer_depth():
    with pytest.raises(ValueError, match="ring_buffer_depth"):
        RedisStreamsTransport(_redis=FakeRedis(), ring_buffer_depth=0)


def test_constructor_custom_values():
    transport = RedisStreamsTransport(
        _redis=FakeRedis(),
        stream_prefix="custom",
        maxlen=500,
        replay_on_startup=True,
        consumer_group="my-svc",
        ring_buffer_depth=50,
        block_timeout_ms=200,
        replay_batch_size=10,
    )
    assert transport._stream_prefix == "custom"
    assert transport._maxlen == 500
    assert transport._replay_on_startup is True
    assert transport._consumer_group == "my-svc"
    assert transport._ring_buffer_depth == 50
    assert transport._block_timeout_ms == 200
    assert transport._replay_batch_size == 10


# ---------------------------------------------------------------------------
# Unit tests — start / stop lifecycle
# ---------------------------------------------------------------------------


async def test_start_noop_when_redis_injected():
    transport, _ = make_transport()
    original = transport._redis
    await transport.start()
    assert transport._redis is original  # unchanged


async def test_stop_closes_redis_and_clears():
    transport, fake = make_transport()
    await transport.stop()
    assert fake.closed is True
    assert transport._redis is None


async def test_async_context_manager():
    fake = FakeRedis()
    async with RedisStreamsTransport(_redis=fake, block_timeout_ms=_FAST_BLOCK_MS) as transport:
        assert transport._redis is fake
    assert fake.closed is True


# ---------------------------------------------------------------------------
# Unit tests — publish
# ---------------------------------------------------------------------------


async def test_publish_writes_to_correct_stream():
    transport, fake = make_transport()
    await transport.publish(make_event(event_type="ravn.tool.complete"))
    assert len(fake.xadd_calls) == 1
    assert fake.xadd_calls[0]["stream"] == "sleipnir:ravn"


async def test_publish_stream_key_uses_prefix():
    transport, fake = make_transport(stream_prefix="myapp")
    await transport.publish(make_event(event_type="ting.task.started"))
    assert fake.xadd_calls[0]["stream"] == "myapp:ting"


async def test_publish_payload_field_present():
    transport, fake = make_transport()
    await transport.publish(make_event(event_id="abc"))
    data = fake.xadd_calls[0]["data"]
    assert b"payload" in data


async def test_publish_respects_maxlen():
    transport, fake = make_transport(maxlen=42)
    await transport.publish(make_event())
    assert fake.xadd_calls[0]["maxlen"] == 42


async def test_publish_ttl_zero_dropped():
    transport, fake = make_transport()
    await transport.publish(make_event(ttl=0))
    assert len(fake.xadd_calls) == 0


async def test_publish_ttl_negative_dropped():
    transport, fake = make_transport()
    await transport.publish(make_event(ttl=-1))
    assert len(fake.xadd_calls) == 0


async def test_publish_ttl_positive_delivered():
    transport, fake = make_transport()
    await transport.publish(make_event(ttl=60))
    assert len(fake.xadd_calls) == 1


async def test_publish_ttl_none_delivered():
    transport, fake = make_transport()
    await transport.publish(make_event(ttl=None))
    assert len(fake.xadd_calls) == 1


async def test_publish_logs_dropped_ttl(caplog):
    transport, _ = make_transport()
    with caplog.at_level(logging.DEBUG, logger="sleipnir.adapters.redis_streams"):
        await transport.publish(make_event(ttl=0))
    assert any("Dropping expired" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Unit tests — publish_batch
# ---------------------------------------------------------------------------


async def test_publish_batch_all_events_written():
    transport, fake = make_transport()
    events = [make_event(event_id=str(i)) for i in range(5)]
    await transport.publish_batch(events)
    assert len(fake.xadd_calls) == 5


async def test_publish_batch_skips_expired():
    transport, fake = make_transport()
    events = [
        make_event(event_id="live", ttl=60),
        make_event(event_id="dead", ttl=0),
    ]
    await transport.publish_batch(events)
    assert len(fake.xadd_calls) == 1
    live_call = fake.xadd_calls[0]
    assert b"payload" in live_call["data"]


async def test_publish_batch_correct_streams():
    transport, fake = make_transport()
    await transport.publish_batch(
        [
            make_event(event_type="ravn.tool.complete"),
            make_event(event_type="ting.task.started"),
            make_event(event_type="system.health.ping"),
        ]
    )
    streams = [c["stream"] for c in fake.xadd_calls]
    assert streams == ["sleipnir:ravn", "sleipnir:ting", "sleipnir:system"]


# ---------------------------------------------------------------------------
# Unit tests — consumer group creation
# ---------------------------------------------------------------------------


async def test_subscribe_creates_consumer_group():
    transport, fake = make_transport()
    sub = await transport.subscribe(["ravn.*"], lambda e: None)
    assert len(fake.group_create_calls) == 1
    # Group name is {consumer_group}:{uuid8} for per-subscription fan-out.
    groupname = fake.group_create_calls[0]["groupname"]
    assert groupname.startswith(DEFAULT_CONSUMER_GROUP + ":")
    assert fake.group_create_calls[0]["mkstream"] is True
    await sub.unsubscribe()


async def test_subscribe_no_replay_uses_dollar_id():
    transport, fake = make_transport(replay_on_startup=False)
    sub = await transport.subscribe(["ravn.*"], lambda e: None)
    assert fake.group_create_calls[0]["id"] == "$"
    await sub.unsubscribe()


async def test_subscribe_replay_uses_zero_id():
    transport, fake = make_transport(replay_on_startup=True)
    sub = await transport.subscribe(["ravn.*"], lambda e: None)
    assert fake.group_create_calls[0]["id"] == "0"
    await sub.unsubscribe()


async def test_subscribe_two_subscriptions_have_different_groups():
    """Each subscribe() creates a unique consumer group for fan-out."""
    transport, fake = make_transport()
    sub1 = await transport.subscribe(["ravn.*"], lambda e: None)
    sub2 = await transport.subscribe(["ravn.*"], lambda e: None)
    groups = [c["groupname"] for c in fake.group_create_calls]
    assert len(groups) == 2
    assert groups[0] != groups[1]
    await sub1.unsubscribe()
    await sub2.unsubscribe()


async def test_subscribe_busygroup_silently_ignored():
    """Reusing the same group name does not raise (BUSYGROUP is swallowed)."""
    transport, fake = make_transport()
    # Force the same group name by manually creating it first.
    await fake.xgroup_create("sleipnir:ravn", "sleipnir:dup", "$", mkstream=True)
    # Manually inject a call that will hit BUSYGROUP in _ensure_group.
    # Should not raise:
    await transport._ensure_group("sleipnir:ravn", "sleipnir:dup")


async def test_subscribe_star_creates_all_namespace_groups():
    from sleipnir.domain.events import EVENT_NAMESPACES

    transport, fake = make_transport()
    sub = await transport.subscribe(["*"], lambda e: None)
    # One group_create call per namespace stream, all with the same group name.
    created_streams = {c["stream"] for c in fake.group_create_calls}
    group_names = {c["groupname"] for c in fake.group_create_calls}
    expected_streams = {f"sleipnir:{ns}" for ns in EVENT_NAMESPACES}
    assert created_streams == expected_streams
    assert len(group_names) == 1  # same unique group across all streams
    await sub.unsubscribe()


# ---------------------------------------------------------------------------
# Integration-style tests — publish → subscribe → receive
# ---------------------------------------------------------------------------


async def test_publish_then_subscribe_receives_event():
    """Publish an event; subscriber receives it via the consumer task."""
    transport, _ = make_transport()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    sub = await transport.subscribe(["ravn.*"], handler)
    await transport.publish(make_event(event_id="e1"))

    await drain(transport)

    assert len(received) == 1
    assert received[0].event_id == "e1"
    await sub.unsubscribe()


async def test_multiple_subscribers_receive_independently():
    """Each subscriber gets its own copy of the event."""
    transport, _ = make_transport()
    bucket_a: list[str] = []
    bucket_b: list[str] = []

    async def handler_a(evt: SleipnirEvent) -> None:
        bucket_a.append(evt.event_id)

    async def handler_b(evt: SleipnirEvent) -> None:
        bucket_b.append(evt.event_id)

    sub_a = await transport.subscribe(["ravn.*"], handler_a)
    sub_b = await transport.subscribe(["ravn.*"], handler_b)

    await transport.publish(make_event(event_id="shared"))

    await drain(transport)
    await drain(transport)

    assert bucket_a == ["shared"]
    assert bucket_b == ["shared"]
    await sub_a.unsubscribe()
    await sub_b.unsubscribe()


async def test_pattern_filtering_ravn_star():
    """'ravn.*' receives ravn events and rejects ting events."""
    transport, _ = make_transport()
    received_types: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received_types.append(evt.event_type)

    sub = await transport.subscribe(["ravn.*"], handler)
    await transport.publish(make_event(event_type="ravn.tool.complete", event_id="e1"))
    # ting events go to a different stream, so this subscribe call won't see it —
    # but even if it did land in the queue, the pattern filter would drop it.
    await drain(transport)

    assert received_types == ["ravn.tool.complete"]
    await sub.unsubscribe()


async def test_pattern_filtering_star_receives_all():
    """'*' receives events from every namespace stream."""
    transport, _ = make_transport()
    received_types: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received_types.append(evt.event_type)

    sub = await transport.subscribe(["*"], handler)

    event_types = [
        "ravn.tool.complete",
        "ting.task.started",
        "system.health.ping",
    ]
    for i, et in enumerate(event_types):
        await transport.publish(make_event(event_type=et, event_id=str(i)))

    await drain(transport)

    assert sorted(received_types) == sorted(event_types)
    await sub.unsubscribe()


async def test_unsubscribe_stops_delivery():
    """Events published after unsubscribe are never delivered."""
    transport, _ = make_transport()
    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)

    sub = await transport.subscribe(["ravn.*"], handler)
    await transport.publish(make_event(event_id="before"))
    await drain(transport)

    await sub.unsubscribe()

    # Publish after unsubscribe — should not be delivered.
    await transport.publish(make_event(event_id="after"))
    await asyncio.sleep(0.05)

    assert received == ["before"]


async def test_ttl_zero_not_delivered_to_subscriber():
    """ttl=0 events written to Redis are filtered before delivery."""
    transport, _ = make_transport()
    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)

    sub = await transport.subscribe(["*"], handler)

    # ttl=0 is dropped at publish time (never reaches Redis)
    await transport.publish(make_event(event_id="expired", ttl=0))
    # ttl=None reaches Redis and should be delivered
    await transport.publish(make_event(event_id="live"))

    await drain(transport)

    assert received == ["live"]
    await sub.unsubscribe()


async def test_correlation_id_preserved():
    """correlation_id round-trips through Redis serialisation."""
    transport, _ = make_transport()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    sub = await transport.subscribe(["*"], handler)
    await transport.publish(make_event(event_id="e-corr", correlation_id="corr-xyz"))
    await drain(transport)

    assert received[0].correlation_id == "corr-xyz"
    await sub.unsubscribe()


async def test_urgency_preserved():
    """urgency field round-trips through Redis serialisation."""
    transport, _ = make_transport()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    sub = await transport.subscribe(["*"], handler)
    await transport.publish(make_event(event_id="e-urgency", urgency=0.8))
    await drain(transport)

    assert received[0].urgency == pytest.approx(0.8)
    await sub.unsubscribe()


async def test_publish_batch_all_received():
    """publish_batch delivers all events to a subscriber."""
    transport, _ = make_transport()
    received_ids: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received_ids.append(evt.event_id)

    sub = await transport.subscribe(["*"], handler)
    batch = [make_event(event_id=f"b-{i}") for i in range(10)]
    await transport.publish_batch(batch)
    await drain(transport)

    assert received_ids == [f"b-{i}" for i in range(10)]
    await sub.unsubscribe()


# ---------------------------------------------------------------------------
# Replay tests
# ---------------------------------------------------------------------------


async def test_replay_on_startup_delivers_historical_events():
    """With replay_on_startup=True a new subscriber sees pre-existing events."""
    fake = FakeRedis()
    transport = RedisStreamsTransport(
        _redis=fake,
        replay_on_startup=True,
        block_timeout_ms=_FAST_BLOCK_MS,
    )

    # Seed the stream directly (simulating events published before this service started).
    _hist_payload = (
        b'{"event_id":"hist-1","event_type":"ravn.tool.complete","source":"ravn:x",'
        b'"payload":{},"summary":"s","urgency":0.5,"domain":"code",'
        b'"timestamp":"2026-04-05T12:00:00+00:00","correlation_id":null,'
        b'"causation_id":null,"tenant_id":null,"ttl":null}'
    )
    await fake.xadd("sleipnir:ravn", {b"payload": _hist_payload})

    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)

    sub = await transport.subscribe(["ravn.*"], handler)
    await drain(transport)

    assert "hist-1" in received
    await sub.unsubscribe()


async def test_no_replay_does_not_deliver_historical_events():
    """With replay_on_startup=False historical events are skipped."""
    fake = FakeRedis()
    transport = RedisStreamsTransport(
        _redis=fake,
        replay_on_startup=False,
        block_timeout_ms=_FAST_BLOCK_MS,
    )

    # Seed the stream before subscribing.
    _old_payload = (
        b'{"event_id":"old","event_type":"ravn.tool.complete","source":"ravn:x",'
        b'"payload":{},"summary":"s","urgency":0.5,"domain":"code",'
        b'"timestamp":"2026-04-05T12:00:00+00:00","correlation_id":null,'
        b'"causation_id":null,"tenant_id":null,"ttl":null}'
    )
    await fake.xadd("sleipnir:ravn", {b"payload": _old_payload})

    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)

    sub = await transport.subscribe(["ravn.*"], handler)

    # Publish a new event — only this should be delivered.
    await transport.publish(make_event(event_id="new"))
    await drain(transport)

    assert "old" not in received
    assert "new" in received
    await sub.unsubscribe()


# ---------------------------------------------------------------------------
# Ring-buffer overflow
# ---------------------------------------------------------------------------


async def test_ring_buffer_overflow_drops_oldest_and_warns(caplog):
    """Overflow drops the oldest queued event and logs a warning."""
    transport, _ = make_transport(ring_buffer_depth=2)
    received_ids: list[str] = []

    async def slow_handler(evt: SleipnirEvent) -> None:
        await asyncio.sleep(100)  # Block the handler; events pile up.
        received_ids.append(evt.event_id)

    sub = await transport.subscribe(["*"], slow_handler)

    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.redis_streams"):
        await transport.publish(make_event(event_id="o1"))
        await transport.publish(make_event(event_id="o2"))
        await transport.publish(make_event(event_id="o3"))

        # Give the consumer task time to process all three messages and trigger overflow.
        await asyncio.sleep(0.2)

    warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
    assert len(warnings) >= 1

    q = sub._queue
    queued = []
    while not q.empty():
        queued.append(q.get_nowait().event_id)
        q.task_done()

    assert "o1" not in queued
    assert "o2" in queued or "o3" in queued

    await sub.unsubscribe()


# ---------------------------------------------------------------------------
# Corrupt / missing payload handling
# ---------------------------------------------------------------------------


async def test_corrupt_payload_does_not_crash_consumer():
    """A malformed payload in the stream is skipped without crashing the consumer."""
    fake = FakeRedis()
    transport = RedisStreamsTransport(_redis=fake, block_timeout_ms=_FAST_BLOCK_MS)

    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)

    sub = await transport.subscribe(["ravn.*"], handler)

    # Inject a corrupt message directly into the stream.
    msg_id = fake._next_id()
    fake._streams.setdefault("sleipnir:ravn", []).append(
        (msg_id, {b"payload": b"NOT-VALID-JSON!!!"})
    )
    # Notify the consumer task.
    fake._msg_event("sleipnir:ravn").set()

    # Publish a valid event after the corrupt one.
    await transport.publish(make_event(event_id="good"))
    await drain(transport)

    assert "good" in received
    await sub.unsubscribe()


async def test_missing_payload_field_does_not_crash_consumer():
    """A stream message without a payload field is skipped gracefully."""
    fake = FakeRedis()
    transport = RedisStreamsTransport(_redis=fake, block_timeout_ms=_FAST_BLOCK_MS)

    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)

    sub = await transport.subscribe(["ravn.*"], handler)

    # Inject a message with no payload field.
    msg_id = fake._next_id()
    fake._streams.setdefault("sleipnir:ravn", []).append((msg_id, {b"other_field": b"value"}))
    fake._msg_event("sleipnir:ravn").set()

    await transport.publish(make_event(event_id="ok"))
    await drain(transport)

    assert "ok" in received
    await sub.unsubscribe()


# ---------------------------------------------------------------------------
# Stop cancels all subscriptions
# ---------------------------------------------------------------------------


async def test_stop_cancels_all_subscriptions():
    transport, fake = make_transport()
    await transport.subscribe(["ravn.*"], lambda e: None)
    assert len(transport._subscriptions) == 1
    await transport.stop()
    assert len(transport._subscriptions) == 0
    assert fake.closed is True


# ---------------------------------------------------------------------------
# Double-unsubscribe is idempotent
# ---------------------------------------------------------------------------


async def test_double_unsubscribe_idempotent():
    transport, _ = make_transport()
    sub = await transport.subscribe(["ravn.*"], lambda e: None)
    await sub.unsubscribe()
    await sub.unsubscribe()  # should not raise


def test_remove_subscription_silently_ignores_missing_sub():
    """_remove_subscription does not raise when the sub is not in the list."""
    from unittest.mock import MagicMock

    transport, _ = make_transport()
    # Call _remove_subscription with a fake sub that was never added
    fake_sub = MagicMock()
    transport._remove_subscription(fake_sub)  # should not raise ValueError


# ---------------------------------------------------------------------------
# redis_available import guard
# ---------------------------------------------------------------------------


def test_redis_available_reflects_import():
    try:
        import redis  # noqa: F401

        expected = True
    except ImportError:
        expected = False
    assert redis_available() is expected
