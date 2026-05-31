"""Audit log subscriber for Sleipnir.

Subscribes to every event on the bus (pattern ``"*"``) and appends each one
to the configured :class:`~sleipnir.ports.audit.AuditRepository`.

A background task runs at a configurable interval to purge events whose TTL
has elapsed.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.audit import AuditRepository
from sleipnir.ports.events import SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

#: Default TTL cleanup interval in seconds (1 hour).
DEFAULT_TTL_CLEANUP_INTERVAL_SECONDS = 3600


@dataclass
class AuditConfig:
    """Configuration for the :class:`AuditSubscriber`.

    :param enabled: When ``False`` the subscriber is a no-op.
    :param ttl_cleanup_interval_seconds: How often (in seconds) expired events
        are purged from the store.
    """

    enabled: bool = True
    ttl_cleanup_interval_seconds: int = field(default=DEFAULT_TTL_CLEANUP_INTERVAL_SECONDS)


class AuditSubscriber:
    """Subscribes to all Sleipnir events and writes them to the audit store.

    Lifecycle::

        subscriber = AuditSubscriber(bus, repo)
        await subscriber.start()
        # … system runs …
        await subscriber.stop()

    The subscriber is idempotent: calling :meth:`start` when already running
    is safe (it returns immediately).
    """

    def __init__(
        self,
        bus: SleipnirSubscriber,
        repository: AuditRepository,
        config: AuditConfig | None = None,
    ) -> None:
        self._bus = bus
        self._repository = repository
        self._config = config or AuditConfig()
        self._subscription: Subscription | None = None
        self._ttl_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        """``True`` while the subscriber is active."""
        return self._running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to the bus and start the TTL cleanup background task."""
        if not self._config.enabled:
            logger.info("Audit subscriber disabled — skipping start")
            return
        if self._running:
            return
        self._running = True
        self._subscription = await self._bus.subscribe(["*"], self._handle)
        self._ttl_task = asyncio.create_task(self._ttl_loop(), name="audit-ttl-cleanup")
        logger.info("Audit subscriber started")

    async def stop(self) -> None:
        """Unsubscribe and cancel the TTL cleanup task."""
        if not self._running:
            return
        self._running = False
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
        if self._ttl_task is not None:
            self._ttl_task.cancel()
            with suppress(asyncio.CancelledError):
                _ = await self._ttl_task
            self._ttl_task = None
        logger.info("Audit subscriber stopped")

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    async def _handle(self, event: SleipnirEvent) -> None:
        """Persist *event* to the audit repository."""
        try:
            await self._repository.append(event)
        except Exception:
            logger.exception(
                "Failed to append audit record for event %s (%s)",
                event.event_id,
                event.event_type,
            )

    # ------------------------------------------------------------------
    # TTL cleanup loop
    # ------------------------------------------------------------------

    async def _ttl_loop(self) -> None:
        """Periodically purge expired audit events."""
        while self._running:
            try:
                await asyncio.sleep(self._config.ttl_cleanup_interval_seconds)
            except asyncio.CancelledError:
                return
            if not self._running:
                return
            try:
                deleted = await self._repository.purge_expired()
                logger.info("Audit TTL cleanup: purged %d expired event(s)", deleted)
            except Exception:
                logger.exception("Audit TTL cleanup failed")
