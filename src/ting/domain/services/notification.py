"""Notification service — subscribes to EventBus and dispatches to channels.

Maps domain events (run state changes, saga completions, etc.) to
user-facing notifications and delivers them through configured channels
(Telegram, Slack, etc.) using the dynamic adapter pattern.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from ting.ports.channel_resolver import ChannelResolverPort
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.notification_channel import (
    Notification,
    NotificationUrgency,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event → Notification mapping configuration
# ---------------------------------------------------------------------------

_STATUS_NOTIFICATION_MAP: dict[str, dict[str, Any]] = {
    "RUNNING": {
        "title": "Run started",
        "body_template": "Run {tracker_id} has started running.",
        "urgency": NotificationUrgency.LOW,
    },
    "REVIEW": {
        "title": "Run ready for review",
        "body_template": "Run {tracker_id} is ready for review.",
        "urgency": NotificationUrgency.HIGH,
    },
    "FAILED": {
        "title": "Run failed",
        "body_template": "Run {tracker_id} failed (retry #{retry_count}).",
        "urgency": NotificationUrgency.HIGH,
    },
    "MERGED": {
        "title": "Run merged",
        "body_template": "Run {tracker_id} has been merged.",
        "urgency": NotificationUrgency.LOW,
    },
}

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class NotificationService:
    """Subscribes to EventBus events and dispatches notifications to channels.

    Runs as a background task alongside the main application. For each event,
    it resolves the owning user, builds a Notification, resolves that user's
    configured channels, and delivers.
    """

    def __init__(
        self,
        event_bus: EventBusPort,
        channel_factory: ChannelResolverPort,
        *,
        confidence_threshold: float,
        public_origin: str = "http://localhost:8080",
    ) -> None:
        self._event_bus = event_bus
        self._channel_factory = channel_factory
        self._confidence_threshold = confidence_threshold
        self._public_origin = public_origin
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[TingEvent] | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the notification background loop."""
        self._queue = self._event_bus.subscribe()
        self._running = True
        self._task = asyncio.create_task(self._run(), name="notification-service")
        logger.info("Notification service started")

    async def stop(self) -> None:
        """Gracefully stop the notification service."""
        self._running = False
        if self._queue is not None:
            self._event_bus.unsubscribe(self._queue)
            self._queue = None
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                _ = await self._task
            self._task = None
        logger.info("Notification service stopped")

    async def _run(self) -> None:
        """Main loop — consume events and dispatch notifications."""
        while self._running:
            try:
                if self._queue is None:
                    break
                event = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                await self._handle_event(event)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error processing notification event")

    async def _handle_event(self, event: TingEvent) -> None:
        """Map an event to a notification and dispatch it."""
        notification = await self._map_event(event)
        if notification is None:
            return

        channels = await self._channel_factory.for_owner(notification.owner_id)
        if not channels:
            return

        for channel in channels:
            if not channel.should_notify(notification):
                continue
            try:
                await channel.send(notification)
            except Exception:
                logger.warning(
                    "Failed to send notification via %s",
                    type(channel).__name__,
                    exc_info=True,
                )

    async def _map_event(self, event: TingEvent) -> Notification | None:
        """Convert a TingEvent into a Notification, or None if unmapped."""
        match event.event:
            case "run.state_changed":
                return self._map_run_state_changed(event)
            case "confidence.updated":
                return self._map_confidence_updated(event)
            case "saga.pr_created":
                return self._map_saga_pr_created(event.data)
            case "phase.unlocked":
                return self._map_phase_unlocked(event.data)
            case "run.needs_approval":
                return self._map_run_needs_approval(event.data)
            case "run.feedback_requested":
                return self._map_run_feedback_requested(event.data)
            case _:
                return None
        return None

    def _map_run_state_changed(self, event: TingEvent) -> Notification | None:
        """Map a run.state_changed event to a notification."""
        data = event.data
        status = data.get("status", "")
        mapping = _STATUS_NOTIFICATION_MAP.get(status)
        if mapping is None:
            return None

        owner_id = event.owner_id or data.get("owner_id", "")
        if not owner_id:
            return None

        tracker_id = data.get("tracker_id", "") or data.get("run_id", "")
        url = data.get("url", "") or ""
        pr_url = data.get("pr_url", "") or ""
        pr_id = data.get("pr_id", "") or ""
        retry_count = data.get("retry_count", 0)

        body = mapping["body_template"].format(
            tracker_id=tracker_id,
            retry_count=retry_count,
        )

        if pr_url:
            body += f"\nPR: {pr_url}"
        if url:
            body += f"\nTicket: {url}"

        metadata: dict[str, str] = {}
        if tracker_id:
            metadata["tracker_id"] = tracker_id
        if url:
            metadata["url"] = url
        if pr_url:
            metadata["pr_url"] = pr_url
        if pr_id:
            metadata["pr_id"] = pr_id

        return Notification(
            title=mapping["title"],
            body=body,
            urgency=mapping["urgency"],
            owner_id=owner_id,
            event_type=f"run.{status.lower()}",
            metadata=metadata,
        )

    def _map_confidence_updated(self, event: TingEvent) -> Notification | None:
        """Map a confidence.updated event when confidence drops below threshold."""
        data = event.data
        confidence = data.get("score_after", 1.0)
        if confidence >= self._confidence_threshold:
            return None

        owner_id = event.owner_id or data.get("owner_id", "")
        if not owner_id:
            return None

        tracker_id = data.get("tracker_id", "") or data.get("run_id", "")

        return Notification(
            title="Confidence dropped",
            body=f"Run {tracker_id} confidence dropped to {confidence:.0%}.",
            urgency=NotificationUrgency.MEDIUM,
            owner_id=owner_id,
            event_type="confidence.low",
            metadata={"tracker_id": tracker_id} if tracker_id else {},
        )

    @staticmethod
    def _map_saga_pr_created(data: dict[str, Any]) -> Notification | None:
        """Map a saga.pr_created event."""
        owner_id = data.get("owner_id", "")
        if not owner_id:
            return None

        saga_name = data.get("saga_name", "")
        pr_url = data.get("pr_url", "")

        body = f'Saga "{saga_name}" complete — final PR ready.'
        if pr_url:
            body += f"\nPR: {pr_url}"

        metadata: dict[str, str] = {}
        if pr_url:
            metadata["pr_url"] = pr_url

        return Notification(
            title="Saga complete",
            body=body,
            urgency=NotificationUrgency.HIGH,
            owner_id=owner_id,
            event_type="saga.complete",
            metadata=metadata,
        )

    @staticmethod
    def _map_run_needs_approval(data: dict[str, Any]) -> Notification | None:
        """Map a run.needs_approval event to a notification."""
        owner_id = data.get("owner_id", "")
        if not owner_id:
            return None

        run_name = data.get("run_name", data.get("run_id", ""))
        saga_name = data.get("saga_name", "")

        body = f'Run "{run_name}" is waiting for your approval before it can start.'
        if saga_name:
            body += f'\nSaga: "{saga_name}"'

        return Notification(
            title="Run awaiting approval",
            body=body,
            urgency=NotificationUrgency.HIGH,
            owner_id=owner_id,
            event_type="run.needs_approval",
            metadata={
                "run_id": str(data.get("run_id", "")),
                "saga_id": str(data.get("saga_id", "")),
            },
        )

    @staticmethod
    def _map_phase_unlocked(data: dict[str, Any]) -> Notification | None:
        """Map a phase.unlocked event."""
        owner_id = data.get("owner_id", "")
        if not owner_id:
            logger.warning("phase.unlocked event missing owner_id, skipping notification")
            return None

        phase_number = data.get("phase_number", "?")
        queued_runs = data.get("queued_runs", 0)

        return Notification(
            title="Phase unlocked",
            body=f"Phase {phase_number} unlocked, {queued_runs} runs queued.",
            urgency=NotificationUrgency.MEDIUM,
            owner_id=owner_id,
            event_type="phase.unlocked",
        )

    def _map_run_feedback_requested(self, data: dict[str, Any]) -> Notification | None:
        owner_id = str(data.get("owner_id") or "").strip()
        if not owner_id:
            return None
        run_name = str(data.get("run_name") or data.get("tracker_id") or data.get("run_id") or "")
        summary = str(data.get("summary") or "A council member needs your feedback.")
        reason = str(data.get("reason") or "").strip()
        ui_path = str(data.get("ui_path") or "").strip()
        ui_url = _ui_url(ui_path, self._public_origin)

        body = f'We need your feedback on "{run_name}".\n{summary}'
        if reason:
            body += f"\nReason: {reason}"
        if ui_url:
            body += f"\nTing: {ui_url}"

        metadata = {
            "run_id": str(data.get("run_id") or ""),
            "saga_id": str(data.get("saga_id") or ""),
            "ui_path": ui_path,
        }
        if ui_url:
            metadata["ui_url"] = ui_url

        return Notification(
            title="We need your feedback",
            body=body,
            urgency=NotificationUrgency.HIGH,
            owner_id=owner_id,
            event_type="run.feedback_requested",
            metadata=metadata,
        )


def _ui_url(path: str, public_origin: str) -> str:
    normalized = path.strip()
    if not normalized:
        return ""
    if normalized.startswith(("http://", "https://")):
        return normalized

    origin = public_origin.strip().rstrip("/") or "http://localhost:8080"
    suffix = normalized if normalized.startswith("/") else f"/{normalized}"
    return f"{origin}{suffix}"
