"""Sleipnir (RabbitMQ) cost event emitter.

Publishes bifrost cost events to a RabbitMQ topic exchange using aio-pika.

Exchange: bifrost.events  (topic, durable)
Routing keys:
  - bifrost.cost.request_completed
  - bifrost.cost.budget_warning

aio-pika is an optional dependency (install the [rabbitmq] extra).
The adapter connects lazily on first emit and drops events with a warning
when the broker is unreachable.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from dataclasses import asdict

from bifrost.ports.events import (
    BudgetDegradedEvent,
    BudgetWarningEvent,
    CostEventEmitter,
    RequestCompletedEvent,
)

logger = logging.getLogger(__name__)


class SleipnirEventEmitter(CostEventEmitter):
    """AMQP adapter that publishes cost events to the Sleipnir RabbitMQ exchange."""

    def __init__(
        self,
        url: str,
        exchange: str = "bifrost.events",
        exchange_type: str = "topic",
    ) -> None:
        self._url = url
        self._exchange_name = exchange
        self._exchange_type = exchange_type
        self._connection = None
        self._channel = None
        self._exchange = None
        self._healthy = False

    async def _ensure_connected(self) -> bool:
        """Lazily establish the AMQP connection on first use.

        Returns True when the connection is healthy, False on error.
        """
        if self._healthy:
            return True
        # Close stale connection before reconnecting to avoid resource leaks.
        if self._connection is not None:
            with suppress(Exception):
                await self._connection.close()
            self._connection = None
            self._channel = None
            self._exchange = None
        try:
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
                "Sleipnir emitter connected: exchange=%s type=%s",
                self._exchange_name,
                self._exchange_type,
            )
        except Exception as exc:
            logger.error("Sleipnir emitter failed to connect: %s", exc)
            self._healthy = False
        return self._healthy

    async def emit_request_completed(self, event: RequestCompletedEvent) -> None:
        await self._publish(event.type, asdict(event))

    async def emit_budget_warning(self, event: BudgetWarningEvent) -> None:
        await self._publish(event.type, asdict(event))

    async def emit_budget_degraded(self, event: BudgetDegradedEvent) -> None:
        await self._publish(event.type, asdict(event))

    async def close(self) -> None:
        self._healthy = False
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._channel = None
            self._exchange = None
        logger.info("Sleipnir emitter closed")

    async def _publish(self, routing_key: str, payload: dict) -> None:
        if not await self._ensure_connected():
            logger.warning("Sleipnir emitter not connected, dropping event type=%s", routing_key)
            return
        try:
            import aio_pika

            message = aio_pika.Message(
                body=json.dumps(payload).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={"event_type": routing_key},
            )
            await self._exchange.publish(message, routing_key=routing_key)
        except Exception as exc:
            logger.error("Sleipnir publish failed for %s: %s", routing_key, exc)
            self._healthy = False
