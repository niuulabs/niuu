"""Tests for ResidentRelay (platform Sleipnir events → resident turns)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from skuld.resident_relay import ResidentRelay, _matches_subscription
from sleipnir.domain.events import SleipnirEvent

_RESIDENT = "flock-product-steward"


def _participant(subscribes_to: tuple[str, ...]):
    return SimpleNamespace(peer_id=_RESIDENT, subscribes_to=subscribes_to)


class _FakeSubscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _FakeSubscriber:
    def __init__(self) -> None:
        self.patterns: list[str] | None = None
        self.handler = None
        self.subscription = _FakeSubscription()

    async def subscribe(self, patterns, handler):
        self.patterns = list(patterns)
        self.handler = handler
        return self.subscription


def _event(event_type: str, payload: dict | None = None, summary: str = "") -> SleipnirEvent:
    return SleipnirEvent(
        event_type=event_type,
        source="ting",
        summary=summary,
        payload=payload or {},
        urgency=0.5,
        domain="code",
        timestamp=datetime.now(UTC),
    )


def _relay(subscribes_to=("research.completed",)):
    subscriber = _FakeSubscriber()
    room_bridge = MagicMock()
    room_bridge.participants = {_RESIDENT: _participant(tuple(subscribes_to))}
    send_directed = AsyncMock(return_value="msg-1")
    notify = AsyncMock()
    relay = ResidentRelay(
        subscriber,
        room_bridge,
        resident_peer_id=_RESIDENT,
        patterns=["research.*", "plan.*"],
        send_directed=send_directed,
        broadcast_notification=notify,
    )
    return relay, subscriber, room_bridge, send_directed, notify


class TestSubscriptionMatching:
    def test_exact_match(self):
        assert _matches_subscription("research.completed", ("research.completed",))

    def test_prefix_wildcard(self):
        assert _matches_subscription("research.completed", ("research.*",))
        assert _matches_subscription("plan.brief.approved", ("plan.*",))

    def test_no_match(self):
        assert not _matches_subscription("delivery.completed", ("research.*",))

    def test_empty_subscriptions(self):
        assert not _matches_subscription("research.completed", ())


class TestRelayLifecycle:
    async def test_start_subscribes_with_patterns(self):
        relay, subscriber, *_ = _relay()
        await relay.start()
        assert subscriber.patterns == ["research.*", "plan.*"]

    async def test_stop_unsubscribes(self):
        relay, subscriber, *_ = _relay()
        await relay.start()
        await relay.stop()
        assert subscriber.subscription.unsubscribed is True


class TestRelayDelivery:
    async def test_matching_event_wakes_resident(self):
        relay, subscriber, _room, send_directed, notify = _relay()
        await relay.start()
        await subscriber.handler(
            _event("research.completed", {"campaign": "x"}, summary="campaign done")
        )

        notify.assert_awaited_once()
        frame = notify.await_args.args[0]
        assert frame["type"] == "room_notification"
        assert frame["event_type"] == "research.completed"

        send_directed.assert_awaited_once()
        target, content, metadata = send_directed.await_args.args
        assert target == _RESIDENT
        assert "research.completed" in content
        assert "campaign done" in content
        assert metadata["platform_event"] is True
        assert metadata["payload"] == {"campaign": "x"}

    async def test_unsubscribed_event_type_ignored(self):
        relay, subscriber, _room, send_directed, notify = _relay(
            subscribes_to=("plan.completed",)
        )
        await relay.start()
        await subscriber.handler(_event("research.completed"))
        send_directed.assert_not_awaited()
        notify.assert_not_awaited()

    async def test_mesh_events_ignored(self):
        relay, subscriber, _room, send_directed, notify = _relay(
            subscribes_to=("ravn.mesh.activity",)
        )
        await relay.start()
        await subscriber.handler(_event("ravn.mesh.activity.flock_coder"))
        send_directed.assert_not_awaited()

    async def test_provenance_mismatch_ignored(self):
        relay, subscriber, _room, send_directed, notify = _relay()
        await relay.start()
        await subscriber.handler(
            _event("research.completed", {"resident_peer_id": "someone-else"})
        )
        send_directed.assert_not_awaited()

    async def test_provenance_match_delivered(self):
        relay, subscriber, _room, send_directed, notify = _relay()
        await relay.start()
        await subscriber.handler(
            _event("research.completed", {"resident_peer_id": _RESIDENT})
        )
        send_directed.assert_awaited_once()

    async def test_resident_absent_drops_quietly(self):
        relay, subscriber, room, send_directed, notify = _relay()
        room.participants = {}
        await relay.start()
        await subscriber.handler(_event("research.completed"))
        send_directed.assert_not_awaited()
        notify.assert_not_awaited()

    async def test_unreachable_resident_keeps_notification(self):
        relay, subscriber, _room, send_directed, notify = _relay()
        send_directed.side_effect = LookupError("gone")
        await relay.start()
        await subscriber.handler(_event("research.completed"))
        notify.assert_awaited_once()

    async def test_payload_truncated_in_message(self):
        relay, subscriber, _room, send_directed, notify = _relay()
        await relay.start()
        await subscriber.handler(
            _event("research.completed", {"blob": "x" * 5000})
        )
        _target, content, _metadata = send_directed.await_args.args
        assert "…(truncated)" in content
