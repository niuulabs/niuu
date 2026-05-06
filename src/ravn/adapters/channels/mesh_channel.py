"""MeshActivityChannel — publishes agent activity events to the Ravn mesh (NIU-634).

Bridges the ChannelPort interface to the MeshPort so that DriveLoop
can forward thought/tool_start/tool_result/response events to any mesh
peer without a direct WebSocket connection to Skuld.

Topic: ``activity.{peer_id}``
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ravn.domain.events import RavnEvent
from ravn.ports.channel import ChannelPort

if TYPE_CHECKING:
    from ravn.ports.mesh import MeshPort

logger = logging.getLogger(__name__)


class MeshActivityChannel(ChannelPort):
    """Publishes each emitted RavnEvent to the mesh under ``activity.{peer_id}``.

    Used alongside SkuldChannel in a CompositeChannel so that activity events
    are delivered via Sleipnir mesh in addition to (or instead of) WebSocket.
    RoomMeshBridge picks them up and translates them to room wire events.
    """

    def __init__(self, mesh: MeshPort, peer_id: str) -> None:
        self._mesh = mesh
        self._topic = f"activity.{peer_id}"

    async def emit(self, event: RavnEvent) -> None:
        try:
            await self._mesh.publish(event, topic=self._topic)
        except Exception:
            logger.warning("MeshActivityChannel: failed to publish event", exc_info=True)
