"""Integration tests for JetStream consumer/stream recovery against a real broker.

fix/sleipnir-nats-consumer-recovery: the unit tests in ``test_nats_transport.py``
mock nats-py and cannot see server-enforced behavior.  Two production bugs only
showed up against a real nats-server (confirmed on 2.14/2.14.2, the version
production runs):

1. The deliver subject the fix first used
   (``{prefix}.system.deliver.<durable>``) sat inside the stream's own
   ``{prefix}.>`` subjects, so the server rejected every new durable with
   ``400 err_code=10081`` ("consumer deliver subject forms a cycle").  Fixed
   by using ``_INBOX.sleipnir.<durable>``, which no stream ever captures.
2. A later fix tried to *migrate* a mismatched durable by updating its
   deliver_subject on the server before rebinding.  The server refuses that
   too: it will not change a push durable's deliver subject while anything is
   still subscribed to its current inbox (``400 err_code=10013 "consumer name
   already in use"``) — and every pod already is, at startup, so every pod
   retried every durable forever.  The fix does not write to the server at
   all: a health check compares the durable's live deliver_subject/
   deliver_group against what *this* subscription is actually bound to (not
   a deterministic target), and on a mismatch just resubscribes — nats-py
   binds to whatever the server currently has.

Requires a running NATS server with JetStream enabled (``nats-server --jetstream``).
Set ``TEST_NATS_URL`` to override the default ``nats://localhost:4222``.  Like the
rest of ``tests/integration/sleipnir/``, these are marked ``broker`` and excluded
from the default test run; they run in CI only (the "Python: Integration Test
Sleipnir" job), never locally.
"""

from __future__ import annotations

import asyncio
import uuid

import nats
import nats.js.api as js_api
import pytest

from sleipnir.adapters.nats_transport import (
    NatsTransport,
    _durable_name_for_subject,
    _nats_subjects_for_patterns,
)
from sleipnir.domain.events import SleipnirEvent

from .conftest import (
    NATS_URL,
    TEST_NATS_MAX_AGE_SECONDS,
    TEST_NATS_MAX_BYTES,
    collect_events,
    make_event,
)

pytestmark = pytest.mark.broker

# Fast enough to observe recovery within a test timeout, slow enough not to
# hammer a real server.
_HEALTH_CHECK_INTERVAL_S = 0.3
_RECOVERY_BACKOFF_S = [0.2, 0.5]
_RECOVERY_TIMEOUT_S = 10.0


def _transport(
    stream_name: str,
    *,
    consumer_group: str | None = None,
    ensure_stream: bool = True,
) -> NatsTransport:
    return NatsTransport(
        servers=[NATS_URL],
        stream_name=stream_name,
        subject_prefix=stream_name,
        consumer_group=consumer_group,
        ensure_stream=ensure_stream,
        max_reconnect_attempts=3,
        connect_timeout_s=5.0,
        max_bytes=TEST_NATS_MAX_BYTES,
        max_age_seconds=TEST_NATS_MAX_AGE_SECONDS,
        consumer_health_check_interval_s=_HEALTH_CHECK_INTERVAL_S,
        consumer_health_check_jitter_s=0.0,
        consumer_recovery_backoff_s=_RECOVERY_BACKOFF_S,
    )


async def _wait_for(condition, *, timeout: float = _RECOVERY_TIMEOUT_S) -> None:
    """Poll *condition* (a zero-arg callable) until it is true, or *timeout* expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not condition():
        if asyncio.get_event_loop().time() >= deadline:
            return
        await asyncio.sleep(0.05)


async def _add_legacy_durable(
    *, stream_name: str, subject_prefix: str, consumer_group: str, pattern: str
) -> tuple[str, str]:
    """Create a stream plus a durable with a random (pre-deterministic-inbox) deliver subject.

    Simulates a durable created before this fix shipped, or by nats-py's own
    auto-assigned inbox.  Returns ``(durable, old_inbox)``.
    """
    subject = _nats_subjects_for_patterns([pattern], subject_prefix)[0]
    durable = _durable_name_for_subject(consumer_group, subject)

    nc = await nats.connect(servers=[NATS_URL])
    js = nc.jetstream()
    await js.add_stream(
        config=js_api.StreamConfig(
            name=stream_name,
            subjects=[f"{subject_prefix}.>"],
            retention=js_api.RetentionPolicy.LIMITS,
            storage=js_api.StorageType.FILE,
        )
    )
    old_inbox = nc.new_inbox()
    await js.add_consumer(
        stream_name,
        config=js_api.ConsumerConfig(
            durable_name=durable,
            deliver_subject=old_inbox,
            deliver_group=durable,
            ack_policy=js_api.AckPolicy.EXPLICIT,
            deliver_policy=js_api.DeliverPolicy.NEW,
            filter_subject=subject,
        ),
    )
    await nc.close()
    return durable, old_inbox


# ---------------------------------------------------------------------------
# 1. A durable push consumer must create without a cycle error.
# ---------------------------------------------------------------------------


async def test_durable_push_consumer_creates_without_a_cycle_error():
    """The deterministic deliver subject must not sit inside the stream's own subjects.

    Before the fix, ``subscribe()`` with a ``consumer_group`` raised on this
    real server: ``nats: JetStreamAPIError ... consumer deliver subject
    forms a cycle`` — every mesh RPC reply durable
    (``ravn.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter.send``) would
    have failed the same way.
    """
    stream_name = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    transport = _transport(stream_name, consumer_group="workers")
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with transport:
        await transport.subscribe(["cycle.*"], handler)
        await transport.publish(make_event(event_type="cycle.check"))
        await collect_events(1, received)

    assert [event.event_type for event in received] == ["cycle.check"]


# ---------------------------------------------------------------------------
# 2. A queue group of two subscribers survives the durable being recreated.
# ---------------------------------------------------------------------------


async def test_queue_group_survives_durable_recreation():
    """Both replicas of a queue group must still receive once their durable is recreated.

    Before the deterministic deliver subject, only the replica that happened
    to recreate the durable got its new (random) inbox; the other stayed
    bound to the deleted durable's old inbox and never received anything
    again.  Both replicas' watchdogs race to recreate the same missing
    durable here, exercising the "consumer name already in use" tolerance:
    the loser must bind to what the winner created, not treat it as failure.
    """
    stream_name = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    publisher = _transport(stream_name)
    worker_a = _transport(stream_name, consumer_group="workers")
    worker_b = _transport(stream_name, consumer_group="workers")
    received_a: list[SleipnirEvent] = []
    received_b: list[SleipnirEvent] = []

    async def handler_a(event: SleipnirEvent) -> None:
        received_a.append(event)

    async def handler_b(event: SleipnirEvent) -> None:
        received_b.append(event)

    async with publisher, worker_a, worker_b:
        handle_a = await worker_a.subscribe(["recreate.*"], handler_a)
        await worker_b.subscribe(["recreate.*"], handler_b)
        durable = handle_a.consumer_watches[0].durable
        assert durable is not None

        # Simulate the JetStream store being reset: delete the shared
        # durable directly, bypassing both subscribers (as the NACK
        # controller recreating streams would).
        nc = await nats.connect(servers=[NATS_URL])
        js = nc.jetstream()
        await js.delete_consumer(stream_name, durable)
        await nc.close()

        await _wait_for(
            lambda: (
                worker_a._subscriber.stats()["consumer_recovered"] >= 1  # noqa: SLF001
                and worker_b._subscriber.stats()["consumer_recovered"] >= 1  # noqa: SLF001
            )
        )
        assert worker_a._subscriber.stats()["consumer_recovered"] >= 1  # noqa: SLF001
        assert worker_b._subscriber.stats()["consumer_recovered"] >= 1  # noqa: SLF001

        events = [
            make_event(event_id=f"recreate-{idx}", event_type="recreate.signal") for idx in range(6)
        ]
        for event in events:
            await publisher.publish(event)

        await _wait_for(lambda: len(received_a) + len(received_b) >= len(events), timeout=5.0)

    delivered_ids = [event.event_id for event in [*received_a, *received_b]]
    assert set(delivered_ids) == {event.event_id for event in events}
    assert len(delivered_ids) == len(set(delivered_ids)), "no duplicate delivery"
    assert received_a, "worker A must still receive after the durable was recreated"
    assert received_b, "worker B must still receive after the durable was recreated"


# ---------------------------------------------------------------------------
# 3. A new pod joining an old random-inbox durable must not churn.
# ---------------------------------------------------------------------------


async def test_new_pod_binds_to_an_existing_random_inbox_durable_without_churn():
    """A legacy durable's non-deterministic inbox is not, by itself, a mismatch.

    Comparing against what *this* subscription is actually bound to (rather
    than the deterministic target every fresh durable now gets) means a
    pre-existing random-inbox durable is left alone: both an already-running
    pod and a newly-joining one simply bind to whatever the durable already
    has, share the load as an ordinary queue group, and the server is never
    written to.
    """
    stream_name = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    subject_prefix = stream_name
    consumer_group = "workers"
    pattern = "legacy.*"
    durable, old_inbox = await _add_legacy_durable(
        stream_name=stream_name,
        subject_prefix=subject_prefix,
        consumer_group=consumer_group,
        pattern=pattern,
    )

    existing_pod = _transport(stream_name, consumer_group=consumer_group, ensure_stream=False)
    new_pod = _transport(stream_name, consumer_group=consumer_group, ensure_stream=False)
    received_existing: list[SleipnirEvent] = []
    received_new: list[SleipnirEvent] = []

    async def handler_existing(event: SleipnirEvent) -> None:
        received_existing.append(event)

    async def handler_new(event: SleipnirEvent) -> None:
        received_new.append(event)

    async with existing_pod, new_pod:
        await existing_pod.subscribe([pattern], handler_existing)
        # The "existing" pod binds first; give it a moment before the "new"
        # pod joins the same queue group, as a rolling deploy would.
        await asyncio.sleep(0.1)
        await new_pod.subscribe([pattern], handler_new)

        # Several health-check intervals: neither pod's watch should ever
        # see a mismatch, since each is bound to exactly what the server
        # reports for the (unchanged) legacy durable.
        await asyncio.sleep(_HEALTH_CHECK_INTERVAL_S * 5)

        assert existing_pod._subscriber.stats()["consumer_rebound"] == 0  # noqa: SLF001
        assert new_pod._subscriber.stats()["consumer_rebound"] == 0  # noqa: SLF001
        assert existing_pod._subscriber.stats()["consumer_recovered"] == 0  # noqa: SLF001
        assert new_pod._subscriber.stats()["consumer_recovered"] == 0  # noqa: SLF001

        publisher = _transport(stream_name)
        async with publisher:
            events = [
                make_event(event_id=f"legacy-{idx}", event_type="legacy.signal") for idx in range(4)
            ]
            for event in events:
                await publisher.publish(event)

            await _wait_for(
                lambda: len(received_existing) + len(received_new) >= len(events), timeout=5.0
            )

    delivered_ids = [event.event_id for event in [*received_existing, *received_new]]
    assert set(delivered_ids) == {event.event_id for event in events}
    assert len(delivered_ids) == len(set(delivered_ids)), "no duplicate delivery"

    nc = await nats.connect(servers=[NATS_URL])
    js = nc.jetstream()
    info = await js.consumer_info(stream_name, durable)
    await nc.close()
    assert info.config.deliver_subject == old_inbox, "the durable's inbox must never be rewritten"


# ---------------------------------------------------------------------------
# 4. The stream is deleted and recreated: the consumer recovers.
# ---------------------------------------------------------------------------


async def test_stream_deleted_and_recreated_consumer_recovers():
    """A stream reset (delete + recreate) is detected and the consumer recreated.

    This is the exact production incident: the NATS JetStream store was
    reset, streams came back (recreated by the NACK controller) but the
    resident's consumers stayed missing until the pod restarted.
    """
    stream_name = f"sleipnir_test_{uuid.uuid4().hex[:8]}"
    transport = _transport(stream_name, consumer_group="workers")
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with transport:
        await transport.subscribe(["wipe.*"], handler)
        await transport.publish(make_event(event_id="before-wipe", event_type="wipe.signal"))
        await collect_events(1, received)
        assert [event.event_id for event in received] == ["before-wipe"]

        nc = await nats.connect(servers=[NATS_URL])
        js = nc.jetstream()
        await js.delete_stream(stream_name)
        await js.add_stream(
            config=js_api.StreamConfig(
                name=stream_name,
                subjects=[f"{stream_name}.>"],
                retention=js_api.RetentionPolicy.LIMITS,
                storage=js_api.StorageType.FILE,
            )
        )
        await nc.close()

        await _wait_for(lambda: transport._subscriber.stats()["consumer_recovered"] >= 1)  # noqa: SLF001
        assert transport._subscriber.stats()["consumer_recovered"] >= 1  # noqa: SLF001

        await transport.publish(make_event(event_id="after-recovery", event_type="wipe.signal"))
        await collect_events(2, received)

    assert [event.event_id for event in received] == ["before-wipe", "after-recovery"]
