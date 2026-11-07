"""Sleipnir channel adapter — publishes MeshEvents to RabbitMQ (NIU-438).

Events are wrapped in a SleipnirEnvelope and published to the ``ravn.events``
topic exchange with routing key ``ravn.<event_type>.<agent_id>``.

Connection is lazy: established on the first call to ``emit()``, not at
startup.  If RabbitMQ is unavailable the adapter swallows the error and logs
at DEBUG level so the agent never fails due to Sleipnir being down.
"""

from __future__ import annotations

import json
import logging
import socket
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from niuu.mesh.events import MeshEvent, MeshEventType
from ravn.adapters.channels._rabbitmq_base import RabbitMQPublishMixin
from ravn.domain.models import SleipnirEnvelope
from ravn.ports.channel import ChannelPort

if TYPE_CHECKING:
    from ravn.config import SleipnirConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Urgency mapping (set by the adapter, not by the event factories)
# ---------------------------------------------------------------------------

_URGENCY: dict[MeshEventType, float] = {
    MeshEventType.THOUGHT: 0.1,
    MeshEventType.TOOL_START: 0.1,
    MeshEventType.TOOL_RESULT: 0.1,
    MeshEventType.RESPONSE: 0.2,
    MeshEventType.ERROR: 0.6,
    MeshEventType.DECISION: 0.9,
    MeshEventType.TASK_COMPLETE: 0.2,  # overridden for failures below
}


def _urgency_for(event: MeshEvent) -> float:
    """Return the Sleipnir urgency hint for *event*.

    TASK_COMPLETE urgency is elevated to 0.7 when the task failed (detected
    via the ``success`` key in the event payload).
    """
    if event.type == MeshEventType.TASK_COMPLETE:
        return 0.2 if event.payload.get("success", True) else 0.7
    return _URGENCY.get(event.type, 0.2)


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------


def _serialise_envelope(envelope: SleipnirEnvelope) -> bytes:
    """Serialise *envelope* to UTF-8 JSON bytes."""
    event = envelope.event
    data = {
        "event": {
            "type": event.type,
            "source": event.source,
            "payload": event.payload,
            "timestamp": event.timestamp.isoformat(),
            "urgency": event.urgency,
            "correlation_id": event.correlation_id,
            "session_id": event.session_id,
            "task_id": event.task_id,
        },
        "source_agent": envelope.source_agent,
        "session_id": envelope.session_id,
        "task_id": envelope.task_id,
        "urgency": envelope.urgency,
        "correlation_id": envelope.correlation_id,
        "published_at": envelope.published_at.isoformat(),
    }
    return json.dumps(data).encode("utf-8")


# ---------------------------------------------------------------------------
# SleipnirChannel
# ---------------------------------------------------------------------------


class SleipnirChannel(RabbitMQPublishMixin, ChannelPort):
    """Publishes MeshEvents to RabbitMQ via the Sleipnir event backbone.

    Parameters
    ----------
    config:
        Sleipnir section from Ravn settings.
    session_id:
        Session identifier forwarded to the envelope.
    task_id:
        Drive-loop task ID (NIU-539 integration point), or ``None`` for
        interactive turns.
    """

    _log_prefix = "sleipnir"

    def __init__(
        self,
        config: SleipnirConfig,
        *,
        session_id: str,
        task_id: str | None = None,
    ) -> None:
        self._config = config
        self._session_id = session_id
        self._task_id = task_id
        self._agent_id = config.agent_id or socket.gethostname()
        self._init_publish_state()

    # ------------------------------------------------------------------
    # ChannelPort interface
    # ------------------------------------------------------------------

    async def emit(self, event: MeshEvent) -> None:
        """Emit *event* to RabbitMQ. Never raises — failures are logged."""
        envelope = SleipnirEnvelope(
            event=event,
            source_agent=self._agent_id,
            session_id=self._session_id,
            task_id=self._task_id,
            urgency=_urgency_for(event),
            correlation_id=event.correlation_id,
            published_at=datetime.now(UTC),
        )
        routing_key = f"ravn.{envelope.event.type}.{self._agent_id}"
        body = _serialise_envelope(envelope)
        await self._publish_to_exchange(routing_key, body)
