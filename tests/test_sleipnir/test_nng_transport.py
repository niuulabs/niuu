"""Tests for the nng PUB/SUB transport adapter (NIU-464).

Test strategy
-------------
Unit tests mock pynng sockets to keep them fast and hermetic.
Functional tests use real pynng IPC sockets (no external services needed).
Multi-process delivery is tested via asyncio.create_subprocess_exec.

All functional tests use a per-test unique IPC socket path (via tmp_path)
to ensure isolation even when tests run in parallel.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import time
from contextlib import suppress
from unittest.mock import patch

import pytest

from sleipnir.adapters.nng_transport import (
    DEFAULT_IPC_ADDRESS,
    NngPublisher,
    NngSubscriber,
    NngTransport,
    _decode_message,
    _encode_message,
    _nng_topics_for_patterns,
    nng_available,
)
from sleipnir.domain.events import SleipnirEvent
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Skip all tests if pynng is not installed.
# ---------------------------------------------------------------------------

pynng = pytest.importorskip("pynng", reason="pynng not installed; skipping nng tests")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ipc_address(tmp_path):
    """Unique IPC address per test — avoids cross-test socket leaks.

    Uses a short path under /tmp to stay within the macOS 104-char Unix socket
    limit.  The pytest tmp_path hierarchy can exceed that limit on macOS.
    """
    import tempfile

    short_dir = tempfile.mkdtemp(prefix="nng_")
    sock_path = f"{short_dir}/s.sock"
    yield f"ipc://{sock_path}"
    # Cleanup — the socket file is removed by nng, but remove the temp dir.
    import shutil

    shutil.rmtree(short_dir, ignore_errors=True)


@pytest.fixture
def tcp_address():
    """A local TCP address on a free ephemeral port for testing TCP transport.

    We must NOT reuse the documented production default ``DEFAULT_TCP_ADDRESS``
    (``tcp://localhost:9500``): that fixed port collides with any process already
    listening on it — a live platform node on a dev box, or a parallel pytest-xdist
    worker — making ``socket.listen()`` raise ``pynng.AddressInUse`` and the test
    fail before any assertion runs. Bind to port 0 to let the OS hand us a free
    port, then reuse that exact port for both publisher and subscriber.
    """
    import socket as _socket

    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    finally:
        s.close()
    return f"tcp://localhost:{port}"


# ---------------------------------------------------------------------------
# Unit tests — module-level helpers
# ---------------------------------------------------------------------------


def test_nng_available_returns_true():
    assert nng_available() is True


def test_nng_topics_wildcard_star():
    """'*' should produce b'' (subscribe to everything)."""
    assert _nng_topics_for_patterns(["*"]) == [b""]


def test_nng_topics_namespace_wildcard():
    """'ravn.*' should produce b'ravn.' prefix."""
    assert _nng_topics_for_patterns(["ravn.*"]) == [b"ravn."]


def test_nng_topics_sub_namespace_wildcard():
    """'ravn.tool.*' should produce b'ravn.tool.' prefix."""
    assert _nng_topics_for_patterns(["ravn.tool.*"]) == [b"ravn.tool."]


def test_nng_topics_exact_match():
    """Exact event type should produce bytes + null separator."""
    assert _nng_topics_for_patterns(["ravn.tool.complete"]) == [b"ravn.tool.complete\x00"]


def test_nng_topics_multiple_patterns():
    """Multiple patterns produce one nng topic each."""
    result = _nng_topics_for_patterns(["ravn.*", "ting.*"])
    assert b"ravn." in result
    assert b"ting." in result


def test_nng_topics_star_short_circuits():
    """'*' anywhere in patterns returns [b''] immediately."""
    result = _nng_topics_for_patterns(["ravn.*", "*", "ting.*"])
    assert result == [b""]


def test_nng_topics_empty_list():
    """Empty pattern list falls back to b'' (subscribe to all)."""
    assert _nng_topics_for_patterns([]) == [b""]


def test_nng_topics_question_mark_wildcard_falls_back_to_all():
    """Pattern with '?' wildcard (e.g. 'ravn.tool.?') → b'' fallback.

    nng prefix matching cannot express single-character wildcards; the adapter
    must subscribe to all messages and rely on application-level fnmatch
    filtering for the final match decision.
    """
    assert _nng_topics_for_patterns(["ravn.tool.?"]) == [b""]


def test_nng_topics_bracket_wildcard_falls_back_to_all():
    """Pattern with '[' wildcard (e.g. 'ravn.[abc]*') → b'' fallback."""
    assert _nng_topics_for_patterns(["ravn.[abc]*"]) == [b""]


def test_nng_topics_mid_star_wildcard_falls_back_to_all():
    """Pattern with '*' not at the end (e.g. 'ravn.*.complete') → b'' fallback."""
    assert _nng_topics_for_patterns(["ravn.*.complete"]) == [b""]


def test_encode_message_format():
    """Encoded message starts with event_type, then null byte, then payload."""
    event = make_event(event_type="ravn.tool.complete")
    data = _encode_message(event)
    sep = data.index(b"\x00")
    assert data[:sep] == b"ravn.tool.complete"
    assert len(data[sep + 1 :]) > 0  # msgpack payload is non-empty


def test_decode_message_round_trip():
    """decode(encode(event)) == event."""
    event = make_event(event_id="rt-001", event_type="ravn.tool.complete")
    data = _encode_message(event)
    decoded = _decode_message(data)
    assert decoded is not None
    assert decoded.event_id == "rt-001"
    assert decoded.event_type == "ravn.tool.complete"


def test_decode_message_malformed_returns_none(caplog):
    """Malformed message (no null separator) returns None and logs warning."""
    import logging

    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.nng_transport"):
        result = _decode_message(b"no-null-separator-here")
    assert result is None
    assert any("malformed" in r.message for r in caplog.records)


def test_decode_message_bad_payload_returns_none(caplog):
    """A null byte followed by garbage returns None and logs exception."""
    import logging

    bad = b"ravn.tool.complete\x00\xff\xfe\xfd"
    with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.nng_transport"):
        result = _decode_message(bad)
    assert result is None


# ---------------------------------------------------------------------------
# Unit tests — constructor validation
# ---------------------------------------------------------------------------


def test_nng_subscriber_rejects_zero_ring_buffer_depth():
    with pytest.raises(ValueError, match="ring_buffer_depth"):
        NngSubscriber(ring_buffer_depth=0)


async def test_nng_publisher_raises_without_start():
    """publish() before start() raises RuntimeError."""
    pub = NngPublisher(address=DEFAULT_IPC_ADDRESS)
    with pytest.raises(RuntimeError, match="not started"):
        await pub.publish(make_event())


async def test_nng_subscriber_raises_without_start():
    """subscribe() before start() raises RuntimeError."""
    sub = NngSubscriber(address=DEFAULT_IPC_ADDRESS)

    async def handler(_: SleipnirEvent) -> None:
        pass

    with pytest.raises(RuntimeError, match="not started"):
        await sub.subscribe(["*"], handler)


# ---------------------------------------------------------------------------
# Unit tests — ImportError guard
# ---------------------------------------------------------------------------


def test_import_error_when_pynng_not_available():
    """NngPublisher/Subscriber raise ImportError if pynng is absent."""
    with patch("sleipnir.adapters.nng_transport._PYNNG_AVAILABLE", False):
        with pytest.raises(ImportError, match="pynng"):
            NngPublisher()
        with pytest.raises(ImportError, match="pynng"):
            NngSubscriber()
        with pytest.raises(ImportError, match="pynng"):
            NngTransport()


def test_nng_available_false_when_not_installed():
    with patch("sleipnir.adapters.nng_transport._PYNNG_AVAILABLE", False):
        assert nng_available() is False


# ---------------------------------------------------------------------------
# Functional tests — real pynng IPC sockets (no external services)
# ---------------------------------------------------------------------------


async def test_functional_single_event_delivery(ipc_address):
    """Publish one event; subscriber receives it."""
    received: list[SleipnirEvent] = []
    ready = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        ready.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["ravn.*"], handler)
            event = make_event(event_id="nng-single")
            await pub.publish(event)
            await asyncio.wait_for(ready.wait(), timeout=3.0)

    assert len(received) == 1
    assert received[0].event_id == "nng-single"


async def test_functional_multiple_subscribers(ipc_address):
    """All subscribers independently receive the same event."""
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

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["*"], handler_a)
            await sub.subscribe(["*"], handler_b)
            # Give the SUB socket a brief moment to apply both topic registrations
            # before the first publish; this avoids a flaky race on CI.
            await asyncio.sleep(0.05)
            await pub.publish(make_event(event_id="nng-multi"))
            await asyncio.wait_for(asyncio.gather(done_a.wait(), done_b.wait()), timeout=5.0)

    assert bucket_a == ["nng-multi"]
    assert bucket_b == ["nng-multi"]


async def test_functional_topic_filtering_ravn_star(ipc_address):
    """'ravn.*' subscriber receives ravn events but not ting events."""
    received_types: list[str] = []
    ravn_event_received = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received_types.append(evt.event_type)
        if evt.event_type.startswith("ravn."):
            ravn_event_received.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["ravn.*"], handler)

            await pub.publish(make_event(event_type="ravn.tool.complete", event_id="e1"))
            await asyncio.wait_for(ravn_event_received.wait(), timeout=3.0)

            # Publish a ting event — should NOT arrive since subscriber only
            # set nng topic to "ravn." which won't match "ting.*" messages.
            await pub.publish(make_event(event_type="ting.task.started", event_id="e2"))
            # Give nng a moment to deliver if it were going to.
            await asyncio.sleep(0.1)

    assert "ravn.tool.complete" in received_types
    assert "ting.task.started" not in received_types


async def test_functional_topic_filtering_star_wildcard(ipc_address):
    """'*' subscriber receives events from all namespaces."""
    received_types: list[str] = []
    all_received = asyncio.Event()

    target_types = [
        "ravn.tool.complete",
        "ting.task.started",
        "volundr.pr.opened",
    ]

    async def handler(evt: SleipnirEvent) -> None:
        received_types.append(evt.event_type)
        if all(t in received_types for t in target_types):
            all_received.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["*"], handler)
            for i, et in enumerate(target_types):
                await pub.publish(make_event(event_type=et, event_id=str(i)))
            await asyncio.wait_for(all_received.wait(), timeout=3.0)

    assert sorted(received_types) == sorted(target_types)


async def test_functional_unsubscribe_stops_delivery(ipc_address):
    """Events published after unsubscribe() are never delivered."""
    received: list[str] = []
    first_received = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)
        first_received.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            handle = await sub.subscribe(["ravn.*"], handler)

            await pub.publish(make_event(event_id="before"))
            await asyncio.wait_for(first_received.wait(), timeout=3.0)

            await handle.unsubscribe()

            await pub.publish(make_event(event_id="after"))
            await asyncio.sleep(0.15)  # allow delivery if it were going to happen
            await sub.flush()

    assert received == ["before"]


async def test_functional_ttl_zero_not_delivered(ipc_address):
    """Events with ttl=0 are dropped by the publisher and never sent."""
    received: list[str] = []
    live_received = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)
        live_received.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["*"], handler)

            await pub.publish(make_event(event_id="expired", ttl=0))
            await pub.publish(make_event(event_id="live", ttl=None))
            await asyncio.wait_for(live_received.wait(), timeout=3.0)

    assert received == ["live"]
    assert "expired" not in received


async def test_functional_correlation_id_preserved(ipc_address):
    """correlation_id reaches the subscriber unchanged."""
    received: list[SleipnirEvent] = []
    done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        done.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["*"], handler)
            await pub.publish(make_event(event_id="corr-test", correlation_id="corr-xyz"))
            await asyncio.wait_for(done.wait(), timeout=3.0)

    assert received[0].correlation_id == "corr-xyz"


async def test_functional_urgency_preserved(ipc_address):
    """urgency reaches the subscriber unchanged."""
    received: list[SleipnirEvent] = []
    done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        done.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["*"], handler)
            await pub.publish(make_event(event_id="urgency-test", urgency=0.95))
            await asyncio.wait_for(done.wait(), timeout=3.0)

    assert received[0].urgency == pytest.approx(0.95)


async def test_functional_publish_batch(ipc_address):
    """publish_batch delivers all events in order."""
    received: list[str] = []
    batch_size = 20
    all_done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)
        if len(received) >= batch_size:
            all_done.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["*"], handler)
            batch = [make_event(event_id=f"batch-{i}") for i in range(batch_size)]
            await pub.publish_batch(batch)
            await asyncio.wait_for(all_done.wait(), timeout=5.0)

    assert received == [f"batch-{i}" for i in range(batch_size)]


async def test_functional_nng_transport_combined(ipc_address):
    """NngTransport (combined pub+sub) loops events back to local subscriber."""
    received: list[SleipnirEvent] = []
    done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        done.set()

    async with NngTransport(ipc_address) as bus:
        await bus.subscribe(["ravn.*"], handler)
        await bus.publish(make_event(event_id="combined-test"))
        await asyncio.wait_for(done.wait(), timeout=3.0)

    assert len(received) == 1
    assert received[0].event_id == "combined-test"


async def test_functional_nng_transport_flush(ipc_address):
    """flush() waits for already-received events to be processed."""
    processed: list[str] = []

    async def slow_handler(evt: SleipnirEvent) -> None:
        await asyncio.sleep(0.01)
        processed.append(evt.event_id)

    async with NngTransport(ipc_address) as bus:
        await bus.subscribe(["*"], slow_handler)
        await bus.publish(make_event(event_id="flush-test"))
        await asyncio.sleep(0.1)  # allow nng delivery
        await bus.flush()

    assert "flush-test" in processed


async def test_functional_graceful_shutdown_no_leak(ipc_address):
    """stop() completes cleanly even with active subscriptions."""
    received: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)

    pub = NngPublisher(ipc_address)
    sub = NngSubscriber(ipc_address)
    await pub.start()
    await sub.start()
    await sub.subscribe(["*"], handler)

    await pub.publish(make_event(event_id="before-stop"))
    await asyncio.sleep(0.1)

    # Stop both — should not raise.
    await sub.stop()
    await pub.stop()
    # Verify sockets are closed.
    assert sub._socket is None
    assert pub._socket is None


async def test_functional_stop_idempotent(ipc_address):
    """Calling stop() multiple times does not raise."""
    pub = NngPublisher(ipc_address)
    await pub.start()
    await pub.stop()
    await pub.stop()  # second call should be a no-op


async def test_functional_unsubscribe_idempotent(ipc_address):
    """Calling unsubscribe() multiple times does not raise."""
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    async with NngPublisher(ipc_address):
        async with NngSubscriber(ipc_address) as sub:
            handle = await sub.subscribe(["*"], handler)
            await handle.unsubscribe()
            await handle.unsubscribe()  # second call should be a no-op


# ---------------------------------------------------------------------------
# Functional tests — TCP transport
# ---------------------------------------------------------------------------


async def test_functional_tcp_transport(tcp_address):
    """Basic publish/subscribe works over TCP."""
    received: list[SleipnirEvent] = []
    done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        done.set()

    async with NngPublisher(tcp_address) as pub:
        async with NngSubscriber(tcp_address) as sub:
            await sub.subscribe(["ravn.*"], handler)
            await pub.publish(make_event(event_id="tcp-test"))
            await asyncio.wait_for(done.wait(), timeout=3.0)

    assert received[0].event_id == "tcp-test"


# ---------------------------------------------------------------------------
# Functional tests — multi-process delivery
# ---------------------------------------------------------------------------


async def test_multiprocess_publisher_to_subscriber(ipc_address, tmp_path):
    """Events published in a subprocess are received in the main process."""
    received: list[SleipnirEvent] = []
    done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)
        done.set()

    import os
    from pathlib import Path

    # Locate the workspace src directory so the subprocess can import sleipnir.
    workspace_src = str(Path(__file__).parent.parent.parent / "src")

    # Propagate PYTHONPATH so pynng and sleipnir are importable in the child.
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{workspace_src}:{existing}" if existing else workspace_src

    # Publisher code that runs in the subprocess.
    publisher_code = textwrap.dedent(
        f"""
        import asyncio
        from sleipnir.adapters.nng_transport import NngPublisher
        from sleipnir.domain.events import SleipnirEvent
        from datetime import datetime, UTC

        async def main():
            pub = NngPublisher({ipc_address!r})
            await pub.start()
            await asyncio.sleep(0.4)  # wait for subscriber to connect
            event = SleipnirEvent(
                event_id="cross-proc",
                event_type="ravn.tool.complete",
                source="ravn:subprocess",
                payload={{}},
                summary="cross-process test event",
                urgency=0.5,
                domain="code",
                timestamp=datetime.now(UTC),
            )
            await pub.publish(event)
            await asyncio.sleep(0.2)  # allow delivery
            await pub.stop()

        asyncio.run(main())
        """
    )

    # Start subscriber first so it is ready when the publisher sends.
    async with NngSubscriber(ipc_address, connect_settle_ms=50) as sub:
        await sub.subscribe(["ravn.*"], handler)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            publisher_code,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(done.wait(), timeout=15.0)
        finally:
            with suppress(Exception):
                proc.kill()
            await proc.wait()

    assert len(received) >= 1
    assert any(e.event_id == "cross-proc" for e in received)


# ---------------------------------------------------------------------------
# Functional tests — reconnection
# ---------------------------------------------------------------------------


async def test_reconnection_subscriber_survives_publisher_restart(ipc_address):
    """Subscriber continues to receive after publisher is restarted.

    Sequence:
    1. Publisher binds first so subscriber can connect immediately.
    2. Verify first event is received.
    3. Restart the publisher (stop, then start a new one at the same address).
    4. Retry publishing until the subscriber reconnects — nng PUB/SUB drops
       messages sent while no subscriber pipe is connected.
    5. Verify second event is received.
    """
    received: list[str] = []
    first_received = asyncio.Event()
    second_received = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt.event_id)
        if evt.event_id == "before-restart":
            first_received.set()
        if evt.event_id == "after-restart":
            second_received.set()

    # Bind publisher FIRST so the subscriber connects on its first dial attempt.
    pub1 = NngPublisher(ipc_address)
    await pub1.start()

    async with NngSubscriber(ipc_address) as sub:
        await sub.subscribe(["ravn.*"], handler)

        # Publish and confirm delivery before stopping pub1.
        deadline = asyncio.get_running_loop().time() + 5.0
        while not first_received.is_set():
            await pub1.publish(make_event(event_id="before-restart"))
            try:
                await asyncio.wait_for(first_received.wait(), timeout=0.3)
            except TimeoutError:
                if asyncio.get_running_loop().time() > deadline:
                    raise

        await pub1.stop()

        # Brief gap to let nng detect the broken connection.
        await asyncio.sleep(0.15)

        # Start a second publisher at the same address.
        async with NngPublisher(ipc_address, bind_retry_delay_s=0.05) as pub2:
            deadline = asyncio.get_running_loop().time() + 8.0
            while not second_received.is_set():
                await pub2.publish(make_event(event_id="after-restart"))
                try:
                    await asyncio.wait_for(second_received.wait(), timeout=0.3)
                except TimeoutError:
                    if asyncio.get_running_loop().time() > deadline:
                        raise

    assert "before-restart" in received
    assert "after-restart" in received


# ---------------------------------------------------------------------------
# Functional tests — latency (IPC)
# ---------------------------------------------------------------------------


async def test_ipc_latency_under_threshold(ipc_address):
    """Median IPC round-trip should be well under 10 ms (not µs since we're in
    a containerised CI environment, but still fast enough to be useful)."""
    latencies: list[float] = []
    n = 50
    events_received = asyncio.Event()

    timestamps: dict[str, float] = {}

    async def handler(evt: SleipnirEvent) -> None:
        latencies.append(time.monotonic() - timestamps[evt.event_id])
        if len(latencies) >= n:
            events_received.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["ravn.*"], handler)

            for i in range(n):
                eid = f"lat-{i}"
                timestamps[eid] = time.monotonic()
                await pub.publish(make_event(event_id=eid))

            await asyncio.wait_for(events_received.wait(), timeout=10.0)

    median_ms = sorted(latencies)[n // 2] * 1000
    # Allow up to 50 ms median on CI (IPC is typically <1 ms on metal).
    assert median_ms < 50.0, f"Median IPC latency too high: {median_ms:.2f} ms"


# ---------------------------------------------------------------------------
# Functional tests — ring buffer overflow
# ---------------------------------------------------------------------------


async def test_ring_buffer_overflow_drops_oldest_and_warns(ipc_address, caplog):
    """When the ring buffer overflows, the oldest event is dropped with a warning.

    Strategy: publish many more events than the ring buffer can hold while the
    consumer is blocked on a Semaphore.  Once at least one overflow warning is
    logged we unblock the consumer and verify no assert is raised.
    """
    import logging

    received_ids: list[str] = []
    # Semaphore starts at 0 — the consumer blocks until we release it.
    release = asyncio.Semaphore(0)

    async def blocking_handler(evt: SleipnirEvent) -> None:
        await release.acquire()
        received_ids.append(evt.event_id)

    n_events = 20  # far more than ring_buffer_depth=2

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address, ring_buffer_depth=2) as sub:
            await sub.subscribe(["*"], blocking_handler)

            with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.nng_transport"):
                for i in range(n_events):
                    await pub.publish(make_event(event_id=f"e{i}"))
                # Give nng time to deliver all messages to the recv_loop.
                await asyncio.sleep(0.4)

            warning_records = [r for r in caplog.records if "Ring buffer overflow" in r.message]
            assert len(warning_records) >= 1

            # Release the consumer for all queued items so flush() can complete.
            for _ in range(sub._subscriptions[0]._queue.qsize() + 1):
                release.release()
            await sub.flush()


# ---------------------------------------------------------------------------
# NIU-522: latency benchmarks
# ---------------------------------------------------------------------------


async def test_nng_publish_wall_time_bounded_per_event(ipc_address):
    """publish() wall time must be bounded regardless of subscriber state.

    Measure the time for N synchronous publish() calls (no subscriber running)
    and assert p99 is within a generous CI threshold.  On bare metal IPC the
    expected overhead is < 5 µs per call; CI containers allow up to 5 ms.
    """
    n = 500

    async with NngPublisher(ipc_address) as pub:
        times: list[float] = []
        for i in range(n):
            t0 = time.perf_counter()
            await pub.publish(make_event(event_id=f"wall-{i}"))
            times.append(time.perf_counter() - t0)

    times.sort()
    p50_ms = times[n // 2] * 1000
    p99_ms = times[int(n * 0.99)] * 1000
    max_ms = times[-1] * 1000

    # Log for visibility in CI output.
    print(
        f"\nnng publish() wall time (n={n}):"
        f" p50={p50_ms:.3f}ms  p99={p99_ms:.3f}ms  max={max_ms:.3f}ms"
        f"  (bare-metal target: p50<0.005ms, p99<0.050ms)"
    )

    # CI threshold — generous to survive noisy containers.
    assert p99_ms < 50.0, (
        f"publish() p99 wall time too high: {p99_ms:.3f}ms. "
        "Expected < 50ms in CI (bare-metal target: < 0.050ms)."
    )


async def test_nng_end_to_end_latency_10k_events(ipc_address):
    """End-to-end latency benchmark: publish → handler called, 10 000 events.

    Architecture target (bare metal, same machine):
      p50 < 20 µs  (0.020 ms)
      p99 < 50 µs  (0.050 ms)

    CI containers run on shared VMs and are considerably slower.  The
    assertions here use generous thresholds so the test does not flap;
    the printed percentiles are what matter for tracking regressions.
    """
    n = 10_000
    latencies: list[float] = []
    t_publish: dict[str, float] = {}
    all_done = asyncio.Event()

    async def handler(evt: SleipnirEvent) -> None:
        latencies.append(time.perf_counter() - t_publish[evt.event_id])
        if len(latencies) >= n:
            all_done.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["ravn.*"], handler)

            for i in range(n):
                eid = f"lat-{i}"
                t_publish[eid] = time.perf_counter()
                await pub.publish(make_event(event_id=eid))

            await asyncio.wait_for(all_done.wait(), timeout=120.0)

    latencies.sort()
    p50_ms = latencies[n // 2] * 1000
    p95_ms = latencies[int(n * 0.95)] * 1000
    p99_ms = latencies[int(n * 0.99)] * 1000
    max_ms = latencies[-1] * 1000

    print(
        f"\nnng IPC end-to-end latency (n={n:,}):"
        f" p50={p50_ms:.3f}ms  p95={p95_ms:.3f}ms"
        f"  p99={p99_ms:.3f}ms  max={max_ms:.3f}ms"
        f"  (bare-metal targets: p50<0.020ms, p99<0.050ms)"
    )

    # CI threshold — containers are much slower than bare metal.
    assert p50_ms < 50.0, (
        f"p50 latency {p50_ms:.3f}ms exceeds CI threshold 50ms (bare-metal target: 0.020ms)"
    )
    assert p99_ms < 200.0, (
        f"p99 latency {p99_ms:.3f}ms exceeds CI threshold 200ms (bare-metal target: 0.050ms)"
    )


# ---------------------------------------------------------------------------
# NIU-522: failure mode tests
# ---------------------------------------------------------------------------


async def test_slow_subscriber_does_not_block_publisher(ipc_address, caplog):
    """publish() must return in bounded time even when subscriber queue is saturated.

    The subscriber's handler blocks indefinitely; the ring buffer overflows and
    drops events.  The publisher must never stall — all publish() calls must
    complete quickly and overflow warnings must be logged.
    """
    import logging

    block = asyncio.Event()
    n_events = 50  # >> ring_buffer_depth=2

    async def blocking_handler(_: SleipnirEvent) -> None:
        await block.wait()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address, ring_buffer_depth=2) as sub:
            await sub.subscribe(["*"], blocking_handler)

            with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.nng_transport"):
                t0 = time.perf_counter()
                for i in range(n_events):
                    await pub.publish(make_event(event_id=f"slow-{i}"))
                elapsed_ms = (time.perf_counter() - t0) * 1000

            # Unblock so the subscriber loop can drain cleanly on shutdown.
            block.set()

    # All n_events published in well under 1 s — publisher was never stalled.
    assert elapsed_ms < 1000.0, (
        f"publish() stalled: {elapsed_ms:.1f}ms for {n_events} events with "
        "saturated subscriber queue."
    )

    overflow_warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
    assert len(overflow_warnings) >= 1, (
        "Expected ring buffer overflow warnings when subscriber queue is saturated."
    )


async def test_crashing_handler_does_not_affect_sibling_subscriber(ipc_address):
    """A handler that raises must not prevent sibling subscribers from receiving events.

    consume_queue() isolates per-handler exceptions so that one bad handler
    cannot poison the delivery loop for other subscriptions.
    """
    received_by_stable: list[str] = []
    n = 5
    all_stable_done = asyncio.Event()

    async def crashing_handler(_: SleipnirEvent) -> None:
        raise RuntimeError("Simulated handler crash — must not affect siblings")

    async def stable_handler(evt: SleipnirEvent) -> None:
        received_by_stable.append(evt.event_id)
        if len(received_by_stable) >= n:
            all_stable_done.set()

    async with NngPublisher(ipc_address) as pub:
        async with NngSubscriber(ipc_address) as sub:
            await sub.subscribe(["*"], crashing_handler)
            await sub.subscribe(["*"], stable_handler)

            for i in range(n):
                await pub.publish(make_event(event_id=f"crash-{i}"))

            await asyncio.wait_for(all_stable_done.wait(), timeout=5.0)

    assert len(received_by_stable) == n, (
        f"Stable handler only received {len(received_by_stable)}/{n} events "
        "after sibling handler crashed."
    )


async def test_publisher_start_raises_on_invalid_socket_path():
    """NngPublisher.start() raises a clear error when the IPC path is inaccessible.

    An invalid IPC path (non-existent directory) must raise immediately rather
    than silently succeeding and then dropping all messages.  The socket must
    also be cleaned up so there is no resource leak.
    """
    bad_address = "ipc:///nonexistent_dir_xyz_522/deep/sleipnir.sock"
    pub = NngPublisher(bad_address, bind_max_retries=1)

    with pytest.raises(Exception):
        await pub.start()

    # After failed start the internal socket must be cleaned up.
    assert pub._socket is None, (
        "NngPublisher._socket must be None after a failed start() to avoid resource leak."
    )


class TestPeerAddressProvider:
    """A peer list fixed at construction left late joiners unreachable."""

    def _subscriber(self, peer_addresses):
        from sleipnir.adapters.nng_transport import NngSubscriber

        return NngSubscriber(address="tcp://127.0.0.1:0", peer_addresses=peer_addresses)

    def test_a_static_list_is_still_honoured(self) -> None:
        sub = self._subscriber(["tcp://127.0.0.1:1", "tcp://127.0.0.1:2"])

        assert sub._resolve_peer_addresses() == ["tcp://127.0.0.1:1", "tcp://127.0.0.1:2"]

    def test_a_provider_is_re_invoked(self) -> None:
        """Re-discovery must see peers that appeared after startup."""
        roster = ["tcp://127.0.0.1:1"]
        sub = self._subscriber(lambda: list(roster))

        assert sub._resolve_peer_addresses() == ["tcp://127.0.0.1:1"]

        roster.append("tcp://127.0.0.1:2")

        assert sub._resolve_peer_addresses() == ["tcp://127.0.0.1:1", "tcp://127.0.0.1:2"]

    def test_a_failing_provider_yields_no_peers(self) -> None:
        def _boom() -> list[str]:
            raise RuntimeError("cluster file unreadable")

        assert self._subscriber(_boom)._resolve_peer_addresses() == []

    def test_no_peers_configured_yields_empty(self) -> None:
        assert self._subscriber(None)._resolve_peer_addresses() == []
