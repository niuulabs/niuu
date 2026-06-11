"""Persist huddle transcripts into Mimir from the resident daemon.

The Skuld broker renders the transcript when a huddle closes and publishes it
in the ``room.transcript.recorded`` payload — the broker has no Mimir access.
This archiver runs inside the resident Ravn daemon (which does), consuming
those events and writing the transcript page so huddle history lands in
memory for audit and learning (NIU-1024).
"""

from __future__ import annotations

import logging
from typing import Any

from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)


class HuddleTranscriptArchiver:
    """Subscribe to huddle transcript events and persist them via Mimir."""

    def __init__(self, *, subscriber: SleipnirSubscriber, mimir: Any) -> None:
        self._subscriber = subscriber
        self._mimir = mimir
        self._subscription: Subscription | None = None
        self._archived: list[str] = []

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    def archived_paths(self) -> list[str]:
        return list(self._archived)

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            [registry.ROOM_TRANSCRIPT_RECORDED],
            self._handle_event,
        )

    async def stop(self) -> None:
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None

    async def _handle_event(self, event: SleipnirEvent) -> None:
        payload = event.payload
        transcript_ref = str(payload.get("transcript_ref") or "").strip()
        content = str(payload.get("transcript_content") or "")
        if not transcript_ref or not content.strip():
            logger.warning(
                "huddle archiver: transcript event %s missing ref or content; skipping",
                event.event_id,
            )
            return
        await self._mimir.upsert_page(transcript_ref, content)
        self._archived.append(transcript_ref)
        logger.info("huddle archiver: wrote transcript %s", transcript_ref)
