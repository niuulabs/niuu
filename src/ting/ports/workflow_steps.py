"""Ports of the Ting step engine: the ledger, the sessions and the run definitions.

The engine service (``ting.domain.services.workflow_step_engine``) depends only on
these. ``WorkflowStepRepository`` is implemented over Postgres, ``StepSessionPort`` over
Forge's sessions, ``OfferSigner`` over the platform signer, and ``StepRunDefinitions`` over
the campaigns; tests fake them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
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
class RunUnit:
    """A launched run: its root session and the session of every member it hosts.

    One run is one unit of compute: the root and every member start together at launch,
    and the members are fixed from then on.
    """

    root_id: str
    #: Stage member alias -> that member's session id.
    members: Mapping[str, str]


class SessionLiveness(StrEnum):
    """Whether a member's session can still run a step, as its Forge reports it."""

    #: Up and answering.
    RUNNING = "running"
    #: Created or starting; it has not come up yet.
    STARTING = "starting"
    #: Stopped, stopping or archived.
    STOPPED = "stopped"
    FAILED = "failed"
    #: Forge no longer has the session.
    GONE = "gone"


class StepSessionPort(ABC):
    """The unit of harness sessions that runs a run's stage members.

    A run is one unit: the root session (the run's room, which carries Ting's offers onto
    the mesh) and one session per stage member, all started together at launch and kept
    for the whole run. Ting never talks to a member session about a step: an offer goes to
    the root, the members answer on the mesh, and each Skuld reports what it did to Forge.
    """

    @abstractmethod
    async def launch_run(self, run_id: UUID, members: Mapping[str, PersonaRef]) -> RunUnit:
        """Create the run's root and every member hosted by it, then start the unit.

        Idempotent: a run that is already launched is returned as it is. A member the unit
        was not launched with cannot be added to it, and that raises.
        """

    @abstractmethod
    async def publish_offer(self, run_id: UUID, offer: Mapping[str, Any]) -> None:
        """Hand a signed offer to the run's root, whose Skuld publishes it on the run's mesh.

        ``offer`` is the body of the room's ``offer`` operation: the ``step.offered``
        payload and Ting's attestation of it. Raises when the root cannot take it (its
        broker is not up, or its Skuld refuses the offer); the caller holds the offer back.
        """

    @abstractmethod
    async def stop_run_sessions(self, run_id: UUID) -> None:
        """Stop every session of the run (it completed, failed or was canceled)."""

    @abstractmethod
    async def session_failure(self, run_id: UUID, session_id: str) -> str:
        """Why the session failed, as its Forge recorded it; empty when it recorded nothing.

        A broker that could not start (its harness refused the persona, its mesh never came
        up) reports why, and that reason is the step's. Raises when Forge cannot be asked.
        """

    @abstractmethod
    async def session_liveness(self, run_id: UUID, session_id: str) -> SessionLiveness:
        """Whether the session is up, still starting, or ended, as its Forge reports it.

        Raises when Forge cannot be asked: a question that cannot be answered is never
        reported as an answer.
        """

    @abstractmethod
    async def replace_session(
        self,
        run_id: UUID,
        member: str,
        persona_ref: PersonaRef,
        replaces: str | None,
    ) -> str:
        """Restart the member's harness in place and return its session id (it does not change).

        A running unit cannot take a new member, so a reassigned member is restarted under a
        new launch of the same session. ``replaces`` is the session the caller believes the
        member has; a different one raises.
        """


class OfferSigner(ABC):
    """Signs the offers Ting hands to a run's root, as the platform service Ting is."""

    @abstractmethod
    def sign(self, offer: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """The attestation of ``offer``, or ``None`` when the deployment runs unsigned.

        ``None`` is the operator's decision (mesh attestation not required), never a
        failure to sign: a signer that cannot sign raises.
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
    async def reopen_flag(self, flag: StepFlag) -> bool:
        """Raise a resolved flag again as ``flag`` (a kind that ``recurs``): its resolution,
        notification and action are cleared. True if a resolved flag was reopened."""

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
