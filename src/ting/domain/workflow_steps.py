"""The step ledger: what a workflow run has activated, closed and decided.

A workflow run advances one *step activation* at a time. Ting activates a step for
a persona (or opens a gate, or a wait), a harness session closes it, and the step
engine (``ting.domain.step_engine``) decides what activates next. Everything the
engine needs is in two durable collections, kept per run:

* ``WorkflowStep`` rows, one per activation, with the state the activation is in;
* ``RunEvent`` rows, an append-only log of every outcome event and every engine
  transition, so a run can be replayed and audited.

This module holds the models and the vocabulary they share. It has no behaviour
beyond validating a close against the step it names (fencing).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

#: Event types the engine itself records are namespaced; a workflow may not declare one.
ENGINE_EVENT_PREFIX = "engine."

#: Engine transitions recorded in the run's event log.
EVENT_RUN_LAUNCHED = "engine.run.launched"
EVENT_RUN_COMPLETED = "engine.run.completed"
EVENT_RUN_FAILED = "engine.run.failed"
EVENT_RUN_CANCELED = "engine.run.canceled"
EVENT_STEP_ACTIVATED = "engine.step.activated"
EVENT_STEP_QUEUED = "engine.step.queued"
EVENT_STEP_CLOSED = "engine.step.closed"
EVENT_STEP_FAILED = "engine.step.failed"
EVENT_STEP_WAITING = "engine.step.waiting"
EVENT_STEP_STALE = "engine.step.stale"
EVENT_STEP_CANCELED = "engine.step.canceled"
EVENT_GATE_OPENED = "engine.gate.opened"
EVENT_GATE_RESOLVED = "engine.gate.resolved"
EVENT_WAIT_OPENED = "engine.wait.opened"
EVENT_REWORK_TAKEN = "engine.rework.taken"
EVENT_DELIVERY_COALESCED = "engine.delivery.coalesced"
EVENT_END_REJECTED = "engine.end.rejected"
EVENT_CLOSE_REJECTED = "engine.close.rejected"
EVENT_STEP_UNCLOSED = "engine.step.unclosed"
#: Stuck detection (NIU-1181): a flag raised on an open step, and a recovery action taken on one.
EVENT_STEP_FLAGGED = "engine.step.flagged"
EVENT_RECOVERY = "engine.recovery"

#: The event a flagged step puts on Ting's event bus for the notification feed.
NOTIFY_STEP_FLAGGED = "workflow.step_flagged"

#: Who the log names for an action Ting took on its own, with no person behind it.
SYSTEM_ACTOR_KIND = "system"
SYSTEM_ACTOR_ID = "ting.stuck-step-sweep"

TERMINAL_RUN_EVENTS = frozenset({EVENT_RUN_COMPLETED, EVENT_RUN_FAILED, EVENT_RUN_CANCELED})

#: Verdicts a reviewer may give that count as a pass (matched case-insensitively).
#: This is the vocabulary ``reviewVerdictPolicy`` joins on.
REVIEW_PASS_VERDICTS = frozenset({"pass", "approve", "approved"})

#: The verdict a wait node closes with when its condition is observed.
WAIT_OBSERVED_VERDICT = "observed"
#: The verdicts a subworkflow node closes with: its children joined, or they are blocked.
SUBWORKFLOW_JOINED_VERDICT = "joined"
SUBWORKFLOW_BLOCKED_VERDICT = "blocked"


class StepKind(StrEnum):
    STAGE_MEMBER = "stage_member"
    GATE = "gate"
    WAIT = "wait"
    END = "end"


class StepState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    STALE = "stale"
    CANCELED = "canceled"


#: A step in one of these states can still produce something.
OPEN_STEP_STATES = frozenset({StepState.PENDING, StepState.ACTIVE, StepState.WAITING})

#: A step in one of these states accepts a close from the session that owns it.
CLOSABLE_STEP_STATES = frozenset({StepState.ACTIVE, StepState.WAITING})


class WaitingOnKind(StrEnum):
    PERSON = "person"
    SESSION = "session"
    EVENT = "event"
    TIME = "time"
    AGENT = "agent"


class EndingKind(StrEnum):
    VERDICT = "verdict"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"


class FlagKind(StrEnum):
    """Why an open step is flagged as stuck. Each is raised once per step."""

    #: The step is past its ``deadline_at``.
    OVERDUE = "overdue"
    #: The step is active but has no session after the start grace.
    NOT_STARTED = "not_started"
    #: The step has a session but the session does not know the activation.
    UNDELIVERED = "undelivered"
    #: A turn ended with the step still open.
    UNCLOSED = "unclosed"
    #: The step's Forge session failed, stopped or no longer exists.
    SESSION_GONE = "session_gone"


class RecoveryAction(StrEnum):
    """What an operator may do about a stuck or failed step, or a run."""

    RETRY = "retry"
    REASSIGN = "reassign"
    ABORT = "abort"


class GateDecision(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


class ActorKind(StrEnum):
    PERSON = "person"
    AGENT = "agent"


class StepEngineError(RuntimeError):
    """Base class of every refusal the step engine raises."""


class StepFencingError(StepEngineError):
    """A close named a step that is not the one currently owned by the caller.

    Raised for an unknown activation, a mismatched attempt or session, and a step
    that is no longer ``active`` or ``waiting`` (done, stale, canceled, ...). It is
    never swallowed: a close that loses to a newer activation must be seen.
    """

    def __init__(self, activation_id: UUID, reason: str) -> None:
        self.activation_id = activation_id
        self.reason = reason
        super().__init__(f"close of activation {activation_id} rejected: {reason}")


class InvalidEndingError(StepEngineError):
    """A close is well-formed but the step's contract does not allow it.

    The step stays as it was, so its session can close it again correctly.
    """


class GateRefusedError(StepEngineError):
    """A gate decision was refused: wrong actor, unknown gate, or no open gate."""


class RunStateError(StepEngineError):
    """The call is not valid for the run's state (already launched, already finished, ...)."""


class RecoveryRefusedError(StepEngineError):
    """A recovery action makes no sense for the step or run as it is; ``reason`` says why.

    ``missing`` is set when the run has no such step at all.
    """

    def __init__(self, action: RecoveryAction, reason: str, *, missing: bool = False) -> None:
        self.action = action
        self.reason = reason
        self.missing = missing
        super().__init__(f"{action.value} refused: {reason}")


@dataclass(frozen=True)
class WaitingOn:
    """What a waiting step is waiting for. The step stays owned until it resolves."""

    kind: WaitingOnKind
    ref: str
    since: datetime

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "ref": self.ref, "since": self.since.isoformat()}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> WaitingOn:
        return cls(
            kind=WaitingOnKind(raw["kind"]),
            ref=str(raw["ref"]),
            since=datetime.fromisoformat(raw["since"]),
        )


@dataclass(frozen=True)
class StepInput:
    """One event a step consumed, and the edge it arrived over."""

    seq: int
    event_type: str
    edge_id: str
    node_id: str
    member: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "edge_id": self.edge_id,
            "node_id": self.node_id,
            "member": self.member,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> StepInput:
        return cls(
            seq=int(raw["seq"]),
            event_type=str(raw["event_type"]),
            edge_id=str(raw["edge_id"]),
            node_id=str(raw["node_id"]),
            member=raw.get("member"),
        )


@dataclass(frozen=True)
class WorkflowStep:
    """One activation of a node (and member) in a run."""

    id: UUID
    run_id: UUID
    node_id: str
    member: str | None
    kind: StepKind
    attempt: int
    generation: int
    state: StepState
    created_at: datetime
    session_id: str | None = None
    inputs: tuple[StepInput, ...] = ()
    verdict: str | None = None
    outcome: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    waiting_on: WaitingOn | None = None
    deadline_at: datetime | None = None
    activated_at: datetime | None = None
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        if (self.state is StepState.WAITING) != (self.waiting_on is not None):
            raise ValueError("a step has a waiting_on handle exactly while it is waiting")

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STEP_STATES

    def with_state(self, state: StepState, **changes: Any) -> WorkflowStep:
        """The step in another state; ``waiting_on`` is dropped unless the step is waiting."""
        if state is not StepState.WAITING:
            changes.setdefault("waiting_on", None)
        return replace(self, state=state, **changes)


@dataclass(frozen=True)
class RunEvent:
    """One entry of a run's append-only event log."""

    run_id: UUID
    seq: int
    event_type: str
    generation: int
    created_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    source_activation_id: UUID | None = None

    @property
    def is_engine_event(self) -> bool:
        return self.event_type.startswith(ENGINE_EVENT_PREFIX)

    @property
    def node_id(self) -> str:
        return str(self.payload.get("node_id") or "")

    @property
    def member(self) -> str | None:
        member = self.payload.get("member")
        return str(member) if member else None

    @property
    def verdict(self) -> str:
        return str(self.payload.get("verdict") or "")

    @property
    def outcome(self) -> dict[str, Any]:
        outcome = self.payload.get("outcome")
        return outcome if isinstance(outcome, dict) else {}


@dataclass(frozen=True)
class StepFlag:
    """A durable mark that an open step is stuck: one per step and kind, raised once.

    ``since`` is when the condition began as far as Ting can tell (the deadline, the
    activation, the end of the turn), ``raised_at`` when a sweep first saw it. A flag
    stays on the row after it is resolved, with how it ended.
    """

    run_id: UUID
    activation_id: UUID
    kind: FlagKind
    since: datetime
    raised_at: datetime
    detail: str = ""
    notified_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution: str = ""
    #: The one automatic action tried for this flag: when it was claimed, when it finished
    #: and why it failed ("" if it did not). It is never tried twice for one flag.
    action_at: datetime | None = None
    action_done_at: datetime | None = None
    action_error: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def action_attempted(self) -> bool:
        return self.action_at is not None


@dataclass(frozen=True)
class RunState:
    """Everything the engine reads about a run: its steps, its event log and its flags."""

    run_id: UUID
    steps: tuple[WorkflowStep, ...] = ()
    events: tuple[RunEvent, ...] = ()
    flags: tuple[StepFlag, ...] = ()

    @property
    def generation(self) -> int:
        """The current rework generation: the highest generation any event carries."""
        return max((event.generation for event in self.events), default=0)

    @property
    def finished(self) -> bool:
        return any(event.event_type in TERMINAL_RUN_EVENTS for event in self.events)

    @property
    def launched(self) -> bool:
        return any(event.event_type == EVENT_RUN_LAUNCHED for event in self.events)

    @property
    def next_seq(self) -> int:
        return max((event.seq for event in self.events), default=0) + 1

    def step(self, activation_id: UUID) -> WorkflowStep | None:
        return next((step for step in self.steps if step.id == activation_id), None)

    def event(self, seq: int) -> RunEvent | None:
        return next((event for event in self.events if event.seq == seq), None)

    def open_steps(self) -> tuple[WorkflowStep, ...]:
        return tuple(step for step in self.steps if step.is_open)

    def flag(self, activation_id: UUID, kind: FlagKind) -> StepFlag | None:
        return next(
            (
                flag
                for flag in self.flags
                if flag.activation_id == activation_id and flag.kind is kind
            ),
            None,
        )

    def live_flags(self) -> tuple[StepFlag, ...]:
        """Unresolved flags of steps that are still open: what is stuck right now."""
        open_ids = {step.id for step in self.steps if step.is_open}
        return tuple(
            flag for flag in self.flags if not flag.is_resolved and flag.activation_id in open_ids
        )


@dataclass(frozen=True)
class Ending:
    """How a step activation ends. Every activation ends in exactly one of these.

    ``verdict`` hands off: the verdict maps to an event through the persona's
    ``produces`` contract, and that event follows the graph's edges. ``waiting``
    parks the step with a ``waiting_on`` handle; the step stays owned and is closed
    again when the handle resolves. ``done`` ends a member that hands nothing off.
    ``failed`` ends the step's own path, with a reason.
    """

    kind: EndingKind
    verdict: str | None = None
    outcome: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    waiting_on: WaitingOn | None = None

    def __post_init__(self) -> None:
        if self.kind is EndingKind.VERDICT and not (self.verdict or "").strip():
            raise InvalidEndingError("a verdict ending needs a verdict")
        if self.kind is EndingKind.WAITING and self.waiting_on is None:
            raise InvalidEndingError("a waiting ending needs a waiting_on handle")
        if self.kind is EndingKind.FAILED and not self.note.strip():
            raise InvalidEndingError("a failed ending needs the reason in note")
        try:
            json.dumps([self.outcome, self.evidence], allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise InvalidEndingError("outcome and evidence must be finite JSON values") from exc

    @classmethod
    def with_verdict(
        cls,
        verdict: str,
        *,
        outcome: dict[str, Any] | None = None,
        note: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> Ending:
        return cls(
            EndingKind.VERDICT,
            verdict=verdict,
            outcome=outcome or {},
            note=note,
            evidence=evidence or {},
        )

    @classmethod
    def waiting(cls, waiting_on: WaitingOn, *, note: str = "") -> Ending:
        return cls(EndingKind.WAITING, waiting_on=waiting_on, note=note)

    @classmethod
    def done(cls, *, note: str = "") -> Ending:
        return cls(EndingKind.DONE, note=note)

    @classmethod
    def failed(cls, reason: str) -> Ending:
        return cls(EndingKind.FAILED, note=reason)


@dataclass(frozen=True)
class GateActor:
    """Who resolved a gate."""

    kind: ActorKind
    id: str


def activation_id_for(run_id: UUID, node_id: str, member: str | None, attempt: int) -> UUID:
    """The id of one activation: deterministic, so repeating it cannot duplicate the row."""
    return uuid5(NAMESPACE_URL, f"niuu:workflow-step:{run_id}:{node_id}:{member or ''}:{attempt}")


def check_fence(
    step: WorkflowStep | None,
    *,
    activation_id: UUID,
    session_id: str | None,
    attempt: int,
) -> WorkflowStep:
    """Accept a close only for the row that is open and owned by this session and attempt.

    A close is accepted only for an ``active`` or ``waiting`` row whose id, attempt
    and session all match. Anything else raises ``StepFencingError``.
    """
    if step is None:
        raise StepFencingError(activation_id, "no such activation")
    if step.state not in CLOSABLE_STEP_STATES:
        raise StepFencingError(
            activation_id,
            f"the step is {step.state.value}, not active or waiting",
        )
    if step.attempt != attempt:
        raise StepFencingError(
            activation_id,
            f"attempt {attempt} is not the step's attempt {step.attempt}",
        )
    if step.session_id != session_id:
        raise StepFencingError(
            activation_id,
            f"session {session_id!r} does not own the step (owner {step.session_id!r})",
        )
    return step
