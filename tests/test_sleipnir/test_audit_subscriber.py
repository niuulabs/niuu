"""Tests for AuditSubscriber — start/stop lifecycle and event handling."""

from __future__ import annotations

from unittest.mock import patch

from sleipnir.adapters.audit_subscriber import AuditConfig, AuditSubscriber
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.ports.audit import AuditRepository
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _InMemoryAuditRepository(AuditRepository):
    """Minimal in-memory AuditRepository for testing."""

    def __init__(self) -> None:
        self.appended: list = []
        self.purge_count = 0

    async def append(self, event) -> None:
        self.appended.append(event)

    async def query(self, q) -> list:
        return list(self.appended)

    async def purge_expired(self) -> int:
        self.purge_count += 1
        return 0


def _make_subscriber(
    bus: InProcessBus | None = None,
    repo: AuditRepository | None = None,
    config: AuditConfig | None = None,
) -> tuple[AuditSubscriber, InProcessBus, _InMemoryAuditRepository]:
    bus = bus or InProcessBus()
    repo = repo or _InMemoryAuditRepository()
    subscriber = AuditSubscriber(bus, repo, config)
    return subscriber, bus, repo


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


async def test_start_sets_running_flag():
    subscriber, _, _ = _make_subscriber()
    await subscriber.start()
    assert subscriber.running is True
    await subscriber.stop()


async def test_stop_clears_running_flag():
    subscriber, _, _ = _make_subscriber()
    await subscriber.start()
    await subscriber.stop()
    assert subscriber.running is False


async def test_start_is_idempotent():
    """Calling start() twice must not raise or create duplicate subscriptions."""
    subscriber, bus, repo = _make_subscriber()
    await subscriber.start()
    await subscriber.start()  # second call is a no-op
    assert subscriber.running is True
    await subscriber.stop()


async def test_stop_before_start_is_safe():
    subscriber, _, _ = _make_subscriber()
    await subscriber.stop()  # must not raise
    assert subscriber.running is False


async def test_disabled_subscriber_does_not_start():
    config = AuditConfig(enabled=False)
    subscriber, _, _ = _make_subscriber(config=config)
    await subscriber.start()
    assert subscriber.running is False


# ---------------------------------------------------------------------------
# Event handling tests
# ---------------------------------------------------------------------------


async def test_subscriber_captures_all_events():
    subscriber, bus, repo = _make_subscriber()
    await subscriber.start()

    event = make_event()
    await bus.publish(event)
    await bus.flush()

    await subscriber.stop()
    assert len(repo.appended) == 1
    assert repo.appended[0].event_id == event.event_id


async def test_subscriber_captures_multiple_event_types():
    subscriber, bus, repo = _make_subscriber()
    await subscriber.start()

    events = [
        make_event(event_type="ravn.tool.complete"),
        make_event(event_id="evt-002", event_type="ting.task.started"),
        make_event(event_id="evt-003", event_type="volundr.session.started"),
    ]
    for evt in events:
        await bus.publish(evt)
    await bus.flush()

    await subscriber.stop()
    assert len(repo.appended) == 3


async def test_handler_exception_does_not_crash_subscriber(caplog):
    """If the repository raises, the subscriber must log and continue."""
    bus = InProcessBus()
    repo = _InMemoryAuditRepository()

    call_count = 0

    async def failing_append(event) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("DB write failed")
        repo.appended.append(event)

    repo.append = failing_append

    subscriber = AuditSubscriber(bus, repo)
    await subscriber.start()

    await bus.publish(make_event(event_id="evt-fail"))
    await bus.publish(make_event(event_id="evt-ok"))
    await bus.flush()

    await subscriber.stop()

    # Second event should still be recorded
    assert len(repo.appended) == 1
    assert repo.appended[0].event_id == "evt-ok"


# ---------------------------------------------------------------------------
# TTL cleanup tests
# ---------------------------------------------------------------------------


async def test_ttl_loop_calls_purge_after_interval():
    """TTL loop must call purge_expired each time sleep completes."""
    config = AuditConfig(ttl_cleanup_interval_seconds=3600)
    subscriber, bus, repo = _make_subscriber(config=config)

    call_count = 0

    async def controlled_sleep(_interval: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            # After purge runs once, stop the subscriber so the loop exits.
            subscriber._running = False

    subscriber._running = True
    with patch("sleipnir.adapters.audit_subscriber.asyncio.sleep", side_effect=controlled_sleep):
        await subscriber._ttl_loop()

    assert repo.purge_count == 1


async def test_ttl_loop_exception_does_not_crash():
    """Exceptions in purge_expired are logged, not propagated."""
    config = AuditConfig(ttl_cleanup_interval_seconds=3600)
    bus = InProcessBus()
    repo = _InMemoryAuditRepository()

    purge_calls = 0

    async def failing_purge() -> int:
        nonlocal purge_calls
        purge_calls += 1
        if purge_calls == 1:
            raise RuntimeError("Purge failure")
        return 0

    repo.purge_expired = failing_purge

    subscriber = AuditSubscriber(bus, repo, config)

    call_count = 0

    async def controlled_sleep(_interval: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            subscriber._running = False

    subscriber._running = True
    with patch("sleipnir.adapters.audit_subscriber.asyncio.sleep", side_effect=controlled_sleep):
        await subscriber._ttl_loop()

    # Second purge should have succeeded; subscriber exited cleanly
    assert purge_calls == 2


# ---------------------------------------------------------------------------
# AuditConfig tests
# ---------------------------------------------------------------------------


def test_audit_config_defaults():
    config = AuditConfig()
    assert config.enabled is True
    assert config.ttl_cleanup_interval_seconds == 3600


def test_audit_config_custom():
    config = AuditConfig(enabled=False, ttl_cleanup_interval_seconds=7200)
    assert config.enabled is False
    assert config.ttl_cleanup_interval_seconds == 7200


async def test_stop_cancels_ttl_task():
    """stop() must cancel a running TTL task and swallow CancelledError."""
    subscriber, _, _ = _make_subscriber()
    await subscriber.start()
    assert subscriber._ttl_task is not None

    # The TTL task is sleeping; stopping should cancel it without raising
    await subscriber.stop()
    assert subscriber.running is False
    assert subscriber._ttl_task is None
