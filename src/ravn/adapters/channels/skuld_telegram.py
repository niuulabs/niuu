"""Ravn channel adapter for Skuld's Telegram channel."""

from __future__ import annotations

from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.channel import ChannelPort
from skuld.channels import TelegramChannel
from skuld.room_bridge import help_needed_frame_to_room_notification
from skuld.room_models import ParticipantMeta


class TelegramRavnChannel(ChannelPort):
    """Send Ravn help-needed events through an existing Skuld Telegram channel."""

    def __init__(self, telegram: TelegramChannel) -> None:
        self._telegram = telegram
        self.sent_events: list[RavnEvent] = []

    async def emit(self, event: RavnEvent) -> None:
        self.sent_events.append(event)
        if event.type != RavnEventType.HELP_NEEDED:
            return
        persona = str(event.payload.get("persona") or "resident")
        meta = ParticipantMeta(
            peer_id=event.source,
            persona=persona,
            color="cyan",
            participant_type="ravn",
            display_name=persona,
            participant_kind="ravn",
            wakefulness="wakeful",
        )
        frame = {
            "session_id": event.session_id,
            "type": str(event.type),
            "data": event.payload,
            "metadata": {"urgency": event.urgency},
            "source": event.source,
            "persona": persona,
            "root_correlation_id": event.root_correlation_id,
            "correlation_id": event.correlation_id,
        }
        await self._telegram.send_event(help_needed_frame_to_room_notification(meta, frame))
