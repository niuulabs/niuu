"""RabbitMQ event publisher — publishes drive-loop lifecycle events to RabbitMQ."""

from __future__ import annotations

import json
import logging
import socket
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from niuu.mesh.events import MeshEvent
from ravn.adapters.channels._rabbitmq_base import RabbitMQPublishMixin
from ravn.ports.event_publisher import EventPublisherPort

if TYPE_CHECKING:
    from ravn.config import SleipnirConfig

logger = logging.getLogger(__name__)


def _serialise_event(event: MeshEvent) -> bytes:
    """Serialise *event* to UTF-8 JSON bytes."""
    data = {
        "type": event.type,
        "source": event.source,
        "payload": event.payload,
        "timestamp": event.timestamp.isoformat(),
        "urgency": event.urgency,
        "correlation_id": event.correlation_id,
        "session_id": event.session_id,
        "task_id": event.task_id,
        "published_at": datetime.now(UTC).isoformat(),
    }
    return json.dumps(data).encode("utf-8")


class RabbitMQEventPublisher(RabbitMQPublishMixin, EventPublisherPort):
    """Publishes drive-loop lifecycle events to RabbitMQ.

    Shares connection logic with SleipnirChannel (NIU-438) but operates
    independently — no session_id, no correlation_id from a turn.

    Routing key: ``ravn.system.<event_type>.<agent_id>``
    Exchange: ``ravn.events`` (same as SleipnirChannel — already declared durable)

    Connection is lazy: established on the first call to ``publish()``.
    Never raises — failures are logged at DEBUG level.
    """

    _log_prefix = "rabbitmq_publisher"

    def __init__(self, config: SleipnirConfig) -> None:
        self._config = config
        self._agent_id = config.agent_id or socket.gethostname()
        self._init_publish_state()

    async def publish(self, event: MeshEvent) -> None:
        """Publish *event* to RabbitMQ. Never raises — failures are logged."""
        routing_key = f"ravn.system.{event.type}.{self._agent_id}"
        body = _serialise_event(event)
        await self._publish_to_exchange(routing_key, body)

    async def close(self) -> None:
        """Close the RabbitMQ connection."""
        await self._invalidate()
