"""MeshActivityChannel — publishes durable agent activity to the Ravn mesh (NIU-634).

Bridges the ChannelPort interface to the MeshPort so that DriveLoop can forward
task, tool, response, usage, and outcome boundaries to any mesh peer without a
direct WebSocket connection to Skuld. Token-level THOUGHT events stay on live
channels and metrics; persisting them would create replay noise and make
best-effort observability contend with agent execution.

USAGE is published: it is emitted once per completed turn, not per token, and
for a mesh-only peer (a flock persona with no WebSocket to Skuld) this is the
only route by which a session learns what its work cost. Skuld's room adapter
already consumes ``kind: "usage"`` and dedupes on ``usage_id``, and the payload
keys line up field for field — filtering it here was the one link missing, and
it left every flock session reporting zero tokens and zero cost.

Topic: ``activity.{peer_id}``
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.channel import ChannelPort

if TYPE_CHECKING:
    from ravn.ports.mesh import MeshPort

logger = logging.getLogger(__name__)

_EPHEMERAL_EVENT_TYPES = {RavnEventType.THOUGHT}


class MeshActivityChannel(ChannelPort):
    """Publishes durable RavnEvent boundaries under ``activity.{peer_id}``.

    Used alongside SkuldChannel in a CompositeChannel so that activity events
    are delivered via Sleipnir mesh in addition to (or instead of) WebSocket.
    The collaboration mesh bridge forwards their Ravn-projected room events.
    Publishing is best-effort and detached from the caller so a slow telemetry
    stream cannot hold up model or tool execution.
    """

    def __init__(self, mesh: MeshPort, peer_id: str) -> None:
        self._mesh = mesh
        self._topic = f"activity.{peer_id}"
        self._publishes: set[asyncio.Task[None]] = set()

    async def emit(self, event: RavnEvent) -> None:
        if event.type in _EPHEMERAL_EVENT_TYPES:
            return
        task = asyncio.create_task(self._publish(event))
        self._publishes.add(task)
        task.add_done_callback(self._publishes.discard)

    async def _publish(self, event: RavnEvent) -> None:
        try:
            await self._mesh.publish(event, topic=self._topic)
        except Exception:
            logger.warning("MeshActivityChannel: failed to publish event", exc_info=True)
