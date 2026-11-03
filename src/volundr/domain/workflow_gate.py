"""Workflow gates as Forge's notification feed sees them.

A gate lives in a session's broker, and Forge's durable log holds its every state: one
``workflow_gate`` frame when it opens and one when it is decided, wherever the decision
was made. Forge projects those frames: a gate that waits for a person raises one
attention item, and its decided frame settles that item. This module is the vocabulary
of that projection: the gate as a frame carries it and the key that keeps it idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from niuu.domain.workflow_gate import (
    AGENT_ANSWERABLE_GATE_MODE,
    SILENT_GATE_BEHAVIOR,
    WORKFLOW_GATE_FRAME,
    WORKFLOW_GATE_PENDING,
)

#: ``input_kind`` of a gate's notification. Unlike a session's ``question`` /
#: ``confirmation`` / ``permission``, it is not answered on the session socket but on
#: ``POST /sessions/{id}/workflow/gates/{request_id}/resolve``, so clients route on it.
WORKFLOW_GATE_INPUT_KIND = "workflow_gate"


@dataclass(frozen=True)
class WorkflowGateFrame:
    """The state of one gate at one moment, as a log frame carries it."""

    id: str
    node_id: str
    activation_id: str
    label: str
    status: str
    mode: str
    pending_behavior: str
    summary: str
    instructions: str

    @property
    def pending(self) -> bool:
        return self.status == WORKFLOW_GATE_PENDING

    @property
    def waits_for_person(self) -> bool:
        """Whether someone must be told: not an agent's gate, and not a silent one."""
        return (
            self.mode != AGENT_ANSWERABLE_GATE_MODE
            and self.pending_behavior != SILENT_GATE_BEHAVIOR
        )

    @property
    def title(self) -> str:
        return self.label or self.node_id

    @property
    def prompt(self) -> str:
        """What the person is shown; what they decide on, else how to decide it."""
        return self.summary or self.instructions

    def notification_metadata(self) -> dict[str, str]:
        return {
            "input_kind": WORKFLOW_GATE_INPUT_KIND,
            "request_id": self.id,
            "workflow_gate_node_id": self.node_id,
            "workflow_activation_id": self.activation_id,
        }


def is_workflow_gate_frame(payload: dict) -> bool:
    return isinstance(payload, dict) and payload.get("type") == WORKFLOW_GATE_FRAME


def workflow_gate_from_log_payload(payload: dict) -> WorkflowGateFrame:
    """Read the gate out of a ``workflow_gate`` frame.

    Raises ``ValueError`` when the frame does not name a gate and its status.
    """
    gate: Any = payload.get("gate")
    if not isinstance(gate, dict):
        raise ValueError("A workflow_gate frame needs a gate object")
    fields = {name: str(gate.get(name) or "").strip() for name in ("id", "status")}
    if not all(fields.values()):
        raise ValueError("A workflow_gate frame needs a gate id and status")
    return WorkflowGateFrame(
        **fields,
        node_id=str(gate.get("node_id") or ""),
        activation_id=str(gate.get("activation_id") or ""),
        label=str(gate.get("label") or "").strip(),
        mode=str(gate.get("mode") or ""),
        pending_behavior=str(gate.get("pending_behavior") or ""),
        summary=str(gate.get("summary") or "").strip(),
        instructions=str(gate.get("instructions") or "").strip(),
    )


def workflow_gate_dedupe_key(session_id: UUID, gate_id: str) -> str:
    """One notification per opening of a gate: its id names the attempt.

    A gate sent back and opened again is a new attempt, so it asks again; a restarted
    session restores the same gate, so a retried or replayed frame records nothing.
    """
    return f"workflow-gate:{session_id}:{gate_id}"
