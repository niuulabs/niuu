"""The push gateway's question pushes (LEX-1932): silent pushes and alert collapse ids."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from tests.support.push_fakes import (
    MINI,
    THOR,
    Clock,
    FakeApns,
    FakeDeviceRepository,
    FakeGuildFeed,
    FakePushDeliveryRepository,
    device,
    gateway_settings,
    guild_notification,
)
from volundr.domain.models import DevicePlatform
from volundr.domain.push import ApnsResult, GuildQuestionEvent, PushTokenKind
from volundr.domain.push_ports import GuildFeedError
from volundr.domain.services.push_gateway import (
    BackgroundSettings,
    PushGateway,
    PushGatewaySettings,
)

OWNER = "dev-user"


class Harness:
    def __init__(
        self,
        settings: PushGatewaySettings | None = None,
        *,
        feed: FakeGuildFeed | None = None,
        deliveries: FakePushDeliveryRepository | None = None,
    ) -> None:
        self.clock = Clock()
        self.feed = feed or FakeGuildFeed()
        self.devices = FakeDeviceRepository(self.clock)
        self.deliveries = deliveries or FakePushDeliveryRepository(self.clock)
        self.apns = FakeApns()
        self.gateway = PushGateway(
            self.feed,
            self.devices,
            self.deliveries,
            self.apns,
            settings or gateway_settings(),
            clock=self.clock,
        )

    async def started(self) -> Harness:
        self.feed.add_node(THOR)
        self.feed.add_node(MINI)
        await self.gateway.run_once()
        return self


def event(seq: int, notification_id: str = "q-1", *, node: str = MINI, state: str = "answered"):
    return GuildQuestionEvent(
        seq=seq,
        ref=f"question:{node}:{notification_id}",
        notification_id=notification_id,
        instance_id=node,
        owner_id=OWNER,
        state=state,
        answered_by=(
            {"device_id": "install-mac", "device_label": "Mac"} if state == "answered" else None
        ),
        at="2026-11-05T14:05:00+00:00",
    )


BACKGROUND = BackgroundSettings(enabled=True, priority=5, min_interval_seconds=900.0)


async def harness(**settings) -> Harness:
    h = Harness(gateway_settings(**{"background": BACKGROUND, **settings}))
    h.devices.add(device("phone", owner_id=OWNER), device("ipad", owner_id=OWNER))
    h.devices.add(device("android", owner_id=OWNER, platform=DevicePlatform.ANDROID))
    h.devices.add(device("widget", PushTokenKind.WIDGET, owner_id=OWNER))
    h.feed.question_feeds[THOR] = []
    h.feed.question_feeds[MINI] = [event(1, "q-old")]
    await h.started()
    return h


def question_pushes(h: Harness):
    return [p for p in h.apns.sent if p.payload.get("forge", {}).get("reason") == "question_state"]


async def test_a_new_node_starts_at_its_head():
    h = await harness()

    assert question_pushes(h) == []
    assert h.deliveries.positions[f"questions:{MINI}"] == 1
    assert h.deliveries.positions[f"questions:{THOR}"] == 0
    assert h.gateway.status()["question_positions"] == {MINI: 1, THOR: 0}


async def test_each_change_reaches_every_ios_alert_token():
    h = await harness()
    h.feed.add_question_event(event(2, "q-1"))
    h.feed.add_question_event(event(3, "q-2", state="withdrawn"))

    await h.gateway.run_once()

    pushed = question_pushes(h)
    assert sorted((p.token, p.payload["forge"]["question"]["notification_id"]) for p in pushed) == [
        ("ipad", "q-1"),
        ("ipad", "q-2"),
        ("phone", "q-1"),
        ("phone", "q-2"),
    ]
    assert {(p.push_type.value, p.priority, p.collapse_id) for p in pushed} == {
        ("background", 5, None)
    }
    assert pushed[0].payload == {
        "aps": {"content-available": 1},
        "forge": {
            "reason": "question_state",
            "question": {
                "ref": f"question:{MINI}:q-1",
                "notification_id": "q-1",
                "instance_id": MINI,
                "state": "answered",
                "answered_by": {"device_id": "install-mac", "device_label": "Mac"},
                "at": "2026-11-05T14:05:00+00:00",
            },
        },
    }
    assert h.gateway.stats.question_pushes == 4
    assert h.deliveries.positions[f"questions:{MINI}"] == 3

    await h.gateway.run_once()
    assert len(question_pushes(h)) == 4


async def test_a_throttled_change_is_retried_with_the_ones_after_it():
    h = await harness()
    h.feed.add_question_event(event(2, "q-1"))
    h.feed.add_question_event(event(3, "q-2"))
    h.apns.answer("phone", ApnsResult(status_code=429, reason="TooManyRequests"))

    await h.gateway.run_once()

    assert h.deliveries.positions[f"questions:{MINI}"] == 1
    assert h.gateway.stats.question_retries == 1

    await h.gateway.run_once()

    delivered = [
        (p.token, p.payload["forge"]["question"]["notification_id"]) for p in question_pushes(h)
    ]
    assert delivered.count(("phone", "q-1")) == 2
    assert delivered.count(("ipad", "q-1")) == 1
    assert ("phone", "q-2") in delivered
    assert h.deliveries.positions[f"questions:{MINI}"] == 3


async def test_a_dead_token_does_not_hold_the_change_back():
    h = await harness()
    h.feed.add_question_event(event(2, "q-1"))
    h.apns.answer("phone", ApnsResult(status_code=410, reason="Unregistered"))

    await h.gateway.run_once()

    assert h.deliveries.positions[f"questions:{MINI}"] == 2
    assert "phone" not in h.devices.tokens()


async def test_a_node_without_the_route_is_said_once(caplog):
    h = await harness()
    del h.feed.question_feeds[THOR]
    caplog.set_level(logging.INFO)

    await h.gateway.run_once()
    await h.gateway.run_once()

    said = [r for r in caplog.records if "cannot read question changes" in r.getMessage()]
    assert len(said) == 1
    assert said[0].levelno == logging.INFO
    assert THOR in h.gateway.status()["question_unreadable_nodes"]


async def test_an_unreadable_node_warns_and_the_others_continue(caplog):
    h = await harness()
    h.feed.question_down[THOR] = GuildFeedError("Guild node thor's question events answered 502")
    h.feed.add_question_event(event(2, "q-1"))

    await h.gateway.run_once()

    assert {p.payload["forge"]["question"]["notification_id"] for p in question_pushes(h)} == {
        "q-1"
    }
    assert any(
        r.levelno == logging.WARNING and "answered 502" in r.getMessage() for r in caplog.records
    )
    h.feed.question_down.clear()
    await h.gateway.run_once()
    assert h.gateway.status()["question_unreadable_nodes"] == {}


async def test_a_node_whose_changes_went_back_restarts_at_its_head(caplog):
    h = await harness()
    h.feed.question_feeds[MINI] = []

    await h.gateway.run_once()

    assert h.deliveries.positions[f"questions:{MINI}"] == 0
    assert "went back from 1 to 0" in caplog.text


@pytest.mark.parametrize(
    "background",
    [
        BackgroundSettings(enabled=True, priority=5, min_interval_seconds=900.0, questions=False),
        BackgroundSettings(enabled=False, priority=5, min_interval_seconds=900.0),
    ],
)
async def test_question_pushes_can_be_turned_off(background):
    h = await harness(background=background)
    h.feed.add_question_event(event(2, "q-1"))

    await h.gateway.run_once()

    assert question_pushes(h) == []
    assert h.feed.question_calls == []


async def test_a_retry_skips_the_devices_that_took_it():
    h = await harness()
    h.feed.add_question_event(event(2, "q-1"))
    h.apns.answer("phone", ApnsResult(status_code=503, reason="ServiceUnavailable"))

    await h.gateway.run_once()
    await h.gateway.run_once()

    delivered = [
        (p.token, p.payload["forge"]["question"]["notification_id"]) for p in question_pushes(h)
    ]
    assert delivered.count(("ipad", "q-1")) == 1
    assert delivered.count(("phone", "q-1")) == 2
    assert h.deliveries.positions[f"questions:{MINI}"] == 2


async def test_positions_survive_a_restart_apart_from_the_feeds():
    h = await harness()
    h.feed.add_question_event(event(2, "q-1"))
    await h.gateway.run_once()

    restarted = Harness(
        gateway_settings(background=BACKGROUND), feed=h.feed, deliveries=h.deliveries
    )
    restarted.devices.add(device("phone", owner_id=OWNER))
    await restarted.gateway.run_once()

    assert restarted.gateway.status()["nodes"].keys() == {THOR, MINI}
    assert restarted.gateway.status()["question_positions"] == {MINI: 2, THOR: 0}
    assert question_pushes(restarted) == []


async def test_a_questions_alerts_collapse_under_its_ref():
    h = await harness()
    ref = f"question:{MINI}:q-9"
    h.devices.add(device("phone-2", owner_id=OWNER))
    h.feed.add(
        replace(
            guild_notification("q-9", instance_id=MINI, seq=50, kind="decision"),
            question_ref=ref,
        ),
        guild_notification("plain", instance_id=MINI, seq=51),
    )

    await h.gateway.run_once()

    alerts = {p.payload["notification_id"]: p for p in h.apns.of_type("alert")}
    assert alerts["q-9"].collapse_id == ref
    assert alerts["q-9"].payload["question_ref"] == ref
    assert alerts["plain"].collapse_id == "plain"
    assert "question_ref" not in alerts["plain"].payload
