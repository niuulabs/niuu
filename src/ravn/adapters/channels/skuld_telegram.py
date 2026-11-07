"""Ravn channel adapter for Skuld's Telegram channel."""

from __future__ import annotations

from niuu.mesh.events import MeshEvent, MeshEventType
from niuu.mesh.projection import project_mesh_event
from ravn.ports.channel import ChannelPort
from skuld.channels import TelegramChannel


class TelegramRavnChannel(ChannelPort):
    """Send Ravn help-needed events through an existing Skuld Telegram channel."""

    def __init__(self, telegram: TelegramChannel) -> None:
        self._telegram = telegram
        self.sent_events: list[MeshEvent] = []

    async def emit(self, event: MeshEvent) -> None:
        self.sent_events.append(event)
        if event.type != MeshEventType.HELP_NEEDED:
            return
        projected = project_mesh_event(
            event, persona=str(event.payload.get("persona") or "resident")
        )[0]
        notification = {
            "type": "room_notification",
            "notificationType": projected["notificationType"],
            "sourceEventId": projected["sourceEventId"],
            "participantId": event.source,
            "persona": projected["persona"],
            "reason": projected["reason"],
            "summary": projected["summary"],
            "attempted": projected["attempted"],
            "recommendation": projected["recommendation"],
            "urgency": projected["urgency"],
        }
        if projected.get("context"):
            notification["context"] = projected["context"]
        if projected.get("traceContext"):
            notification["trace_context"] = projected["traceContext"]
        await self._telegram.send_event(notification)
