"""Sleipnir event bridge for Ting — mirrors TingEvents onto the Sleipnir bus.

:class:`SleipnirEventBridge` is a decorator around :class:`~ting.ports.event_bus.EventBusPort`.
All calls are forwarded to the inner bus unchanged; in addition, each emitted
:class:`~ting.ports.event_bus.TingEvent` is mapped to a
:class:`~sleipnir.domain.events.SleipnirEvent`
and published on the Sleipnir bus.

This enables Skuld (and other Sleipnir subscribers) to receive Ting activity
events (saga progression, run state changes, dispatcher health) without
polling Ting's SSE endpoint.

Event mapping
-------------
``dispatcher.state``      → ``ting.task.started``
``saga.created``          → ``ting.saga.created``
``saga.step``             → ``ting.saga.step``
``saga.completed``        → ``ting.saga.complete``
``saga.failed``           → ``ting.saga.failed``
``run.state_changed``    → ``ting.task.*``  (derived from new_status)
``notification.*``        → silently dropped (internal)
"""

from __future__ import annotations

import asyncio
import logging

from sleipnir.domain.events import SleipnirEvent
from sleipnir.domain.registry import (
    TING_SAGA_COMPLETE,
    TING_SAGA_CREATED,
    TING_SAGA_FAILED,
    TING_SAGA_STEP,
    TING_TASK_CANCELLED,
    TING_TASK_COMPLETE,
    TING_TASK_FAILED,
    TING_TASK_QUEUED,
    TING_TASK_STARTED,
)
from sleipnir.ports.events import SleipnirPublisher
from ting.ports.event_bus import EventBusPort, TingEvent

logger = logging.getLogger(__name__)

_SOURCE = "ting:event-bridge"

# Map saga.* TingEvent types to Sleipnir constants.
_SAGA_TYPE_MAP: dict[str, str] = {
    "saga.created": TING_SAGA_CREATED,
    "saga.step": TING_SAGA_STEP,
    "saga.completed": TING_SAGA_COMPLETE,
    "saga.failed": TING_SAGA_FAILED,
}

# Map run new_status values to Sleipnir task event types.
_RUN_STATUS_MAP: dict[str, str] = {
    "QUEUED": TING_TASK_QUEUED,
    "RUNNING": TING_TASK_STARTED,
    "MERGED": TING_TASK_COMPLETE,
    "FAILED": TING_TASK_FAILED,
    "CANCELLED": TING_TASK_CANCELLED,
}


class SleipnirEventBridge(EventBusPort):
    """Decorator around :class:`EventBusPort` that also publishes to Sleipnir.

    All :class:`EventBusPort` operations are delegated to the *inner* bus.
    ``emit`` additionally publishes a :class:`SleipnirEvent` to *publisher*;
    publication errors are logged and swallowed to protect the inner bus.

    Args:
        inner: The actual :class:`EventBusPort` implementation.
        publisher: Sleipnir publisher to mirror events onto.
    """

    def __init__(self, inner: EventBusPort, publisher: SleipnirPublisher) -> None:
        self._inner = inner
        self._publisher = publisher

    # ------------------------------------------------------------------
    # EventBusPort delegation
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[TingEvent]:
        return self._inner.subscribe()

    def unsubscribe(self, q: asyncio.Queue[TingEvent]) -> None:
        self._inner.unsubscribe(q)

    async def emit(self, event: TingEvent) -> None:
        """Broadcast to inner bus *and* publish to Sleipnir."""
        await self._inner.emit(event)
        await self._mirror_to_sleipnir(event)

    def get_snapshot(self) -> list[TingEvent]:
        return self._inner.get_snapshot()

    def get_log(self, n: int) -> list[TingEvent]:
        return self._inner.get_log(n)

    @property
    def client_count(self) -> int:
        return self._inner.client_count

    @property
    def at_capacity(self) -> bool:
        return self._inner.at_capacity

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _mirror_to_sleipnir(self, event: TingEvent) -> None:
        """Map *event* to a Sleipnir event and publish; swallow errors."""
        sleipnir_event = self._to_sleipnir(event)
        if sleipnir_event is None:
            return
        try:
            await self._publisher.publish(sleipnir_event)
        except Exception:
            logger.error(
                "SleipnirEventBridge: failed to publish %s (%s) to Sleipnir",
                event.event,
                event.id,
                exc_info=True,
            )

    def _to_sleipnir(self, event: TingEvent) -> SleipnirEvent | None:
        """Return a mapped :class:`SleipnirEvent` or ``None`` if not forwarded."""
        owner_id = event.owner_id or None

        if event.event in _SAGA_TYPE_MAP:
            return self._map_saga(event, _SAGA_TYPE_MAP[event.event], owner_id)

        if event.event == "run.state_changed":
            return self._map_run(event, owner_id)

        if event.event == "dispatcher.state":
            return self._map_dispatcher_state(event, owner_id)

        return None

    def _map_saga(
        self,
        event: TingEvent,
        sleipnir_type: str,
        owner_id: str | None,
    ) -> SleipnirEvent:
        saga_id = str(event.data.get("saga_id", ""))
        saga_name = str(event.data.get("name", ""))
        return SleipnirEvent(
            event_type=sleipnir_type,
            source=_SOURCE,
            payload={"owner_id": owner_id or "", **event.data},
            summary=f"Saga {event.event.split('.')[-1]}: {saga_name or saga_id}",
            urgency=0.6,
            domain="code",
            timestamp=event.timestamp,
            correlation_id=saga_id or None,
            tenant_id=owner_id,
        )

    def _map_run(self, event: TingEvent, owner_id: str | None) -> SleipnirEvent | None:
        new_status = str(event.data.get("new_status", "")).upper()
        sleipnir_type = _RUN_STATUS_MAP.get(new_status)
        if sleipnir_type is None:
            return None
        run_id = str(event.data.get("run_id", ""))
        return SleipnirEvent(
            event_type=sleipnir_type,
            source=_SOURCE,
            payload={"owner_id": owner_id or "", **event.data},
            summary=f"Run {new_status.lower()}: {run_id}",
            urgency=0.5,
            domain="code",
            timestamp=event.timestamp,
            correlation_id=run_id or None,
            tenant_id=owner_id,
        )

    def _map_dispatcher_state(self, event: TingEvent, owner_id: str | None) -> SleipnirEvent:
        return SleipnirEvent(
            event_type=TING_TASK_STARTED,
            source=_SOURCE,
            payload={"owner_id": owner_id or "", **event.data},
            summary="Dispatcher state updated",
            urgency=0.3,
            domain="infrastructure",
            timestamp=event.timestamp,
            tenant_id=owner_id,
        )


class TingSleipnirBridge:
    """Lifecycle wrapper around :class:`SleipnirEventBridge` for use in main.py.

    Runs as a background asyncio task: subscribes to *event_bus*, delegates
    each :class:`TingEvent` to :class:`SleipnirEventBridge` for mapping and
    publication.  This avoids replacing the ``event_bus`` reference already
    wired into SSE endpoints and dependency injection.

    Usage::

        bridge = TingSleipnirBridge(event_bus=bus, publisher=sleipnir)
        await bridge.start()
        # ... application runs ...
        await bridge.stop()
    """

    def __init__(self, event_bus: EventBusPort, publisher: SleipnirPublisher) -> None:
        self._event_bus = event_bus
        self._inner = SleipnirEventBridge(inner=event_bus, publisher=publisher)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background mirroring task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="ting-sleipnir-bridge")
        logger.info("TingSleipnirBridge started")

    async def stop(self) -> None:
        """Cancel the mirroring task and clean up."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("TingSleipnirBridge stopped")

    @property
    def is_running(self) -> bool:
        """True when the background task is active."""
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        """Subscribe to event_bus and mirror events to Sleipnir until cancelled."""
        q = self._event_bus.subscribe()
        try:
            while True:
                event: TingEvent = await q.get()
                await self._inner._mirror_to_sleipnir(event)
        except asyncio.CancelledError:
            pass
        finally:
            self._event_bus.unsubscribe(q)
