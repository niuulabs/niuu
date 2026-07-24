"""Flock-side acknowledgement of Skuld workflow kickoff dispatches.

The drive loop's mesh outcome handler calls :class:`WorkflowKickoffAcknowledger`
the moment a kickoff event is consumed — before any LLM work starts — so Skuld
learns the dispatch actually landed and can stop redelivering. Redelivered
kickoffs (same kickoff id) are re-acknowledged but not re-executed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from niuu.domain.workflow_kickoff import (
    WORKFLOW_KICKOFF_ACK_EVENT_TYPE,
    WORKFLOW_KICKOFF_ID_KEY,
    WORKFLOW_KICKOFF_REDELIVERY_KEY,
    WORKFLOW_TRIGGER_NODE_ID_KEY,
    is_workflow_kickoff_payload,
)
from ravn.domain.events import RavnEvent, RavnEventType

if TYPE_CHECKING:
    from ravn.ports.mesh import MeshPort

logger = logging.getLogger(__name__)

#: Acks are routing signals, not results — keep them below outcome urgency.
_ACK_URGENCY = 0.5


class WorkflowKickoffAcknowledger:
    """Acknowledge workflow kickoffs on the mesh and drop redeliveries.

    One instance lives per daemon (created when the mesh subscriptions are
    wired), so the seen-kickoff set spans every consumed event for the
    process lifetime.
    """

    def __init__(self, mesh: MeshPort, peer_id: str, persona: str) -> None:
        self._mesh = mesh
        self._peer_id = peer_id
        self._persona = persona
        self._seen_kickoff_keys: set[str] = set()

    @staticmethod
    def is_kickoff(event: RavnEvent) -> bool:
        """Return True when *event* is a Skuld workflow kickoff dispatch."""
        return is_workflow_kickoff_payload(event.payload)

    @staticmethod
    def _kickoff_key(event: RavnEvent) -> str:
        """Identity of one logical kickoff, stable across redeliveries.

        Skuld stamps each dispatch (and its redeliveries) with a unique
        kickoff id; a deliberate re-dispatch (operator resend) gets a fresh
        id and is executed again. Older Skulds send no kickoff id — fall
        back to the correlation id (the session id) for those.
        """
        payload = event.payload or {}
        kickoff_id = str(payload.get(WORKFLOW_KICKOFF_ID_KEY) or "").strip()
        if kickoff_id:
            return kickoff_id
        return str(event.correlation_id or event.root_correlation_id or "").strip()

    async def acknowledge(self, event: RavnEvent) -> bool:
        """Publish the kickoff ack; return True when this is the first delivery.

        The ack is published on every delivery — a redelivery means Skuld
        never saw the previous ack — but only the first delivery of a
        kickoff should reach the task queue.
        """
        payload = event.payload or {}
        key = self._kickoff_key(event)
        first_delivery = not key or key not in self._seen_kickoff_keys
        if key:
            self._seen_kickoff_keys.add(key)

        correlation_id = str(event.correlation_id or event.root_correlation_id or "")
        ack = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=self._peer_id,
            payload={
                "event_type": WORKFLOW_KICKOFF_ACK_EVENT_TYPE,
                WORKFLOW_KICKOFF_ID_KEY: str(payload.get(WORKFLOW_KICKOFF_ID_KEY) or ""),
                WORKFLOW_KICKOFF_REDELIVERY_KEY: payload.get(WORKFLOW_KICKOFF_REDELIVERY_KEY, 0),
                WORKFLOW_TRIGGER_NODE_ID_KEY: str(payload.get(WORKFLOW_TRIGGER_NODE_ID_KEY) or ""),
                "peer_id": self._peer_id,
                "persona": self._persona,
                "duplicate": not first_delivery,
                # Deliver through the room mesh bridge without rendering a
                # browser outcome card for every ack.
                "routing_only": True,
            },
            timestamp=datetime.now(UTC),
            urgency=_ACK_URGENCY,
            correlation_id=correlation_id,
            session_id=event.session_id,
            root_correlation_id=str(event.root_correlation_id or correlation_id),
        )
        await self._mesh.publish(ack, topic=WORKFLOW_KICKOFF_ACK_EVENT_TYPE)
        logger.info(
            "workflow_kickoff: acknowledged kickoff key=%s node=%s persona=%s duplicate=%s",
            key or "-",
            payload.get(WORKFLOW_TRIGGER_NODE_ID_KEY, ""),
            self._persona,
            not first_delivery,
        )
        return first_delivery
