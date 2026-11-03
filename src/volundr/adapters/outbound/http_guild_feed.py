"""Each Guild node's notification feed, read through the Guild API.

The Guild facade tags every notification with its node (``instance_id``) and routes
image URLs to it, so what the push gateway pushes names the same ids the app sees.
The gateway reads the facade over HTTP like any client:

* the node list comes from the Guild's registry (``GET /api/v1/niuu/instances``),
  which contacts no node;
* each feed read names **one** node, through the merged feed's own cursor
  (:mod:`niuu.adapters.inbound.forge_notification_feed`): a cursor that names a
  single node makes the facade read only that node, and answer 502 when it does
  not respond. A node that hangs therefore costs one request's timeout, on its own
  read, and never delays the others.

The gateway reads the Guild's **delivery view** (``view=delivery``): every owner's
and every org's notifications, each item read as its own owner reads it, with each
owner's unread count. On an authenticated Guild only a
``forge:notifications:deliver`` workload credential may read it; ``auth`` supplies
it (and an AllowAll Guild needs none). A node that answers without the view's
``delivery`` block (an older Forge) fails its read: its pushes would otherwise carry
someone else's read state.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from niuu.adapters.inbound.forge_notification_feed import (
    FEEDLESS_INSTANCES_HEADER,
    CursorError,
    decode_cursor,
    encode_cursor,
)
from niuu.adapters.inbound.forge_session_stream import remote_events
from niuu.adapters.inbound.remote_urls import build_remote_url
from niuu.ports.http_auth import HttpAuthPort
from volundr.domain.push import GuildNotification
from volundr.domain.push_ports import (
    GuildFeed,
    GuildFeedError,
    GuildFeedPage,
    GuildNode,
    GuildNodeHasNoFeedError,
)

logger = logging.getLogger(__name__)

FORGE_API_PREFIX = "/api/v1/forge"
REGISTRY_API_PREFIX = "/api/v1/niuu"
NOTIFICATIONS_PATH = "/notifications"
INSTANCES_PATH = "/instances"
STREAM_PATH = "/sessions/stream"
NOTIFICATION_EVENT = "session_notification"
#: The registry's kind for a Forge node.
FORGE_INSTANCE_KIND = "volundr"
_ALL_INSTANCES = ("all_instances", "true")
_DELIVERY_VIEW = ("view", "delivery")
#: The registry ``config`` key that marks the Guild's push gateway node.
PUSH_GATEWAY_CONFIG_KEY = "push_gateway"


class HttpGuildFeed(GuildFeed):
    """Reads the Guild's registry and, node by node, its notification feed."""

    def __init__(
        self,
        *,
        guild_url: str,
        timeout_seconds: float,
        auth: HttpAuthPort,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not guild_url:
            raise ValueError("The push gateway needs the Guild API origin (push.gateway.guild_url)")
        self._feed_url = build_remote_url(guild_url, FORGE_API_PREFIX, NOTIFICATIONS_PATH)
        self._instances_url = build_remote_url(guild_url, REGISTRY_API_PREFIX, INSTANCES_PATH)
        self._stream_url = (
            build_remote_url(guild_url, FORGE_API_PREFIX, STREAM_PATH) + "?all_instances=true"
        )
        self._auth = auth
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def _get(
        self, url: str, params: list[tuple[str, str]], what: str, *, node_feed: bool = False
    ) -> Any:
        try:
            response = await self._client.get(url, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise GuildFeedError(f"{what} is unreachable: {type(exc).__name__}") from None
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            # A stale exchanged token is refetched on the next read.
            self._auth.invalidate()
            raise GuildFeedError(
                f"{what} refused the gateway's credential (HTTP {response.status_code}): it "
                "needs a forge:notifications:deliver workload credential (push.gateway.reader_auth)"
            )
        if node_feed and response.headers.get(FEEDLESS_INSTANCES_HEADER):
            # The Guild says so explicitly; any other 404 is an error like the rest.
            raise GuildNodeHasNoFeedError(
                f"{what} does not exist (an older Forge without the feed)"
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise GuildFeedError(f"{what} answered HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise GuildFeedError(f"{what} answered with invalid JSON") from None

    def _headers(self) -> dict[str, str]:
        try:
            return self._auth.headers()
        except (RuntimeError, httpx.HTTPError) as exc:
            raise GuildFeedError(f"The gateway's Guild credential is unavailable: {exc}") from None

    async def nodes(self) -> list[GuildNode]:
        payload = await self._get(
            self._instances_url,
            [("kind", FORGE_INSTANCE_KIND), ("enabledOnly", "true")],
            "The Guild registry",
        )
        if not isinstance(payload, list):
            raise GuildFeedError(
                "The Guild registry answered with an unexpected shape: "
                "push.gateway.guild_url must be a Guild API origin"
            )
        return [
            GuildNode(
                id=item["id"],
                push_gateway=isinstance(item.get("config"), Mapping)
                and item["config"].get(PUSH_GATEWAY_CONFIG_KEY) is True,
            )
            for item in payload
            if isinstance(item, Mapping) and isinstance(item.get("id"), str) and item["id"]
        ]

    async def _page(self, node: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        payload = await self._get(
            self._feed_url,
            [_ALL_INSTANCES, _DELIVERY_VIEW, *params],
            f"Guild node {node}'s feed",
            node_feed=True,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise GuildFeedError(f"Guild node {node}'s feed answered with an unexpected shape")
        return payload

    @staticmethod
    def _delivery(node: str, payload: Mapping[str, Any]) -> tuple[dict[str, int], bool]:
        """The node's per-owner unread counts and whether it pushes alerts itself."""
        summaries = payload.get("instances")
        summary = summaries.get(node) if isinstance(summaries, Mapping) else None
        delivery = summary.get("delivery") if isinstance(summary, Mapping) else None
        if not isinstance(delivery, Mapping):
            raise GuildFeedError(
                f"Guild node {node} answered without the delivery view: it runs a Forge "
                "older than the push gateway's delivery view, so its notifications are not "
                "pushed until it is upgraded"
            )
        counts = delivery.get("owner_unread_counts")
        pushes = delivery.get("node_pushes_alerts")
        if not isinstance(counts, Mapping) or type(pushes) is not bool:
            raise GuildFeedError(f"Guild node {node}'s delivery view has an unexpected shape")
        return (
            {str(owner): n for owner, n in counts.items() if type(n) is int},
            pushes,
        )

    @staticmethod
    def _items(node: str, payload: Mapping[str, Any]) -> list[GuildNotification]:
        items: list[GuildNotification] = []
        for raw in payload["items"]:
            if not isinstance(raw, Mapping):
                continue
            try:
                item = GuildNotification.from_feed_item(raw)
            except ValueError as exc:
                # One item a node sent wrong must not stop every later notification.
                logger.warning("Ignoring a malformed Guild feed item: %s", exc)
                continue
            if item.instance_id == node:
                items.append(item)
        return items

    async def newest(self, node: str, *, limit: int, owner_id: str | None = None) -> GuildFeedPage:
        # A ``before`` cursor naming only this node, at no position: its newest page.
        params = [("before", encode_cursor({node: None})), ("limit", str(limit))]
        if owner_id is not None:
            params.append(("owner_id", owner_id))
        payload = await self._page(node, params)
        summaries = payload.get("instances")
        summary = summaries.get(node) if isinstance(summaries, Mapping) else None
        if not isinstance(summary, Mapping) or type(summary.get("head_seq")) is not int:
            raise GuildFeedError(f"Guild node {node}'s feed returned no head position")
        counts, pushes = self._delivery(node, payload)
        return GuildFeedPage(
            items=self._items(node, payload),
            position=summary["head_seq"],
            owner_unread_counts=counts,
            node_pushes_alerts=pushes,
        )

    async def after(self, node: str, position: int, *, limit: int) -> GuildFeedPage:
        payload = await self._page(
            node, [("after", encode_cursor({node: position})), ("limit", str(limit))]
        )
        cursor = payload.get("next_after")
        if not isinstance(cursor, str):
            raise GuildFeedError(f"Guild node {node}'s feed returned no next_after cursor")
        try:
            advanced = decode_cursor(cursor).get(node)
        except CursorError as exc:
            raise GuildFeedError(
                f"Guild node {node}'s feed returned an unreadable cursor: {exc}"
            ) from None
        counts, pushes = self._delivery(node, payload)
        return GuildFeedPage(
            items=self._items(node, payload),
            position=advanced if advanced is not None else position,
            owner_unread_counts=counts,
            node_pushes_alerts=pushes,
        )

    async def notification_events(self) -> AsyncIterator[str]:
        try:
            # remote_events closes the client it reads from, so each stream gets its own.
            stream_client = httpx.AsyncClient(timeout=self._timeout_seconds)
            async for name, payload in remote_events(
                stream_client, self._stream_url, self._headers()
            ):
                if name != NOTIFICATION_EVENT:
                    continue
                instance_id = payload.get("instance_id")
                yield instance_id if isinstance(instance_id, str) else ""
        except httpx.HTTPError as exc:
            raise GuildFeedError(
                f"The Guild event stream is unreachable: {type(exc).__name__}"
            ) from None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
