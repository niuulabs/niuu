"""SilentChannel — discards all events; detects [SURFACE] escalation."""

from __future__ import annotations

from niuu.mesh.events import MeshEvent, MeshEventType
from niuu.mesh.surface import SURFACE_PREFIX as _SURFACE_PREFIX
from ravn.ports.channel import ChannelPort


class SilentChannel(ChannelPort):
    """Discards all events.  Memory and outcome recording still happen.

    If the agent's response starts with ``[SURFACE]``, the drive loop
    detects this via :attr:`surface_triggered` and re-delivers the
    response via the configured surface channel.
    """

    def __init__(self) -> None:
        self._response_text: str = ""
        self.surface_triggered: bool = False

    async def emit(self, event: MeshEvent) -> None:
        if event.type != MeshEventType.RESPONSE:
            return
        self._response_text = event.payload.get("text", "")
        self.surface_triggered = self._response_text.startswith(_SURFACE_PREFIX)

    @property
    def response_text(self) -> str:
        return self._response_text
