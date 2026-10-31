"""The notification outbox dispatcher against the in-memory outbox (Postgres claim semantics)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from niuu.domain.notifications import (
    NotificationKind,
    NotificationLink,
    NotificationSeverity,
    NotificationSource,
)
from niuu.ports.notifications import (
    DeliveryResult,
    NotificationDeliveryError,
    NotificationSink,
    NotificationSinkUnavailableError,
    OutboundNotification,
)
from tests.support.notifications import InMemoryNotificationStore
from volundr.domain.notification_ports import NotificationSinkProvider
from volundr.domain.notifications import (
    DeliveryStatus,
    NotificationCandidate,
    NotificationQuietHours,
    NotificationRateLimit,
    NotificationRule,
    NotificationRuleMatch,
)
from volundr.domain.services.notification_dispatcher import (
    DispatcherSettings,
    NotificationDispatcher,
    NotificationRenderer,
    _RateWindows,
)

START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)  # 13:00 in Europe/London
SESSION = UUID("5f5f5f5f-0000-4000-8000-000000000001")


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Monotonic:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class FakeSink(NotificationSink):
    def __init__(self, name: str = "ops", outcomes=(), *, skip: str | None = None, delay=0.0):
        self._name = name
        self.outcomes = list(outcomes)
        self.skip = skip
        self.delay = delay
        self.sent: list[OutboundNotification] = []
        self.closed = False

    @property
    def name(self) -> str:
        return self._name

    def skip_reason(self, message: OutboundNotification) -> str | None:
        return self.skip

    async def send(self, message: OutboundNotification) -> DeliveryResult:
        self.sent.append(message)
        if self.delay:
            await asyncio.sleep(self.delay)
        outcome = self.outcomes.pop(0) if self.outcomes else DeliveryResult("remote-1")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True


class FakeProvider(NotificationSinkProvider):
    def __init__(self, sinks: dict[str, NotificationSink] | None = None, *, error=None) -> None:
        self.sinks = sinks or {}
        self.error = error
        self.opened = 0
        self.released: list[NotificationSink] = []

    async def open(self, rule: NotificationRule) -> NotificationSink:
        self.opened += 1
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return self.sinks[rule.sink]

    async def release(self, sink: NotificationSink) -> None:
        self.released.append(sink)

    async def close(self) -> None:
        return None


def settings(**overrides) -> DispatcherSettings:
    values = {
        "poll_interval_seconds": 0.01,
        "batch_size": 10,
        "lease_seconds": 60.0,
        "send_timeout_seconds": 5.0,
        "max_concurrent_sends": 4,
        "max_attempts": 3,
        "backoff_base_seconds": 10.0,
        "backoff_max_seconds": 300.0,
        "backoff_jitter_ratio": 0.2,
        "default_rate_limit": None,
    }
    values.update(overrides)
    return DispatcherSettings(**values)


def renderer(base: str = "https://forge.example.com") -> NotificationRenderer:
    return NotificationRenderer(
        public_base_url=base,
        session_link_path="/volundr/session/{session_id}#notification-{notification_id}",
        feed_link_path="/volundr/notifications",
        resident_link_path="/ravn?ravn={resident_id}&tab=chat",
        resident_session_link_path=(
            "/ravn?ravn={resident_id}&tab=chat&session={resident_session_id}"
        ),
        host_label="thor",
    )


class Harness:
    def __init__(self, *, sink: FakeSink | None = None, provider=None, **setting_overrides):
        self.clock = Clock()
        self.mono = Monotonic()
        self.store = InMemoryNotificationStore(clock=self.clock)
        self.sink = sink or FakeSink()
        self.provider = provider or FakeProvider({self.sink.name: self.sink})
        self.settings = settings(**setting_overrides)
        self.dispatcher = self.new_dispatcher()

    def new_dispatcher(self) -> NotificationDispatcher:
        return NotificationDispatcher(
            self.store.outbox,
            self.provider,
            renderer(),
            self.settings,
            clock=self.clock,
            monotonic=self.mono,
            jitter=lambda: 0.5,  # no spread: 1 + ratio * (2 * 0.5 - 1) == 1
        )

    def rule(self, **overrides) -> NotificationRule:
        values = {
            "id": uuid4(),
            "owner_id": "user-1",
            "name": "ops",
            "sink": self.sink.name,
            "match": NotificationRuleMatch(),
            "created_at": START,
            "updated_at": START,
        }
        values.update(overrides)
        rule = NotificationRule(**values)
        self.store.rules[rule.id] = rule
        return rule

    async def notify(self, key: str = "n1", **overrides):
        values = {
            "dedupe_key": key,
            "session_id": SESSION,
            "session_seq": 7,
            "session_name": "fix-auth",
            "owner_id": "user-1",
            "kind": NotificationKind.MILESTONE,
            "severity": NotificationSeverity.INFO,
            "source": NotificationSource.AGENT,
            "title": f"title {key}",
            "body": "body",
            "links": [
                NotificationLink(label="PR", url="https://github.com/o/r/pull/1"),
                NotificationLink(label="Report", url="/volundr/files/report.pdf"),
            ],
            "metadata": {"turn_id": "t1"},
        }
        values.update(overrides)
        created = await self.store.feed.project([NotificationCandidate(**values)])
        return created[0] if created else None

    def deliveries(self):
        return sorted(self.store.deliveries.values(), key=lambda d: d.created_at)

    def only(self):
        (delivery,) = self.store.deliveries.values()
        return delivery


class TestDelivery:
    async def test_matching_rule_is_delivered_with_a_rendered_message(self):
        h = Harness()
        rule = h.rule()
        notification = await h.notify()

        assert await h.dispatcher.run_once() == 1

        delivery = h.only()
        assert delivery.status is DeliveryStatus.DELIVERED
        assert delivery.delivered_at == START and delivery.attempts == 1
        (message,) = h.sink.sent
        assert message.notification_id == str(notification.id)
        assert message.url == (
            f"https://forge.example.com/volundr/session/{SESSION}#notification-{notification.id}"
        )
        assert message.session_label == "fix-auth" and message.host_label == "thor"
        assert [link.url for link in message.links] == [
            "https://github.com/o/r/pull/1",
            "https://forge.example.com/volundr/files/report.pdf",
        ]
        assert message.delivery_id == str(delivery.id) and message.attempt == 1
        assert message.metadata["rule_id"] == str(rule.id)
        assert message.metadata["turn_id"] == "t1"
        assert h.dispatcher.stats.delivered == 1
        assert h.dispatcher.stats.by_sink == {"ops": 1}
        assert h.provider.released == [h.sink]

    async def test_nothing_due_claims_nothing(self):
        h = Harness()
        assert await h.dispatcher.run_once() == 0
        assert h.dispatcher.stats.ticks == 1

    async def test_each_distinct_sink_is_resolved_once_per_poll(self):
        h = Harness()
        h.rule()
        for index in range(3):
            await h.notify(f"n{index}")
        await h.dispatcher.run_once()
        assert len(h.sink.sent) == 3
        assert h.provider.opened == 1
        assert h.provider.released == [h.sink]


class TestRetries:
    async def test_retryable_failure_backs_off_exponentially_then_delivers(self):
        failure = NotificationDeliveryError("HTTP 503", retryable=True)
        h = Harness(sink=FakeSink(outcomes=[failure, failure]))
        h.rule()
        await h.notify()

        await h.dispatcher.run_once()
        first = h.only()
        assert first.status is DeliveryStatus.FAILED
        assert first.attempts == 1
        assert first.next_attempt_at == START + timedelta(seconds=10)
        assert first.last_error == "HTTP 503"

        h.clock.advance(9)
        assert await h.dispatcher.run_once() == 0  # not due yet
        h.clock.advance(1)
        await h.dispatcher.run_once()
        second = h.only()
        assert second.attempts == 2
        assert second.next_attempt_at == h.clock.now + timedelta(seconds=20)

        h.clock.advance(20)
        await h.dispatcher.run_once()
        assert h.only().status is DeliveryStatus.DELIVERED
        assert h.only().last_error is None
        assert h.dispatcher.stats.retried == 2

    async def test_provider_retry_after_is_honoured(self):
        h = Harness(
            sink=FakeSink(
                outcomes=[NotificationDeliveryError("429", retryable=True, retry_after=120)]
            )
        )
        h.rule()
        await h.notify()
        await h.dispatcher.run_once()
        assert h.only().next_attempt_at == START + timedelta(seconds=120)

    async def test_permanent_failure_is_dead_immediately(self):
        h = Harness(
            sink=FakeSink(outcomes=[NotificationDeliveryError("chat not found", retryable=False)])
        )
        h.rule()
        await h.notify()
        await h.dispatcher.run_once()
        dead = h.only()
        assert dead.status is DeliveryStatus.DEAD
        assert dead.last_error == "chat not found"
        assert h.dispatcher.stats.dead == 1

    async def test_max_attempts_makes_it_dead(self):
        failure = NotificationDeliveryError("HTTP 500", retryable=True)
        h = Harness(sink=FakeSink(outcomes=[failure] * 5), max_attempts=2)
        h.rule()
        await h.notify()
        await h.dispatcher.run_once()
        h.clock.advance(3600)
        await h.dispatcher.run_once()
        dead = h.only()
        assert dead.status is DeliveryStatus.DEAD
        assert dead.last_error == "HTTP 500 (gave up after 2 attempts)"
        h.clock.advance(3600)
        assert await h.dispatcher.run_once() == 0
        assert len(h.sink.sent) == 2

    async def test_a_send_that_times_out_is_retried(self):
        h = Harness(sink=FakeSink(delay=1.0), send_timeout_seconds=0.01)
        h.rule()
        await h.notify()
        await h.dispatcher.run_once()
        failed = h.only()
        assert failed.status is DeliveryStatus.FAILED
        assert "did not answer within 0.01s" in failed.last_error

    async def test_an_unexpected_sink_error_is_retried_without_its_message(self):
        h = Harness(sink=FakeSink(outcomes=[RuntimeError("token=hunter2")]))
        h.rule()
        await h.notify()
        await h.dispatcher.run_once()
        failed = h.only()
        assert failed.status is DeliveryStatus.FAILED
        assert failed.last_error == "ops failed: RuntimeError"

    def test_backoff_is_capped_and_jittered(self):
        h = Harness()
        assert h.dispatcher.backoff_seconds(1) == 10
        assert h.dispatcher.backoff_seconds(3) == 40
        assert h.dispatcher.backoff_seconds(30) == 300
        assert h.dispatcher.backoff_seconds(1, retry_after=500) == 500
        low = NotificationDispatcher(
            h.store.outbox, h.provider, renderer(), h.settings, jitter=lambda: 0.0
        )
        high = NotificationDispatcher(
            h.store.outbox, h.provider, renderer(), h.settings, jitter=lambda: 1.0
        )
        assert low.backoff_seconds(2) == pytest.approx(16.0)
        assert high.backoff_seconds(2) == pytest.approx(24.0)
        assert high.backoff_seconds(30) == 300  # jitter never exceeds the cap


class TestSinkResolution:
    async def test_unavailable_sink_is_dead_with_its_reason(self):
        error = NotificationSinkUnavailableError(
            "Integration connection c is disabled", retryable=False
        )
        h = Harness(provider=FakeProvider(error=error))
        h.rule()
        await h.notify("a")
        await h.notify("b")
        await h.dispatcher.run_once()
        assert {d.status for d in h.deliveries()} == {DeliveryStatus.DEAD}
        assert h.deliveries()[0].last_error == "Integration connection c is disabled"
        assert h.provider.opened == 1

    async def test_an_infrastructure_error_while_resolving_is_retried(self):
        h = Harness(provider=FakeProvider(error=ConnectionError("db down")))
        h.rule()
        await h.notify()
        await h.dispatcher.run_once()
        assert h.only().status is DeliveryStatus.FAILED
        assert h.only().last_error == "Resolving the sink failed: ConnectionError"

    async def test_sink_skip_reason_suppresses(self):
        h = Harness(sink=FakeSink(skip="already pushed"))
        h.rule()
        await h.notify()
        await h.dispatcher.run_once()
        assert h.only().status is DeliveryStatus.SUPPRESSED
        assert h.only().last_error == "already pushed"
        assert h.sink.sent == []


class TestGates:
    async def test_quiet_hours_suppress_but_let_urgent_items_through(self):
        h = Harness()
        h.rule(
            quiet_hours=NotificationQuietHours(
                start="12:30",
                end="14:00",
                timezone="Europe/London",
                allow_min_severity=NotificationSeverity.CRITICAL,
            )
        )
        await h.notify("quiet", severity=NotificationSeverity.WARNING)
        await h.notify("urgent", severity=NotificationSeverity.CRITICAL)
        await h.dispatcher.run_once()

        by_title = {d.notification_id: d for d in h.deliveries()}
        statuses = {n.title: by_title[n.id].status for n in h.store.notifications.values()}
        assert statuses == {
            "title quiet": DeliveryStatus.SUPPRESSED,
            "title urgent": DeliveryStatus.DELIVERED,
        }
        quiet = next(d for d in h.deliveries() if d.status is DeliveryStatus.SUPPRESSED)
        assert quiet.last_error.startswith("Quiet hours 12:30-14:00 Europe/London")
        assert [m.title for m in h.sink.sent] == ["title urgent"]

    async def test_outside_quiet_hours_everything_is_delivered(self):
        h = Harness()
        h.rule(quiet_hours=NotificationQuietHours(start="22:00", end="07:00", timezone="UTC"))
        await h.notify()
        await h.dispatcher.run_once()
        assert h.only().status is DeliveryStatus.DELIVERED

    async def test_rule_rate_limit_suppresses_beyond_the_window(self):
        h = Harness()
        h.rule(config={"rate_limit": {"max_count": 2, "window_seconds": 3600}})
        for index in range(3):
            await h.notify(f"n{index}")
        await h.dispatcher.run_once()
        statuses = sorted(d.status.value for d in h.deliveries())
        assert statuses == ["delivered", "delivered", "suppressed"]
        suppressed = next(d for d in h.deliveries() if d.status is DeliveryStatus.SUPPRESSED)
        assert suppressed.last_error == "Rate limit: at most 2 per 3600s for this rule"

        # Earlier deliveries still count in the next poll; after the window they don't.
        await h.notify("later")
        await h.dispatcher.run_once()
        assert h.dispatcher.stats.suppressed == 2
        h.clock.advance(3601)
        await h.notify("next-hour")
        await h.dispatcher.run_once()
        assert h.dispatcher.stats.delivered == 3

    async def test_a_send_settled_this_poll_is_counted_once(self):
        h = Harness()
        rule = h.rule()
        await h.notify("a")
        await h.notify("b")
        limit = NotificationRateLimit(max_count=2, window_seconds=3600)
        windows = _RateWindows(h.store.outbox)
        claimed = await h.store.outbox.claim_due(limit=10, lease_seconds=60)

        assert await windows.reserve(rule, limit, h.clock.now)
        # The first send settles before the second row asks for a slot.
        assert await h.store.outbox.mark_delivered(claimed[0].delivery)
        assert await windows.reserve(rule, limit, h.clock.now)
        assert not await windows.reserve(rule, limit, h.clock.now)
        windows.release(rule)
        assert await windows.reserve(rule, limit, h.clock.now)

    async def test_default_rate_limit_applies_to_rules_without_one(self):
        h = Harness(default_rate_limit=NotificationRateLimit(max_count=1, window_seconds=60))
        h.rule()
        await h.notify("a")
        await h.notify("b")
        await h.dispatcher.run_once()
        assert sorted(d.status.value for d in h.deliveries()) == ["delivered", "suppressed"]

    async def test_a_failed_send_does_not_use_up_the_rate_limit(self):
        failure = NotificationDeliveryError("HTTP 500", retryable=True)
        h = Harness(sink=FakeSink(outcomes=[failure]))
        h.rule(config={"rate_limit": {"max_count": 1, "window_seconds": 3600}})
        await h.notify()
        await h.dispatcher.run_once()
        h.clock.advance(10)
        await h.dispatcher.run_once()
        assert h.only().status is DeliveryStatus.DELIVERED

    async def test_disabled_rule_is_suppressed(self):
        h = Harness()
        rule = h.rule()
        await h.notify()
        h.store.rules[rule.id] = rule.model_copy(update={"enabled": False})
        await h.dispatcher.run_once()
        assert h.only().status is DeliveryStatus.SUPPRESSED
        assert h.only().last_error == "The rule is disabled"


class TestLeases:
    async def test_a_crashed_claim_is_re_claimed_after_its_lease(self):
        h = Harness()
        h.rule()
        await h.notify()
        # Worker A claims and dies before settling.
        (crashed,) = await h.store.outbox.claim_due(limit=10, lease_seconds=60)
        assert await h.dispatcher.run_once() == 0  # still leased

        h.clock.advance(61)
        assert await h.dispatcher.run_once() == 1
        delivered = h.only()
        assert delivered.status is DeliveryStatus.DELIVERED and delivered.attempts == 2
        # A's late settle is fenced out.
        assert await h.store.outbox.mark_delivered(crashed.delivery) is False
        assert await h.store.outbox.mark_failed(crashed.delivery, error="x", retry_at=None) is False

    async def test_a_row_past_max_attempts_is_dead_without_sending(self):
        h = Harness(max_attempts=1)
        h.rule()
        await h.notify()
        await h.store.outbox.claim_due(limit=10, lease_seconds=60)
        h.clock.advance(61)
        await h.dispatcher.run_once()
        dead = h.only()
        assert dead.status is DeliveryStatus.DEAD
        assert dead.last_error == "Gave up after 1 attempts (the last claim's lease expired)"
        assert h.sink.sent == []

    async def test_no_send_starts_without_enough_lease_left(self):
        h = Harness()
        h.rule()
        await h.notify()

        real_claim = h.store.outbox.claim_due

        async def slow_claim(**kwargs):
            claimed = await real_claim(**kwargs)
            h.mono.value += 56  # 4s of the 60s lease left; sends need 5s
            return claimed

        h.store.outbox.claim_due = slow_claim
        await h.dispatcher.run_once()
        assert h.sink.sent == []
        assert h.only().status is DeliveryStatus.CLAIMED
        assert h.dispatcher.stats.lease_skips == 1

    async def test_losing_the_lease_mid_send_discards_the_result(self):
        h = Harness()
        h.rule()
        await h.notify()

        class Stealing(FakeSink):
            async def send(inner, message):  # noqa: N805
                delivery = h.only()
                h.store.deliveries[delivery.id] = delivery.model_copy(
                    update={"attempts": delivery.attempts + 1}
                )
                return DeliveryResult()

        h.sink = Stealing()
        h.provider.sinks["ops"] = h.sink
        await h.dispatcher.run_once()
        assert h.dispatcher.stats.stale_settles == 1
        assert h.dispatcher.stats.delivered == 0

    async def test_concurrent_dispatchers_deliver_each_row_exactly_once(self):
        h = Harness(sink=FakeSink(delay=0.001), batch_size=3)
        h.rule()
        for index in range(10):
            await h.notify(f"n{index}")
        others = [h.new_dispatcher() for _ in range(3)]
        dispatchers = [h.dispatcher, *others]

        while any(d.status is not DeliveryStatus.DELIVERED for d in h.deliveries()):
            await asyncio.gather(*(d.run_once() for d in dispatchers))

        sent_ids = [m.delivery_id for m in h.sink.sent]
        assert len(sent_ids) == 10 and len(set(sent_ids)) == 10
        assert sum(d.stats.delivered for d in dispatchers) == 10
        assert sum(d.stats.stale_settles for d in dispatchers) == 0


class TestLifecycle:
    async def test_loop_delivers_and_stops_cleanly(self):
        h = Harness(batch_size=1)
        h.rule()
        await h.notify("a")
        await h.notify("b")
        await h.dispatcher.start()
        await h.dispatcher.start()  # idempotent
        assert h.dispatcher.running
        for _ in range(200):
            if h.dispatcher.stats.delivered == 2:
                break
            await asyncio.sleep(0.005)
        await h.dispatcher.stop()
        assert not h.dispatcher.running
        assert h.dispatcher.stats.delivered == 2
        await h.dispatcher.stop()  # idempotent

    async def test_loop_survives_a_failing_poll(self):
        h = Harness()
        calls = 0
        real_claim = h.store.outbox.claim_due

        async def flaky(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("database restarting")
            return await real_claim(**kwargs)

        h.store.outbox.claim_due = flaky
        h.rule()
        await h.notify()
        await h.dispatcher.start()
        for _ in range(200):
            if h.dispatcher.stats.delivered:
                break
            await asyncio.sleep(0.005)
        await h.dispatcher.stop()
        assert h.dispatcher.stats.loop_errors == 1
        assert h.dispatcher.stats.delivered == 1

    async def test_stop_cancels_work_that_outlives_the_grace_period(self):
        hung = asyncio.Event()

        class HangingProvider(FakeProvider):
            async def open(self, rule):
                hung.set()
                await asyncio.sleep(3600)

        h = Harness(provider=HangingProvider(), send_timeout_seconds=0.05)
        h.rule()
        await h.notify()
        await h.dispatcher.start()
        await asyncio.wait_for(hung.wait(), timeout=1)
        await h.dispatcher.stop()
        assert not h.dispatcher.running
        # The claim was never settled; another dispatcher re-claims it after the lease.
        assert h.only().status is DeliveryStatus.CLAIMED

    async def test_a_bug_processing_one_row_does_not_stall_the_batch(self):
        h = Harness()
        h.rule()
        await h.notify("a")
        await h.notify("b")

        class Broken(NotificationRenderer):
            def render(self, claimed):
                if claimed.notification.title == "title a":
                    raise ValueError("bad row")
                return super().render(claimed)

        dispatcher = NotificationDispatcher(
            h.store.outbox,
            h.provider,
            Broken(
                public_base_url="",
                session_link_path="/s/{session_id}",
                feed_link_path="/f",
                resident_link_path="/r/{resident_id}",
                resident_session_link_path="/r/{resident_id}/{resident_session_id}",
            ),
            h.settings,
            clock=h.clock,
            monotonic=h.mono,
        )
        await dispatcher.run_once()
        assert dispatcher.stats.loop_errors == 1
        assert dispatcher.stats.delivered == 1


class TestRenderer:
    async def test_without_a_public_url_there_is_no_deep_link(self):
        h = Harness()
        h.rule()
        notification = await h.notify(session_id=None)
        (claimed,) = await h.store.outbox.claim_due(limit=1, lease_seconds=60)
        message = renderer("").render(claimed)
        assert message.url is None
        assert message.links[1].url == "/volundr/files/report.pdf"
        assert message.session_id is None
        feed = renderer("https://forge.example.com/").render(claimed)
        assert feed.url == "https://forge.example.com/volundr/notifications"
        assert feed.notification_id == str(notification.id)


class TestSettings:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"batch_size": 0},
            {"send_timeout_seconds": 60.0},
            {"backoff_base_seconds": 500.0},
            {"backoff_jitter_ratio": 1.5},
        ],
    )
    def test_invalid_settings_are_rejected(self, overrides):
        with pytest.raises(ValueError):
            settings(**overrides)
