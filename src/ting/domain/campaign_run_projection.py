"""A workflow campaign's state, read off the step engine's ledger.

A campaign is the user-facing record of a run. While a run is on the step engine its
state is not inferred from session activity: it is the ledger. This module projects a
``RunState`` onto the fields the rest of Ting reads from a campaign:

* ``status`` (pending, running, blocked on a person, completed or failed);
* ``active_stage_id`` and ``stage_state`` (the stage rail);
* the open and answered gates (``run_gates``), in the dict shape the board and the
  gate API already read;
* how the run ended, taken from the engine's own terminal events.

It decides nothing about the run; it only reads what the engine recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ting.domain.campaign_stages import (
    FAILURE_ERROR_KEY,
    FAILURE_STAGE_KEY,
    render_stage_state,
    workflow_stages,
)
from ting.domain.models import CampaignStageState, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.workflow_board import (
    GATE_STATUS_APPROVED,
    GATE_STATUS_CHANGES_REQUESTED,
    GATE_STATUS_PENDING,
    node_label,
)
from ting.domain.workflow_steps import (
    EVENT_RUN_CANCELED,
    EVENT_RUN_COMPLETED,
    EVENT_RUN_FAILED,
    GateDecision,
    RunState,
    StepKind,
    StepState,
    WaitingOnKind,
    WorkflowStep,
)

#: Campaign metadata key that lists the gates a run is waiting on.
PENDING_GATES_KEY = "pending_workflow_gates"
#: Campaign metadata key that lists the open steps stuck detection has flagged.
STUCK_STEPS_KEY = "stuck_workflow_steps"
#: Campaign metadata key that records a run canceled through the engine.
CANCELED_KEY = "run_canceled"

_GATE_STATUS_BY_DECISION = {
    GateDecision.APPROVE.value: GATE_STATUS_APPROVED,
    GateDecision.REQUEST_CHANGES.value: GATE_STATUS_CHANGES_REQUESTED,
}


@dataclass(frozen=True)
class RunProjection:
    """What a campaign should say about a run, given the run's ledger."""

    status: WorkflowCampaignStatus
    active_stage_id: str | None
    stage_state: list[CampaignStageState]
    metadata: dict[str, Any]
    #: The reason the run failed or was canceled, when it did.
    failure: str = ""

    def differs_from(self, campaign: WorkflowCampaign) -> bool:
        return (
            self.status != campaign.status
            or self.active_stage_id != campaign.active_stage_id
            or self.stage_state != campaign.stage_state
            or self.metadata != campaign.metadata
        )


def run_gates(campaign: WorkflowCampaign, state: RunState) -> list[dict[str, Any]]:
    """The run's gates, newest first: the one open, and those a person has answered.

    The ``id`` of a gate is its node id: a run has at most one open request per gate
    node, and ``resolve_gate`` names the node.
    """
    gates = [
        _gate_dict(campaign, state, step)
        for step in state.steps
        if step.kind is StepKind.GATE and step.state in _GATE_REPORTED_STATES
    ]
    gates.sort(key=lambda gate: (gate["requested_at"], gate["activation_id"]), reverse=True)
    return gates


_GATE_REPORTED_STATES = frozenset({StepState.WAITING, StepState.DONE})


def open_gates(campaign: WorkflowCampaign, state: RunState) -> list[dict[str, Any]]:
    return [gate for gate in run_gates(campaign, state) if gate["status"] == GATE_STATUS_PENDING]


def project_run(campaign: WorkflowCampaign, state: RunState, *, now: datetime) -> RunProjection:
    """Project a run's ledger onto the campaign that records it."""
    status = _status(state)
    metadata = dict(campaign.metadata)
    gates = open_gates(campaign, state)
    if gates:
        metadata[PENDING_GATES_KEY] = gates
    else:
        metadata.pop(PENDING_GATES_KEY, None)
    stuck = stuck_steps(state)
    if stuck:
        metadata[STUCK_STEPS_KEY] = stuck
    else:
        metadata.pop(STUCK_STEPS_KEY, None)

    failure = _failure(state) if status is WorkflowCampaignStatus.FAILED else ""
    failed_stage = _failed_stage(state) if failure else None
    if failure:
        metadata[FAILURE_ERROR_KEY] = failure
        if failed_stage:
            metadata[FAILURE_STAGE_KEY] = failed_stage
    if any(event.event_type == EVENT_RUN_CANCELED for event in state.events):
        metadata[CANCELED_KEY] = True

    stages = workflow_stages(campaign.workflow_snapshot)
    completed = (
        [stage.id for stage in stages]
        if status is WorkflowCampaignStatus.COMPLETED
        else _completed_stages(state)
    )
    stage_state = render_stage_state(
        stages,
        completed,
        status=status,
        previous=campaign.stage_state,
        now=now,
        failed_stage_id=failed_stage,
        failure_reason=failure or None,
    )
    return RunProjection(
        status=status,
        active_stage_id=_active_stage(state, failed_stage),
        stage_state=stage_state,
        metadata=metadata,
        failure=failure,
    )


def stuck_steps(state: RunState) -> list[dict[str, Any]]:
    """The run's open steps that stuck detection flagged, oldest flag first.

    One entry per live flag, so a step stuck in two ways appears twice. A flag leaves
    the list when its step closes or the condition clears.
    """
    steps = {step.id: step for step in state.steps}
    flagged = sorted(state.live_flags(), key=lambda flag: (flag.raised_at, flag.kind.value))
    return [
        {
            "activation_id": str(flag.activation_id),
            "node_id": steps[flag.activation_id].node_id,
            "member": steps[flag.activation_id].member,
            "attempt": steps[flag.activation_id].attempt,
            "session_id": steps[flag.activation_id].session_id,
            "kind": flag.kind.value,
            "since": flag.since.isoformat(),
            "raised_at": flag.raised_at.isoformat(),
            "detail": flag.detail,
        }
        for flag in flagged
    ]


def _status(state: RunState) -> WorkflowCampaignStatus:
    if not state.launched:
        return WorkflowCampaignStatus.PENDING
    for event in reversed(state.events):
        if event.event_type == EVENT_RUN_COMPLETED:
            return WorkflowCampaignStatus.COMPLETED
        if event.event_type in {EVENT_RUN_FAILED, EVENT_RUN_CANCELED}:
            return WorkflowCampaignStatus.FAILED
    if any(
        step.state is StepState.WAITING
        and step.waiting_on is not None
        and step.waiting_on.kind is WaitingOnKind.PERSON
        for step in state.steps
    ):
        return WorkflowCampaignStatus.BLOCKED
    return WorkflowCampaignStatus.RUNNING


def _failure(state: RunState) -> str:
    for event in reversed(state.events):
        if event.event_type == EVENT_RUN_FAILED:
            return str(event.payload.get("reason") or "the run failed")
        if event.event_type == EVENT_RUN_CANCELED:
            return str(event.payload.get("reason") or "the run was canceled")
    return ""


def _failed_stage(state: RunState) -> str | None:
    failed = [step for step in state.steps if step.state is StepState.FAILED]
    if not failed:
        return None
    return max(failed, key=lambda step: step.closed_at or step.created_at).node_id


def _active_stage(state: RunState, failed_stage: str | None) -> str | None:
    if state.finished:
        return failed_stage
    live = [step for step in state.steps if step.state in _LIVE_STATES]
    if not live:
        return None
    return min(live, key=lambda step: step.activated_at or step.created_at).node_id


_LIVE_STATES = frozenset({StepState.ACTIVE, StepState.WAITING})


def _completed_stages(state: RunState) -> list[str]:
    """Stage nodes whose current-generation steps are all done."""
    generation = state.generation
    by_node: dict[str, list[WorkflowStep]] = {}
    for step in state.steps:
        if step.kind is StepKind.STAGE_MEMBER and step.generation == generation:
            by_node.setdefault(step.node_id, []).append(step)
    return [
        node_id
        for node_id, steps in by_node.items()
        if all(step.state is StepState.DONE for step in steps)
    ]


def _gate_dict(campaign: WorkflowCampaign, state: RunState, step: WorkflowStep) -> dict[str, Any]:
    node = _node(campaign, step.node_id)
    gate: dict[str, Any] = {
        "id": step.node_id,
        "activation_id": str(step.id),
        "node_id": step.node_id,
        "status": GATE_STATUS_PENDING
        if step.state is StepState.WAITING
        else _GATE_STATUS_BY_DECISION.get(step.verdict or "", GATE_STATUS_PENDING),
        "label": node_label(campaign.workflow_snapshot, step.node_id),
        "condition": str(node.get("condition") or ""),
        "instructions": str(node.get("instructions") or ""),
        "summary": _gate_summary(state, step),
        "requested_at": (step.activated_at or step.created_at).isoformat(),
        "attempt": step.attempt,
    }
    if step.state is StepState.DONE:
        gate["notes"] = step.note
        gate["updated_at"] = (step.closed_at or step.created_at).isoformat()
        decided_by = _decided_by(state, step)
        if decided_by:
            gate["decided_by"] = decided_by
    return gate


def _gate_summary(state: RunState, step: WorkflowStep) -> str:
    for item in reversed(step.inputs):
        event = state.event(item.seq)
        if event is None:
            continue
        note = str(event.payload.get("note") or "").strip()
        if note:
            return note
    return ""


def _decided_by(state: RunState, step: WorkflowStep) -> str:
    for event in reversed(state.events):
        if event.source_activation_id != step.id:
            continue
        by = event.payload.get("by")
        if isinstance(by, dict) and by.get("id"):
            return str(by["id"])
    return ""


def _node(campaign: WorkflowCampaign, node_id: str) -> dict[str, Any]:
    graph = campaign.workflow_snapshot.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    for node in nodes or []:
        if isinstance(node, dict) and str(node.get("id") or "") == node_id:
            return node
    return {}
