"""OpenTelemetry decorators for Ravn's shared Sleipnir event-bus port."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from niuu.observability import get_observability
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import EventHandler, SleipnirPublisher, SleipnirSubscriber, Subscription


class ObservedSleipnirBus(SleipnirPublisher, SleipnirSubscriber):
    """Trace publish/consume boundaries while preserving the configured bus."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    async def start(self) -> None:
        start = getattr(self._delegate, "start", None)
        if start is not None:
            await start()

    async def stop(self) -> None:
        stop = getattr(self._delegate, "stop", None)
        if stop is not None:
            await stop()

    async def publish(self, event: SleipnirEvent) -> None:
        telemetry = get_observability()
        attributes = _event_attributes(event, direction="publish")
        metric_attributes = _event_metric_attributes(event, direction="publish")
        started = monotonic()
        with telemetry.span(
            f"publish {event.event_type}",
            attributes=attributes,
            carrier=event.trace_context,
        ) as span:
            event.trace_context = telemetry.inject()
            telemetry.event("ravn.event.publish", attributes=attributes, content=event.payload)
            try:
                await self._delegate.publish(event)
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__)
                telemetry.count(
                    "ravn.event.bus.operations",
                    attributes={**metric_attributes, "error.type": type(exc).__name__},
                )
                raise
            finally:
                telemetry.duration(
                    "ravn.event.bus.duration",
                    monotonic() - started,
                    attributes=metric_attributes,
                    description="Duration of a Sleipnir event-bus operation.",
                )
            telemetry.count("ravn.event.bus.operations", attributes=metric_attributes)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        if not events:
            return
        telemetry = get_observability()
        attributes = {
            "messaging.system": "sleipnir",
            "messaging.operation.name": "publish",
            "messaging.batch.message_count": len(events),
            "ravn.event.direction": "publish",
        }
        metric_attributes = {
            "messaging.system": "sleipnir",
            "messaging.operation.name": "publish",
            "ravn.event.direction": "publish",
        }
        started = monotonic()
        with telemetry.span("publish sleipnir batch", attributes=attributes) as span:
            carrier = telemetry.inject()
            for event in events:
                event.trace_context = dict(carrier)
                telemetry.event(
                    "ravn.event.publish",
                    attributes=_event_attributes(event, direction="publish"),
                    content=event.payload,
                )
            try:
                await self._delegate.publish_batch(events)
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__)
                telemetry.count(
                    "ravn.event.bus.operations",
                    value=len(events),
                    attributes={**metric_attributes, "error.type": type(exc).__name__},
                )
                raise
            finally:
                telemetry.duration(
                    "ravn.event.bus.duration",
                    monotonic() - started,
                    attributes=metric_attributes,
                    description="Duration of a Sleipnir event-bus operation.",
                )
            telemetry.count(
                "ravn.event.bus.operations",
                value=len(events),
                attributes=metric_attributes,
            )

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        handler_name = _handler_name(handler)

        async def observed_handler(event: SleipnirEvent) -> None:
            telemetry = get_observability()
            attributes = {
                **_event_attributes(event, direction="consume"),
                "ravn.event.handler": handler_name,
            }
            metric_attributes = {
                **_event_metric_attributes(event, direction="consume"),
                "ravn.event.handler": handler_name,
            }
            started = monotonic()
            with telemetry.span(
                f"process {event.event_type}",
                attributes=attributes,
                carrier=event.trace_context,
            ) as span:
                telemetry.event(
                    "ravn.event.consume",
                    attributes=attributes,
                    content=event.payload,
                )
                try:
                    await handler(event)
                except Exception as exc:
                    telemetry.mark_error(span, type(exc).__name__)
                    telemetry.count(
                        "ravn.event.bus.operations",
                        attributes={**metric_attributes, "error.type": type(exc).__name__},
                    )
                    raise
                finally:
                    telemetry.duration(
                        "ravn.event.bus.duration",
                        monotonic() - started,
                        attributes=metric_attributes,
                        description="Duration of a Sleipnir event-bus operation.",
                    )
                telemetry.count("ravn.event.bus.operations", attributes=metric_attributes)

        return await self._delegate.subscribe(event_types, observed_handler)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def _event_attributes(event: SleipnirEvent, *, direction: str) -> dict[str, Any]:
    return {
        "messaging.system": "sleipnir",
        "messaging.operation.name": direction,
        "messaging.message.id": event.event_id,
        "ravn.event.type": event.event_type,
        "ravn.event.source": event.source,
        "ravn.event.direction": direction,
        "ravn.correlation.id": event.correlation_id or "",
        "ravn.causation.id": event.causation_id or "",
    }


def _event_metric_attributes(event: SleipnirEvent, *, direction: str) -> dict[str, Any]:
    """Return bounded labels; per-message identifiers belong only in traces."""
    return {
        "messaging.system": "sleipnir",
        "messaging.operation.name": direction,
        "ravn.event.type": event.event_type,
        "ravn.event.source": event.source,
        "ravn.event.direction": direction,
    }


def _handler_name(handler: Callable[[SleipnirEvent], Awaitable[None]]) -> str:
    owner = getattr(handler, "__self__", None)
    owner_name = type(owner).__name__ if owner is not None else ""
    function_name = str(getattr(handler, "__name__", type(handler).__name__))
    return f"{owner_name}.{function_name}" if owner_name else function_name


__all__ = ["ObservedSleipnirBus"]
