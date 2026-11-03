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
from volundr.domain.services.push_gateway import PushGateway

OWNER = "dev-user"
REMOTE_FEED = "http://remote/api/v1/forge/notifications"
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


class Guild:
    """The facade over one embedded node and one remote node, both with real feeds."""

    def __init__(self) -> None:
        self.local: list[dict[str, Any]] = [_item(1, 1, OWNER), _item(2, 3, OWNER)]
        self.remote: list[dict[str, Any]] = [_item(10, 2, OWNER), _item(11, 4, OWNER)]
        self.remote_error: Exception | None = None
        self.remote_requests: list[dict[str, str]] = []
        node = FastAPI()

        @node.get("/api/v1/forge/notifications")
        async def feed(request: Request):
            return _page(self.local, request.query_params, default_limit=50)

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
            client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)),
        )

    def remote_feed(self, request: httpx.Request) -> Response:
        self.remote_requests.append(dict(request.url.params))
        if self.remote_error is not None:
            raise self.remote_error
        return Response(200, json=_page(self.remote, request.url.params, default_limit=50))


def gateway_for(guild: Guild, clock: Clock) -> tuple[PushGateway, FakeApns, Any]:
    devices = FakeDeviceRepository(clock)
    devices.add(device("alert-1", owner_id=OWNER))
    devices.add(device("la-1", PushTokenKind.LIVE_ACTIVITY, owner_id=OWNER))
    deliveries, apns = FakePushDeliveryRepository(clock), FakeApns()
    gateway = PushGateway(guild.feed, devices, deliveries, apns, gateway_settings(), clock=clock)
    return gateway, apns, deliveries


@respx.mock
async def test_the_feed_reader_speaks_the_facades_api():
    guild = Guild()
    respx.get(REMOTE_FEED).mock(side_effect=guild.remote_feed)

    # The registry lists the enabled Forge nodes without contacting them.
    assert await guild.feed.nodes() == ["local", "remote"]
    assert guild.remote_requests == []

    # One node's newest page: only that node is read, with its head and unread count.
    newest = await guild.feed.newest("remote", limit=1)
    assert [(n.id, n.instance_id, n.seq) for n in newest.items] == [("n11", "remote", 11)]
    assert (newest.position, newest.unread_count) == (11, 2)
    assert guild.remote_requests == [{"limit": "1"}]

    local = await guild.feed.newest("local", limit=5)
    assert [n.seq for n in local.items] == [2, 1]
    assert local.position == 2
    assert len(guild.remote_requests) == 1

    # One node after its position, oldest first.
    guild.remote.append(_item(12, 10, OWNER))
    after = await guild.feed.after("remote", 10, limit=50)
    assert [n.seq for n in after.items] == [11, 12]
    assert after.position == 12
    assert guild.remote_requests[-1] == {"limit": "50", "after": "10"}
    nothing = await guild.feed.after("remote", 12, limit=50)
    assert (nothing.items, nothing.position) == ([], 12)
    await guild.feed.close()


@respx.mock
async def test_gateway_follows_every_node_of_the_guild():
    guild = Guild()
    respx.get(REMOTE_FEED).mock(side_effect=guild.remote_feed)
    clock = Clock(T0 + timedelta(minutes=10))
    gateway, apns, deliveries = gateway_for(guild, clock)

    # First tick: every enabled node is found at its head; history is not replayed.
    assert await gateway.run_once() == 0
    assert deliveries.positions == {"local": 2, "remote": 11}
    assert apns.sent == []

    # One notification on each node.
    guild.local.append(_item(3, 9, OWNER))
    guild.remote.append(_item(12, 10, OWNER))
    assert await gateway.run_once() == 2
    assert deliveries.positions == {"local": 3, "remote": 12}

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
    respx.get(REMOTE_FEED).mock(side_effect=guild.remote_feed)
    clock = Clock(T0 + timedelta(minutes=10))
    gateway, apns, deliveries = gateway_for(guild, clock)
    await gateway.run_once()

    # The remote node hangs: the facade answers 502 for it, and only for it.
    guild.remote_error = httpx.ConnectTimeout("asleep")
    guild.remote.append(_item(12, 10, OWNER))
    guild.local.append(_item(3, 9, OWNER))
    assert await gateway.run_once() == 1
    assert deliveries.positions == {"local": 3, "remote": 11}
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
    assert deliveries.positions == {"local": 4, "remote": 12}
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
    respx.get(REMOTE_FEED).mock(side_effect=guild.remote_feed)
    clock = Clock(T0 + timedelta(minutes=10))
    gateway, apns, deliveries = gateway_for(guild, clock)

    await gateway.run_once()
    assert deliveries.positions == {"local": 2}

    guild.remote_error = None
    clock.advance(31)  # Its retry time.
    await gateway.run_once()
    assert deliveries.positions == {"local": 2, "remote": 11}

    guild.remote.append(_item(12, 11, OWNER))
    assert await gateway.run_once() == 1
    assert [p.payload["notification_id"] for p in apns.of_type("alert")] == ["n12"]
    await guild.feed.close()
