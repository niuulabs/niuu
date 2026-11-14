"""RabbitMQ event sink — publishes session events to an AMQP exchange.

Events are published as JSON messages with routing keys derived from
the event type, enabling downstream consumers to subscribe selectively:

  Exchange: volundr.events (topic)
  Routing key: session.{event_type}  (e.g. session.message_assistant)

Message properties:
  - content_type: application/json
  - delivery_mode: 2 (persistent)
  - headers: session_id, event_type, sequence, model
"""

import json
import logging

from volundr.domain.models import SessionEvent
from volundr.domain.ports import EventSink

logger = logging.getLogger(__name__)


class RabbitMQEventSink(EventSink):
    """AMQP adapter for the session event pipeline.

    Uses aio_pika for async RabbitMQ communication. The caller provides
    a connection URL; the sink manages its own channel and exchange.
    """

    def __init__(
        self,
        url: str,
        exchange_name: str = "volundr.events",
        exchange_type: str = "topic",
    ):
        self._url = url
        self._exchange_name = exchange_name
        self._exchange_type = exchange_type
        self._connection = None
        self._channel = None
        self._exchange = None
        self._healthy = False

    async def connect(self) -> None:
        """Establish connection, channel, and declare exchange."""
        import aio_pika

        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        self._exchange = await self._channel.declare_exchange(
            self._exchange_name,
            aio_pika.ExchangeType(self._exchange_type),
            durable=True,
        )
        self._healthy = True
        logger.info(
            "RabbitMQ sink connected: exchange=%s type=%s",
            self._exchange_name,
            self._exchange_type,
        )

    # -- EventSink interface --------------------------------------------------

    async def emit(self, event: SessionEvent) -> None:
        self._require_connected()
        await self._publish(event)

    async def emit_batch(self, events: list[SessionEvent]) -> None:
        self._require_connected()
        for event in events:
            await self._publish(event)

    async def flush(self) -> None:
        # AMQP publishes are immediate — nothing to flush
        pass

    async def close(self) -> None:
        self._healthy = False
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._channel = None
            self._exchange = None
        logger.info("RabbitMQ sink closed")

    @property
    def sink_name(self) -> str:
        return "rabbitmq"

    @property
    def healthy(self) -> bool:
        return self._healthy

    # -- Internal -------------------------------------------------------------

    def _require_connected(self) -> None:
        """Refuse to emit on a sink that is not connected, rather than drop the events."""
        if self._healthy and self._exchange is not None:
            return
        raise RuntimeError(
            "RabbitMQ event sink is not connected (call connect() first, or it was closed); "
            "events were not published"
        )

    async def _publish(self, event: SessionEvent) -> None:
        import aio_pika

        routing_key = f"session.{event.event_type.value}"
        body = self._serialize(event)

        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={
                "session_id": str(event.session_id),
                "event_type": event.event_type.value,
                "sequence": event.sequence,
                "model": event.model or "",
            },
        )
        await self._exchange.publish(message, routing_key=routing_key)

    @staticmethod
    def _serialize(event: SessionEvent) -> bytes:
        payload = {
            "id": str(event.id),
            "session_id": str(event.session_id),
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "data": event.data,
            "sequence": event.sequence,
            "tokens_in": event.tokens_in,
            "tokens_out": event.tokens_out,
            "cost": float(event.cost) if event.cost is not None else None,
            "duration_ms": event.duration_ms,
            "model": event.model,
        }
        return json.dumps(payload).encode("utf-8")
