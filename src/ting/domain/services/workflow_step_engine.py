"""The step engine service: persists the engine's decisions and drives the sessions.

``WorkflowStepEngine`` is the only thing that writes a run's step ledger. Each public
call is one serialised decision on one run:

* ``launch`` records the trigger and activates the first steps;
* ``record_close`` accepts a step's ending from the session that owns it, after
  fencing, and activates what follows;
* ``resolve_gate`` records a person's (or agent's) decision on an open gate;
* ``cancel`` ends a run.

The decision itself is the pure ``step_engine.next_actions``; this service reads the
ledger through ``WorkflowStepRepository``, applies the actions inside the run's
transaction and, once that has committed, starts sessions through ``StepSessionPort``.
It never relays conversation between agents: it activates steps and records how they
end.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niuu.observability import get_observability
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.step_engine import (
    Action,
    ActivateStep,
    CancelStep,
    ClosePlan,
    CompleteRun,
    EngineSettings,
    FailRun,
    MarkStale,
    OpenGate,
    OpenWait,
    QueueStep,
    RecordEvent,
    cancel_actions,
    next_actions,
    plan_close,
    plan_gate_resolution,
    plan_retry,
)
from ting.domain.step_graph import NodeKind, StepGraph, compile_step_graph
from ting.domain.step_recovery import check_recoverable
from ting.domain.step_stuck import Condition
from ting.domain.workflow_document import validate_workflow_graph
from ting.domain.workflow_steps import (
    EVENT_CLOSE_REJECTED,
    EVENT_RECOVERY,
    EVENT_RUN_COMPLETED,
    EVENT_RUN_FAILED,
    EVENT_RUN_LAUNCHED,
    EVENT_STEP_FLAGGED,
    EVENT_STEP_UNCLOSED,
    SYSTEM_ACTOR_ID,
    SYSTEM_ACTOR_KIND,
    TERMINAL_RUN_EVENTS,
    Ending,
    FlagKind,
    GateActor,
    GateDecision,
    InvalidEndingError,
    RecoveryAction,
    RecoveryRefusedError,
    RunEvent,
    RunState,
    RunStateError,
    StepEngineError,
    StepFencingError,
    StepFlag,
    StepKind,
    StepState,
    WorkflowStep,
    check_fence,
)
from ting.ports.workflow_steps import (
    DeliveredEvent,
    LedgerTransaction,
    SessionLiveness,
    StepActivation,
    StepRunDefinition,
    StepRunDefinitions,
    StepSessionPort,
    WorkflowStepRepository,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class StepRunRequest:
    """A run to launch: the campaign it belongs to and the payload its trigger carries."""

    run_id: UUID
    payload: dict[str, Any] = field(default_factory=dict)
    trigger_node_id: str | None = None


@dataclass(frozen=True)
class Recovered:
    """The outcome of a retry or reassign: the run as it is now and the new attempt."""

    state: RunState
    step: WorkflowStep


class _Ledger:
    """One transaction's view of a run: applies changes and keeps the state they make."""

    def __init__(self, tx: LedgerTransaction, state: RunState, now: datetime) -> None:
        self._tx = tx
        self._now = now
        self.run_id = state.run_id
        self.flags = state.flags
        self.steps: dict[UUID, WorkflowStep] = {step.id: step for step in state.steps}
        self.events: list[RunEvent] = list(state.events)
        self.generation = state.generation
        self.to_start: list[WorkflowStep] = []
        self.terminal: str | None = None
        self.cap_hit: str | None = None
        self.failure: str | None = None

    def state(self) -> RunState:
        return RunState(
            run_id=self.run_id,
            steps=tuple(self.steps.values()),
            events=tuple(self.events),
            flags=self.flags,
        )

    async def save(self, step: WorkflowStep) -> None:
        await self._tx.save_step(step)
        self.steps[step.id] = step

    async def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        source: UUID | None = None,
        generation: int | None = None,
    ) -> RunEvent:
        effective = self.generation if generation is None else generation
        event = await self._tx.append_event(
            event_type,
            payload,
            generation=effective,
            source_activation_id=source,
            created_at=self._now,
        )
        self.events.append(event)
        self.generation = max(self.generation, effective)
        if event_type in TERMINAL_RUN_EVENTS:
            self.terminal = event_type
        return event

    async def apply(self, actions: list[Action]) -> None:
        for action in actions:
            await self._apply_one(action)

    async def _apply_one(self, action: Action) -> None:
        if isinstance(action, ActivateStep):
            await self.save(action.step)
            self.to_start.append(action.step)
        elif isinstance(action, QueueStep | OpenGate | OpenWait):
            await self.save(action.step)
        elif isinstance(action, MarkStale):
            for step_id in action.step_ids:
                await self.save(self.steps[step_id].with_state(StepState.STALE))
        elif isinstance(action, CancelStep):
            await self.save(
                self.steps[action.step_id].with_state(StepState.CANCELED, closed_at=self._now)
            )
        elif isinstance(action, RecordEvent):
            await self.append(
                action.event_type,
                action.payload,
                source=action.source_activation_id,
                generation=action.generation,
            )
        elif isinstance(action, CompleteRun):
            await self._complete(action)
        elif isinstance(action, FailRun):
            self.failure = action.reason
            self.cap_hit = action.cap
            await self.append(EVENT_RUN_FAILED, {"reason": action.reason, "cap": action.cap})
        else:
            raise StepEngineError(f"unhandled engine action {type(action).__name__}")

    async def _complete(self, action: CompleteRun) -> None:
        await self.save(action.end_step)
        if action.completion_event:
            await self.append(
                action.completion_event,
                {
                    "node_id": action.end_step.node_id,
                    "member": None,
                    "verdict": "",
                    "outcome": {},
                    "activation_id": str(action.end_step.id),
                },
                source=action.end_step.id,
            )
        await self.append(
            EVENT_RUN_COMPLETED,
            {"end_node": action.end_step.node_id, "completion_event": action.completion_event},
            source=action.end_step.id,
        )


class WorkflowStepEngine:
    """Launches runs and records how their steps end."""

    def __init__(
        self,
        *,
        repository: WorkflowStepRepository,
        sessions: StepSessionPort,
        definitions: StepRunDefinitions,
        settings: EngineSettings,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._definitions = definitions
        self._settings = settings
        self._clock = clock

    # -- public API -------------------------------------------------------------

    async def launch(self, request: StepRunRequest) -> RunState:
        """Record the trigger and activate the first steps. A run is launched once."""
        definition, graph = await self._definition(request.run_id)
        validate_workflow_graph(
            definition.graph, persona_definitions=definition.persona_definitions
        )
        self._require_personas(graph, definition)
        trigger = self._trigger(graph, request)
        now = self._clock()
        async with self._repository.transaction(request.run_id) as tx:
            state = await tx.load()
            if state.launched:
                raise RunStateError(f"run {request.run_id} was already launched")
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_RUN_LAUNCHED,
                {"trigger_node_id": trigger.id, "dispatch_event": trigger.dispatch_event},
            )
            dispatched = await ledger.append(
                trigger.dispatch_event,
                {
                    "node_id": trigger.id,
                    "member": None,
                    "verdict": "",
                    "outcome": dict(request.payload),
                },
            )
            await self._advance(ledger, graph, dispatched, now)
        return await self._after(ledger, definition, graph)

    async def record_close(
        self,
        activation_id: UUID,
        session_id: str | None,
        attempt: int,
        ending: Ending,
    ) -> RunState:
        """Accept a step's ending from the session that owns it, then activate what follows.

        Raises ``StepFencingError`` when the activation is unknown, is not ``active``
        or ``waiting``, or is not this attempt of this session's; and
        ``InvalidEndingError`` when the ending is not one the step's contract allows.
        Both are counted, recorded in the run's log and raised: a close that loses is
        never silent.
        """
        found = await self._repository.find_step(activation_id)
        if found is None:
            self._count_rejection("unknown_activation")
            raise StepFencingError(activation_id, "no such activation")
        definition, graph = await self._definition(found.run_id)
        now = self._clock()
        try:
            async with self._repository.transaction(found.run_id) as tx:
                state = await tx.load()
                step = check_fence(
                    state.step(activation_id),
                    activation_id=activation_id,
                    session_id=session_id,
                    attempt=attempt,
                )
                plan = plan_close(graph, state, step, ending, now=now)
                ledger = _Ledger(tx, state, now)
                await self._commit_close(ledger, graph, plan, now)
        except StepFencingError as exc:
            await self._reject(found.run_id, activation_id, "fencing", exc.reason)
            raise
        except InvalidEndingError as exc:
            await self._reject(found.run_id, activation_id, "invalid_ending", str(exc))
            raise
        return await self._after(ledger, definition, graph)

    async def note_unclosed(
        self,
        activation_id: UUID,
        session_id: str | None,
        attempt: int,
    ) -> RunState:
        """Record that a session ended its turn with the step still open.

        The step stays open: nothing is guessed from what the session said. The fact
        is written to the run's event log and counted, for stuck detection to act on.
        A report for a step the session does not own is a fencing rejection, as for a
        close.
        """
        found = await self._repository.find_step(activation_id)
        if found is None:
            self._count_rejection("unknown_activation")
            raise StepFencingError(activation_id, "no such activation")
        now = self._clock()
        async with self._repository.transaction(found.run_id) as tx:
            state = await tx.load()
            step = check_fence(
                state.step(activation_id),
                activation_id=activation_id,
                session_id=session_id,
                attempt=attempt,
            )
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_STEP_UNCLOSED,
                {
                    "activation_id": str(step.id),
                    "node_id": step.node_id,
                    "member": step.member,
                    "attempt": step.attempt,
                    "session_id": session_id,
                },
                source=step.id,
            )
        logger.error(
            "Workflow step %s (%s/%s) of run %s: its session %s ended a turn without closing it",
            step.id,
            step.node_id,
            step.member,
            step.run_id,
            session_id,
        )
        get_observability().count(
            "ting_workflow_step_unclosed_total",
            description=(
                "Turns a harness session ended with its workflow step still open and no "
                "close call. The step stays open for stuck detection."
            ),
        )
        return await self._repository.load(found.run_id)

    async def resolve_gate(
        self,
        run_id: UUID,
        node_id: str,
        decision: GateDecision,
        by: GateActor,
        note: str = "",
    ) -> RunState:
        """Record who decided an open gate, when and why, and follow the decision's event.

        A gate waits for a person, unless it is an ``automated_approval`` gate, which
        an agent may answer too. Anything else is refused with ``GateRefusedError``.
        """
        definition, graph = await self._definition(run_id)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            plan = plan_gate_resolution(
                graph, state, node_id=node_id, decision=decision, by=by, note=note, now=now
            )
            ledger = _Ledger(tx, state, now)
            await self._commit_close(ledger, graph, plan, now)
        return await self._after(ledger, definition, graph)

    async def cancel(self, run_id: UUID, reason: str, by: GateActor | None = None) -> RunState:
        """Cancel a run: its open steps are canceled and its sessions stopped.

        ``by`` names the person who aborted it; the log then records who, when and why.
        """
        if not reason.strip():
            raise RunStateError("a run is canceled with a reason")
        definition, graph = await self._definition(run_id)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            if not state.launched:
                raise RunStateError(f"run {run_id} was never launched")
            if state.finished:
                raise RunStateError(f"run {run_id} already finished")
            ledger = _Ledger(tx, state, now)
            if by is not None:
                await ledger.append(
                    EVENT_RECOVERY,
                    {"action": RecoveryAction.ABORT.value, "by": _actor_json(by), "reason": reason},
                )
            await ledger.apply(cancel_actions(state, reason=reason))
        return await self._after(ledger, definition, graph)

    # -- stuck steps (NIU-1181) -------------------------------------------------

    async def raise_flags(self, run_id: UUID, conditions: Sequence[Condition]) -> list[StepFlag]:
        """Flag the stuck steps, once per step and kind; returns the flags that are new.

        A flag already on the step (open or resolved) is left alone, so a pass that
        finds the same condition again raises nothing. Each new flag is written to the
        run's log, logged at error and counted by kind.
        """
        if not conditions:
            return []
        now = self._clock()
        raised: list[StepFlag] = []
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            ledger = _Ledger(tx, state, now)
            for condition in conditions:
                step = state.step(condition.step.id)
                if step is None or not step.is_open:
                    continue
                if state.flag(step.id, condition.kind) is not None:
                    continue
                flag = StepFlag(
                    run_id=run_id,
                    activation_id=step.id,
                    kind=condition.kind,
                    since=condition.since,
                    raised_at=now,
                    detail=condition.detail,
                )
                if not await tx.raise_flag(flag):
                    continue
                await ledger.append(
                    EVENT_STEP_FLAGGED,
                    {
                        "activation_id": str(step.id),
                        "node_id": step.node_id,
                        "member": step.member,
                        "attempt": step.attempt,
                        "kind": condition.kind.value,
                        "since": condition.since.isoformat(),
                        "detail": condition.detail,
                    },
                    source=step.id,
                )
                raised.append(flag)
        for flag in raised:
            step = state.step(flag.activation_id)
            logger.error(
                "Workflow step %s (%s/%s) of run %s is stuck (%s): %s",
                flag.activation_id,
                step.node_id if step else "?",
                step.member if step else "?",
                run_id,
                flag.kind.value,
                flag.detail,
            )
            get_observability().count(
                "ting_workflow_step_flags_raised_total",
                attributes={"kind": flag.kind.value},
                description=(
                    "Open workflow steps flagged as stuck by the sweep, by kind. Each step is "
                    "flagged once per kind."
                ),
            )
        return raised

    async def resolve_flags(
        self,
        run_id: UUID,
        still_holding: Collection[tuple[UUID, FlagKind]],
        *,
        unobserved: Collection[UUID] = (),
    ) -> int:
        """Resolve the run's open flags that no longer hold; returns how many.

        A flag holds while its step is open and ``still_holding`` names it. The flags
        of ``unobserved`` steps are left alone: a step Ting could not ask about is not
        known to have recovered.
        """
        now = self._clock()
        resolved = 0
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            for flag in state.flags:
                if flag.is_resolved or flag.activation_id in unobserved:
                    continue
                step = state.step(flag.activation_id)
                open_step = step is not None and step.is_open
                if open_step and (flag.activation_id, flag.kind) in still_holding:
                    continue
                resolution = (
                    "condition cleared"
                    if open_step
                    else f"step is {step.state.value if step else 'gone'}"
                )
                await tx.resolve_flag(flag.activation_id, flag.kind, at=now, resolution=resolution)
                resolved += 1
        return resolved

    async def mark_notified(self, run_id: UUID, flags: Sequence[StepFlag]) -> None:
        """Record that a person was told about each flag."""
        if not flags:
            return
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            for flag in flags:
                await tx.mark_flag_notified(flag.activation_id, flag.kind, at=now)

    async def claim_flag_action(self, run_id: UUID, flag: StepFlag) -> bool:
        """Claim the flag's one automatic action; False if it was already claimed.

        The claim is recorded before the action runs, so an action that crashes or fails
        is not tried again for this flag.
        """
        async with self._repository.transaction(run_id) as tx:
            return await tx.claim_flag_action(flag.activation_id, flag.kind, at=self._clock())

    async def finish_flag_action(self, run_id: UUID, flag: StepFlag, error: str) -> None:
        """Record how the flag's claimed automatic action ended; ``error`` "" means it worked."""
        async with self._repository.transaction(run_id) as tx:
            await tx.finish_flag_action(
                flag.activation_id, flag.kind, at=self._clock(), error=error
            )

    async def resume_start(self, run_id: UUID, activation_id: UUID, reason: str) -> bool:
        """Finish starting a step whose start was interrupted; True if it was resumed.

        Creates or finds the member's session, records it on the step and delivers the
        same activation again (delivery is idempotent by activation id). Nothing about
        the outcome changes, so unlike the engine's own start this never fails the step
        when the session cannot be reached: the error is raised for the caller to count,
        and the step stays as it was. A step that is no longer ``active`` is left alone.
        """
        definition, graph = await self._definition(run_id)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            step = state.step(activation_id)
            if (
                state.finished
                or step is None
                or step.run_id != run_id
                or step.kind is not StepKind.STAGE_MEMBER
                or step.state is not StepState.ACTIVE
            ):
                return False
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_RECOVERY,
                _recovery_payload("resume_start", _system_actor(), reason, step),
                source=step.id,
            )
        member = step.member or ""
        session_id = await self._sessions.ensure_session(
            run_id, member, definition.persona_refs[member]
        )
        bound = await self._bind_session(step, session_id)
        if bound is None:
            return False
        activation = self._activation(
            bound, graph, await self._repository.load(run_id), definition.workflow_name
        )
        await self._sessions.deliver(activation)
        return True

    async def fail_step(self, run_id: UUID, activation_id: UUID, reason: str) -> bool:
        """Fail an open step because its session is gone; True if it was failed.

        The step ends like any step that ended ``failed``: the engine's sibling and
        run-failure rules apply to it. A step that is no longer open is left alone.
        """
        definition, graph = await self._definition(run_id)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            step = state.step(activation_id)
            if (
                state.finished
                or step is None
                or step.run_id != run_id
                or step.kind is not StepKind.STAGE_MEMBER
                or step.state not in (StepState.ACTIVE, StepState.WAITING)
            ):
                return False
            plan = plan_close(graph, state, step, Ending.failed(reason), now=now)
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_RECOVERY,
                _recovery_payload("fail_step", _system_actor(), reason, step),
                source=step.id,
            )
            await self._commit_close(ledger, graph, plan, now)
        await self._after(ledger, definition, graph)
        return True

    async def retry_step(
        self, run_id: UUID, activation_id: UUID, *, by: GateActor, reason: str
    ) -> Recovered:
        """Replace a failed or stuck step by a new attempt on the member's session.

        The old attempt is closed first (a failed step already is; an open one is
        canceled), so a late close from it is fenced out. A member whose session has
        failed gets a fresh one. Refused with ``RecoveryRefusedError`` when the step is
        in no state to retry, or when its session still holds the old attempt open and
        cannot let go of it: that needs ``reassign_step``.
        """
        return await self._redo(RecoveryAction.RETRY, run_id, activation_id, by, reason)

    async def reassign_step(
        self, run_id: UUID, activation_id: UUID, *, by: GateActor, reason: str
    ) -> Recovered:
        """Move an open or failed step to a fresh session of the same member.

        For when the session itself is broken: the old session is stopped, the old
        attempt closed and a new attempt starts on the new session.
        """
        return await self._redo(RecoveryAction.REASSIGN, run_id, activation_id, by, reason)

    async def _redo(
        self,
        action: RecoveryAction,
        run_id: UUID,
        activation_id: UUID,
        by: GateActor,
        reason: str,
    ) -> Recovered:
        if not reason.strip():
            raise RecoveryRefusedError(action, "a recovery is recorded with a reason")
        definition, graph = await self._definition(run_id)
        step = check_recoverable(action, await self._repository.load(run_id), run_id, activation_id)
        await self._prepare_session(action, definition, step)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            step = check_recoverable(action, state, run_id, activation_id)
            actions = plan_retry(
                graph, state, step, reason=reason, now=now, settings=self._settings
            )
            successor = next(
                item.step for item in actions if isinstance(item, ActivateStep | QueueStep)
            )
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_RECOVERY,
                _recovery_payload(
                    action.value,
                    _actor_json(by),
                    reason,
                    step,
                    new_activation_id=str(successor.id),
                    new_attempt=successor.attempt,
                ),
                source=step.id,
            )
            await ledger.apply(actions)
        logger.warning(
            "Workflow step %s (%s/%s) of run %s: %s by %s (%s); attempt %d is %s",
            step.id,
            step.node_id,
            step.member,
            run_id,
            action.value,
            by.id,
            reason,
            successor.attempt,
            successor.state.value,
        )
        get_observability().count(
            "ting_workflow_step_recoveries_total",
            attributes={"action": action.value},
            description="Recovery actions an operator took on workflow steps, by action.",
        )
        final = await self._after(ledger, definition, graph)
        created = next(item for item in final.steps if item.id == successor.id)
        return Recovered(final, created)

    async def _prepare_session(
        self, action: RecoveryAction, definition: StepRunDefinition, step: WorkflowStep
    ) -> None:
        """Make the member's session fit for the new attempt, or refuse the retry."""
        if not step.session_id:
            return
        if action is RecoveryAction.REASSIGN:
            await self._replace(definition, step)
            return
        observed = await self._sessions.observe(step.run_id, step.session_id, step.id)
        if observed.liveness is SessionLiveness.FAILED:
            await self._replace(definition, step)
            return
        held = observed.reported
        if held is None:
            return
        where = f"{step.node_id}/{step.member}"
        if str(held.get("state") or "") in ("active", "waiting"):
            raise RecoveryRefusedError(
                action,
                f"session {step.session_id} still holds {where} open and cannot let go of it, "
                "so a new attempt could not be delivered there; reassign the step to a fresh "
                "session instead",
            )
        if step.is_open:
            raise RecoveryRefusedError(
                action,
                f"session {step.session_id} already closed {where} but that close is not "
                "recorded yet; the sweep records it, then recover the step if it is still needed",
            )

    async def _replace(self, definition: StepRunDefinition, step: WorkflowStep) -> None:
        member = step.member or ""
        await self._sessions.replace_session(
            step.run_id, member, definition.persona_refs[member], step.session_id
        )

    # -- deciding ---------------------------------------------------------------

    async def _commit_close(
        self,
        ledger: _Ledger,
        graph: StepGraph,
        plan: ClosePlan,
        now: datetime,
    ) -> None:
        await ledger.save(plan.step)
        lifecycle = await ledger.append(
            plan.lifecycle_event, plan.lifecycle_payload, source=plan.step.id
        )
        trigger = lifecycle
        if plan.outcome_event is not None and plan.outcome_payload is not None:
            trigger = await ledger.append(
                plan.outcome_event, plan.outcome_payload, source=plan.step.id
            )
        await self._advance(ledger, graph, trigger, now)

    async def _advance(
        self,
        ledger: _Ledger,
        graph: StepGraph,
        event: RunEvent,
        now: datetime,
    ) -> None:
        actions = next_actions(graph, ledger.state(), event, now=now, settings=self._settings)
        await ledger.apply(actions)

    # -- after the commit -------------------------------------------------------

    async def _after(
        self,
        ledger: _Ledger,
        definition: StepRunDefinition,
        graph: StepGraph,
    ) -> RunState:
        """Count what the commit decided, start the sessions it asked for, stop them at the end."""
        if ledger.failure is not None:
            self._report_failure(ledger)
        for step in ledger.to_start:
            await self._start(step, definition, graph)
        if ledger.terminal is not None:
            await self._sessions.stop_run_sessions(ledger.run_id)
        return await self._repository.load(ledger.run_id)

    async def _start(
        self,
        step: WorkflowStep,
        definition: StepRunDefinition,
        graph: StepGraph,
    ) -> None:
        if step.kind is not StepKind.STAGE_MEMBER or step.member is None:
            return
        persona_ref = definition.persona_refs[step.member]
        try:
            session_id = await self._sessions.ensure_session(step.run_id, step.member, persona_ref)
        except Exception as exc:
            await self._fail_start(
                step, graph, definition, f"no session for {step.member!r}: {exc}"
            )
            return
        bound = await self._bind_session(step, session_id)
        if bound is None:
            return
        activation = self._activation(
            bound,
            graph,
            await self._repository.load(step.run_id),
            definition.workflow_name,
        )
        try:
            await self._sessions.deliver(activation)
        except Exception as exc:
            await self._fail_start(
                bound, graph, definition, f"delivery to {step.member!r} failed: {exc}"
            )

    async def _bind_session(self, step: WorkflowStep, session_id: str) -> WorkflowStep | None:
        """Record the session that owns an activated step; ``None`` if the step moved on."""
        async with self._repository.transaction(step.run_id) as tx:
            state = await tx.load()
            current = state.step(step.id)
            if current is None or current.state is not StepState.ACTIVE:
                return None
            bound = current.with_state(StepState.ACTIVE, session_id=session_id)
            await tx.save_step(bound)
            return bound

    async def _fail_start(
        self,
        step: WorkflowStep,
        graph: StepGraph,
        definition: StepRunDefinition,
        reason: str,
    ) -> None:
        """A step that could not be started fails: recorded, counted and logged, never retried."""
        logger.error(
            "Workflow step %s (%s/%s) could not be started: %s",
            step.id,
            step.node_id,
            step.member,
            reason,
        )
        get_observability().count(
            "ting_workflow_step_start_failures_total",
            description="Workflow step activations that could not reach a harness session.",
        )
        now = self._clock()
        async with self._repository.transaction(step.run_id) as tx:
            state = await tx.load()
            current = state.step(step.id)
            if current is None or current.state is not StepState.ACTIVE:
                return
            plan = plan_close(graph, state, current, Ending.failed(reason), now=now)
            ledger = _Ledger(tx, state, now)
            await self._commit_close(ledger, graph, plan, now)
        await self._after(ledger, definition, graph)

    @staticmethod
    def _activation(
        step: WorkflowStep, graph: StepGraph, state: RunState, workflow_name: str
    ) -> StepActivation:
        node = graph.nodes[step.node_id]
        member = node.member(step.member or "")
        contract = member.contract if member is not None else None
        delivered = tuple(
            DeliveredEvent(
                seq=item.seq,
                event_type=item.event_type,
                edge_id=item.edge_id,
                node_id=item.node_id,
                member=item.member,
                payload=event.payload,
            )
            for item in step.inputs
            if (event := state.event(item.seq)) is not None
        )
        return StepActivation(
            run_id=step.run_id,
            activation_id=step.id,
            node_id=step.node_id,
            node_label=node.label,
            member=step.member or "",
            attempt=step.attempt,
            generation=step.generation,
            session_id=step.session_id or "",
            inputs=delivered,
            deadline_at=step.deadline_at,
            valid_verdicts=tuple(sorted(contract.produces_map)) if contract is not None else (),
            outcome_schema=contract.outcome_schema if contract is not None else None,
            verdict_events=dict(contract.produces_map) if contract is not None else {},
            default_event=contract.produces_event if contract is not None else "",
            workflow_name=workflow_name,
        )

    # -- helpers ----------------------------------------------------------------

    async def _definition(self, run_id: UUID) -> tuple[StepRunDefinition, StepGraph]:
        definition = await self._definitions.get(run_id)
        graph = compile_step_graph(definition.graph, definition.persona_definitions)
        return definition, graph

    @staticmethod
    def _require_personas(graph: StepGraph, definition: StepRunDefinition) -> None:
        aliases = {
            member.alias
            for node in graph.nodes.values()
            if node.kind is NodeKind.STAGE
            for member in node.members
        }
        missing = sorted(aliases - set(definition.persona_refs))
        if missing:
            raise WorkflowDocumentError(
                "run cannot launch: no pinned persona for stage member(s) "
                + ", ".join(missing)
                + "; pin them in the workflow's persona_dependencies"
            )

    @staticmethod
    def _trigger(graph: StepGraph, request: StepRunRequest) -> Any:
        triggers = graph.triggers()
        if request.trigger_node_id is not None:
            trigger = graph.nodes.get(request.trigger_node_id)
            if trigger is None or trigger.kind is not NodeKind.TRIGGER:
                raise RunStateError(
                    f"{request.trigger_node_id!r} is not a trigger of this workflow"
                )
            return trigger
        if len(triggers) != 1:
            names = ", ".join(sorted(node.id for node in triggers))
            raise RunStateError(
                f"workflow has {len(triggers)} triggers ({names}); name the one to launch "
                "with trigger_node_id"
            )
        return triggers[0]

    async def _reject(self, run_id: UUID, activation_id: UUID, kind: str, reason: str) -> None:
        logger.error(
            "Workflow step close rejected (%s) for activation %s of run %s: %s",
            kind,
            activation_id,
            run_id,
            reason,
        )
        self._count_rejection(kind)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_CLOSE_REJECTED,
                {"activation_id": str(activation_id), "kind": kind, "reason": reason},
                source=activation_id,
            )

    @staticmethod
    def _count_rejection(kind: str) -> None:
        get_observability().count(
            "ting_workflow_step_close_rejections_total",
            attributes={"reason": kind},
            description=(
                "Step closes the engine refused: fencing (stale, unknown, wrong session or "
                "attempt) and endings the step's contract does not allow."
            ),
        )

    @staticmethod
    def _report_failure(ledger: _Ledger) -> None:
        logger.error("Workflow run %s failed: %s", ledger.run_id, ledger.failure)
        if ledger.cap_hit is None:
            return
        get_observability().count(
            "ting_workflow_step_cap_hits_total",
            attributes={"knob": ledger.cap_hit},
            description="Bounds a workflow run hit, by the graph knob that bounds it.",
        )


def _actor_json(by: GateActor) -> dict[str, str]:
    return {"kind": by.kind.value, "id": by.id}


def _system_actor() -> dict[str, str]:
    return {"kind": SYSTEM_ACTOR_KIND, "id": SYSTEM_ACTOR_ID}


def _recovery_payload(
    action: str,
    by: dict[str, str],
    reason: str,
    step: WorkflowStep,
    **extra: Any,
) -> dict[str, Any]:
    """The log entry of a recovery: what was done, to which step, by whom and why."""
    return {
        "action": action,
        "by": by,
        "reason": reason,
        "activation_id": str(step.id),
        "node_id": step.node_id,
        "member": step.member,
        "attempt": step.attempt,
        **extra,
    }


__all__ = ["Recovered", "StepRunRequest", "WorkflowStepEngine"]
