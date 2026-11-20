"""The push gateway reads the real Guild facade: its registry, each node's feed, its tags.

``HttpGuildFeed`` is a client of ``create_instances_router`` and
``create_volundr_router``. These tests run the gateway against both in-process (one
embedded node, one remote node), so its assumptions about the Guild API (the
single-node cursors, the node tags, how a node that does not answer is reported)
cannot drift from the facade.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import respx
from fastapi import FastAPI, Request
from httpx import Response

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_instances import create_instances_router
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.adapters.outbound.http_auth import NoAuthHeaderAdapter
from tests.support.push_fakes import (
    Clock,
    FakeApns,
    FakeDeviceRepository,
    FakePushDeliveryRepository,
    device,
    gateway_settings,
)
from tests.test_niuu.test_rest_instances import StubHealthChecker
from tests.test_niuu.test_rest_volundr import StubInstanceService, _instance
from tests.test_niuu.test_rest_volundr_notifications import T0, _item, _page
from volundr.adapters.outbound.http_guild_feed import HttpGuildFeed
from volundr.domain.push import PushTokenKind
from volundr.domain.services.push_gateway import BackgroundSettings, PushGateway

OWNER = "dev-user"
REMOTE_FEED = "http://remote/api/v1/forge/notifications"
REMOTE_QUESTIONS = "http://remote/api/v1/forge/notifications/questions/events"


def _question_page(events: list[dict[str, Any]], params) -> dict[str, Any]:
    """Volundr's question-events semantics over a fixed list (LEX-1932)."""
    after, limit = int(params.get("after", 0)), int(params.get("limit", 50))
    rows = [e for e in sorted(events, key=lambda e: e["seq"]) if e["seq"] > after][:limit]
    return {
        "items": rows,
        "next_after": rows[-1]["seq"] if rows else after,
        "head_seq": max((e["seq"] for e in events), default=0),
    }


def _question_event(seq: int, notification_id: str, state: str, host: str) -> dict[str, Any]:
    return {
        "seq": seq,
        "ref": f"question:{host}:{notification_id}",
        "notification_id": notification_id,
        "owner_id": OWNER,
        "state": state,
        "answered_by": (
            {"device_id": "install-1", "device_label": "Mac"} if state == "answered" else None
        ),
        "at": "2026-11-05T14:05:00+00:00",
    }


# The gateway sends no credential: the facade then serves its local developer
# principal, whose tenant these instances belong to (the AllowAll Guild).
LOCAL = _instance(
    "local",
    base_url="embedded://local",
    tenant_id="default",
    is_default=True,
    config={"transport": "embedded"},
)
REMOTE = _instance("remote", base_url="http://remote", tenant_id="default")
DISABLED = _instance("off", base_url="http://off", tenant_id="default", enabled=False)


def _delivery_page(items: list[dict[str, Any]], params, *, pushes: bool = False) -> dict:
    """A node's delivery view: every owner's items (or one owner's), with each listed
    owner's unread count, as Forge serves it to the gateway."""
    owner = params.get("owner_id")
    rows = [item for item in items if owner is None or item["owner_id"] == owner]
    page = _page(rows, params, default_limit=50)
    owners = {item["owner_id"] for item in rows} | ({owner} if owner else set())
    page["delivery"] = {
        "owner_unread_counts": {
            o: sum(1 for item in items if item["owner_id"] == o and not item["read"])
            for o in owners
        },
        "node_pushes_alerts": pushes,
    }
    return page


class Guild:
    """The facade over one embedded node and one remote node, both with real feeds."""

    def __init__(self) -> None:
        self.local: list[dict[str, Any]] = [_item(1, 1, OWNER), _item(2, 3, OWNER)]
        self.remote: list[dict[str, Any]] = [_item(10, 2, OWNER), _item(11, 4, OWNER)]
        self.remote_error: Exception | None = None
        self.remote_requests: list[dict[str, str]] = []
        self.local_questions: list[dict[str, Any]] = []
        self.remote_questions: list[dict[str, Any]] = []
        node = FastAPI()

        @node.get("/api/v1/forge/notifications/questions/events")
        async def question_events(request: Request):
            return _question_page(self.local_questions, request.query_params)

        @node.get("/api/v1/forge/notifications")
        async def feed(request: Request):
            assert request.query_params["view"] == "delivery"
            return _delivery_page(self.local, request.query_params)

        registry = StubInstanceService([LOCAL, REMOTE, DISABLED])
        app = FastAPI()
        # The gateway sends no credential without reader_token_path: an AllowAll Guild.
        app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
        app.include_router(
            create_instances_router(registry, health_checker=StubHealthChecker())  # type: ignore[arg-type]
        )
        app.include_router(  # type: ignore[arg-type]
            create_volundr_router(registry, embedded_forge_app=node)
        )
        self.feed = HttpGuildFeed(
            guild_url="http://guild",
            timeout_seconds=5,
            auth=NoAuthHeaderAdapter(),
            client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)),
        )

    def mock_remote(self) -> None:
        respx.get(REMOTE_FEED).mock(side_effect=self.remote_feed)
        respx.get(REMOTE_QUESTIONS).mock(
            side_effect=lambda request: Response(
                200, json=_question_page(self.remote_questions, request.url.params)
            )
        )

    def remote_feed(self, request: httpx.Request) -> Response:
        self.remote_requests.append(dict(request.url.params))
        if self.remote_error is not None:
            raise self.remote_error
        return Response(200, json=_delivery_page(self.remote, request.url.params))


def feed_positions(deliveries: Any) -> dict[str, int]:
    """The stored feed positions, without the question-event ones (LEX-1932)."""
    return {k: v for k, v in deliveries.positions.items() if not k.startswith("questions:")}


def gateway_for(
    guild: Guild, clock: Clock, *, background: BackgroundSettings | None = None
) -> tuple[PushGateway, FakeApns, Any]:
    devices = FakeDeviceRepository(clock)
    devices.add(device("alert-1", owner_id=OWNER))
    devices.add(device("la-1", PushTokenKind.LIVE_ACTIVITY, owner_id=OWNER))
    deliveries, apns = FakePushDeliveryRepository(clock), FakeApns()
    gateway = PushGateway(
        guild.feed, devices, deliveries, apns, gateway_settings(background=background), clock=clock
    )
    return gateway, apns, deliveries


@respx.mock
async def test_the_feed_reader_speaks_the_facades_api():
    guild = Guild()
    guild.mock_remote()

    # The registry lists the enabled Forge nodes without contacting them.
    assert [node.id for node in await guild.feed.nodes()] == ["local", "remote"]
    assert guild.remote_requests == []

    # One node's newest page: only that node is read, with its head and each owner's
    # unread count, in the delivery view.
    newest = await guild.feed.newest("remote", limit=1)
    assert [(n.id, n.instance_id, n.seq) for n in newest.items] == [("n11", "remote", 11)]
    assert (newest.position, newest.owner_unread_counts) == (11, {OWNER: 2})
    assert guild.remote_requests == [{"limit": "1", "view": "delivery"}]

    local = await guild.feed.newest("local", limit=5)
    assert [n.seq for n in local.items] == [2, 1]
    assert local.position == 2
    assert len(guild.remote_requests) == 1

    # One node after its position, oldest first.
    guild.remote.append(_item(12, 10, OWNER))
    after = await guild.feed.after("remote", 10, limit=50)
    assert [n.seq for n in after.items] == [11, 12]
    assert after.position == 12
    assert guild.remote_requests[-1] == {"limit": "50", "view": "delivery", "after": "10"}
    nothing = await guild.feed.after("remote", 12, limit=50)
    assert (nothing.items, nothing.position) == ([], 12)
    await guild.feed.close()


@respx.mock
async def test_gateway_follows_every_node_of_the_guild():
    guild = Guild()
    guild.mock_remote()
    clock = Clock(T0 + timedelta(minutes=10))
    gateway, apns, deliveries = gateway_for(guild, clock)

    # First tick: every enabled node is found at its head; history is not replayed.
    assert await gateway.run_once() == 0
    assert feed_positions(deliveries) == {"local": 2, "remote": 11}
    assert apns.sent == []

    # One notification on each node.
    guild.local.append(_item(3, 9, OWNER))
    guild.remote.append(_item(12, 10, OWNER))
    assert await gateway.run_once() == 2
    assert feed_positions(deliveries) == {"local": 3, "remote": 12}

    alerts = {push.payload["notification_id"]: push.payload for push in apns.of_type("alert")}
    assert alerts["n3"]["instance_id"] == "local"
    assert alerts["n3"]["deepLink"] == "lexichat://activity/n3?inst=local"
    assert alerts["n12"]["instance_id"] == "remote"
    assert alerts["n12"]["deepLink"] == "lexichat://activity/n12?inst=remote"

    # The Live Activity shows the Guild's newest three, with the facade's node tags
    # and the unread count summed over the nodes.
    (update,) = apns.of_type("liveactivity")
    state = update.payload["aps"]["content-state"]
    assert [(item["id"], item["instanceID"]) for item in state["items"]] == [
        ("n12", "remote"),
        ("n3", "local"),
        ("n11", "remote"),
    ]
    assert state["unreadCount"] == 6
    assert all(item["unread"] for item in state["items"])

    # Nothing new: nothing is read twice.
    assert await gateway.run_once() == 0
    assert len(apns.of_type("alert")) == 2
    await guild.feed.close()


@respx.mock
async def test_a_node_that_does_not_answer_fails_alone_and_is_caught_up_later():
    guild = Guild()
    guild.mock_remote()
    clock = Clock(T0 + timedelta(minutes=10))
    gateway, apns, deliveries = gateway_for(guild, clock)
    await gateway.run_once()

    # The remote node hangs: the facade answers 502 for it, and only for it.
    guild.remote_error = httpx.ConnectTimeout("asleep")
    guild.remote.append(_item(12, 10, OWNER))
    guild.local.append(_item(3, 9, OWNER))
    assert await gateway.run_once() == 1
    assert feed_positions(deliveries) == {"local": 3, "remote": 11}
    assert [p.payload["notification_id"] for p in apns.of_type("alert")] == ["n3"]
    assert gateway.status()["unreachable_nodes"] == {
        "remote": "Guild node remote's feed answered HTTP 502"
    }

    # While it is set aside the remote node is not asked again.
    asked = len(guild.remote_requests)
    guild.local.append(_item(4, 10, OWNER))
    assert await gateway.run_once() == 1
    assert len(guild.remote_requests) == asked

    # It answers again after its retry time: its notification is caught up.
    guild.remote_error = None
    clock.advance(31)
    assert await gateway.run_once() == 1
    assert feed_positions(deliveries) == {"local": 4, "remote": 12}
    assert sorted(p.payload["notification_id"] for p in apns.of_type("alert")) == [
        "n12",
        "n3",
        "n4",
    ]
    assert gateway.status()["unreachable_nodes"] == {}
    await guild.feed.close()


@respx.mock
async def test_a_node_down_at_the_first_tick_is_found_once_it_is_back():
    guild = Guild()
    guild.remote_error = httpx.ConnectError("refused")
    guild.mock_remote()
    clock = Clock(T0 + timedelta(minutes=10))
    gateway, apns, deliveries = gateway_for(guild, clock)

    await gateway.run_once()
    assert feed_positions(deliveries) == {"local": 2}

    guild.remote_error = None
    clock.advance(31)  # Its retry time.
    await gateway.run_once()
    assert feed_positions(deliveries) == {"local": 2, "remote": 11}

    guild.remote.append(_item(12, 11, OWNER))
    assert await gateway.run_once() == 1
    assert [p.payload["notification_id"] for p in apns.of_type("alert")] == ["n12"]
    await guild.feed.close()


@respx.mock
async def test_question_changes_reach_every_ios_device_through_the_facade():
    """LEX-1932: each node's question changes, read through the facade, become one
    silent push per change and device, naming the node the question lives on."""
    clock = Clock(T0 + timedelta(seconds=30))
    guild = Guild()
    guild.mock_remote()
    guild.remote_questions.append(_question_event(1, "q-old", "answered", "remote"))
    gateway, apns, deliveries = gateway_for(
        guild,
        clock,
        background=BackgroundSettings(enabled=True, priority=5, min_interval_seconds=900.0),
    )

    await gateway.run_once()
    # First sight of a node: earlier changes are history, the gateway starts at its head.
    assert deliveries.positions["questions:remote"] == 1
    assert deliveries.positions["questions:local"] == 0
    assert [
        p for p in apns.sent if p.payload.get("forge", {}).get("reason") == "question_state"
    ] == []

    guild.remote_questions.append(_question_event(2, "q-1", "answered", "remote"))
    guild.local_questions.append(_question_event(1, "q-2", "withdrawn", "local"))
    await gateway.run_once()

    pushed = [p for p in apns.sent if p.payload.get("forge", {}).get("reason") == "question_state"]
    assert sorted(
        (p.token, p.push_type.value, p.priority, p.payload["forge"]["question"]["ref"])
        for p in pushed
    ) == [
        ("alert-1", "background", 5, "question:local:q-2"),
        ("alert-1", "background", 5, "question:remote:q-1"),
    ]
    remote = next(p for p in pushed if p.payload["forge"]["question"]["instance_id"] == "remote")
    assert remote.payload == {
        "aps": {"content-available": 1},
        "forge": {
            "reason": "question_state",
            "question": {
                "ref": "question:remote:q-1",
                "notification_id": "q-1",
                "instance_id": "remote",
                "state": "answered",
                "answered_by": {"device_id": "install-1", "device_label": "Mac"},
                "at": "2026-11-05T14:05:00+00:00",
            },
        },
    }
    assert deliveries.positions["questions:remote"] == 2
    assert deliveries.positions["questions:local"] == 1
    await guild.feed.close()
