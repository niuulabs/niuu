"""Service for sending messages to running Volundr sessions.

Handles resolving raid → session, sending via VolundrPort, persisting
the message for audit, recording a confidence event, and emitting an
SSE event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from tyr.domain.exceptions import RaidNotFoundError
from tyr.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    RaidStatus,
    SessionMessage,
)
from tyr.ports.event_bus import EventBusPort, TyrEvent
from tyr.ports.tracker import TrackerPort
from tyr.ports.volundr import VolundrPort

logger = logging.getLogger(__name__)

RUNNING_STATUSES = frozenset({RaidStatus.RUNNING, RaidStatus.REVIEW, RaidStatus.ESCALATED})


class NoActiveSessionError(Exception):
    def __init__(self, raid_id: UUID | str) -> None:
        self.raid_id = raid_id
        super().__init__(f"Raid {raid_id} has no active session")


class RaidNotRunningError(Exception):
    def __init__(self, raid_id: UUID | str, status: str) -> None:
        self.raid_id = raid_id
        self.status = status
        super().__init__(f"Raid {raid_id} is in {status} state, not running")


@dataclass(frozen=True)
class MessageResult:
    """Outcome of sending a message to a session."""

    message: SessionMessage
    raid_id: UUID
    session_id: str


class SessionMessageService:
    """Sends messages to running sessions and tracks them for audit."""

    def __init__(
        self,
        tracker: TrackerPort,
        volundr: VolundrPort,
        event_bus: EventBusPort | None = None,
    ) -> None:
        self._tracker = tracker
        self._volundr = volundr
        self._event_bus = event_bus

    async def send_message(
        self,
        raid_id: UUID,
        content: str,
        *,
        sender: str = "user",
        auth_token: str | None = None,
        target_peer_id: str | None = None,
    ) -> MessageResult:
        """Send a message to the session running a raid.

        1. Resolve raid → session_id
        2. Send message via VolundrPort
        3. Persist audit record
        4. Record confidence event (zero delta)
        5. Emit SSE event
        """
        raid = await self._tracker.get_raid_by_id(raid_id)
        if raid is None:
            raise RaidNotFoundError(raid_id)

        if raid.status not in RUNNING_STATUSES:
            raise RaidNotRunningError(raid_id, raid.status.value)

        # In REVIEW state, prefer the reviewer session; otherwise use the working session
        target_session = raid.reviewer_session_id if raid.status == RaidStatus.REVIEW else None
        target_session = target_session or raid.session_id
        if not target_session:
            raise NoActiveSessionError(raid_id)

        # Send the message to Volundr
        if target_peer_id:
            await self._volundr.send_directed_room_message(
                target_session,
                target_peer_id,
                content,
                auth_token=auth_token,
            )
        else:
            await self._volundr.send_message(target_session, content, auth_token=auth_token)

        # Persist audit record
        now = datetime.now(UTC)
        msg = SessionMessage(
            id=uuid4(),
            raid_id=raid_id,
            session_id=target_session,
            content=content,
            sender=sender,
            created_at=now,
        )
        await self._tracker.save_session_message(msg)

        # Record confidence event with zero delta (message doesn't change score)
        event = ConfidenceEvent(
            id=uuid4(),
            raid_id=raid_id,
            event_type=ConfidenceEventType.MESSAGE_SENT,
            delta=0.0,
            score_after=raid.confidence,
            created_at=now,
        )
        await self._tracker.add_confidence_event(raid.tracker_id, event)

        # Emit SSE event
        if self._event_bus:
            await self._event_bus.emit(
                TyrEvent(
                    event="session.message_sent",
                    data={
                        "raid_id": str(raid_id),
                        "session_id": raid.session_id,
                        "sender": sender,
                        "content_length": len(content),
                        "target_peer_id": target_peer_id or "",
                    },
                )
            )

        logger.info(
            "Message sent to session %s for raid %s (sender=%s, length=%d)",
            raid.session_id,
            raid_id,
            sender,
            len(content),
        )

        return MessageResult(
            message=msg,
            raid_id=raid_id,
            session_id=target_session,
        )
