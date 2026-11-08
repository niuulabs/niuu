"""Test support for the step engine: an in-memory ledger, fake sessions and a driver.

The engine is exercised through its real service with the real bundled workflows;
only the ports (ledger storage, harness sessions, run definitions) are faked.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

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
    WorkflowStep,
)
from ting.ports.workflow_steps import (
    LedgerTransaction,
    PersonaRef,
    SessionLiveness,
    StepActivation,
    StepObservation,
    StepRunDefinition,
    StepRunDefinitions,
    StepSessionPort,
    WorkflowStepRepository,
)
from ting.system_workflows import load_system_workflows

STEP_DEADLINE_SECONDS = 600.0
SESSION_POLL_SECONDS = 0.01


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
    """Kept-alive sessions, one per (run, member), and a record of every delivery."""

    def __init__(self) -> None:
        self.sessions: dict[tuple[UUID, str], str] = {}
        self.created: list[tuple[UUID, str]] = []
        self.delivered: list[StepActivation] = []
        self.stopped: list[UUID] = []
        self.fail_ensure: set[str] = set()
        self.fail_deliver: set[str] = set()
        #: Per session id: what ``observe`` says. Unlisted sessions are running and hold nothing.
        self.liveness: dict[str, SessionLiveness] = {}
        #: Per (session id, activation id): the session's durable record of the step.
        self.reported: dict[tuple[str, UUID], dict[str, Any]] = {}
        self.fail_observe: set[str] = set()
        self.replaced: list[tuple[UUID, str, str | None]] = []
        self.fail_replace = False

    async def ensure_session(self, run_id: UUID, member: str, persona_ref: PersonaRef) -> str:
        if member in self.fail_ensure:
            raise RuntimeError(f"no capacity for {member}")
        key = (run_id, member)
        if key not in self.sessions:
            self.sessions[key] = f"session-{member}"
            self.created.append(key)
        return self.sessions[key]

    async def deliver(self, activation: StepActivation) -> None:
        if activation.member in self.fail_deliver:
            raise RuntimeError(f"{activation.member} is unreachable")
        self.delivered.append(activation)

    async def stop_run_sessions(self, run_id: UUID) -> None:
        self.stopped.append(run_id)

    async def session_liveness(self, run_id: UUID, session_id: str) -> SessionLiveness:
        if session_id in self.fail_observe:
            raise ConnectionError(f"cannot ask {session_id}")
        return self.liveness.get(session_id, SessionLiveness.RUNNING)

    async def observe(self, run_id: UUID, session_id: str, activation_id: UUID) -> StepObservation:
        if session_id in self.fail_observe:
            raise ConnectionError(f"cannot ask {session_id}")
        liveness = self.liveness.get(session_id, SessionLiveness.RUNNING)
        if liveness is not SessionLiveness.RUNNING:
            return StepObservation(liveness)
        reported = self.reported.get((session_id, activation_id))
        if reported is None and any(
            item.activation_id == activation_id and item.session_id == session_id
            for item in self.delivered
        ):
            reported = {"state": "active", "activation_id": str(activation_id), "ending": None}
        return StepObservation(liveness, reported)

    async def replace_session(
        self,
        run_id: UUID,
        member: str,
        persona_ref: PersonaRef,
        replaces: str | None,
    ) -> str:
        if self.fail_replace:
            raise RuntimeError("cannot create a replacement session")
        self.replaced.append((run_id, member, replaces))
        fresh = f"session-{member}-r{len(self.replaced) + 1}"
        self.sessions[(run_id, member)] = fresh
        if replaces is not None:
            self.liveness[replaces] = SessionLiveness.STOPPED
        return fresh


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

    @property
    def state(self) -> RunState:
        return self.repository.snapshot(self.run_id)

    @property
    def graph(self) -> Any:
        return compile_step_graph(self.definition.graph, self.definition.persona_definitions)

    async def launch(self, payload: dict[str, Any] | None = None) -> RunState:
        return await self.engine.launch(StepRunRequest(self.run_id, payload or {}))

    def steps(self, node_id: str, member: str | None = None) -> list[WorkflowStep]:
        return [
            step
            for step in self.state.steps
            if step.node_id == node_id and (member is None or step.member == member)
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
        return await self.engine.record_close(
            step.id,
            step.session_id,
            step.attempt,
            Ending.with_verdict(verdict, outcome=outcome or {}),
        )

    async def fail(self, step: WorkflowStep, reason: str) -> RunState:
        return await self.engine.record_close(
            step.id, step.session_id, step.attempt, Ending.failed(reason)
        )

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

    engine = WorkflowStepEngine(
        repository=repository,
        sessions=sessions,
        definitions=FixedDefinitions(definition),
        settings=EngineSettings(
            step_deadline_seconds=STEP_DEADLINE_SECONDS, session_poll_seconds=SESSION_POLL_SECONDS
        ),
        clock=clock,
    )
    if monkeypatch is not None and recorder is not None:
        monkeypatch.setattr(
            "ting.domain.services.workflow_step_engine.get_observability", lambda: recorder
        )
    return Run(engine, repository, sessions, uuid4(), definition, ticks)


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

    engine = WorkflowStepEngine(
        repository=repository,
        sessions=sessions,
        definitions=FixedDefinitions(definition),
        settings=EngineSettings(
            step_deadline_seconds=STEP_DEADLINE_SECONDS, session_poll_seconds=SESSION_POLL_SECONDS
        ),
        clock=clock,
    )
    return Run(engine, repository, sessions, uuid4(), definition, ticks)
