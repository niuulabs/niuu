"""Child-task records exchanged with Ting's durable workflow ledger over A2A."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ChildLaunchRequest:
    intent_id: str
    message_id: str
    agent_id: str
    skill_id: str
    work_order: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChildTaskHandle:
    agent_id: str
    task_id: str
    context_id: str = ""


@dataclass(frozen=True)
class ChildPendingQuestion:
    request_id: str
    persona: str = ""
    question: str = ""
    reason: str = ""
    recommendation: str = ""
    attempted: tuple[str, ...] = ()

    def to_a2a_metadata(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "persona": self.persona,
            "question": self.question,
            "reason": self.reason,
            "recommendation": self.recommendation,
            "attempted": list(self.attempted),
        }


@dataclass(frozen=True)
class ChildPendingGate:
    gate_id: str
    node_id: str = ""
    label: str = ""
    condition: str = ""
    instructions: str = ""
    summary: str = ""

    def to_a2a_metadata(self) -> dict[str, Any]:
        return {
            "gateId": self.gate_id,
            "nodeId": self.node_id,
            "label": self.label,
            "condition": self.condition,
            "instructions": self.instructions,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ChildTaskObservation:
    handle: ChildTaskHandle
    state: str
    result: dict[str, Any] | None
    artifacts: tuple[dict[str, Any], ...]
    observed_at: datetime
    event_id: str = ""
    failure_kind: str | None = None
    error: str = ""
    pending_questions: tuple[ChildPendingQuestion, ...] = ()
    pending_gates: tuple[ChildPendingGate, ...] = ()
