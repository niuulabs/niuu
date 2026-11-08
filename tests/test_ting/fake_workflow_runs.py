"""A recording stand-in for ``WorkflowRuns`` in tests of the routes that start and decide runs.

It records what the routes hand the run service (launches, gate decisions, cancels) and
serves the gates a test sets up, so a route test asserts the route's behaviour without
driving the step engine. The engine itself is covered by ``test_step_engine*.py`` and
the end-to-end scenario test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ting.domain.models import WorkflowCampaign
from ting.domain.workflow_steps import (
    GateActor,
    GateDecision,
    RunEvent,
    RunState,
    WorkflowStep,
)


@dataclass
class GateDecisionRecord:
    campaign_id: UUID
    node_id: str
    decision: GateDecision
    by: GateActor
    note: str


@dataclass
class FakeWorkflowRuns:
    """What a route asks of the run service, recorded."""

    launched: list[tuple[WorkflowCampaign, dict[str, Any]]] = field(default_factory=list)
    decisions: list[GateDecisionRecord] = field(default_factory=list)
    canceled: list[tuple[WorkflowCampaign, str]] = field(default_factory=list)
    gates_by_run: dict[UUID, list[dict[str, Any]]] = field(default_factory=dict)
    events_by_run: dict[UUID, list[RunEvent]] = field(default_factory=dict)
    steps_by_run: dict[UUID, list[WorkflowStep]] = field(default_factory=dict)
    launch_error: Exception | None = None
    gate_error: Exception | None = None

    async def launch(
        self,
        campaign: WorkflowCampaign,
        payload: dict[str, Any] | None = None,
        *,
        trigger_node_id: str | None = None,
    ) -> RunState:
        if self.launch_error is not None:
            raise self.launch_error
        self.launched.append((campaign, dict(payload or {})))
        return self._state(campaign.id)

    async def state(self, run_id: UUID) -> RunState:
        return self._state(run_id)

    async def gates(self, campaign: WorkflowCampaign) -> list[dict[str, Any]]:
        return list(self.gates_by_run.get(campaign.id, []))

    async def pending_gates(self, campaign: WorkflowCampaign) -> list[dict[str, Any]]:
        return [
            gate
            for gate in self.gates_by_run.get(campaign.id, [])
            if gate.get("status") == "pending"
        ]

    async def resolve_gate(
        self,
        campaign: WorkflowCampaign,
        node_id: str,
        decision: GateDecision,
        *,
        by: GateActor,
        note: str = "",
    ) -> RunState:
        if self.gate_error is not None:
            raise self.gate_error
        self.decisions.append(GateDecisionRecord(campaign.id, node_id, decision, by, note))
        return self._state(campaign.id)

    async def cancel(self, campaign: WorkflowCampaign, reason: str) -> RunState:
        self.canceled.append((campaign, reason))
        return self._state(campaign.id)

    def _state(self, run_id: UUID) -> RunState:
        events = tuple(self.events_by_run.get(run_id, ()))
        return RunState(
            run_id=run_id,
            steps=tuple(self.steps_by_run.get(run_id, ())),
            events=events,
        )
