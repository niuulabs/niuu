"""The push gateway's reader of the Guild: its registry and each node's feed."""

from __future__ import annotations

import httpx
import pytest

from niuu.adapters.inbound.forge_notification_feed import decode_cursor, encode_cursor
from niuu.adapters.outbound.http_auth import NoAuthHeaderAdapter, StaticBearerTokenAuthAdapter
from niuu.ports.http_auth import HttpAuthPort
from volundr.adapters.outbound import http_guild_feed
from volundr.adapters.outbound.http_guild_feed import HttpGuildFeed
from volundr.domain.push_ports import GuildFeedError, GuildNode, GuildNodeHasNoFeedError

GUILD = "http://127.0.0.1:8080"
THOR = "56499f00-02b3-4d80-8137-09d6d3238056"
MINI = "c3d1d72f-14fb-4aca-91ae-c3a40106a27a"


def item(notification_id: str, instance_id: str, seq: int, **overrides) -> dict:
    data = {
        "id": notification_id,
        "seq": seq,
        "session_id": "sess-1",
        "session_name": "horde-coordinator",
        "owner_id": "dev-user",
        "kind": "milestone",
        "severity": "success",
        "source": "agent",
        "title": "Build finished",
        "body": "",
        "turn_id": "nt_1",
        "created_at": "2026-10-02T06:40:14.596304Z",
        "image": None,
        "read": False,
        "instance_id": instance_id,
    }
    data.update(overrides)
    return data


class Guild:
    def __init__(self, answer: httpx.Response | Exception) -> None:
        self.answer = answer
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


#: A node's delivery view: each owner's unread count, and whether it pushes itself.
DELIVERY = {"owner_unread_counts": {"dev-user": 1}, "node_pushes_alerts": False}


def summary(**fields) -> dict:
    """One node's summary in the delivery view."""
    return {"delivery": DELIVERY, **fields}


def _auth(reader_token: str) -> HttpAuthPort:
    if reader_token:
        return StaticBearerTokenAuthAdapter(token=reader_token)
    return NoAuthHeaderAdapter()


def feed_for(
    guild: Guild, *, reader_token: str = "", auth: HttpAuthPort | None = None
) -> HttpGuildFeed:
    return HttpGuildFeed(
        guild_url=GUILD,
        timeout_seconds=5,
        auth=auth or _auth(reader_token),
        client=httpx.AsyncClient(transport=httpx.MockTransport(guild.handler)),
    )


class ExpiringAuth(HttpAuthPort):
    """A credential that can go stale, or be unavailable."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.invalidated = 0

    def headers(self) -> dict[str, str]:
        if self.error is not None:
            raise self.error
        return {"Authorization": "Bearer delivery-1"}

    def invalidate(self) -> bool:
        self.invalidated += 1
        return True


class TestNodes:
    async def test_lists_the_enabled_forge_nodes_from_the_registry(self):
        guild = Guild(
            httpx.Response(
                200,
                json=[
                    {"id": THOR, "kind": "volundr", "slug": "local", "enabled": True},
                    {"id": MINI, "kind": "volundr", "slug": "mini-m6", "enabled": True},
                    {"id": "", "kind": "volundr"},
                    {"slug": "no-id"},
                    "nope",
                ],
            )
        )

        assert await feed_for(guild).nodes() == [GuildNode(THOR), GuildNode(MINI)]

        request = guild.requests[0]
        assert request.url.path == "/api/v1/niuu/instances"
        assert dict(request.url.params) == {"kind": "volundr", "enabledOnly": "true"}
        assert "authorization" not in request.headers

    async def test_the_registry_marks_the_push_gateway(self):
        guild = Guild(
            httpx.Response(
                200,
                json=[
                    {"id": THOR, "config": {"push_gateway": True}},
                    {"id": MINI, "config": {"push_gateway": "yes"}},
                ],
            )
        )

        assert await feed_for(guild).nodes() == [
            GuildNode(THOR, push_gateway=True),
            GuildNode(MINI, push_gateway=False),
        ]

    async def test_the_reader_token_is_sent_as_a_bearer(self):
        guild = Guild(httpx.Response(200, json=[]))
        assert await feed_for(guild, reader_token="tok-123").nodes() == []
        assert guild.requests[0].headers["authorization"] == "Bearer tok-123"

    async def test_something_that_is_not_a_guild(self):
        guild = Guild(httpx.Response(200, json={"detail": "Not Found"}))
        with pytest.raises(GuildFeedError, match="must be a Guild API origin"):
            await feed_for(guild).nodes()

    async def test_an_unreachable_guild(self):
        guild = Guild(httpx.ConnectError("refused"))
        with pytest.raises(GuildFeedError, match="The Guild registry is unreachable: ConnectError"):
            await feed_for(guild).nodes()

    @pytest.mark.parametrize("status", [404, 500])
    async def test_a_refusing_guild(self, status):
        guild = Guild(httpx.Response(status, json={"detail": "no"}))
        with pytest.raises(GuildFeedError, match=f"The Guild registry answered HTTP {status}"):
            await feed_for(guild).nodes()

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_credential_is_dropped_and_named(self, status):
        auth = ExpiringAuth()
        guild = Guild(httpx.Response(status, json={"detail": "no"}))

        with pytest.raises(GuildFeedError, match="forge:notifications:deliver"):
            await feed_for(guild, auth=auth).nodes()

        assert auth.invalidated == 1

    async def test_an_unavailable_credential_is_a_feed_error(self):
        guild = Guild(httpx.Response(200, json=[]))
        auth = ExpiringAuth(RuntimeError("workload identity proof token not found"))

        with pytest.raises(GuildFeedError, match="credential is unavailable: workload identity"):
            await feed_for(guild, auth=auth).nodes()
        assert guild.requests == []


class TestNewest:
    async def test_reads_one_nodes_newest_page_with_its_head_and_unread_count(self):
        guild = Guild(
            httpx.Response(
                200,
                json={
                    "items": [item("n-2", THOR, 323), item("n-1", THOR, 322, read=True)],
                    "next_before": encode_cursor({THOR: 322}),
                    "next_after": encode_cursor({THOR: 323}),
                    "unread_count": 0,
                    "instances": {
                        THOR: summary(head_seq=323, read_through_seq=0, unread_count=0),
                    },
                },
            )
        )

        page = await feed_for(guild).newest(THOR, limit=20)

        request = guild.requests[0]
        assert request.url.path == "/api/v1/forge/notifications"
        params = dict(request.url.params)
        assert params["all_instances"] == "true" and params["limit"] == "20"
        # Every owner's notifications, each read as its owner reads it.
        assert params["view"] == "delivery"
        assert "owner_id" not in params
        # A cursor naming only this node, at no position: the facade reads only it.
        assert decode_cursor(params["before"]) == {THOR: None}
        assert [(n.id, n.read) for n in page.items] == [("n-2", False), ("n-1", True)]
        assert page.position == 323
        assert page.owner_unread_counts == {"dev-user": 1}
        assert page.node_pushes_alerts is False

    async def test_one_owners_newest_page(self):
        guild = Guild(
            httpx.Response(200, json={"items": [], "instances": {THOR: summary(head_seq=4)}})
        )

        await feed_for(guild).newest(THOR, limit=3, owner_id="user-7")

        assert dict(guild.requests[0].url.params)["owner_id"] == "user-7"

    async def test_the_reader_token_is_sent_as_a_bearer(self):
        guild = Guild(
            httpx.Response(200, json={"items": [], "instances": {THOR: summary(head_seq=0)}})
        )
        page = await feed_for(guild, reader_token="tok-123").newest(THOR, limit=1)
        assert guild.requests[0].headers["authorization"] == "Bearer tok-123"
        assert (page.items, page.position) == ([], 0)

    async def test_a_node_that_pushes_its_own_alerts_says_so(self):
        delivery = {**DELIVERY, "node_pushes_alerts": True}
        guild = Guild(
            httpx.Response(
                200,
                json={"items": [], "instances": {THOR: {"head_seq": 0, "delivery": delivery}}},
            )
        )

        assert (await feed_for(guild).newest(THOR, limit=1)).node_pushes_alerts is True

    async def test_a_node_without_the_delivery_view_is_an_error(self):
        guild = Guild(httpx.Response(200, json={"items": [], "instances": {THOR: {"head_seq": 3}}}))

        with pytest.raises(GuildFeedError, match="without the delivery view"):
            await feed_for(guild).newest(THOR, limit=1)

    @pytest.mark.parametrize(
        "delivery",
        [
            {"owner_unread_counts": [], "node_pushes_alerts": False},
            {"owner_unread_counts": {}, "node_pushes_alerts": "no"},
        ],
    )
    async def test_a_malformed_delivery_view_is_an_error(self, delivery):
        guild = Guild(
            httpx.Response(
                200,
                json={"items": [], "instances": {THOR: {"head_seq": 3, "delivery": delivery}}},
            )
        )

        with pytest.raises(GuildFeedError, match="delivery view has an unexpected shape"):
            await feed_for(guild).newest(THOR, limit=1)

    @pytest.mark.parametrize(
        "instances", [None, {}, {MINI: {"head_seq": 7}}, {THOR: {"head_seq": "7"}}, {THOR: 7}]
    )
    async def test_a_node_the_guild_did_not_read_has_no_head(self, instances):
        guild = Guild(httpx.Response(200, json={"items": [], "instances": instances}))
        with pytest.raises(GuildFeedError, match="returned no head position"):
            await feed_for(guild).newest(THOR, limit=1)

    async def test_malformed_items_and_other_nodes_items_are_skipped(self, caplog):
        guild = Guild(
            httpx.Response(
                200,
                json={
                    "items": ["nope", {"id": "x"}, item("m-1", MINI, 9), item("n-1", THOR, 1)],
                    "instances": {THOR: summary(head_seq=1)},
                },
            )
        )
        page = await feed_for(guild).newest(THOR, limit=5)
        assert [n.id for n in page.items] == ["n-1"]
        assert "Ignoring a malformed Guild feed item" in caplog.text


class TestAfter:
    async def test_reads_one_node_after_its_position(self):
        guild = Guild(
            httpx.Response(
                200,
                json={
                    "items": [item("n-3", THOR, 324), item("n-4", THOR, 325)],
                    "next_after": encode_cursor({THOR: 325}),
                    "instances": {THOR: summary(head_seq=325)},
                },
            )
        )

        page = await feed_for(guild).after(THOR, 323, limit=50)

        params = dict(guild.requests[0].url.params)
        assert params["all_instances"] == "true" and params["limit"] == "50"
        assert decode_cursor(params["after"]) == {THOR: 323}
        assert "before" not in params
        assert params["view"] == "delivery"
        assert [n.id for n in page.items] == ["n-3", "n-4"]
        assert page.position == 325
        assert page.owner_unread_counts == {"dev-user": 1}

    async def test_nothing_new_keeps_the_position(self):
        guild = Guild(
            httpx.Response(
                200,
                json={
                    "items": [],
                    "next_after": encode_cursor({}),
                    "instances": {THOR: summary(head_seq=323)},
                },
            )
        )
        page = await feed_for(guild).after(THOR, 323, limit=5)
        assert (page.items, page.position) == ([], 323)

    @pytest.mark.parametrize("cursor", [None, 12])
    async def test_a_missing_cursor_is_an_error(self, cursor):
        guild = Guild(httpx.Response(200, json={"items": [], "next_after": cursor}))
        with pytest.raises(GuildFeedError, match="no next_after cursor"):
            await feed_for(guild).after(THOR, 0, limit=5)

    async def test_an_unreadable_cursor_is_an_error(self):
        guild = Guild(httpx.Response(200, json={"items": [], "next_after": "!!!"}))
        with pytest.raises(GuildFeedError, match="unreadable cursor"):
            await feed_for(guild).after(THOR, 0, limit=5)


class TestNodeFailures:
    async def test_a_node_that_hangs_times_out_on_its_own_read(self):
        guild = Guild(httpx.ReadTimeout("no answer"))
        with pytest.raises(GuildFeedError) as excinfo:
            await feed_for(guild).after(THOR, 0, limit=5)
        assert str(excinfo.value) == f"Guild node {THOR}'s feed is unreachable: ReadTimeout"

    @pytest.mark.parametrize("status", [502, 500])
    async def test_refused_or_not_answering(self, status):
        guild = Guild(httpx.Response(status, json={"detail": "no"}))
        with pytest.raises(GuildFeedError) as excinfo:
            await feed_for(guild).newest(THOR, limit=1)
        assert str(excinfo.value) == f"Guild node {THOR}'s feed answered HTTP {status}"

    async def test_a_node_the_guild_names_feedless_says_so(self):
        guild = Guild(
            httpx.Response(
                404,
                json={"detail": "No notifications feed"},
                headers={"X-Forge-Feedless-Instances": THOR},
            )
        )
        with pytest.raises(GuildNodeHasNoFeedError) as excinfo:
            await feed_for(guild).newest(THOR, limit=1)
        assert "an older Forge without the feed" in str(excinfo.value)

    async def test_any_other_404_is_an_ordinary_failure(self):
        guild = Guild(httpx.Response(404, json={"detail": "Forge instance not found"}))
        with pytest.raises(GuildFeedError) as excinfo:
            await feed_for(guild).newest(THOR, limit=1)
        assert not isinstance(excinfo.value, GuildNodeHasNoFeedError)

    async def test_not_json(self):
        guild = Guild(httpx.Response(200, content=b"<html>"))
        with pytest.raises(GuildFeedError, match="invalid JSON"):
            await feed_for(guild).newest(THOR, limit=1)

    @pytest.mark.parametrize("payload", [[], {"items": "x"}, {}])
    async def test_unexpected_shape(self, payload):
        guild = Guild(httpx.Response(200, json=payload))
        with pytest.raises(GuildFeedError, match="unexpected shape"):
            await feed_for(guild).after(THOR, 0, limit=1)


class TestConstruction:
    def test_guild_url_is_required(self):
        with pytest.raises(ValueError, match="push.gateway.guild_url"):
            HttpGuildFeed(guild_url="", timeout_seconds=5, auth=NoAuthHeaderAdapter())

    def test_guild_url_must_be_http(self):
        with pytest.raises(ValueError, match="http or https"):
            HttpGuildFeed(guild_url="ftp://guild", timeout_seconds=5, auth=NoAuthHeaderAdapter())

    async def test_owns_and_closes_its_client(self):
        feed = HttpGuildFeed(guild_url=GUILD + "/", timeout_seconds=5, auth=NoAuthHeaderAdapter())
        assert feed._feed_url == f"{GUILD}/api/v1/forge/notifications"
        assert feed._instances_url == f"{GUILD}/api/v1/niuu/instances"
        assert feed._client.timeout == httpx.Timeout(5)
        await feed.close()
        assert feed._client.is_closed

    async def test_an_injected_client_is_left_open(self):
        client = httpx.AsyncClient()
        feed = HttpGuildFeed(
            guild_url=GUILD, timeout_seconds=5, auth=NoAuthHeaderAdapter(), client=client
        )
        await feed.close()
        assert not client.is_closed
        await client.aclose()


class TestEvents:
    async def test_yields_the_node_of_each_notification_event(self, monkeypatch):
        seen: dict = {}

        async def events(client, url, headers):
            seen["url"], seen["headers"] = url, dict(headers)
            seen["client"] = client
            yield "session_updated", {"instance_id": THOR}
            yield "session_notification", {"id": "n-1", "instance_id": MINI}
            yield "heartbeat", {}
            yield "session_notification", {"id": "n-2"}

        monkeypatch.setattr(http_guild_feed, "remote_events", events)
        feed = HttpGuildFeed(guild_url=GUILD, timeout_seconds=5, auth=_auth("tok-123"))

        assert [instance async for instance in feed.notification_events()] == [MINI, ""]
        assert seen["url"] == f"{GUILD}/api/v1/forge/sessions/stream?all_instances=true"
        assert seen["headers"] == {"Authorization": "Bearer tok-123"}
        assert isinstance(seen["client"], httpx.AsyncClient)
        assert seen["client"].timeout.read == 5
        await seen["client"].aclose()
        await feed.close()

    async def test_a_lost_stream_is_a_feed_error(self, monkeypatch):
        async def events(client, url, headers):
            await client.aclose()
            raise httpx.ReadTimeout("idle")
            yield  # pragma: no cover

        monkeypatch.setattr(http_guild_feed, "remote_events", events)
        feed = HttpGuildFeed(guild_url=GUILD, timeout_seconds=5, auth=NoAuthHeaderAdapter())

        with pytest.raises(GuildFeedError, match="event stream is unreachable: ReadTimeout"):
            async for _ in feed.notification_events():
                pass
        await feed.close()
