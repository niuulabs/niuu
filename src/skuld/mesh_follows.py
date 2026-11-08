"""Topics and threads a session follows at runtime, kept durable like its peer inbox.

A pinned persona's ``consumes`` (with ``mesh.consumes_event_types``) is the session's standing
subscription. An agent can also start listening to a topic, or to a thread, while it works
(``mesh_subscribe``) and stop again (``mesh_unsubscribe``). Each change is written to the
session's event log as a ``mesh_subscription`` frame and stored before the call returns, so a
restart restores exactly what the session followed and nothing is followed or dropped
silently. The log is replayed on start (``restore``) and applied once the mesh is up
(``apply_restored_follows``).

Following is allowed for any topic. A session listens inside its flock's realm only, where
every member already sees every announcement and may be sent to, so listening discloses nothing
a member cannot already learn; what it can do to the follower is fill its inbox, and the
follower decides that, sees it (``skuld.peer.messages``, the inbox status) and can stop. Only
publishing is limited to declared topics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from skuld.mesh_topics import thread_topic

logger = logging.getLogger("skuld.mesh_follows")

MESH_SUBSCRIPTION_FRAME = "mesh_subscription"


class MeshFollowError(RuntimeError):
    """A topic could not be followed or unfollowed; the message says why and what to do."""


@dataclass(frozen=True)
class Follow:
    """One thing a session follows at runtime."""

    topic: str
    #: The correlation id when this follows a thread; empty for a plain topic.
    thread: str = ""

    def frame(self, *, followed: bool) -> dict[str, Any]:
        return {
            "type": MESH_SUBSCRIPTION_FRAME,
            "topic": self.topic,
            "thread": self.thread,
            "subscribed": followed,
        }


def follow_for(*, topic: str = "", correlation_id: str = "") -> Follow:
    """The follow a ``topic`` or ``correlation_id`` names. Exactly one must be given."""
    if bool(topic) == bool(correlation_id):
        raise MeshFollowError("give exactly one of topic or correlation_id")
    if correlation_id:
        return Follow(topic=thread_topic(correlation_id), thread=correlation_id)
    return Follow(topic=topic)


class MeshFollowMixin:
    """Broker behavior for following topics and threads at runtime."""

    _mesh_follows: dict[str, Follow]

    def _init_mesh_follows(self) -> None:
        self._mesh_follows = {}

    # -- restore

    def _restore_durable_mesh_follows(self, frames: list) -> None:
        """Rebuild what the session followed from the log: each topic at its latest state."""
        for frame in frames:
            if getattr(frame, "kind", "") != MESH_SUBSCRIPTION_FRAME:
                continue
            payload = frame.payload
            topic = payload["topic"]
            if payload["subscribed"]:
                self._mesh_follows[topic] = Follow(topic=topic, thread=payload.get("thread", ""))
            else:
                self._mesh_follows.pop(topic, None)

    async def apply_restored_follows(self) -> None:
        """Listen again to what the session followed before it restarted.

        A topic the mesh cannot subscribe to raises: a follow is never dropped to get the
        session up.
        """
        adapter = self._mesh_adapter
        for follow in self._mesh_follows.values():
            await adapter.follow(follow.topic)
        self._announce_followed_topics()

    # -- follow and unfollow

    async def follow_mesh(self, follow: Follow) -> bool:
        """Start listening now and remember it durably. False when it was already followed.

        The change is stored in the session log first, so a follow that is not durable is
        refused and never started.
        """
        adapter = self._require_mesh_adapter()
        if follow.topic in self._mesh_follows or follow.topic in adapter.standing_topics:
            return False
        await self._record_follow(follow, followed=True)
        try:
            await adapter.follow(follow.topic)
        except Exception:
            # Not listening, so it must not be restored as followed either.
            await self._record_follow(follow, followed=False)
            raise
        self._mesh_follows[follow.topic] = follow
        self._announce_followed_topics()
        return True

    async def unfollow_mesh(self, follow: Follow) -> None:
        """Stop listening to something followed at runtime and forget it durably."""
        adapter = self._require_mesh_adapter()
        if follow.topic in adapter.standing_topics:
            raise MeshFollowError(
                f"{follow.topic!r} is a standing subscription (this session's persona or "
                "mesh.consumes_event_types declares it), not one followed at runtime; change "
                "the persona to stop consuming it"
            )
        if follow.topic not in self._mesh_follows:
            raise MeshFollowError(
                f"this session does not follow {follow.thread or follow.topic!r}; it follows "
                f"{self.mesh_subscriptions()['followed'] or 'nothing at runtime'}"
            )
        await self._record_follow(follow, followed=False)
        await adapter.unfollow(follow.topic)
        del self._mesh_follows[follow.topic]
        self._announce_followed_topics()

    def mesh_subscriptions(self) -> dict[str, Any]:
        """What this session listens to: the standing topics and what it follows."""
        adapter = self._require_mesh_adapter()
        return {
            "standing": list(adapter.standing_topics),
            "followed": [
                {"topic": f.topic, **({"correlation_id": f.thread} if f.thread else {})}
                for f in self._mesh_follows.values()
            ],
        }

    def _require_mesh_adapter(self) -> Any:
        if self._mesh_adapter is None:
            raise MeshFollowError(
                "this session's mesh is not running (mesh.enabled is set but the adapter did "
                "not start); check the broker log, or set mesh.enabled to false"
            )
        return self._mesh_adapter

    def _announce_followed_topics(self) -> None:
        """Put the plain topics followed at runtime in the announcement beside the persona's.

        A thread is not announced: it is one conversation, and the next heartbeat carries
        the topic list.
        """
        identity = self._mesh_identity
        if identity is None:
            return
        standing = list(self._mesh_adapter.standing_topics)
        identity.consumes_event_types = [
            *standing,
            *(f.topic for f in self._mesh_follows.values() if not f.thread),
        ]

    # -- durable record

    async def _record_follow(self, follow: Follow, *, followed: bool) -> None:
        """Write the change to the session event log; wait until Forge stored it."""
        if not (self._settings.event_log_enabled and self.volundr_api_url):
            raise MeshFollowError(
                "this session has no durable Forge log (volundr_api_url unset or "
                "event_log_enabled=false), so what it follows could not survive a restart. "
                "Nothing was changed"
            )
        logged = self._enqueue_event_log(follow.frame(followed=followed))
        if logged is None:
            raise MeshFollowError("the durable event log refused the subscription frame")
        timeout_s = self._settings.forge_mcp.notify_confirm_timeout_s
        stored = await self._await_event_log_receipt(self._watch_event_log_entry(logged), timeout_s)
        if stored is False:
            raise MeshFollowError(
                "the subscription change was not stored (it was dropped while Forge was "
                "unreachable, or its sequence conflicted); nothing was changed, try again"
            )
        if stored is None:
            raise MeshFollowError(
                f"Forge did not confirm the subscription change within "
                f"forge_mcp.notify_confirm_timeout_s ({timeout_s}s); it may have been stored. "
                "Call again: following and unfollowing are safe to repeat"
            )
