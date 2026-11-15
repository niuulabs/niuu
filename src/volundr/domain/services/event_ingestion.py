"""Event ingestion service — fans out raw session events to all registered sinks."""

import asyncio
import logging

from niuu.cap_telemetry import record_degraded
from volundr.domain.models import SessionEvent
from volundr.domain.ports import EventSink

logger = logging.getLogger(__name__)


class EventIngestionService:
    """Receives raw session events and fans them out to all registered sinks.

    Sink failures are isolated — a failing RabbitMQ connection doesn't
    block DB persistence. Each failure is a degraded path, logged at error and
    counted (``degraded.event_pipeline.sink.<name>`` on the health snapshot),
    never silent.
    """

    def __init__(self, sinks: list[EventSink]):
        self._sinks = sinks

    async def ingest(self, event: SessionEvent) -> None:
        """Fan out a single event to every sink concurrently."""
        results = await asyncio.gather(
            *(sink.emit(event) for sink in self._sinks),
            return_exceptions=True,
        )
        for sink, result in zip(self._sinks, results):
            if isinstance(result, Exception):
                record_degraded(
                    f"event_pipeline.sink.{sink.sink_name}",
                    f"sink {sink.sink_name} failed for event {event.id}: {result}",
                )

    async def ingest_batch(self, events: list[SessionEvent]) -> None:
        """Fan out a batch of events to every sink concurrently."""
        if not events:
            return
        results = await asyncio.gather(
            *(sink.emit_batch(events) for sink in self._sinks),
            return_exceptions=True,
        )
        for sink, result in zip(self._sinks, results):
            if isinstance(result, Exception):
                record_degraded(
                    f"event_pipeline.sink.{sink.sink_name}",
                    f"sink {sink.sink_name} failed for a batch of {len(events)} events: {result}",
                )

    async def flush_all(self) -> None:
        """Flush all sinks. Called on graceful shutdown."""
        for sink in self._sinks:
            try:
                await sink.flush()
            except Exception:
                logger.error("Failed to flush sink %s", sink.sink_name, exc_info=True)

    async def close_all(self) -> None:
        """Close all sinks. Called on application shutdown."""
        await self.flush_all()
        for sink in self._sinks:
            try:
                await sink.close()
            except Exception:
                logger.error("Failed to close sink %s", sink.sink_name, exc_info=True)

    def sink_health(self) -> dict[str, bool]:
        """Returns health status of each registered sink."""
        return {sink.sink_name: sink.healthy for sink in self._sinks}
