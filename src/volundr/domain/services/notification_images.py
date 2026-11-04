"""Copying notification images: the background worker behind ``forge_notification_images``.

When the projection records a notification that carries an image reference, it
also records a ``pending`` image row in the same transaction and wakes this worker.
The worker claims pending rows (lease + attempt fence, like the delivery outbox),
fetches each image ONCE through :class:`NotificationImageFetcher`, decodes it and
builds the thumbnail off the event loop through :class:`NotificationImageProcessor`,
and settles the row ``ready`` or ``failed``. The notification itself is committed
before any of this runs, so an image can never block or fail its notification.

A notification with an image is broadcast as ``session_notification`` once, when
its image settles, so the live event carries the same ``image`` object the feed
serves. Its live event can therefore arrive after that of a later notification;
feed order (``seq``) is unchanged.

Failure is explicit, never silent: a refused or unreachable image settles
``failed`` with the reason, which readers see as ``image_state``/``image_error``,
and is logged. A defect (an unexpected exception) leaves the row claimed; it is
logged and retried after its lease, exactly like a delivery.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

from niuu.domain.guild_membership import GuildInstance
from volundr.domain.models import EventType, RealtimeEvent
from volundr.domain.notification_ports import (
    NotificationImageFetcher,
    NotificationImageProcessor,
    NotificationImageRepository,
    NotificationImageScheduler,
    NotificationRepository,
)
from volundr.domain.notifications import (
    Notification,
    NotificationImageError,
    PendingNotificationImage,
)
from volundr.domain.ports import EventBroadcaster

logger = logging.getLogger(__name__)


def notification_event(
    notification: Notification, instance_fields: Mapping[str, str]
) -> RealtimeEvent:
    """The ``session_notification`` live event for a recorded notification.

    ``instance_fields`` is this node's Guild instance (empty outside a Guild), stamped
    on every item so the live event has the feed's shape.
    """
    return RealtimeEvent(
        type=EventType.SESSION_NOTIFICATION,
        data={**notification.wire(), **instance_fields},
        timestamp=notification.created_at,
    )


@dataclass(frozen=True)
class ImageWorkerSettings:
    poll_interval_seconds: float
    batch_size: int
    lease_seconds: float
    fetch_timeout_seconds: float
    max_concurrent_fetches: int

    def __post_init__(self) -> None:
        if self.fetch_timeout_seconds >= self.lease_seconds:
            raise ValueError("fetch_timeout_seconds must be shorter than lease_seconds")


@dataclass
class ImageWorkerStats:
    claimed: int = 0
    ready: int = 0
    failed: int = 0
    loop_errors: int = 0


class NotificationImageWorker(NotificationImageScheduler):
    """Makes the pending image copies and announces each notification once it settles."""

    def __init__(
        self,
        images: NotificationImageRepository,
        notifications: NotificationRepository,
        fetcher: NotificationImageFetcher,
        processor: NotificationImageProcessor,
        settings: ImageWorkerSettings,
        *,
        broadcaster: EventBroadcaster | None,
        guild_instance: GuildInstance | None = None,
    ) -> None:
        self._images = images
        self._notifications = notifications
        self._fetcher = fetcher
        self._processor = processor
        self._settings = settings
        self._broadcaster = broadcaster
        self._instance_fields = guild_instance.fields() if guild_instance else {}
        self._stopping = asyncio.Event()
        self._woken = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.stats = ImageWorkerStats()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def wake(self) -> None:
        self._woken.set()

    async def start(self) -> None:
        if self.running:
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="notification-image-worker")
        logger.info(
            "Notification image worker started (batch=%d, lease=%.0fs, fetch_timeout=%.0fs)",
            self._settings.batch_size,
            self._settings.lease_seconds,
            self._settings.fetch_timeout_seconds,
        )

    async def stop(self) -> None:
        """Stop polling; in-flight copies get ``fetch_timeout_seconds`` to finish.

        A copy still in flight after that stays claimed and is re-claimed by a
        worker once its lease expires.
        """
        task = self._task
        if task is None:
            return
        self._stopping.set()
        self._woken.set()
        done, _ = await asyncio.wait({task}, timeout=self._settings.fetch_timeout_seconds)
        if not done:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        logger.info(
            "Notification image worker stopped: ready=%d failed=%d",
            self.stats.ready,
            self.stats.failed,
        )

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            self._woken.clear()
            try:
                claimed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.stats.loop_errors += 1
                logger.exception("Notification image poll failed")
                claimed = 0
            if claimed >= self._settings.batch_size:
                continue  # A full batch: more copies are probably pending right now.
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._woken.wait(), timeout=self._settings.poll_interval_seconds
                )

    async def run_once(self) -> int:
        """Claim one batch of pending copies and settle it; returns how many were claimed."""
        batch = await self._images.claim_pending(
            limit=self._settings.batch_size, lease_seconds=self._settings.lease_seconds
        )
        if not batch:
            return 0
        self.stats.claimed += len(batch)
        slots = asyncio.Semaphore(self._settings.max_concurrent_fetches)
        await asyncio.gather(*(self._process(pending, slots) for pending in batch))
        return len(batch)

    async def _process(self, pending: PendingNotificationImage, slots: asyncio.Semaphore) -> None:
        try:
            async with slots:
                settled = await self._copy(pending)
            if settled:
                await self._announce(pending.notification_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A defect in one copy must not stall the batch; the row stays claimed
            # and is retried after its lease.
            self.stats.loop_errors += 1
            logger.exception(
                "Notification image copy for %s failed unexpectedly; retried after its lease",
                pending.notification_id,
            )

    async def _copy(self, pending: PendingNotificationImage) -> bool:
        """Fetch, decode and store one image; returns whether this claim settled it."""
        timeout = self._settings.fetch_timeout_seconds
        try:
            fetched = await asyncio.wait_for(self._fetcher.fetch(pending), timeout=timeout)
            processed = await asyncio.to_thread(self._processor.process, fetched)
        except TimeoutError:
            return await self._fail(pending, f"fetching the image timed out after {timeout:g}s")
        except NotificationImageError as exc:
            return await self._fail(pending, str(exc))
        settled = await self._images.mark_ready(pending, processed)
        if settled:
            self.stats.ready += 1
            logger.info(
                "Stored image for notification %s (%s %dx%d, %d bytes)",
                pending.notification_id,
                processed.mime,
                processed.width,
                processed.height,
                len(processed.content),
            )
        return settled

    async def _fail(self, pending: PendingNotificationImage, reason: str) -> bool:
        settled = await self._images.mark_failed(pending, error=reason)
        if settled:
            self.stats.failed += 1
            logger.warning(
                "Image for notification %s (%s) was not stored: %s",
                pending.notification_id,
                pending.ref.source_kind.value,
                reason,
            )
        return settled

    async def _announce(self, notification_id: UUID) -> None:
        """Broadcast the notification now that its image settled (its only live event)."""
        if self._broadcaster is None:
            return
        notification = await self._notifications.get(notification_id)
        if notification is None:
            return
        await self._broadcaster.publish(notification_event(notification, self._instance_fields))
