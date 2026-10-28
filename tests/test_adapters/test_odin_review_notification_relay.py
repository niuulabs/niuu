"""The ODIN review relay: resident questions become feed items and pushes, once."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from niuu.domain.notifications import NotificationKind, NotificationSeverity, NotificationSource
from tests.support.notifications import InMemoryNotificationStore
from volundr.adapters.outbound.odin_review_notification_relay import OdinReviewNotificationRelay
from volundr.domain.notifications import (
    FEED_ONLY_KEY,
    PUSH_CATEGORY_KEY,
    PUSH_DATA_KEY,
    NotificationQuery,
    NotificationRule,
    NotificationRuleMatch,
    NotificationScope,
    NotificationSinkInfo,
    NotificationValidationError,
    attention_push_rule,
)
from volundr.domain.odin_review import (
    OperatorQuestion,
    OwnerRelayState,
    PendingQuestions,
    digest_dedupe_key,
    question_dedupe_key,
)
from volundr.domain.odin_review_ports import (
    OdinReviewRelayStateStore,
    OdinReviewSource,
    OdinReviewSourceError,
)
from volundr.domain.services.notifications import NotificationService

OWNER = "owner-a"
T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _question(
    review_id: str, *, valkyrie: str = "muninn", at: datetime = T0, text: str = ""
) -> OperatorQuestion:
    return OperatorQuestion(
        review_id=review_id,
        valkyrie_id=valkyrie,
        environment_id="ymir",
        question=text or f"{review_id}: proceed?",
        requested_at=at,
    )


class FakeSource(OdinReviewSource):
    def __init__(self) -> None:
        self.pending: list[OperatorQuestion] = []
        self.malformed: list[str] = []
        self.still_pending: set[str] = set()
        self.fail: Exception | None = None
        self.closed = False
        self.asked_pending: list[str] = []

    async def pending_questions(self) -> PendingQuestions:
        if self.fail is not None:
            raise self.fail
        return PendingQuestions(questions=list(self.pending), malformed=list(self.malformed))

    async def is_pending(self, review_id: str) -> bool:
        self.asked_pending.append(review_id)
        return review_id in self.still_pending

    async def close(self) -> None:
        self.closed = True


class MemoryState(OdinReviewRelayStateStore):
    """Shared by several relays in a test, as the Postgres tables are by replicas."""

    def __init__(self) -> None:
        self.owners: dict[str, tuple[datetime, bool]] = {}
        self.items: dict[tuple[str, str], bool] = {}

    async def begin_owner(self, owner_id: str, watermark_at: datetime) -> OwnerRelayState:
        watermark, digest = self.owners.setdefault(owner_id, (watermark_at, False))
        return OwnerRelayState(watermark_at=watermark, digest_recorded=digest)

    async def mark_digest_recorded(self, owner_id: str) -> None:
        watermark, _ = self.owners[owner_id]
        self.owners[owner_id] = (watermark, True)

    async def recorded_among(self, owner_id: str, review_ids: set[str]) -> set[str]:
        return {rid for (owner, rid) in self.items if owner == owner_id and rid in review_ids}

    async def mark_recorded(self, owner_id: str, review_id: str) -> None:
        self.items.setdefault((owner_id, review_id), False)

    async def open_ids(self, owner_id: str) -> set[str]:
        return {
            rid for (owner, rid), retired in self.items.items() if owner == owner_id and not retired
        }

    async def mark_retired(self, owner_id: str, review_id: str) -> None:
        self.items[(owner_id, review_id)] = True


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _notifications() -> tuple[NotificationService, InMemoryNotificationStore]:
    store = InMemoryNotificationStore()
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        reply_ready_enabled=False,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="push", label="Push")],
    )
    return service, store


def _relay(
    source: FakeSource,
    state: MemoryState,
    service: NotificationService,
    *,
    clock: Clock,
    owners: list[str] | None = None,
) -> OdinReviewNotificationRelay:
    return OdinReviewNotificationRelay(
        source,
        state,
        service,
        owner_ids=owners or [OWNER],
        poll_interval_seconds=30.0,
        clock=clock,
    )


def _give_push_rule(store: InMemoryNotificationStore, owner: str = OWNER) -> NotificationRule:
    rule = attention_push_rule(owner, "push", T0)
    store.rules[rule.id] = rule
    return rule


def _feed(store: InMemoryNotificationStore, owner: str = OWNER) -> list:
    return sorted(
        (n for n in store.notifications.values() if n.owner_id == owner), key=lambda n: n.seq
    )


def _pushed_ids(store: InMemoryNotificationStore) -> set:
    return {d.notification_id for d in store.deliveries.values()}


class TestNewQuestion:
    async def test_a_question_asked_after_the_watermark_is_recorded_and_pushed(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        _give_push_rule(store)
        relay = _relay(source, state, service, clock=clock)
        await relay.sync()  # first poll: empty queue, watermark set at T0

        source.pending = [_question("review:q1", valkyrie="regin", at=T0 + timedelta(minutes=1))]
        clock.now = T0 + timedelta(minutes=2)
        await relay.sync()

        [note] = _feed(store)
        assert (note.kind, note.severity, note.source) == (
            NotificationKind.ATTENTION,
            NotificationSeverity.WARNING,
            NotificationSource.SYSTEM,
        )
        assert note.session_id is None
        assert note.resident_id is None, "these are not Forge resident-runtime ids"
        assert note.owner_id == OWNER
        assert note.title == "regin asks"
        assert note.body == "review:q1: proceed?"
        assert note.metadata == {
            "odin_review_id": "review:q1",
            "odin_review_decide_path": "/api/v1/ravn/odin/reviews/review%3Aq1/decide",
            "valkyrie_id": "regin",
            "environment_id": "ymir",
            "input_kind": "question",
            PUSH_CATEGORY_KEY: "NIUU_RESIDENT_QUESTION",
            PUSH_DATA_KEY: {
                "odin_review_id": "review:q1",
                "odin_review_decide_path": "/api/v1/ravn/odin/reviews/review%3Aq1/decide",
            },
        }
        assert note.dedupe_key == question_dedupe_key(OWNER, "review:q1")
        assert _pushed_ids(store) == {note.id}
        assert note.wire()["odin_review_id"] == "review:q1"
        assert note.wire()["odin_review_decide_path"].endswith("/review%3Aq1/decide")
        assert note.wire()["resident_id"] is None

    async def test_polling_again_and_a_second_replica_record_it_once(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        _give_push_rule(store)
        replica_a = _relay(source, state, service, clock=clock)
        replica_b = _relay(source, state, service, clock=clock)
        await replica_a.sync()
        source.pending = [_question("review:q1", at=T0 + timedelta(minutes=1))]

        await replica_a.sync()
        await replica_a.sync()
        await replica_b.sync()

        assert len(_feed(store)) == 1
        assert len(store.deliveries) == 1

    async def test_a_lost_state_row_is_healed_without_a_second_notification(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = _relay(source, state, service, clock=clock)
        await relay.sync()
        source.pending = [_question("review:q1", at=T0 + timedelta(minutes=1))]
        await relay.sync()
        state.items.clear()  # a crash between recording and remembering

        await relay.sync()

        assert len(_feed(store)) == 1
        assert state.items == {(OWNER, "review:q1"): False}

    async def test_each_configured_owner_gets_their_own_notification(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = _relay(source, state, service, clock=clock, owners=["owner-a", "owner-b"])
        await relay.sync()
        source.pending = [_question("review:q1", at=T0 + timedelta(minutes=1))]
        await relay.sync()

        assert len(_feed(store, "owner-a")) == 1
        assert len(_feed(store, "owner-b")) == 1
        assert _feed(store, "owner-a")[0].id != _feed(store, "owner-b")[0].id


class TestBacklog:
    async def test_the_backlog_is_visible_but_not_pushed_and_one_digest_pushes(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        _give_push_rule(store)
        source.pending = [
            _question(
                f"review:old{i}",
                valkyrie="muninn" if i < 3 else "regin",
                at=T0 - timedelta(hours=i + 1),
            )
            for i in range(5)
        ]
        relay = _relay(source, state, service, clock=clock)

        await relay.sync()

        notes = _feed(store)
        assert len(notes) == 6, "five backlog questions and the digest"
        backlog = [n for n in notes if n.metadata.get("odin_review_id")]
        [digest] = [n for n in notes if not n.metadata.get("odin_review_id")]
        assert len(backlog) == 5
        assert all(n.metadata[FEED_ONLY_KEY] is True for n in backlog)
        assert all(PUSH_CATEGORY_KEY not in n.metadata for n in backlog)
        assert digest.dedupe_key == digest_dedupe_key(OWNER)
        assert digest.title == "5 resident questions waiting"
        assert digest.body == "Waiting on you: muninn (3), regin (2)"
        assert digest.metadata == {}
        assert (digest.kind, digest.source) == (
            NotificationKind.ATTENTION,
            NotificationSource.SYSTEM,
        )
        assert _pushed_ids(store) == {digest.id}, "exactly one push for the whole backlog"

    async def test_the_digest_is_announced_once_across_polls_and_replicas(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        _give_push_rule(store)
        source.pending = [_question("review:old", at=T0 - timedelta(hours=1))]
        replica_a = _relay(source, state, service, clock=clock)
        replica_b = _relay(source, state, service, clock=clock)

        await replica_a.sync()
        await replica_b.sync()
        await replica_a.sync()

        assert len(store.deliveries) == 1
        assert replica_a.stats.digests == 1 and replica_b.stats.digests == 0

    async def test_a_single_backlog_question_reads_in_the_singular(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        source.pending = [_question("review:old", at=T0 - timedelta(hours=1))]

        await _relay(source, state, service, clock=clock).sync()

        assert _feed(store)[-1].title == "1 resident question waiting"

    async def test_an_empty_backlog_makes_no_digest_and_never_one_later(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = _relay(source, state, service, clock=clock)
        await relay.sync()
        assert _feed(store) == []

        # a question asked after the watermark is not backlog, so it earns no digest
        source.pending = [_question("review:new", at=T0 + timedelta(minutes=1))]
        await relay.sync()

        assert [n.title for n in _feed(store)] == ["muninn asks"]

    async def test_a_second_owner_added_later_gets_their_own_backlog_digest(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        source.pending = [_question("review:old", at=T0 - timedelta(hours=1))]
        await _relay(source, state, service, clock=clock).sync()

        clock.now = T0 + timedelta(days=1)
        await _relay(source, state, service, clock=clock, owners=[OWNER, "owner-b"]).sync()

        assert [n.title for n in _feed(store, "owner-b")] == [
            "muninn asks",
            "1 resident question waiting",
        ]
        assert _feed(store, "owner-b")[0].metadata[FEED_ONLY_KEY] is True

    async def test_a_question_asked_exactly_at_the_watermark_counts_as_backlog(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        source.pending = [_question("review:edge", at=T0)]

        await _relay(source, state, service, clock=clock).sync()

        assert _feed(store)[0].metadata[FEED_ONLY_KEY] is True


class TestRetire:
    async def test_a_question_that_leaves_pending_is_marked_read(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = _relay(source, state, service, clock=clock)
        await relay.sync()
        source.pending = [
            _question("review:q1", at=T0 + timedelta(minutes=1)),
            _question("review:q2", at=T0 + timedelta(minutes=2)),
        ]
        await relay.sync()
        q1, q2 = _feed(store)
        assert (OWNER, q1.id) not in store.reads

        source.pending = [source.pending[1]]  # q1 was answered
        await relay.sync()

        assert (OWNER, q1.id) in store.reads
        assert (OWNER, q2.id) not in store.reads
        assert state.items == {(OWNER, "review:q1"): True, (OWNER, "review:q2"): False}
        assert relay.stats.retired == 1

    async def test_a_listing_that_misses_a_still_pending_item_retires_nothing(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = _relay(source, state, service, clock=clock)
        await relay.sync()
        question = _question("review:q1", at=T0 + timedelta(minutes=1))
        source.pending = [question]
        await relay.sync()

        source.pending = []  # paging skipped it ...
        source.still_pending = {"review:q1"}  # ... but the item itself says pending
        await relay.sync()

        assert store.reads == set()
        assert source.asked_pending == ["review:q1"]

    async def test_a_retired_question_is_not_asked_about_again(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = _relay(source, state, service, clock=clock)
        await relay.sync()
        source.pending = [_question("review:q1", at=T0 + timedelta(minutes=1))]
        await relay.sync()
        source.pending = []
        await relay.sync()
        await relay.sync()

        assert source.asked_pending == ["review:q1"]

    async def test_retiring_a_backlog_question_marks_its_feed_item_read(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        source.pending = [_question("review:old", at=T0 - timedelta(hours=1))]
        relay = _relay(source, state, service, clock=clock)
        await relay.sync()
        [backlog, _digest] = _feed(store)

        source.pending = []
        await relay.sync()

        assert (OWNER, backlog.id) in store.reads


class TestRunLoop:
    async def test_a_failed_poll_is_loud_counted_and_retried(self, caplog):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = OdinReviewNotificationRelay(
            source, state, service, owner_ids=[OWNER], poll_interval_seconds=0.01, clock=clock
        )
        source.fail = OdinReviewSourceError("niuu-ravn refused the credential")
        caplog.set_level("ERROR")

        await relay.start()
        await _until(lambda: relay.stats.failures >= 2)
        assert relay.stats.consecutive_failures >= 2
        assert "refused the credential" in relay.stats.last_error
        assert any("poll failed" in r.message and r.levelname == "ERROR" for r in caplog.records)

        source.fail = None
        await _until(lambda: relay.stats.last_success_at is not None)
        await relay.stop()

        assert relay.stats.consecutive_failures == 0 and relay.stats.last_error == ""
        assert source.closed

    async def test_malformed_items_are_counted_not_dropped_silently(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, _store = _notifications()
        relay = _relay(source, state, service, clock=clock)
        source.malformed = ["review:broken"]

        await relay.sync()
        await relay.sync()

        assert relay.stats.malformed == 2

    async def test_stopping_an_unstarted_relay_still_closes_the_source(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, _store = _notifications()
        relay = _relay(source, state, service, clock=clock)

        await relay.stop()

        assert source.closed


class TestMisconfiguration:
    @pytest.mark.parametrize("owners", [[], [" "], [""]])
    def test_the_relay_refuses_to_run_without_an_owner(self, owners):
        service, _store = _notifications()
        with pytest.raises(ValueError, match="owner_ids"):
            OdinReviewNotificationRelay(
                FakeSource(), MemoryState(), service, owner_ids=owners, poll_interval_seconds=1
            )

    def test_the_poll_interval_must_be_positive(self):
        service, _store = _notifications()
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            OdinReviewNotificationRelay(
                FakeSource(), MemoryState(), service, owner_ids=[OWNER], poll_interval_seconds=0
            )


class TestFeedOnlyAndPushRule:
    async def test_a_backlog_item_is_in_the_unread_feed_but_has_no_delivery(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        _give_push_rule(store)
        source.pending = [_question("review:old", at=T0 - timedelta(hours=1))]
        await _relay(source, state, service, clock=clock).sync()

        page = await store.feed.list_feed(
            NotificationScope(user_id=OWNER, tenant_id="", is_admin=False),
            NotificationQuery(limit=10, unread=True),
            read_through_seq=0,
        )

        assert len(page.items) == 2
        [backlog] = [n for n in page.items if n.metadata.get("odin_review_id")]
        assert backlog.id not in _pushed_ids(store)

    def test_a_rule_selecting_attention_system_matches_the_pushed_question_only(self):
        from volundr.domain.notifications import Notification, rules_matching

        rule = NotificationRule(
            id=uuid4(),
            owner_id=OWNER,
            name="all attention",
            match=NotificationRuleMatch(kinds=["attention"], sources=["system"]),
            sink="push",
            created_at=T0,
            updated_at=T0,
        )

        def note(**metadata):
            return Notification(
                id=uuid4(),
                seq=1,
                dedupe_key="k",
                owner_id=OWNER,
                kind="attention",
                severity="warning",
                source="system",
                title="t",
                metadata=metadata,
                created_at=T0,
            )

        assert rules_matching(note(), [rule]) == [rule]
        assert rules_matching(note(**{FEED_ONLY_KEY: False}), [rule]) == [rule]
        assert rules_matching(note(**{FEED_ONLY_KEY: True}), [rule]) == []


async def _until(condition, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


class TestPushRule:
    """An owner's devices register with the Forge their app talks to, which may not be this
    one, so the relay itself gives each owner this Forge's needs-input push rule."""

    async def test_start_gives_each_owner_the_push_rule(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = OdinReviewNotificationRelay(
            source,
            state,
            service,
            owner_ids=[OWNER, "other-owner"],
            poll_interval_seconds=30.0,
            push_sink="push",
            clock=clock,
        )

        await relay.start()
        await relay.stop()

        assert sorted((rule.owner_id, rule.sink) for rule in store.rules.values()) == sorted(
            [(OWNER, "push"), ("other-owner", "push")]
        )

    async def test_an_owner_who_already_routes_to_the_sink_is_left_alone(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        existing = _give_push_rule(store)
        relay = OdinReviewNotificationRelay(
            source,
            state,
            service,
            owner_ids=[OWNER],
            poll_interval_seconds=30.0,
            push_sink="push",
            clock=clock,
        )

        await relay.start()
        await relay.stop()

        assert list(store.rules.values()) == [existing]

    async def test_an_unconfigured_push_sink_stops_the_start(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, _store = _notifications()
        relay = OdinReviewNotificationRelay(
            source,
            state,
            service,
            owner_ids=[OWNER],
            poll_interval_seconds=30.0,
            push_sink="apns-nowhere",
            clock=clock,
        )

        with pytest.raises(NotificationValidationError):
            await relay.start()

    async def test_without_push_no_rule_is_added(self):
        source, state, clock = FakeSource(), MemoryState(), Clock(T0)
        service, store = _notifications()
        relay = _relay(source, state, service, clock=clock)

        await relay.start()
        await relay.stop()

        assert store.rules == {}
