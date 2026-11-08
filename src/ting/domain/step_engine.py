"""The step engine: a pure function from a run's ledger and a new event to actions.

``next_actions(graph, state, event, now=..., settings=...)`` decides what a run does
next. It performs no I/O, holds no state and reads no clock: the ledger passed in is
the whole truth, and the same inputs always give the same actions.
``ting.domain.services.workflow_step_engine`` persists the actions and drives the
harness sessions.

The semantics below are re-specified from the stage-activation rules the removed
Ravn runtime carried (``consumesEventTypes``, ``eventFilters``, ``executionMode``,
the persona ``fan_in`` strategies and ``reviewVerdictPolicy``). They are written here
so they can be read, argued with and tested; they are not a port of that code.

Events
------
An *outcome event* is what a step produced: a trigger's ``dispatchEvent``, a stage
member's verdict mapped to an event, a gate's decision, a wait's observation, a
subworkflow's join. It carries ``node_id``, ``member``, ``verdict`` and ``outcome``
in its payload. Every other event is an *engine event* (``engine.`` prefix) that
records a transition. An outcome event follows the edges of its producing node whose
source event is its type; each such edge *delivers* it to the edge's target as the
edge's target event.

Readiness
---------
A stage member consumes a delivered event when the target event is in its
``consumesEventTypes`` (else its persona's ``consumes.event_types``) and its
``eventFilters`` match: every filter key must equal (a list: contain) the value found
in the event payload, else in ``payload.fields``, else in ``payload.outcome``.

Joins
-----
A consumed delivery activates the member right away, unless a join must be satisfied
first. A *group* is the set of (edge, producer) slots that deliver one target event to
the member; the producers of an edge are the stage members whose persona can produce
its source event, or the source node itself.

* ``joinMode: all`` on the target: the member activates when every *group* of
  forward (non-rework) edges it consumes holds an unconsumed event of the current
  generation from every producer. A late event from a producer replaces its earlier
  one; an event is consumed by the activation that used it.
* ``joinMode: any`` (and ``merge``): each consumed delivery activates the member.
* ``reviewVerdictPolicy`` on the target: whatever the join mode, the policy's event
  type is a quorum: its group must be complete (every reviewer has delivered) before
  the member activates. Rework edges join too when they carry the policy's event.
* A delivery over a rework edge that is not a quorum activates the member at once:
  a rework edge says "go back and redo", not "wait for the rest".

Only events of the current generation count toward a join. A new activation always
carries the events it consumed in ``inputs``.

Concurrency
-----------
``executionMode: sequential`` allows one active member in the node at a time;
``parallel`` allows ``maxConcurrent`` (unbounded when it is not set). A session runs
one step at a time, so a member never has two active steps in a run either. A step
that cannot start yet is created ``pending`` and started, oldest first, when a slot
frees. Waiting steps do not hold a slot: they are parked, not working.

Rework and generations
----------------------
An edge back to an earlier node is marked ``rework: true`` and bounded by
``maxIterations``. When a join fires over a rework edge the edge is *taken*: the run
enters a new generation, every step still open in older generations becomes ``stale``
(its late close is rejected), and the member activates in the new generation, so
downstream reviewers run again against the new revision. Taking an edge more than
``maxIterations`` times fails the run, naming the edge and the knob.

Failure and siblings
--------------------
A step that ends ``failed``, or whose verdict maps to an event no edge carries, ends
only its own path. Siblings keep running and their verdicts are recorded. The run
fails when nothing is open any more (no pending, active or waiting step) and no end
node was reached; the reason lists what failed.

Gates, waits, end
-----------------
A ``gate`` becomes a waiting step opened for a person (or an agent for
``automated_approval``) and is resolved with the node's declared approval or
changes-requested event. ``wait`` and ``subworkflow`` nodes become waiting steps
whose handle names the condition or the child workflow; their closing verdicts are
``observed`` and ``joined``/``blocked``. An ``end`` node completes the run when its
join (``all`` incoming producers of the current generation, or ``any``) holds events
whose verdict is in ``passingVerdicts``; a delivery with another verdict is recorded
as rejected and does not complete the run. Completing cancels whatever is still open.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from ting.domain.step_graph import (
    GATE_MODE_AUTOMATED_APPROVAL,
    JOIN_ALL,
    EdgeSpec,
    MemberSpec,
    NodeKind,
    NodeSpec,
    StepGraph,
)
from ting.domain.workflow_steps import (
    EVENT_DELIVERY_COALESCED,
    EVENT_END_REJECTED,
    EVENT_GATE_OPENED,
    EVENT_GATE_RESOLVED,
    EVENT_REWORK_TAKEN,
    EVENT_RUN_CANCELED,
    EVENT_STEP_ACTIVATED,
    EVENT_STEP_CANCELED,
    EVENT_STEP_CLOSED,
    EVENT_STEP_FAILED,
    EVENT_STEP_QUEUED,
    EVENT_STEP_STALE,
    EVENT_STEP_WAITING,
    EVENT_WAIT_OPENED,
    REVIEW_PASS_VERDICTS,
    SUBWORKFLOW_BLOCKED_VERDICT,
    SUBWORKFLOW_JOINED_VERDICT,
    WAIT_OBSERVED_VERDICT,
    ActorKind,
    Ending,
    EndingKind,
    GateActor,
    GateDecision,
    GateRefusedError,
    InvalidEndingError,
    RunEvent,
    RunState,
    StepEngineError,
    StepInput,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
    activation_id_for,
)

#: The wait condition type that waits on the clock rather than on an outside event.
TIMER_CONDITION = "timer"

#: Keys a gate decision payload carries the person's comment under; the same key the
#: removed runtime used, so a reviewer's comment travels on with the work.
GATE_FEEDBACK_KEY = "gate_feedback"


@dataclass(frozen=True)
class EngineSettings:
    """Engine knobs, from Ting's configuration."""

    step_deadline_seconds: float

    def __post_init__(self) -> None:
        if not self.step_deadline_seconds > 0:
            raise ValueError("step_deadline_seconds must be positive")


@dataclass(frozen=True)
class ActivateStep:
    """Start a stage member's step (new, or promoted from pending). A session must run it."""

    step: WorkflowStep


@dataclass(frozen=True)
class QueueStep:
    """Record a stage member's step that cannot start yet."""

    step: WorkflowStep


@dataclass(frozen=True)
class OpenGate:
    """Open a gate: a waiting step for a person or, for automated gates, an agent."""

    step: WorkflowStep


@dataclass(frozen=True)
class OpenWait:
    """Park a wait or subworkflow node as a waiting step."""

    step: WorkflowStep


@dataclass(frozen=True)
class MarkStale:
    """Steps of an older generation that were still open when a rework edge was taken."""

    step_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class CancelStep:
    """Cancel an open step because its run ended or was canceled."""

    step_id: UUID
    reason: str


@dataclass(frozen=True)
class RecordEvent:
    """Append an event to the run's log."""

    event_type: str
    payload: dict[str, Any]
    source_activation_id: UUID | None = None
    generation: int | None = None


@dataclass(frozen=True)
class CompleteRun:
    """An end node's join is satisfied: the run completed."""

    end_step: WorkflowStep
    completion_event: str


@dataclass(frozen=True)
class FailRun:
    """The run cannot complete. ``cap`` names the knob when a bound was hit."""

    reason: str
    cap: str | None = None


Action = (
    ActivateStep
    | QueueStep
    | OpenGate
    | OpenWait
    | MarkStale
    | CancelStep
    | RecordEvent
    | CompleteRun
    | FailRun
)


def next_actions(
    graph: StepGraph,
    state: RunState,
    event: RunEvent,
    *,
    now: datetime,
    settings: EngineSettings,
) -> list[Action]:
    """What the run does next, given ``event`` has just been appended to ``state``.

    ``state`` already contains ``event``. An outcome event is routed along the edges
    of its producing node; any event then gives pending steps a chance to start and
    lets the run fail if it has nothing left to do.
    """
    if state.finished:
        return []
    planner = _Planner(graph, state, now=now, settings=settings)
    if not event.is_engine_event:
        planner.route(event)
    planner.promote_pending()
    planner.settle()
    return planner.actions


def cancel_actions(state: RunState, *, reason: str) -> list[Action]:
    """Cancel every open step of a run and record that the run was canceled."""
    actions: list[Action] = []
    for step in state.open_steps():
        actions.extend(_cancel(step, reason))
    actions.append(RecordEvent(EVENT_RUN_CANCELED, {"reason": reason}))
    return actions


def plan_retry(
    graph: StepGraph,
    state: RunState,
    step: WorkflowStep,
    *,
    reason: str,
    now: datetime,
    settings: EngineSettings,
) -> list[Action]:
    """Replace a stage member's step by a new attempt of the same node and member.

    The new attempt consumes the inputs the old one did and belongs to the same
    generation. A step that is still open is canceled first, so a late close from its
    session finds a step that is no longer ``active`` and is fenced out; a failed step
    is already closed. The attempt starts at once unless the node's concurrency holds
    it back, in which case it is queued like any other and starts when a slot frees.
    """
    planner = _Planner(graph, state, now=now, settings=settings)
    planner.supersede(step, reason)
    planner.reactivate(step)
    return planner.actions


@dataclass(frozen=True)
class ClosePlan:
    """What closing a step changes: the step as it ends and the events that records.

    ``outcome_payload`` is set when the close produced an outcome event of
    ``outcome_event``; ``lifecycle`` is the engine event that records the close
    otherwise (or as well).
    """

    step: WorkflowStep
    lifecycle_event: str
    lifecycle_payload: dict[str, Any]
    outcome_event: str | None = None
    outcome_payload: dict[str, Any] | None = None


def plan_close(
    graph: StepGraph,
    state: RunState,
    step: WorkflowStep,
    ending: Ending,
    *,
    now: datetime,
) -> ClosePlan:
    """Decide how ``ending`` closes ``step``; raises ``InvalidEndingError`` if it cannot.

    The step must already have passed the fence. Nothing is changed when this raises,
    so the owning session can close the step again correctly.
    """
    node = graph.nodes[step.node_id]
    if step.kind is StepKind.GATE:
        raise InvalidEndingError(
            f"step {step.id} is gate {node.id!r}; a gate is resolved with resolve_gate"
        )
    if ending.kind is EndingKind.WAITING:
        parked = step.with_state(StepState.WAITING, waiting_on=ending.waiting_on, note=ending.note)
        return ClosePlan(
            parked,
            EVENT_STEP_WAITING,
            _step_ref(step)
            | {"waiting_on": ending.waiting_on.to_json() if ending.waiting_on else {}},
        )
    if ending.kind is EndingKind.FAILED:
        failed = step.with_state(StepState.FAILED, note=ending.note, closed_at=now)
        return ClosePlan(failed, EVENT_STEP_FAILED, _step_ref(step) | {"reason": ending.note})
    if ending.kind is EndingKind.DONE:
        return _plan_done(node, step, ending, now)
    return _plan_verdict(graph, state, node, step, ending, now)


def plan_gate_resolution(
    graph: StepGraph,
    state: RunState,
    *,
    node_id: str,
    decision: GateDecision,
    by: GateActor,
    note: str,
    now: datetime,
) -> ClosePlan:
    """Decide how a person's (or agent's) decision closes the open gate of ``node_id``."""
    node = graph.nodes.get(node_id)
    if node is None or node.kind is not NodeKind.GATE:
        raise GateRefusedError(f"{node_id!r} is not a gate of this workflow")
    gate = next(
        (
            step
            for step in state.steps
            if step.node_id == node_id and step.state is StepState.WAITING
        ),
        None,
    )
    if gate is None:
        raise GateRefusedError(f"gate {node_id!r} has no open request to resolve")
    if by.kind is ActorKind.AGENT and node.gate_mode != GATE_MODE_AUTOMATED_APPROVAL:
        raise GateRefusedError(
            f"gate {node_id!r} is mode {node.gate_mode!r} and waits for a person; only an "
            f"{GATE_MODE_AUTOMATED_APPROVAL!r} gate may be answered by an agent"
        )
    event_type = (
        node.approval_event if decision is GateDecision.APPROVE else node.changes_requested_event
    )
    if not event_type:
        raise GateRefusedError(
            f"gate {node_id!r} declares no event for {decision.value!r}; set "
            "approvalEvent and changesRequestedEvent on the gate node"
        )
    outcome: dict[str, Any] = {GATE_FEEDBACK_KEY: note} if note.strip() else {}
    closed = gate.with_state(
        StepState.DONE,
        verdict=decision.value,
        outcome=outcome,
        note=note,
        closed_at=now,
    )
    decided_by = {"kind": by.kind.value, "id": by.id}
    return ClosePlan(
        closed,
        EVENT_GATE_RESOLVED,
        _step_ref(gate) | {"verdict": decision.value, "by": decided_by, "note": note},
        outcome_event=event_type,
        outcome_payload=_outcome_payload(
            gate, decision.value, outcome, note, extra={"by": decided_by}
        ),
    )


def event_matches_filters(payload: Mapping[str, Any], filters: Mapping[str, str]) -> bool:
    """Whether an event satisfies a member's ``eventFilters``.

    Each key is looked up in the payload, then ``payload.fields``, then
    ``payload.outcome``. A list value matches when it contains the expected value;
    anything else when its text equals it.
    """
    if not filters:
        return True
    fields = payload.get("fields")
    outcome = payload.get("outcome")
    sources = [
        payload,
        fields if isinstance(fields, Mapping) else {},
        outcome if isinstance(outcome, Mapping) else {},
    ]
    for key, expected in filters.items():
        actual = next((source[key] for source in sources if key in source), None)
        if isinstance(actual, list):
            if expected not in {str(item).strip() for item in actual if str(item).strip()}:
                return False
            continue
        if str(actual or "").strip() != expected:
            return False
    return True


@dataclass(frozen=True)
class ReviewJoin:
    """The result of joining a review quorum, as the policy judges it."""

    passed: bool
    reasons: tuple[str, ...]


def review_join(
    graph: StepGraph,
    state: RunState,
    node: NodeSpec,
    step: WorkflowStep,
) -> ReviewJoin | None:
    """Judge the review quorum ``step`` consumed, or ``None`` if it consumed none.

    The quorum passes when every reviewer's verdict is a pass and each binding field
    is present, identical across reviewers and, where the reviewer's own input
    carried it, equal to that input: a review of another revision never passes.
    """
    policy = node.review_policy
    if policy is None:
        return None
    reviews = [
        review
        for review in (
            state.event(item.seq) for item in step.inputs if item.event_type == policy.event_type
        )
        if review is not None
    ]
    if not reviews:
        return None
    reasons: list[str] = []
    for review in reviews:
        if review.verdict.casefold() not in REVIEW_PASS_VERDICTS:
            reasons.append(f"{review.member or review.node_id} gave verdict {review.verdict!r}")
    for field_name in policy.binding_fields:
        values = {str(review.outcome.get(field_name) or "").strip() for review in reviews}
        if len(values) != 1 or not next(iter(values)):
            reasons.append(f"binding field {field_name!r} differs or is missing across reviewers")
            continue
        reasons.extend(_binding_drift(state, reviews, field_name))
    return ReviewJoin(passed=not reasons, reasons=tuple(reasons))


def _binding_drift(state: RunState, reviews: list[RunEvent], field_name: str) -> list[str]:
    drift: list[str] = []
    for review in reviews:
        reviewer_step = (
            state.step(review.source_activation_id) if review.source_activation_id else None
        )
        if reviewer_step is None:
            continue
        for item in reviewer_step.inputs:
            reviewed = state.event(item.seq)
            if reviewed is None or field_name not in reviewed.outcome:
                continue
            expected = str(reviewed.outcome[field_name]).strip()
            if str(review.outcome.get(field_name) or "").strip() != expected:
                drift.append(
                    f"{review.member or review.node_id} reviewed {field_name!r} other than "
                    f"the one it was given ({expected!r})"
                )
    return drift


# --- closing -----------------------------------------------------------------


def _plan_done(node: NodeSpec, step: WorkflowStep, ending: Ending, now: datetime) -> ClosePlan:
    if step.kind is StepKind.STAGE_MEMBER:
        member = node.member(step.member or "")
        produced = member.produced_events if member is not None else frozenset()
        if produced:
            raise InvalidEndingError(
                f"{step.member!r} in {node.id!r} hands off a verdict "
                f"({', '.join(sorted(produced))}); a done ending is only for a member that "
                "produces nothing"
            )
    elif step.kind is StepKind.WAIT:
        raise InvalidEndingError(
            f"{node.id!r} closes with a verdict ({WAIT_OBSERVED_VERDICT!r} or "
            f"{SUBWORKFLOW_JOINED_VERDICT!r}), not done"
        )
    done = step.with_state(StepState.DONE, note=ending.note, closed_at=now)
    return ClosePlan(done, EVENT_STEP_CLOSED, _step_ref(step) | {"verdict": None})


def _plan_verdict(
    graph: StepGraph,
    state: RunState,
    node: NodeSpec,
    step: WorkflowStep,
    ending: Ending,
    now: datetime,
) -> ClosePlan:
    verdict = (ending.verdict or "").strip()
    event_type = _event_for_verdict(graph, node, step, verdict)
    if step.kind is StepKind.STAGE_MEMBER:
        _enforce_review_policy(graph, state, node, step, event_type, verdict)
    carried = any(edge.source_event == event_type for edge in graph.outgoing(node.id))
    closed = step.with_state(
        StepState.DONE if carried else StepState.FAILED,
        verdict=verdict,
        outcome=ending.outcome,
        note=ending.note
        if carried
        else (
            f"verdict {verdict!r} maps to event {event_type!r}, which no edge from "
            f"{node.id!r} carries"
        ),
        evidence=ending.evidence,
        closed_at=now,
    )
    return ClosePlan(
        closed,
        EVENT_STEP_CLOSED if carried else EVENT_STEP_FAILED,
        _step_ref(step) | {"verdict": verdict, "event_type": event_type, "routed": carried},
        outcome_event=event_type,
        outcome_payload=_outcome_payload(
            step, verdict, ending.outcome, ending.note, evidence=ending.evidence
        ),
    )


def _event_for_verdict(graph: StepGraph, node: NodeSpec, step: WorkflowStep, verdict: str) -> str:
    if step.kind is StepKind.STAGE_MEMBER:
        member = node.member(step.member or "")
        if member is None or member.contract is None:
            raise InvalidEndingError(f"{step.member!r} has no persona contract in {node.id!r}")
        event_type = member.contract.event_for(verdict)
        if event_type is None:
            valid = ", ".join(sorted(member.contract.produces_map)) or "none"
            raise InvalidEndingError(
                f"verdict {verdict!r} is not one {step.member!r} can give; valid verdicts: {valid}"
            )
        return event_type
    # The remaining open kind is a wait step: a ``wait`` or a ``subworkflow`` node.
    return _wait_event(graph, node, verdict)


def _wait_event(graph: StepGraph, node: NodeSpec, verdict: str) -> str:
    """The event a wait or subworkflow node publishes for a closing verdict."""
    outgoing_events = sorted({edge.source_event for edge in graph.outgoing(node.id)})
    if node.blocked_event:
        # A subworkflow publishes the one event its children joined with, or its blockedEvent.
        if verdict == SUBWORKFLOW_BLOCKED_VERDICT:
            return node.blocked_event
        joined = [event for event in outgoing_events if event != node.blocked_event]
        if verdict == SUBWORKFLOW_JOINED_VERDICT and len(joined) == 1:
            return joined[0]
        raise InvalidEndingError(
            f"subworkflow {node.id!r} closes with {SUBWORKFLOW_JOINED_VERDICT!r} or "
            f"{SUBWORKFLOW_BLOCKED_VERDICT!r}, not {verdict!r}"
        )
    if verdict == WAIT_OBSERVED_VERDICT and len(outgoing_events) == 1:
        return outgoing_events[0]
    raise InvalidEndingError(
        f"wait {node.id!r} closes with {WAIT_OBSERVED_VERDICT!r} once its condition is "
        f"observed, not {verdict!r}"
    )


def _enforce_review_policy(
    graph: StepGraph,
    state: RunState,
    node: NodeSpec,
    step: WorkflowStep,
    event_type: str,
    verdict: str,
) -> None:
    policy = node.review_policy
    if policy is None or event_type not in policy.pass_outcomes:
        return
    joined = review_join(graph, state, node, step)
    if joined is None:
        raise InvalidEndingError(
            f"verdict {verdict!r} produces {event_type!r}, a passing outcome of "
            f"reviewVerdictPolicy on {node.id!r}, but this step consumed no "
            f"{policy.event_type!r} reviews"
        )
    if not joined.passed:
        raise InvalidEndingError(
            f"verdict {verdict!r} produces {event_type!r}, a passing outcome of "
            f"reviewVerdictPolicy on {node.id!r}, but the joined reviews do not pass: "
            + "; ".join(joined.reasons)
        )


def _outcome_payload(
    step: WorkflowStep,
    verdict: str,
    outcome: dict[str, Any],
    note: str,
    *,
    evidence: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "node_id": step.node_id,
        "member": step.member,
        "verdict": verdict,
        "outcome": outcome,
        "note": note,
        "attempt": step.attempt,
        "activation_id": str(step.id),
    }
    if evidence:
        payload["evidence"] = evidence
    if extra:
        payload.update(extra)
    return payload


def _step_ref(step: WorkflowStep) -> dict[str, Any]:
    return {
        "activation_id": str(step.id),
        "node_id": step.node_id,
        "member": step.member,
        "attempt": step.attempt,
    }


def _cancel(step: WorkflowStep, reason: str) -> list[Action]:
    return [
        CancelStep(step.id, reason),
        RecordEvent(
            EVENT_STEP_CANCELED,
            _step_ref(step) | {"reason": reason},
            source_activation_id=step.id,
        ),
    ]


# --- planning ----------------------------------------------------------------


class _Planner:
    """Working state of one ``next_actions`` call."""

    def __init__(
        self,
        graph: StepGraph,
        state: RunState,
        *,
        now: datetime,
        settings: EngineSettings,
    ) -> None:
        self._graph = graph
        self._state = state
        self._now = now
        self._settings = settings
        self._steps: dict[UUID, WorkflowStep] = {step.id: step for step in state.steps}
        self._events: list[RunEvent] = list(state.events)
        self._reworks_taken: dict[str, int] = {}
        for event in state.events:
            if event.event_type == EVENT_REWORK_TAKEN:
                edge_id = str(event.payload.get("edge_id") or "")
                self._reworks_taken[edge_id] = self._reworks_taken.get(edge_id, 0) + 1
        self._generation = state.generation
        self.actions: list[Action] = []
        self._ended = False
        self._rejections: list[dict[str, Any]] = []

    # -- routing ----------------------------------------------------------------

    def route(self, event: RunEvent) -> None:
        producer = self._graph.nodes.get(event.node_id)
        if producer is None:
            raise StepEngineError(
                f"event {event.event_type!r} (seq {event.seq}) names node {event.node_id!r}, "
                "which is not in the workflow graph"
            )
        for edge in self._graph.outgoing(producer.id):
            if self._ended:
                return
            if edge.source_event == event.event_type:
                self._deliver(event, edge)

    def _deliver(self, event: RunEvent, edge: EdgeSpec) -> None:
        target = self._graph.nodes[edge.target]
        if target.kind is NodeKind.STAGE:
            self._deliver_stage(event, edge, target)
        elif target.kind is NodeKind.GATE:
            self._deliver_waiting(event, edge, target, StepKind.GATE)
        elif target.kind in (NodeKind.WAIT, NodeKind.SUBWORKFLOW):
            self._deliver_waiting(event, edge, target, StepKind.WAIT)
        elif target.kind is NodeKind.END:
            self._deliver_end(event, edge, target)

    def _deliver_stage(self, event: RunEvent, edge: EdgeSpec, target: NodeSpec) -> None:
        for member in target.members:
            if self._ended:
                return
            if edge.target_event not in member.consumes:
                continue
            if not event_matches_filters(event.payload, member.event_filters):
                continue
            inputs = self._join_inputs(event, edge, target, member)
            if inputs is None:
                continue
            if not self._take_reworks(inputs, event):
                return
            self._create_step(target, StepKind.STAGE_MEMBER, member.alias, inputs)

    def _deliver_waiting(
        self,
        event: RunEvent,
        edge: EdgeSpec,
        target: NodeSpec,
        kind: StepKind,
    ) -> None:
        coalesced = next(
            (
                step
                for step in self._steps.values()
                if step.node_id == target.id
                and step.state is StepState.WAITING
                and step.generation == self._generation
            ),
            None,
        )
        if coalesced is not None:
            self.actions.append(
                RecordEvent(
                    EVENT_DELIVERY_COALESCED,
                    {
                        "node_id": target.id,
                        "edge_id": edge.id,
                        "event_seq": event.seq,
                        "open_activation_id": str(coalesced.id),
                    },
                )
            )
            return
        inputs = (_input_of(event, edge),)
        if not self._take_reworks(inputs, event):
            return
        self._create_step(target, kind, None, inputs)

    def _deliver_end(self, event: RunEvent, edge: EdgeSpec, target: NodeSpec) -> None:
        if target.passing_verdicts and event.verdict not in target.passing_verdicts:
            rejection = {
                "node_id": target.id,
                "edge_id": edge.id,
                "event_seq": event.seq,
                "verdict": event.verdict,
                "passing_verdicts": list(target.passing_verdicts),
            }
            self._rejections.append(rejection)
            self.actions.append(
                RecordEvent(
                    EVENT_END_REJECTED, rejection, source_activation_id=event.source_activation_id
                )
            )
            return
        inputs = self._end_inputs(event, edge, target)
        if inputs is None:
            return
        attempt = self._next_attempt(target.id, None)
        step = WorkflowStep(
            id=activation_id_for(self._state.run_id, target.id, None, attempt),
            run_id=self._state.run_id,
            node_id=target.id,
            member=None,
            kind=StepKind.END,
            attempt=attempt,
            generation=self._generation,
            state=StepState.DONE,
            created_at=self._now,
            inputs=inputs,
            activated_at=self._now,
            closed_at=self._now,
        )
        self._steps[step.id] = step
        self.actions.append(CompleteRun(step, target.completion_event))
        for open_step in self._open_steps():
            self.actions.extend(_cancel(open_step, f"run completed at end node {target.id!r}"))
            self._steps[open_step.id] = open_step.with_state(StepState.CANCELED)
        self._ended = True

    # -- joins ------------------------------------------------------------------

    def _join_inputs(
        self,
        event: RunEvent,
        edge: EdgeSpec,
        target: NodeSpec,
        member: MemberSpec,
    ) -> tuple[StepInput, ...] | None:
        """The inputs ``member`` activates with now, or ``None`` while its join is open."""
        policy = target.review_policy
        quorum = policy is not None and edge.target_event == policy.event_type
        if edge.rework and not quorum:
            return (_input_of(event, edge),)
        all_join = target.join_mode == JOIN_ALL and not edge.rework
        if not (all_join or quorum):
            return (_input_of(event, edge),)
        groups = self._groups(target, member, only=edge.target_event if not all_join else None)
        consumed = self._consumed(target.id, member.alias)
        chosen: list[StepInput] = []
        for slots in groups.values():
            for slot_edge, producer in slots:
                found = self._latest(slot_edge, producer, member, consumed, event)
                if found is None:
                    return None
                chosen.append(_input_of(found, slot_edge))
        if not any(item.seq == event.seq for item in chosen):
            raise StepEngineError(
                f"event {event.event_type!r} (seq {event.seq}) reached {target.id!r} over edge "
                f"{edge.id!r} but fills no join slot there: {event.member or event.node_id!r} is "
                "not a producer the graph expects for that edge"
            )
        return tuple(chosen)

    def _end_inputs(
        self,
        event: RunEvent,
        edge: EdgeSpec,
        target: NodeSpec,
    ) -> tuple[StepInput, ...] | None:
        if target.join_mode != JOIN_ALL:
            return (_input_of(event, edge),)
        chosen: list[StepInput] = []
        for incoming in self._graph.incoming(target.id):
            if incoming.rework:
                continue
            for producer in self._graph.producers(incoming):
                found = self._latest_for_end(incoming, producer, target, event)
                if found is None:
                    return None
                chosen.append(_input_of(found, incoming))
        return tuple(chosen)

    def _groups(
        self,
        target: NodeSpec,
        member: MemberSpec,
        *,
        only: str | None,
    ) -> dict[str, list[tuple[EdgeSpec, str | None]]]:
        """Slots by target event: (edge, producer) pairs that must all deliver to join."""
        groups: dict[str, list[tuple[EdgeSpec, str | None]]] = {}
        for incoming in self._graph.incoming(target.id):
            event_type = incoming.target_event
            if event_type not in member.consumes or (only is not None and event_type != only):
                continue
            if incoming.rework and only != event_type:
                continue
            for producer in self._graph.producers(incoming):
                groups.setdefault(event_type, []).append((incoming, producer))
        return groups

    def _latest(
        self,
        edge: EdgeSpec,
        producer: str | None,
        member: MemberSpec,
        consumed: set[int],
        current: RunEvent,
    ) -> RunEvent | None:
        candidates = [
            candidate
            for candidate in self._events
            if self._is_delivery(candidate, edge, producer)
            and candidate.seq not in consumed
            and (candidate.generation == self._generation or candidate.seq == current.seq)
            and event_matches_filters(candidate.payload, member.event_filters)
        ]
        return max(candidates, key=lambda candidate: candidate.seq, default=None)

    def _latest_for_end(
        self,
        edge: EdgeSpec,
        producer: str | None,
        target: NodeSpec,
        current: RunEvent,
    ) -> RunEvent | None:
        candidates = [
            candidate
            for candidate in self._events
            if self._is_delivery(candidate, edge, producer)
            and (candidate.generation == self._generation or candidate.seq == current.seq)
            and (not target.passing_verdicts or candidate.verdict in target.passing_verdicts)
        ]
        return max(candidates, key=lambda candidate: candidate.seq, default=None)

    @staticmethod
    def _is_delivery(candidate: RunEvent, edge: EdgeSpec, producer: str | None) -> bool:
        return (
            not candidate.is_engine_event
            and candidate.event_type == edge.source_event
            and candidate.node_id == edge.source
            and candidate.member == producer
        )

    def _consumed(self, node_id: str, alias: str) -> set[int]:
        return {
            item.seq
            for step in self._steps.values()
            if step.node_id == node_id and step.member == alias
            for item in step.inputs
        }

    # -- rework -----------------------------------------------------------------

    def _take_reworks(self, inputs: tuple[StepInput, ...], event: RunEvent) -> bool:
        """Take the rework edges an activation arrives over; ``False`` once the run failed."""
        edges = [
            edge
            for edge_id in dict.fromkeys(item.edge_id for item in inputs)
            if (edge := self._graph.edge(edge_id)) is not None and edge.rework
        ]
        if not edges:
            return True
        for edge in edges:
            limit = edge.max_iterations
            taken = self._reworks_taken.get(edge.id, 0)
            if limit is None or taken >= limit:
                self._fail_for_rework(edge, taken)
                return False
        for edge in edges:
            self._generation += 1
            taken = self._reworks_taken.get(edge.id, 0) + 1
            self._reworks_taken[edge.id] = taken
            self.actions.append(
                RecordEvent(
                    EVENT_REWORK_TAKEN,
                    {
                        "edge_id": edge.id,
                        "from_node": edge.source,
                        "to_node": edge.target,
                        "iteration": taken,
                        "max_iterations": edge.max_iterations,
                        "trigger_event_seq": event.seq,
                    },
                    source_activation_id=event.source_activation_id,
                    generation=self._generation,
                )
            )
        stale = tuple(step.id for step in self._open_steps() if step.generation < self._generation)
        for step_id in stale:
            self._steps[step_id] = self._steps[step_id].with_state(StepState.STALE)
        if stale:
            self.actions.append(MarkStale(stale))
            for step_id in stale:
                self.actions.append(
                    RecordEvent(
                        EVENT_STEP_STALE,
                        {"activation_id": str(step_id), "generation": self._generation},
                        source_activation_id=step_id,
                        generation=self._generation,
                    )
                )
        return True

    def _fail_for_rework(self, edge: EdgeSpec, taken: int) -> None:
        if edge.max_iterations is None:
            reason = (
                f"rework edge {edge.id!r} ({edge.source} -> {edge.target}) has no maxIterations; "
                "set it on the edge"
            )
        else:
            reason = (
                f"rework edge {edge.id!r} ({edge.source} -> {edge.target}) hit "
                f"maxIterations={edge.max_iterations} after {taken} rework(s); raise it on the "
                "edge or fix the step that keeps sending the work back"
            )
        self._fail(reason, cap=f"edge:{edge.id}.maxIterations")

    # -- steps ------------------------------------------------------------------

    def _create_step(
        self,
        node: NodeSpec,
        kind: StepKind,
        member: str | None,
        inputs: tuple[StepInput, ...],
    ) -> None:
        attempt = self._next_attempt(node.id, member)
        step_id = activation_id_for(self._state.run_id, node.id, member, attempt)
        if kind is StepKind.STAGE_MEMBER:
            start = self._can_start(node, member or "")
            step = self._stage_step(node, member or "", step_id, attempt, inputs, active=start)
            self._steps[step.id] = step
            self.actions.append(ActivateStep(step) if start else QueueStep(step))
            self.actions.append(
                RecordEvent(
                    EVENT_STEP_ACTIVATED if start else EVENT_STEP_QUEUED,
                    _step_ref(step) | {"inputs": [item.to_json() for item in inputs]},
                    source_activation_id=step.id,
                )
            )
            return
        step = self._waiting_step(node, kind, step_id, attempt, inputs)
        self._steps[step.id] = step
        opened = EVENT_GATE_OPENED if kind is StepKind.GATE else EVENT_WAIT_OPENED
        self.actions.append(OpenGate(step) if kind is StepKind.GATE else OpenWait(step))
        self.actions.append(
            RecordEvent(
                opened,
                _step_ref(step)
                | {"waiting_on": step.waiting_on.to_json() if step.waiting_on else {}},
                source_activation_id=step.id,
            )
        )

    def _stage_step(
        self,
        node: NodeSpec,
        member: str,
        step_id: UUID,
        attempt: int,
        inputs: tuple[StepInput, ...],
        *,
        active: bool,
    ) -> WorkflowStep:
        return WorkflowStep(
            id=step_id,
            run_id=self._state.run_id,
            node_id=node.id,
            member=member,
            kind=StepKind.STAGE_MEMBER,
            attempt=attempt,
            generation=self._generation,
            state=StepState.ACTIVE if active else StepState.PENDING,
            created_at=self._now,
            inputs=inputs,
            deadline_at=self._deadline(node) if active else None,
            activated_at=self._now if active else None,
        )

    def _waiting_step(
        self,
        node: NodeSpec,
        kind: StepKind,
        step_id: UUID,
        attempt: int,
        inputs: tuple[StepInput, ...],
    ) -> WorkflowStep:
        return WorkflowStep(
            id=step_id,
            run_id=self._state.run_id,
            node_id=node.id,
            member=None,
            kind=kind,
            attempt=attempt,
            generation=self._generation,
            state=StepState.WAITING,
            created_at=self._now,
            inputs=inputs,
            waiting_on=self._waiting_on(node),
            deadline_at=self._now + timedelta(seconds=node.deadline_seconds)
            if node.deadline_seconds is not None
            else None,
            activated_at=self._now,
        )

    def _waiting_on(self, node: NodeSpec) -> WaitingOn:
        if node.kind is NodeKind.GATE:
            agent = node.gate_mode == GATE_MODE_AUTOMATED_APPROVAL
            return WaitingOn(
                WaitingOnKind.AGENT if agent else WaitingOnKind.PERSON, node.id, self._now
            )
        if node.kind is NodeKind.SUBWORKFLOW:
            return WaitingOn(WaitingOnKind.EVENT, f"subworkflow:{node.id}", self._now)
        timed = node.wait_conditions == (TIMER_CONDITION,)
        return WaitingOn(
            WaitingOnKind.TIME if timed else WaitingOnKind.EVENT,
            f"wait:{node.id}:{','.join(node.wait_conditions)}",
            self._now,
        )

    def _deadline(self, node: NodeSpec) -> datetime:
        seconds = (
            node.deadline_seconds
            if node.deadline_seconds is not None
            else self._settings.step_deadline_seconds
        )
        return self._now + timedelta(seconds=seconds)

    def _next_attempt(self, node_id: str, member: str | None) -> int:
        return 1 + max(
            (
                step.attempt
                for step in self._steps.values()
                if step.node_id == node_id and step.member == member
            ),
            default=0,
        )

    def _can_start(self, node: NodeSpec, member: str) -> bool:
        active = [step for step in self._steps.values() if step.state is StepState.ACTIVE]
        if any(step.member == member for step in active):
            return False
        limit = node.concurrency_limit
        if limit is None:
            return True
        return sum(1 for step in active if step.node_id == node.id) < limit

    def _open_steps(self) -> list[WorkflowStep]:
        return [step for step in self._steps.values() if step.is_open]

    def supersede(self, step: WorkflowStep, reason: str) -> None:
        """Cancel ``step`` if it is still open; a closed step is left as it ended."""
        if not step.is_open:
            return
        self.actions.extend(_cancel(step, reason))
        self._steps[step.id] = step.with_state(StepState.CANCELED)

    def reactivate(self, step: WorkflowStep) -> None:
        """Activate the next attempt of ``step``'s node and member on the same inputs."""
        self._create_step(
            self._graph.nodes[step.node_id], StepKind.STAGE_MEMBER, step.member, step.inputs
        )

    def promote_pending(self) -> None:
        if self._ended:
            return
        for step in list(self._steps.values()):
            if step.state is not StepState.PENDING or step.member is None:
                continue
            node = self._graph.nodes[step.node_id]
            if not self._can_start(node, step.member):
                continue
            promoted = step.with_state(
                StepState.ACTIVE, activated_at=self._now, deadline_at=self._deadline(node)
            )
            self._steps[promoted.id] = promoted
            self.actions.append(ActivateStep(promoted))
            self.actions.append(
                RecordEvent(
                    EVENT_STEP_ACTIVATED,
                    _step_ref(promoted) | {"promoted_from_queue": True},
                    source_activation_id=promoted.id,
                )
            )

    # -- outcome ----------------------------------------------------------------

    def settle(self) -> None:
        """Fail the run when it has nothing open and reached no end."""
        if self._ended or self._open_steps():
            return
        self._fail(self._stall_reason(), cap=None)

    def _fail(self, reason: str, *, cap: str | None) -> None:
        for step in self._open_steps():
            self.actions.extend(_cancel(step, "run failed"))
            self._steps[step.id] = step.with_state(StepState.CANCELED)
        self.actions.append(FailRun(reason, cap))
        self._ended = True

    def _stall_reason(self) -> str:
        failed = [
            step
            for step in self._steps.values()
            if step.state is StepState.FAILED and step.generation == self._generation
        ]
        rejected = [
            event.payload
            for event in self._events
            if event.event_type == EVENT_END_REJECTED and event.generation == self._generation
        ] + self._rejections
        parts = ["no path to an end node can complete: nothing is pending, active or waiting"]
        for step in failed:
            who = f"{step.node_id}/{step.member}" if step.member else step.node_id
            parts.append(f"{who} (attempt {step.attempt}) failed: {step.note or 'no reason given'}")
        for payload in rejected:
            parts.append(
                f"end node {payload.get('node_id')} refused verdict "
                f"{payload.get('verdict')!r} (passingVerdicts {payload.get('passing_verdicts')})"
            )
        return "; ".join(parts)


def _input_of(event: RunEvent, edge: EdgeSpec) -> StepInput:
    return StepInput(
        seq=event.seq,
        event_type=edge.target_event,
        edge_id=edge.id,
        node_id=event.node_id,
        member=event.member,
    )


__all__ = [
    "Action",
    "ActivateStep",
    "CancelStep",
    "ClosePlan",
    "CompleteRun",
    "EngineSettings",
    "FailRun",
    "MarkStale",
    "OpenGate",
    "OpenWait",
    "QueueStep",
    "RecordEvent",
    "ReviewJoin",
    "cancel_actions",
    "event_matches_filters",
    "next_actions",
    "plan_close",
    "plan_gate_resolution",
    "plan_retry",
    "review_join",
]
