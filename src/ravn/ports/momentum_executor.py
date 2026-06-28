"""Momentum executor handoff port."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

HandoffStatus = Literal["completed", "failed", "blocked"]
HandoffFollowUp = Literal["none", "reflect", "ask_human", "retry"]


@dataclass(frozen=True)
class MomentumExecutorInput:
    brief_ref: str
    brief_id: str
    input_frame: str
    suggested_context: str = ""


@dataclass(frozen=True)
class MomentumExecutorOutput:
    executor_label: str
    executor_context: str
    status: HandoffStatus
    summary: str
    output: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    produced_refs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    follow_up_recommended: HandoffFollowUp = "none"
    raw_metadata: dict[str, object] = field(default_factory=dict)


class MomentumExecutorHandoffPort(Protocol):
    """Hands one bounded Momentum delegation brief to a configured executor."""

    async def handoff(self, handoff_input: MomentumExecutorInput) -> MomentumExecutorOutput: ...
