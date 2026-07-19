"""Workflow kickoff handshake contract shared by Skuld and flock personas.

Flock workflow sessions start with Skuld publishing the initial Ting task
onto the mesh as a pub/sub outcome event. The mesh retains nothing for late
subscribers, so a kickoff published before a cold-starting persona has armed
its subscription evaporates and the whole flock idles forever.

The handshake closes that race:

1. Skuld stamps every kickoff with a per-dispatch ``workflow_kickoff_id``
   and a ``workflow_kickoff_redelivery`` attempt counter.
2. A persona that receives a kickoff immediately publishes a
   ``workflow.kickoff.acknowledged`` mesh event — before any LLM work —
   and ignores redeliveries it has already consumed (same kickoff id).
3. Skuld republishes the kickoff until acknowledged, and fails the session
   loudly after a bounded number of redeliveries.

Both sides of the handshake import this module, so the wire contract has a
single home that neither package can drift from.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Mesh topic (and payload ``event_type``) of the acknowledgement event.
WORKFLOW_KICKOFF_ACK_EVENT_TYPE = "workflow.kickoff.acknowledged"

#: Payload key carrying the per-dispatch kickoff identity.
WORKFLOW_KICKOFF_ID_KEY = "workflow_kickoff_id"

#: Payload key carrying the redelivery attempt counter (0 = first delivery).
WORKFLOW_KICKOFF_REDELIVERY_KEY = "workflow_kickoff_redelivery"

#: Payload key that marks an event as a workflow kickoff dispatch.
WORKFLOW_TRIGGER_NODE_ID_KEY = "workflow_trigger_node_id"


def is_workflow_kickoff_payload(payload: Mapping[str, object] | None) -> bool:
    """Return True when *payload* is a Skuld workflow kickoff dispatch."""
    if not payload:
        return False
    return bool(str(payload.get(WORKFLOW_TRIGGER_NODE_ID_KEY) or "").strip())


def is_workflow_kickoff_ack_payload(payload: Mapping[str, object] | None) -> bool:
    """Return True when *payload* is a kickoff acknowledgement event."""
    if not payload:
        return False
    return str(payload.get("event_type") or "").strip() == WORKFLOW_KICKOFF_ACK_EVENT_TYPE
