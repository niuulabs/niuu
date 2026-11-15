"""Test support for the step engine: an in-memory ledger, fake sessions and a driver.

The engine is exercised through its real service with the real bundled workflows;
only the ports (ledger storage, the run's unit of sessions, the offer signer, run
definitions) are faked. ``Run`` plays the members the way their Skuld would: it reports
each member listening, and picks up (or passes) every offer the engine publishes.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from tests.support.full_catalogue import load_full_catalogue as load_system_workflows
from ting.domain.models import WorkflowDefinition
from ting.domain.services.workflow_step_engine import StepRunRequest, WorkflowStepEngine
from ting.domain.step_definition import step_run_definition
from ting.domain.step_engine import EngineSettings
from ting.domain.step_graph import compile_step_graph
from ting.domain.workflow_steps import (
    Ending,
    FlagKind,
    RunEvent,
    RunState,
    StepFlag,
    StepKind,
    WorkflowStep,
)
from ting.ports.workflow_steps import (
    LedgerTransaction,
    OfferSigner,
    PersonaRef,
    RunUnit,
    SessionLiveness,
    StepRunDefinition,
    StepRunDefinitions,
    StepSessionPort,
    WorkflowStepRepository,
)

STEP_DEADLINE_SECONDS = 600.0
#: How Skuld mints a pickup's activation id (``skuld.workflow_steps``): same offer, same id.
_ACTIVATION_NAMESPACE = UUID("6f1c9a52-0d3e-4c7b-9a41-2b8e5d7f1c30")


def minted_activation_id(offer_id: str, session_id: str, attempt: int) -> UUID:
    """The activation id Skuld mints for a pickup: one offer, session and attempt, one id."""
    return uuid5(_ACTIVATION_NAMESPACE, f"{offer_id}:{session_id}:{attempt}")


def bundled(name: str) -> WorkflowDefinition:
    """A shipped workflow by its display name, exactly as the package loads it."""
    for workflow in load_system_workflows():
        if workflow.name == name:
            return workflow
    raise AssertionError(f"no bundled workflow named {name!r}")


def definition_of(workflow: WorkflowDefinition, graph: dict[str, Any] | None = None) -> Any:
    """What the engine runs for a workflow, optionally with its graph swapped."""
    definition = step_run_definition(workflow)
    if graph is None:
        return definition
    return StepRunDefinition(
        graph=graph,
        persona_definitions=definition.persona_definitions,
        persona_refs=definition.persona_refs,
    )


class _FakeTransaction(LedgerTransaction):
    def __init__(self, repository: InMemoryStepRepository, run_id: UUID) -> None:
        self._repository = repository
        self._run_id = run_id

    async def load(self) -> RunState:
        return self._repository.snapshot(self._run_id)

    async def save_step(self, step: WorkflowStep) -> None:
        self._repository.steps[step.id] = step

    async def append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        generation: int,
        source_activation_id: UUID | None,
        created_at: datetime,
    ) -> RunEvent:
        events = self._repository.events.setdefault(self._run_id, [])
        event = RunEvent(
            run_id=self._run_id,
            seq=len(events) + 1,
            event_type=event_type,
            generation=generation,
            created_at=created_at,
            payload=copy.deepcopy(payload),
            source_activation_id=source_activation_id,
        )
        events.append(event)
        return event

    async def raise_flag(self, flag: StepFlag) -> bool:
        key = (flag.activation_id, flag.kind)
        if key in self._repository.flags:
            return False
        self._repository.flags[key] = flag
        return True

    async def reopen_flag(self, flag: StepFlag) -> bool:
        key = (flag.activation_id, flag.kind)
        current = self._repository.flags.get(key)
        if current is None or not current.is_resolved:
            return False
        self._repository.flags[key] = flag
        return True

    async def resolve_flag(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime, resolution: str
    ) -> None:
        flag = self._repository.flags[(activation_id, kind)]
        if not flag.is_resolved:
            self._repository.flags[(activation_id, kind)] = replace(
                flag, resolved_at=at, resolution=resolution
            )

    async def claim_flag_action(self, activation_id: UUID, kind: FlagKind, *, at: datetime) -> bool:
        key = (activation_id, kind)
        flag = self._repository.flags[key]
        if flag.action_at is not None:
            return False
        self._repository.flags[key] = replace(flag, action_at=at)
        return True

    async def finish_flag_action(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime, error: str
    ) -> None:
        key = (activation_id, kind)
        self._repository.flags[key] = replace(
            self._repository.flags[key], action_done_at=at, action_error=error
        )

    async def mark_flag_notified(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime
    ) -> None:
        key = (activation_id, kind)
        self._repository.flags[key] = replace(self._repository.flags[key], notified_at=at)


class InMemoryStepRepository(WorkflowStepRepository):
    """The ledger in memory; a transaction that raises leaves nothing behind."""

    def __init__(self) -> None:
        self.steps: dict[UUID, WorkflowStep] = {}
        self.events: dict[UUID, list[RunEvent]] = {}
        self.flags: dict[tuple[UUID, FlagKind], StepFlag] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    def snapshot(self, run_id: UUID) -> RunState:
        steps = tuple(
            sorted(
                (step for step in self.steps.values() if step.run_id == run_id),
                key=lambda step: (step.created_at, str(step.id)),
            )
        )
        flags = tuple(
            sorted(
                (flag for flag in self.flags.values() if flag.run_id == run_id),
                key=lambda flag: (flag.raised_at, str(flag.activation_id), flag.kind.value),
            )
        )
        return RunState(
            run_id=run_id,
            steps=steps,
            events=tuple(self.events.get(run_id, ())),
            flags=flags,
        )

    @asynccontextmanager
    async def transaction(self, run_id: UUID) -> AsyncIterator[LedgerTransaction]:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            steps_before = dict(self.steps)
            events_before = {key: list(value) for key, value in self.events.items()}
            flags_before = dict(self.flags)
            try:
                yield _FakeTransaction(self, run_id)
            except BaseException:
                self.steps = steps_before
                self.events = events_before
                self.flags = flags_before
                raise

    async def find_step(self, activation_id: UUID) -> WorkflowStep | None:
        return self.steps.get(activation_id)

    async def load(self, run_id: UUID) -> RunState:
        return self.snapshot(run_id)


class FakeSessions(StepSessionPort):
    """The run's unit: a root and one session per member, and a record of every offer."""

    def __init__(self) -> None:
        self.units: dict[UUID, RunUnit] = {}
        self.launches: list[tuple[UUID, tuple[str, ...]]] = []
        #: Every body handed to the root's ``offer`` operation, in order.
        self.offers: list[dict[str, Any]] = []
        self.stopped: list[UUID] = []
        self.fail_launch = False
        #: How many publications fail before one lands (the root's broker is not up).
        self.fail_publish = 0
        #: Per session id: what ``session_liveness`` says. Unlisted sessions are running.
        self.liveness: dict[str, SessionLiveness] = {}
        self.failures: dict[str, str] = {}
        self.fail_observe: set[str] = set()
        self.replaced: list[tuple[UUID, str, str | None]] = []
        self.fail_replace = False
        #: When set, stopping the run's sessions raises it (after trying every session).
        self.fail_stop: Exception | None = None

    async def launch_run(self, run_id: UUID, members: Mapping[str, PersonaRef]) -> RunUnit:
        if self.fail_launch:
            raise RuntimeError("no capacity for the run's unit")
        self.launches.append((run_id, tuple(members)))
        if run_id not in self.units:
            self.units[run_id] = RunUnit(
                root_id=f"root-{run_id}", members={alias: f"session-{alias}" for alias in members}
            )
        return self.units[run_id]

    async def publish_offer(self, run_id: UUID, offer: Mapping[str, Any]) -> None:
        if self.fail_publish > 0:
            self.fail_publish -= 1
            raise RuntimeError("the root's broker is not up yet")
        self.offers.append(dict(offer))

    async def stop_run_sessions(self, run_id: UUID) -> None:
        if self.fail_stop is not None:
            raise self.fail_stop
        self.stopped.append(run_id)

    async def session_failure(self, run_id: UUID, session_id: str) -> str:
        return self.failures.get(session_id, "")

    async def session_liveness(self, run_id: UUID, session_id: str) -> SessionLiveness:
        if session_id in self.fail_observe:
            raise ConnectionError(f"cannot ask {session_id}")
        return self.liveness.get(session_id, SessionLiveness.RUNNING)

    async def replace_session(
        self,
        run_id: UUID,
        member: str,
        persona_ref: PersonaRef,
        replaces: str | None,
    ) -> str:
        if self.fail_replace:
            raise RuntimeError("cannot restart the member")
        self.replaced.append((run_id, member, replaces))
        return replaces or f"session-{member}"


class FakeSigner(OfferSigner):
    """Signs as Ting would, and remembers what it signed."""

    def __init__(self) -> None:
        self.signed: list[dict[str, Any]] = []
        self.unsigned = False

    def sign(self, offer: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if self.unsigned:
            return None
        self.signed.append(dict(offer))
        return {"signed_by": "service:ting", "offer_id": offer["offer_id"]}


class FixedDefinitions(StepRunDefinitions):
    def __init__(self, definition: StepRunDefinition) -> None:
        self._definition = definition

    async def get(self, run_id: UUID) -> StepRunDefinition:
        return self._definition


class Recorder:
    """Stands in for the observability counters so a test can read them."""

    def __init__(self) -> None:
        self.counts: list[tuple[str, dict[str, str]]] = []
        self.gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def count(self, name: str, *, attributes: dict[str, str] | None = None, **_: Any) -> None:
        self.counts.append((name, attributes or {}))

    def total(self, name: str) -> int:
        return sum(1 for counted, _ in self.counts if counted == name)

    def counted(self, name: str, **attributes: str) -> int:
        """How many times ``name`` was counted with exactly these attributes."""
        return sum(1 for counted, got in self.counts if counted == name and got == attributes)

    def gauge(
        self, name: str, value: float, *, attributes: dict[str, str] | None = None, **_: Any
    ) -> None:
        self.gauges[(name, tuple(sorted((attributes or {}).items())))] = value

    def gauge_value(self, name: str, **attributes: str) -> float | None:
        return self.gauges.get((name, tuple(sorted(attributes.items()))))


@dataclass
class Run:
    """Drives one run of a workflow through the engine, the way harness sessions would."""

    engine: WorkflowStepEngine
    repository: InMemoryStepRepository
    sessions: FakeSessions
    run_id: UUID
    definition: StepRunDefinition
    clock: list[datetime] = field(default_factory=list)
    signer: FakeSigner = field(default_factory=FakeSigner)
    #: Members whose Skuld passes every offer instead of taking it up.
    passers: set[str] = field(default_factory=set)
    #: Members that never report listening (their session is still booting).
    deaf: set[str] = field(default_factory=set)
    #: Play the members after every call that can publish an offer.
    auto: bool = True
    _answered: set[tuple[str, str]] = field(default_factory=set)
    _started: set[str] = field(default_factory=set)

    @property
    def state(self) -> RunState:
        return self.repository.snapshot(self.run_id)

    @property
    def graph(self) -> Any:
        return compile_step_graph(self.definition.graph, self.definition.persona_definitions)

    async def launch(self, payload: dict[str, Any] | None = None) -> RunState:
        await self.engine.launch(StepRunRequest(self.run_id, payload or {}))
        return await self.pump()

    # -- the members, as their Skuld plays them -------------------------------

    @property
    def offers(self) -> list[dict[str, Any]]:
        """The ``step.offered`` payload of every offer handed to the root, in order."""
        return [body["offer"] for body in self.sessions.offers]

    def addressed(self, offer: dict[str, Any]) -> list[str]:
        return [contract["persona"]["alias"] for contract in offer["contracts"]]

    async def listen(self, member: str, start_id: str = "start-1") -> RunState:
        """Report a member listening (its subscriptions are up)."""
        unit = self.sessions.units[self.run_id]
        self._started.add(member)
        return await self.engine.note_listening(
            run_id=self.run_id,
            session_id=unit.members[member],
            member=member,
            start_id=start_id,
        )

    def activation_id(self, offer: dict[str, Any], member: str) -> UUID:
        unit = self.sessions.units[self.run_id]
        return uuid5(
            _ACTIVATION_NAMESPACE, f"{offer['offer_id']}:{unit.members[member]}:{offer['attempt']}"
        )

    async def pickup(self, offer: dict[str, Any], member: str) -> WorkflowStep:
        """A member takes the offer up; returns the step its pickup made."""
        unit = self.sessions.units[self.run_id]
        activation = self.activation_id(offer, member)
        await self.engine.note_picked_up(
            run_id=self.run_id,
            session_id=unit.members[member],
            member=member,
            offer_id=UUID(offer["offer_id"]),
            activation_id=activation,
            attempt=offer["attempt"],
        )
        self._answered.add((offer["offer_id"], member))
        step = self.state.step(activation)
        assert step is not None
        return step

    async def decline(self, offer: dict[str, Any], member: str, reason: str = "filters") -> None:
        """A member passes the offer: it is not its to take."""
        unit = self.sessions.units[self.run_id]
        await self.engine.note_passed(
            run_id=self.run_id,
            session_id=unit.members[member],
            member=member,
            offer_id=UUID(offer["offer_id"]),
            reason=reason,
        )
        self._answered.add((offer["offer_id"], member))

    async def pump(self) -> RunState:
        """Play every member: listen once, then take up or pass each offer published so far."""
        if not self.auto or self.run_id not in self.sessions.units:
            return self.state
        for member in self.sessions.units[self.run_id].members:
            if member not in self._started and member not in self.deaf:
                await self.listen(member)
        for offer in list(self.offers):
            for member in self.addressed(offer):
                if (offer["offer_id"], member) in self._answered:
                    continue
                if member in self.deaf:
                    continue
                if member in self.passers:
                    await self.decline(offer, member)
                    continue
                await self.pickup(offer, member)
        return self.state

    def steps(self, node_id: str, member: str | None = None) -> list[WorkflowStep]:
        """The pickups (and gate, wait and end steps) of a node; its offers are ``offer_rows``."""
        return [
            step
            for step in self.state.steps
            if step.node_id == node_id
            and step.kind is not StepKind.OFFER
            and (member is None or step.member == member)
        ]

    def offer_rows(self, node_id: str) -> list[WorkflowStep]:
        """The ledger rows of a node's offers, oldest first."""
        return [
            step
            for step in self.state.steps
            if step.node_id == node_id and step.kind is StepKind.OFFER
        ]

    def current(self, node_id: str, member: str | None = None) -> WorkflowStep:
        """The one step of the node (and member) that is open right now."""
        open_steps = [step for step in self.steps(node_id, member) if step.is_open]
        assert len(open_steps) == 1, (
            f"expected one open step at {node_id}/{member}, found "
            f"{[(step.attempt, step.state.value) for step in self.steps(node_id, member)]}"
        )
        return open_steps[0]

    def states(self, node_id: str, member: str | None = None) -> list[str]:
        return [step.state.value for step in self.steps(node_id, member)]

    def event_types(self) -> list[str]:
        return [event.event_type for event in self.state.events]

    async def close(
        self,
        step: WorkflowStep,
        verdict: str,
        outcome: dict[str, Any] | None = None,
    ) -> RunState:
        await self.engine.record_close(
            step.id,
            step.session_id,
            step.attempt,
            Ending.with_verdict(verdict, outcome=outcome or {}),
        )
        return await self.pump()

    async def fail(self, step: WorkflowStep, reason: str) -> RunState:
        await self.engine.record_close(
            step.id, step.session_id, step.attempt, Ending.failed(reason)
        )
        return await self.pump()

    def finished_with(self) -> str | None:
        for event in self.state.events:
            if event.event_type in {"engine.run.completed", "engine.run.failed"}:
                return event.event_type
        return None

    def failure_reason(self) -> str:
        failed = [e for e in self.state.events if e.event_type == "engine.run.failed"]
        assert failed, "the run did not fail"
        return str(failed[0].payload["reason"])


def make_run(
    workflow: WorkflowDefinition,
    *,
    graph: dict[str, Any] | None = None,
    monkeypatch: Any = None,
    recorder: Recorder | None = None,
) -> Run:
    """A run of ``workflow`` (optionally with its graph swapped) on in-memory ports."""
    definition = definition_of(workflow, graph)
    repository = InMemoryStepRepository()
    sessions = FakeSessions()
    ticks = [datetime(2026, 11, 8, 9, 0, tzinfo=UTC)]

    def clock() -> datetime:
        ticks[0] = ticks[0] + timedelta(seconds=1)
        return ticks[0]

    signer = FakeSigner()
    engine = WorkflowStepEngine(
        repository=repository,
        sessions=sessions,
        offers=signer,
        definitions=FixedDefinitions(definition),
        settings=EngineSettings(step_deadline_seconds=STEP_DEADLINE_SECONDS),
        clock=clock,
    )
    if monkeypatch is not None and recorder is not None:
        monkeypatch.setattr(
            "ting.domain.services.workflow_step_engine.get_observability", lambda: recorder
        )
    return Run(engine, repository, sessions, uuid4(), definition, ticks, signer)


def custom_run(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    personas: dict[str, dict[str, Any]],
) -> Run:
    """A run of a hand-written graph, to pin one engine rule at a time.

    ``personas`` maps an alias to the part of a persona document the engine reads:
    ``{"produces": {"event_type_map": ...}, "consumes": {"event_types": ...}}``.
    """
    definition = StepRunDefinition(
        graph={"nodes": nodes, "edges": edges},
        persona_definitions=personas,
        persona_refs={
            alias: PersonaRef(alias, alias, "rev", "sha256:" + "0" * 64) for alias in personas
        },
    )
    repository = InMemoryStepRepository()
    sessions = FakeSessions()
    ticks = [datetime(2026, 11, 8, 9, 0, tzinfo=UTC)]

    def clock() -> datetime:
        ticks[0] = ticks[0] + timedelta(seconds=1)
        return ticks[0]

    signer = FakeSigner()
    engine = WorkflowStepEngine(
        repository=repository,
        sessions=sessions,
        offers=signer,
        definitions=FixedDefinitions(definition),
        settings=EngineSettings(step_deadline_seconds=STEP_DEADLINE_SECONDS),
        clock=clock,
    )
    return Run(engine, repository, sessions, uuid4(), definition, ticks, signer)
