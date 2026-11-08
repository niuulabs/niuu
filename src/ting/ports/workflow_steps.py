"""Ports of the Ting step engine: the ledger, the sessions and the run definitions.

The engine service (``ting.domain.services.workflow_step_engine``) depends only on
these. ``WorkflowStepRepository`` is implemented over Postgres;
``StepSessionPort`` and ``StepRunDefinitions`` are filled in when workflow steps are
wired to harness sessions (NIU-1198), and are faked in tests until then.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from ting.domain.workflow_steps import FlagKind, RunEvent, RunState, StepFlag, WorkflowStep


@dataclass(frozen=True)
class PersonaRef:
    """The pinned persona a stage member runs as."""

    alias: str
    id: str
    revision: str
    digest: str


@dataclass(frozen=True)
class StepRunDefinition:
    """What a run executes: its resolved graph and the personas its stages name.

    ``graph`` is the portable graph with every ``include`` already resolved;
    ``persona_definitions`` maps each stage member alias to its persona document and
    ``persona_refs`` to the pin the session must run.
    """

    graph: Mapping[str, Any]
    persona_definitions: Mapping[str, Mapping[str, Any]]
    persona_refs: Mapping[str, PersonaRef]
    workflow_name: str = ""


@dataclass(frozen=True)
class DeliveredEvent:
    """An event a step consumed, with its payload, as the member's session sees it."""

    seq: int
    event_type: str
    edge_id: str
    node_id: str
    member: str | None
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class StepActivation:
    """A structured step activation, delivered to the member's session."""

    run_id: UUID
    activation_id: UUID
    node_id: str
    node_label: str
    member: str
    attempt: int
    generation: int
    session_id: str
    inputs: tuple[DeliveredEvent, ...]
    deadline_at: datetime | None
    valid_verdicts: tuple[str, ...] = field(default=())
    #: The endings the step may close with.
    valid_endings: tuple[str, ...] = field(default=("verdict", "waiting", "done", "failed"))
    #: The persona's ``produces.schema`` verbatim, or ``None`` when it declares none.
    outcome_schema: Mapping[str, Any] | None = None
    #: What each valid verdict hands off: verdict -> the event it produces.
    verdict_events: Mapping[str, str] = field(default_factory=dict)
    #: The event every verdict produces when the persona names no per-verdict map.
    default_event: str = ""
    workflow_name: str = ""


class SessionLiveness(StrEnum):
    """Whether a step's session can still run the step, as its Forge reports it."""

    #: Up and answering: a step can be asked about.
    RUNNING = "running"
    #: Created or starting; it has not come up yet.
    STARTING = "starting"
    #: Stopped, stopping or archived.
    STOPPED = "stopped"
    FAILED = "failed"
    #: Forge no longer has the session.
    GONE = "gone"


@dataclass(frozen=True)
class StepObservation:
    """What Ting can see of a step's session right now.

    ``reported`` is the session's own durable record of the step (``state`` is
    ``active``, ``waiting`` or ``closed``, with the ``ending`` once it ended); ``None``
    means the session does not know the activation, or was not asked because it is not
    running.
    """

    liveness: SessionLiveness
    reported: Mapping[str, Any] | None = None


class StepSessionPort(ABC):
    """The harness sessions that run a run's stage members.

    There is one session per (run, stage member), kept alive for the whole run and
    reused for the member's later activations, rework included.
    """

    @abstractmethod
    async def ensure_session(self, run_id: UUID, member: str, persona_ref: PersonaRef) -> str:
        """Create the member's session, or return the kept-alive one. Returns its id."""

    @abstractmethod
    async def deliver(self, activation: StepActivation) -> None:
        """Hand a step activation to the member's session."""

    @abstractmethod
    async def stop_run_sessions(self, run_id: UUID) -> None:
        """Stop every session of the run (it completed, failed or was canceled)."""

    @abstractmethod
    async def observe(self, run_id: UUID, session_id: str, activation_id: UUID) -> StepObservation:
        """The session's liveness and, if it is running, what it holds of the activation.

        Raises when Forge or the session cannot be asked: a question that cannot be
        answered is never reported as "the session does not know the step".
        """

    @abstractmethod
    async def session_liveness(self, run_id: UUID, session_id: str) -> SessionLiveness:
        """Whether the session is up, still starting, or ended, as its Forge reports it.

        Asks nothing of the session itself, so it works before the session's broker is
        reachable. Raises when Forge cannot be asked: a question that cannot be answered
        is never reported as an answer.
        """

    @abstractmethod
    async def replace_session(
        self,
        run_id: UUID,
        member: str,
        persona_ref: PersonaRef,
        replaces: str | None,
    ) -> str:
        """Give the member a fresh session in place of ``replaces`` and return its id.

        The replaced session is stopped if it is still up. Later ``ensure_session``
        calls for the member find the replacement, not the replaced session. Asking
        again for a session that was already replaced returns the replacement.
        """


class StepRunDefinitions(ABC):
    """Where the engine finds what a run executes."""

    @abstractmethod
    async def get(self, run_id: UUID) -> StepRunDefinition:
        """The run's definition. Raises when the run is unknown."""


class LedgerTransaction(ABC):
    """One serialised read-decide-write on a single run's ledger.

    Entering the transaction takes the run's lock, so two closes of the same run
    never decide against the same state. Leaving it by an exception writes nothing.
    """

    @abstractmethod
    async def load(self) -> RunState:
        """The run's steps (oldest first), events (by sequence) and flags (oldest first)."""

    @abstractmethod
    async def save_step(self, step: WorkflowStep) -> None:
        """Insert the step, or replace the row with the same id."""

    @abstractmethod
    async def append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        generation: int,
        source_activation_id: UUID | None,
        created_at: datetime,
    ) -> RunEvent:
        """Append to the run's event log; the next sequence number is assigned here."""

    @abstractmethod
    async def raise_flag(self, flag: StepFlag) -> bool:
        """Record the flag unless the step already has one of its kind; True if it is new."""

    @abstractmethod
    async def resolve_flag(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime, resolution: str
    ) -> None:
        """Mark the step's flag of this kind resolved, with how it ended."""

    @abstractmethod
    async def claim_flag_action(self, activation_id: UUID, kind: FlagKind, *, at: datetime) -> bool:
        """Claim the flag's one automatic action; False if it was already claimed."""

    @abstractmethod
    async def finish_flag_action(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime, error: str
    ) -> None:
        """Record how the claimed automatic action ended; ``error`` is "" if it worked."""

    @abstractmethod
    async def mark_flag_notified(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime
    ) -> None:
        """Record that a person was told about the flag."""


class WorkflowStepRepository(ABC):
    """Persistence of the step ledger."""

    @abstractmethod
    def transaction(self, run_id: UUID) -> AbstractAsyncContextManager[LedgerTransaction]:
        """Lock the run's ledger for one decision."""

    @abstractmethod
    async def find_step(self, activation_id: UUID) -> WorkflowStep | None:
        """A step by activation id, whichever run it belongs to."""

    @abstractmethod
    async def load(self, run_id: UUID) -> RunState:
        """The run's ledger (steps, events and flags), without locking it."""

    @abstractmethod
    async def claim_delivery(self, activation_id: UUID, session_id: str, *, at: datetime) -> bool:
        """Mark the step delivered to ``session_id`` if it is still active, bound to that
        session and undelivered; True for the one caller that set it.

        The conditional write is the claim: of several workers waiting on the same step,
        only the one that gets True hands it to the session.
        """

    @abstractmethod
    async def undelivered_steps(self) -> list[WorkflowStep]:
        """Every active stage-member step bound to a session but not yet delivered to it."""
