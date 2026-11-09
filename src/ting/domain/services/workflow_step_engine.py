"""The step engine service: persists the engine's decisions and offers work on the run's mesh.

``WorkflowStepEngine`` is the only thing that writes a run's step ledger. Each public
call is one serialised decision on one run:

* ``launch`` starts the run's unit (its root and every member), records the trigger and
  offers the first steps;
* ``note_listening`` / ``note_picked_up`` / ``note_passed`` / ``note_offer_refused`` /
  ``note_offer_published`` record what the unit's sessions report about the handoff;
* ``record_close`` accepts a step's ending from the session that owns it, after
  fencing, and offers what follows;
* ``resolve_gate`` records a person's (or agent's) decision on an open gate;
* ``cancel`` ends a run.

The decision itself is the pure ``step_engine.next_actions``; this service reads the
ledger through ``WorkflowStepRepository``, applies the actions inside the run's
transaction and, once that has committed, publishes the offers that can go out: the
engine signs an offer as the platform service Ting is and hands it to the run's root
(``StepSessionPort.publish_offer``), whose Skuld puts it on the mesh. It publishes an offer
only once every member that takes its topic reports it is listening. Ting never joins the
mesh and never relays conversation between agents: it offers steps and records how the
members' sessions report them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niuu.domain.workflow_step import (
    OfferInput,
    PersonaPin,
    StepContract,
    StepOffered,
)
from niuu.observability import get_observability
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.step_engine import (
    Action,
    CancelStep,
    ClosePlan,
    CompleteRun,
    EngineSettings,
    FailRun,
    MarkStale,
    OfferStep,
    OpenGate,
    OpenWait,
    QueueStep,
    RecordEvent,
    cancel_actions,
    listening_members,
    next_actions,
    offer_members,
    offer_publications,
    offer_topic,
    plan_close,
    plan_gate_resolution,
    plan_retry,
    unit_root,
    unit_sessions,
)
from ting.domain.step_graph import NodeKind, StepGraph, compile_step_graph
from ting.domain.step_instructions import render_offer_instructions
from ting.domain.step_recovery import check_recoverable
from ting.domain.step_stuck import Condition
from ting.domain.workflow_document import validate_workflow_graph
from ting.domain.workflow_steps import (
    EVENT_CLOSE_REJECTED,
    EVENT_MEMBER_LISTENING,
    EVENT_MEMBER_RESTARTED,
    EVENT_OFFER_PASSED,
    EVENT_OFFER_PICKED_UP,
    EVENT_OFFER_PUBLISHED,
    EVENT_OFFER_REFUSED,
    EVENT_RECOVERY,
    EVENT_RUN_COMPLETED,
    EVENT_RUN_FAILED,
    EVENT_RUN_LAUNCHED,
    EVENT_STEP_FLAGGED,
    EVENT_STEP_UNCLOSED,
    EVENT_UNIT_LAUNCHED,
    SYSTEM_ACTOR_ID,
    SYSTEM_ACTOR_KIND,
    TERMINAL_RUN_EVENTS,
    Ending,
    FlagKind,
    GateActor,
    GateDecision,
    HandoffRefusedError,
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
    is_repeat_close,
)
from ting.ports.workflow_steps import (
    LedgerTransaction,
    OfferSigner,
    PersonaRef,
    SessionLiveness,
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
    """The outcome of a retry or reassign: the run as it is now and the new offer."""

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
        if isinstance(action, OfferStep):
            await self.save(action.step)
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
    """Launches runs, offers their steps on the mesh and records how the members report them."""

    def __init__(
        self,
        *,
        repository: WorkflowStepRepository,
        sessions: StepSessionPort,
        offers: OfferSigner,
        definitions: StepRunDefinitions,
        settings: EngineSettings,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._signer = offers
        self._definitions = definitions
        self._settings = settings
        self._clock = clock
        #: One publication pass at a time per run, so an offer is not published twice at once.
        self._publishing: dict[UUID, asyncio.Lock] = {}

    # -- public API -------------------------------------------------------------

    async def launch(self, request: StepRunRequest) -> RunState:
        """Start the run's unit, record the trigger and offer the first steps. Once per run."""
        definition, graph = await self._definition(request.run_id)
        validate_workflow_graph(
            definition.graph, persona_definitions=definition.persona_definitions
        )
        self._require_personas(graph, definition)
        trigger = self._trigger(graph, request)
        if (await self._repository.load(request.run_id)).launched:
            raise RunStateError(f"run {request.run_id} was already launched")
        unit = await self._launch_unit(request.run_id, graph, definition)
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
            if unit is not None:
                await ledger.append(
                    EVENT_UNIT_LAUNCHED,
                    {"root_id": unit.root_id, "members": dict(unit.members)},
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

    async def _launch_unit(
        self, run_id: UUID, graph: StepGraph, definition: StepRunDefinition
    ) -> Any:
        """Start the run's root and every stage member together; ``None`` for a run with none."""
        members: dict[str, PersonaRef] = {}
        for node in graph.nodes.values():
            if node.kind is not NodeKind.STAGE:
                continue
            for member in node.members:
                members.setdefault(member.alias, definition.persona_refs[member.alias])
        if not members:
            return None
        return await self._sessions.launch_run(run_id, members)

    # -- what the unit's sessions report ----------------------------------------

    async def note_listening(
        self, *, run_id: UUID, session_id: str, member: str, start_id: str
    ) -> RunState:
        """Record that a member's subscriptions are up, then publish what was waiting for it.

        A repeat of the same start (a reconnected stream) changes nothing.
        """
        definition, graph = await self._definition(run_id)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            if state.finished:
                return state
            self._require_member(state, session_id, member, "listening")
            if any(
                event.event_type == EVENT_MEMBER_LISTENING
                and event.payload.get("session_id") == session_id
                and event.payload.get("start_id") == start_id
                for event in state.events
            ):
                self._count_repeat("listening")
                return state
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_MEMBER_LISTENING,
                {"member": member, "session_id": session_id, "start_id": start_id},
            )
        return await self._after(ledger, definition, graph)

    async def note_offer_published(
        self, *, run_id: UUID, session_id: str, offer_id: UUID
    ) -> RunState:
        """Record that the root put an offer on the mesh, if Ting has not seen it published."""
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            if state.finished:
                return state
            if session_id != unit_root(state):
                self._refuse(
                    "unknown_session",
                    f"session {session_id} is not the root of run {run_id}, so it published "
                    "no offer of this run",
                )
            offer = self._require_offer(state, offer_id)
            if str(offer.id) in offer_publications(state):
                self._count_repeat("offer_published")
                return state
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_OFFER_PUBLISHED,
                {
                    "offer_id": offer.offer_id,
                    "node_id": offer.node_id,
                    "attempt": offer.attempt,
                    "reported_by": session_id,
                },
                source=offer.id,
            )
            return await self._repository.load(run_id)

    async def note_picked_up(
        self,
        *,
        run_id: UUID,
        session_id: str,
        member: str,
        offer_id: UUID,
        activation_id: UUID,
        attempt: int,
    ) -> RunState:
        """Record a member's pickup of an offer as a step of its own.

        The step is fenced on the member's session. The activation id is the member's: it
        mints one per pickup, so a pickup reported again is the step already recorded and
        changes nothing. A pickup of an offer the run did not make, that its generation has
        moved past, that no longer stands, or by a member the offer is not addressed to, is
        refused with ``HandoffRefusedError`` (counted, and logged by the caller).
        """
        definition, graph = await self._definition(run_id)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            if state.finished:
                return state
            if state.step(activation_id) is not None:
                self._count_repeat("picked_up")
                return state
            self._require_member(state, session_id, member, "picked_up")
            offer = self._require_offer(state, offer_id)
            self._require_standing(state, offer, member, attempt, graph)
            if any(
                step.kind is StepKind.STAGE_MEMBER
                and step.offer_id == offer.offer_id
                and step.member == member
                for step in state.steps
            ):
                self._refuse(
                    "pickup",
                    f"member {member!r} already took up offer {offer.offer_id} under another "
                    "activation",
                )
            ledger = _Ledger(tx, state, now)
            step = WorkflowStep(
                id=activation_id,
                run_id=run_id,
                node_id=offer.node_id,
                member=member,
                kind=StepKind.STAGE_MEMBER,
                attempt=offer.attempt,
                generation=offer.generation,
                state=StepState.ACTIVE,
                created_at=now,
                session_id=session_id,
                inputs=offer.inputs,
                deadline_at=offer.deadline_at,
                activated_at=now,
                offer_id=offer.offer_id,
                picked_up_at=now,
            )
            await ledger.save(step)
            if offer.state is StepState.ACTIVE:
                await ledger.save(
                    offer.with_state(StepState.DONE, closed_at=now, note=f"picked up by {member}")
                )
            await ledger.append(
                EVENT_OFFER_PICKED_UP,
                {
                    "offer_id": offer.offer_id,
                    "node_id": offer.node_id,
                    "member": member,
                    "session_id": session_id,
                    "activation_id": str(activation_id),
                    "attempt": offer.attempt,
                },
                source=step.id,
            )
        get_observability().count(
            "ting_workflow_offer_pickups_total",
            description="Members' pickups of workflow offers that Ting recorded as steps.",
        )
        return await self._after(ledger, definition, graph)

    async def note_passed(
        self,
        *,
        run_id: UUID,
        session_id: str,
        member: str,
        offer_id: UUID,
        reason: str,
    ) -> RunState:
        """Record that a member received an offer and it is not its to take.

        An offer that every member it is addressed to passed is an answer, not a loss: it
        ends its own path (failed, naming the filters that matched nobody) and is not
        published again. An offer a member refused (busy) is not a pass and is.
        """
        definition, graph = await self._definition(run_id)
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            if state.finished:
                return state
            self._require_member(state, session_id, member, "passed")
            offer = self._require_offer(state, offer_id)
            if _passed(state, offer.offer_id, member):
                self._count_repeat("passed")
                return state
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_OFFER_PASSED,
                {
                    "offer_id": offer.offer_id,
                    "node_id": offer.node_id,
                    "member": member,
                    "session_id": session_id,
                    "reason": reason,
                },
                source=offer.id,
            )
            state = ledger.state()
            addressed = [m.alias for m in offer_members(graph, offer)]
            if offer.state is StepState.ACTIVE and all(
                _passed(state, offer.offer_id, alias) for alias in addressed
            ):
                why = (
                    f"every member offered {offer.node_id!r} passed it ({', '.join(addressed)}): "
                    + "; ".join(_pass_reasons(state, offer.offer_id))
                )
                plan = plan_close(graph, state, offer, Ending.failed(why), now=now)
                await self._commit_close(ledger, graph, plan, now)
        return await self._after(ledger, definition, graph)

    async def note_offer_refused(
        self,
        *,
        run_id: UUID,
        session_id: str,
        member: str,
        offer_id: str,
        reason: str,
        detail: str,
    ) -> RunState:
        """Record that a member's Skuld refused an offer; loud, counted, and not a pass.

        The offer stays outstanding: a member that was busy takes it when the offer is
        published again, and one that refused for good (a pin or contract mismatch) is
        named in the log for an operator.
        """
        now = self._clock()
        async with self._repository.transaction(run_id) as tx:
            state = await tx.load()
            if state.finished:
                return state
            self._require_member(state, session_id, member, "offer_refused")
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_OFFER_REFUSED,
                {
                    "offer_id": offer_id,
                    "member": member,
                    "session_id": session_id,
                    "reason": reason,
                    "detail": detail,
                },
            )
        logger.error(
            "Member %s (session %s) of run %s refused offer %s (%s): %s",
            member,
            session_id,
            run_id,
            offer_id,
            reason,
            detail,
        )
        get_observability().count(
            "ting_workflow_offer_refusals_total",
            attributes={"reason": reason},
            description="Workflow offers a member's Skuld refused, by reason.",
        )
        return await self._repository.load(run_id)

    # -- publishing offers ------------------------------------------------------

    async def publish_ready_offers(self, run_id: UUID) -> int:
        """Publish the run's outstanding offers whose members all listen; returns how many.

        Called after every change, and by the sweep, so an offer that waited for a member to
        come up, or whose publication the root refused, goes out as soon as it can.
        """
        definition, graph = await self._definition(run_id)
        return await self._publish_ready(run_id, definition, graph)

    async def republish_offer(self, run_id: UUID, offer_id: UUID) -> bool:
        """Publish an unclaimed offer again; the same offer, the same event. True if it went out.

        Counted. The pickup grace says when to look again and is no cap: an offer is
        republished for as long as nobody takes it, until the step deadline fails it.
        """
        definition, graph = await self._definition(run_id)
        async with self._lock(run_id):
            state = await self._repository.load(run_id)
            offer = state.step(offer_id)
            if (
                state.finished
                or offer is None
                or offer.kind is not StepKind.OFFER
                or offer.state is not StepState.ACTIVE
            ):
                return False
            sent = await self._publish(state, offer, graph, definition)
        if sent:
            logger.warning(
                "Offer %s (%s) of run %s was not taken up; published it again",
                offer.offer_id,
                offer.node_id,
                run_id,
            )
            get_observability().count(
                "ting_workflow_offers_republished_total",
                description="Workflow offers published again because no member took them up.",
            )
        return sent

    async def _publish_ready(
        self, run_id: UUID, definition: StepRunDefinition, graph: StepGraph
    ) -> int:
        async with self._lock(run_id):
            state = await self._repository.load(run_id)
            if not state.launched or state.finished:
                return 0
            published = offer_publications(state)
            listening = listening_members(state)
            sent = 0
            for offer in state.steps:
                if (
                    offer.kind is not StepKind.OFFER
                    or offer.state is not StepState.ACTIVE
                    or offer.offer_id in published
                ):
                    continue
                waiting = [
                    member.alias
                    for member in offer_members(graph, offer)
                    if member.alias not in listening
                ]
                if waiting:
                    logger.info(
                        "Offer %s (%s) of run %s waits for %s to listen",
                        offer.offer_id,
                        offer.node_id,
                        run_id,
                        ", ".join(waiting),
                    )
                    continue
                if await self._publish(state, offer, graph, definition):
                    sent += 1
            return sent

    async def _publish(
        self,
        state: RunState,
        offer: WorkflowStep,
        graph: StepGraph,
        definition: StepRunDefinition,
    ) -> bool:
        """Sign the offer and hand it to the run's root; record that it went out.

        A root that cannot take it yet (its broker is not up) or an offer its Skuld refuses
        is logged at error and counted, and the offer stays outstanding: it is published
        again by the next pass, and the step deadline fails it if that never works.
        """
        try:
            body = self._offer_body(state, offer, graph, definition)
            await self._sessions.publish_offer(state.run_id, body)
        except Exception as exc:
            logger.error(
                "Could not publish offer %s (%s) of run %s: %s: %s; it stays outstanding and "
                "is published again by the next pass",
                offer.offer_id,
                offer.node_id,
                state.run_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            get_observability().count(
                "ting_workflow_offer_publish_failures_total",
                description=(
                    "Workflow offers the run's root could not take, by the next pass tried again."
                ),
            )
            return False
        now = self._clock()
        async with self._repository.transaction(state.run_id) as tx:
            current = await tx.load()
            ledger = _Ledger(tx, current, now)
            await ledger.append(
                EVENT_OFFER_PUBLISHED,
                {
                    "offer_id": offer.offer_id,
                    "node_id": offer.node_id,
                    "topic": offer_topic(offer),
                    "attempt": offer.attempt,
                    "publication": len(offer_publications(current).get(offer.offer_id, ())) + 1,
                },
                source=offer.id,
            )
        get_observability().count(
            "ting_workflow_offers_published_total",
            description="Workflow offers Ting handed to a run's root for the mesh.",
        )
        return True

    def _offer_body(
        self,
        state: RunState,
        offer: WorkflowStep,
        graph: StepGraph,
        definition: StepRunDefinition,
    ) -> dict[str, Any]:
        """The body of the room's ``offer`` operation: the signed ``step.offered`` payload."""
        node = graph.nodes[offer.node_id]
        contracts = []
        for member in offer_members(graph, offer):
            if member.contract is None:
                raise StepEngineError(
                    f"member {member.alias!r} of {node.id!r} has no persona contract, so the "
                    "offer cannot carry one for it"
                )
            ref = definition.persona_refs[member.alias]
            contracts.append(
                StepContract.from_persona(
                    PersonaPin(
                        alias=ref.alias, id=ref.id, revision=ref.revision, digest=ref.digest
                    ),
                    member.contract,
                )
            )
        inputs = [
            OfferInput(
                seq=item.seq,
                event_type=item.event_type,
                edge_id=item.edge_id,
                node_id=item.node_id,
                member=item.member,
                payload=dict(event.payload),
            )
            for item in offer.inputs
            if (event := state.event(item.seq)) is not None
        ]
        offered = StepOffered(
            run_id=str(state.run_id),
            offer_id=offer.offer_id or str(offer.id),
            node_id=node.id,
            node_label=node.label,
            workflow_name=definition.workflow_name,
            topic=offer_topic(offer),
            attempt=offer.attempt,
            generation=offer.generation,
            inputs=inputs,
            deadline_at=offer.deadline_at,
            instructions=render_offer_instructions(
                workflow_name=definition.workflow_name,
                node_id=node.id,
                node_label=node.label,
                run_id=state.run_id,
                attempt=offer.attempt,
                generation=offer.generation,
                inputs=inputs,
                contracts=contracts,
                deadline_at=offer.deadline_at,
            ),
            contracts=contracts,
        )
        payload = offered.to_payload()
        attestation = self._signer.sign(payload)
        return {"offer": payload, "attestation": dict(attestation) if attestation else None}

    def _lock(self, run_id: UUID) -> asyncio.Lock:
        return self._publishing.setdefault(run_id, asyncio.Lock())

    # -- fencing a report -------------------------------------------------------

    def _refuse(self, kind: str, reason: str) -> None:
        """Count and raise a report that does not fit the run."""
        self._count_rejection(kind)
        raise HandoffRefusedError(reason)

    def _require_member(self, state: RunState, session_id: str, member: str, report: str) -> None:
        """The report is from the session the run's unit launched for ``member``."""
        unit = unit_sessions(state)
        if member not in unit:
            self._refuse(
                "unknown_member",
                f"{report} report names member {member!r}, which is not in run "
                f"{state.run_id}'s unit ({', '.join(sorted(unit)) or 'no members'})",
            )
        if unit[member] != session_id:
            self._refuse(
                "unknown_session",
                f"{report} report for member {member!r} came from session {session_id}, but the "
                f"member's session is {unit[member]}",
            )

    def _require_offer(self, state: RunState, offer_id: UUID) -> WorkflowStep:
        offer = state.step(offer_id)
        if offer is None or offer.kind is not StepKind.OFFER or offer.run_id != state.run_id:
            self._refuse("unknown_offer", f"run {state.run_id} made no offer {offer_id}")
        assert offer is not None  # narrowed by _refuse, which raises
        return offer

    def _require_standing(
        self, state: RunState, offer: WorkflowStep, member: str, attempt: int, graph: StepGraph
    ) -> None:
        if offer.state not in (StepState.ACTIVE, StepState.DONE):
            self._refuse(
                "stale_offer",
                f"offer {offer.offer_id} is {offer.state.value}, so {member!r} cannot take it up",
            )
        if offer.generation != state.generation:
            self._refuse(
                "stale_offer",
                f"offer {offer.offer_id} is of generation {offer.generation} and the run is in "
                f"generation {state.generation}; rework replaced it",
            )
        if member not in {m.alias for m in offer_members(graph, offer)}:
            self._refuse(
                "pickup",
                f"offer {offer.offer_id} is addressed to the members that take "
                f"{offer_topic(offer)!r}, and {member!r} is not one",
            )
        if attempt != offer.attempt:
            self._refuse(
                "pickup",
                f"{member!r} took up attempt {attempt} of offer {offer.offer_id}, which is "
                f"attempt {offer.attempt}",
            )

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
                if is_repeat_close(
                    state.step(activation_id),
                    session_id=session_id,
                    attempt=attempt,
                    ending=ending,
                ):
                    self._count_repeat("closed")
                    return state
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
        turn: int | None = None,
    ) -> RunState:
        """Record that a session ended its turn with the step still open.

        The step stays open: nothing is guessed from what the session said. The fact
        is written to the run's event log and counted, for stuck detection to act on.
        A report for a step the session does not own is a fencing rejection, as for a
        close. ``turn`` is the session's turn count when the report names one: a second
        report of the same turn is the same report delivered again and is not recorded.
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
            if turn is not None and _unclosed_turn_recorded(state, step, session_id, turn):
                self._count_repeat("unclosed")
                return state
            ledger = _Ledger(tx, state, now)
            await ledger.append(
                EVENT_STEP_UNCLOSED,
                {
                    "activation_id": str(step.id),
                    "node_id": step.node_id,
                    "member": step.member,
                    "attempt": step.attempt,
                    "session_id": session_id,
                    **({"turn": turn} if turn is not None else {}),
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

    async def fail_step(self, run_id: UUID, activation_id: UUID, reason: str) -> bool:
        """Fail an open step or offer; True if it was failed.

        For a step whose session is gone, and for an offer that cannot be taken (a member
        that never came up, the step deadline). It ends like any step that ended failed:
        the engine's sibling and run-failure rules apply to it. One that is no longer open is
        left alone.
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
                or step.kind not in (StepKind.STAGE_MEMBER, StepKind.OFFER)
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
                item.step for item in actions if isinstance(item, OfferStep | QueueStep)
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
            "Workflow step %s (%s/%s) of run %s: %s by %s (%s); attempt %d is offered (%s)",
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
        """Make the member's session fit for the new offer, or refuse the recovery."""
        if not step.session_id:
            return
        if action is RecoveryAction.REASSIGN:
            await self._restart(definition, step)
            return
        liveness = await self._sessions.session_liveness(step.run_id, step.session_id)
        if liveness is SessionLiveness.FAILED:
            await self._restart(definition, step)
            return
        if step.is_open:
            raise RecoveryRefusedError(
                action,
                f"session {step.session_id} still holds {step.node_id}/{step.member} open and "
                "cannot let go of it, so a new offer would be refused there as busy; "
                "reassign the step to restart the member instead",
            )

    async def _restart(self, definition: StepRunDefinition, step: WorkflowStep) -> None:
        """Restart the member's harness in place; its offers wait until it listens again.

        The restart is recorded first, so a listening report from the new launch is never
        undone by a late record of the restart.
        """
        member = step.member or ""
        async with self._repository.transaction(step.run_id) as tx:
            state = await tx.load()
            await _Ledger(tx, state, self._clock()).append(
                EVENT_MEMBER_RESTARTED, {"member": member, "session_id": step.session_id}
            )
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
        """Count what the commit decided, publish the offers that can go out, end the unit."""
        if ledger.failure is not None:
            self._report_failure(ledger)
        if ledger.terminal is None:
            await self._publish_ready(ledger.run_id, definition, graph)
        else:
            await self._sessions.stop_run_sessions(ledger.run_id)
        return await self._repository.load(ledger.run_id)

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
    def _count_repeat(report: str) -> None:
        """A session report Ting already applied, delivered again; accepted, changed nothing."""
        logger.debug("Ignored a repeated workflow step %s report", report)
        get_observability().count(
            "ting_workflow_step_reports_repeated_total",
            attributes={"report": report},
            description=(
                "Workflow step reports delivered again after Ting applied them "
                "(a reconnected or second activity stream); accepted, changed nothing."
            ),
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


def _passed(state: RunState, offer_id: str, member: str) -> bool:
    """Whether ``member`` already passed the offer."""
    return any(
        event.event_type == EVENT_OFFER_PASSED
        and event.payload.get("offer_id") == offer_id
        and event.payload.get("member") == member
        for event in state.events
    )


def _pass_reasons(state: RunState, offer_id: str) -> list[str]:
    """Why each member passed the offer, one entry per member."""
    return [
        f"{event.payload.get('member')}: {event.payload.get('reason') or 'no reason given'}"
        for event in state.events
        if event.event_type == EVENT_OFFER_PASSED and event.payload.get("offer_id") == offer_id
    ]


def _actor_json(by: GateActor) -> dict[str, str]:
    return by.to_json()


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


def _unclosed_turn_recorded(
    state: RunState, step: WorkflowStep, session_id: str | None, turn: int
) -> bool:
    """Whether this session's turn ``turn`` was already recorded as ending the step unclosed."""
    return any(
        event.event_type == EVENT_STEP_UNCLOSED
        and event.source_activation_id == step.id
        and event.payload.get("session_id") == session_id
        and event.payload.get("turn") == turn
        for event in state.events
    )
