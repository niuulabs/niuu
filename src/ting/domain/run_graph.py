"""A workflow run drawn as its workflow's graph: each node with where the run is at it.

The run's graph is the workflow snapshot it was launched with, so it reads as the workflow
the run follows even after the workflow changed. Each node's state comes from the run's step
ledger, from the newest round of work at that node (a rework generation and attempt replace
the earlier one): the members that took it up with their state, verdict and session, and the
node's own state, one of:

* ``waiting``: a person or something outside must act (a gate waiting for a decision, a member
  waiting on something);
* ``active``: work at the node is offered or under way;
* ``failed``: the node's work failed;
* ``done``: the node's work closed;
* ``pending``: the run has not reached the node.

A trigger node is ``done`` once the run has any step: the run started from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ting.domain.models import WorkflowCampaign
from ting.domain.workflow_steps import RunState, StepKind, StepState, WorkflowStep

NODE_WAITING = "waiting"
NODE_ACTIVE = "active"
NODE_FAILED = "failed"
NODE_DONE = "done"
NODE_PENDING = "pending"

_STEP_NODE_STATE: Mapping[StepState, str] = {
    StepState.WAITING: NODE_WAITING,
    StepState.ACTIVE: NODE_ACTIVE,
    StepState.PENDING: NODE_ACTIVE,
    StepState.FAILED: NODE_FAILED,
    StepState.DONE: NODE_DONE,
}

#: The order a node's state is taken in from its steps: the first that any step has wins.
_NODE_STATE_ORDER = (NODE_WAITING, NODE_ACTIVE, NODE_FAILED, NODE_DONE)


@dataclass(frozen=True)
class RunGraphMember:
    """One member's part in a node's newest round of work."""

    member: str
    state: str
    verdict: str | None
    session_id: str | None


@dataclass(frozen=True)
class RunGraphNode:
    """Where the run is at one node of its workflow."""

    node_id: str
    state: str
    members: tuple[RunGraphMember, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RunGraph:
    """The run's workflow graph and the state of each of its nodes."""

    workflow_name: str
    graph: Mapping[str, Any]
    nodes: tuple[RunGraphNode, ...]


def run_graph(campaign: WorkflowCampaign, state: RunState) -> RunGraph:
    """The run's workflow graph, each node with its state from the run's step ledger."""
    graph = campaign.workflow_snapshot.get("graph")
    graph = graph if isinstance(graph, Mapping) else {}
    nodes = [node for node in graph.get("nodes") or [] if isinstance(node, Mapping)]
    steps_by_node: dict[str, list[WorkflowStep]] = {}
    for step in state.steps:
        steps_by_node.setdefault(step.node_id, []).append(step)
    started = bool(state.steps)
    return RunGraph(
        workflow_name=campaign.workflow_name,
        graph=graph,
        nodes=tuple(
            _node(str(node.get("id") or ""), str(node.get("kind") or ""), steps_by_node, started)
            for node in nodes
        ),
    )


def _node(
    node_id: str, kind: str, steps_by_node: Mapping[str, list[WorkflowStep]], started: bool
) -> RunGraphNode:
    steps = _newest_round(steps_by_node.get(node_id, []))
    if not steps:
        reached = kind == "trigger" and started
        return RunGraphNode(node_id=node_id, state=NODE_DONE if reached else NODE_PENDING)
    return RunGraphNode(
        node_id=node_id,
        state=_node_state(step.state for step in steps),
        members=_members(steps),
    )


def _newest_round(steps: list[WorkflowStep]) -> list[WorkflowStep]:
    """The node's steps of its newest generation and attempt: earlier rounds were replaced."""
    if not steps:
        return []
    newest = max((step.generation, step.attempt) for step in steps)
    return [step for step in steps if (step.generation, step.attempt) == newest]


def _node_state(states: Iterable[StepState]) -> str:
    seen = {_STEP_NODE_STATE[state] for state in states if state in _STEP_NODE_STATE}
    for node_state in _NODE_STATE_ORDER:
        if node_state in seen:
            return node_state
    # Only stale or canceled steps: the round ended without closing; the node is not reached.
    return NODE_PENDING


def _members(steps: list[WorkflowStep]) -> tuple[RunGraphMember, ...]:
    """Each member's newest step at the node, in the order they took the work up."""
    latest: dict[str, WorkflowStep] = {}
    for step in sorted(steps, key=lambda item: item.created_at):
        if step.kind is StepKind.STAGE_MEMBER and step.member:
            latest[step.member] = step
    return tuple(
        RunGraphMember(
            member=member,
            state=_STEP_NODE_STATE.get(step.state, NODE_PENDING),
            verdict=step.verdict,
            session_id=step.session_id,
        )
        for member, step in latest.items()
    )
