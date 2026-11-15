"""A workflow run's steps on this session: taking offers from the run's mesh, doing them.

A run is one unit: its root (a Skuld with no agent) and every member share a local, signed
mesh. The run's engine hands an offer to the root (``POST /api/room/offer``), which publishes it
on the topic the node's members consume. Every member whose pinned persona consumes the topic
receives it and decides for itself:

* the offer is checked: it came from the run's root (the mesh signature), carries the engine's
  own signature (engine mode), names this member's persona pin and the contract that pin
  derives, and belongs to this run;
* the member's ``eventFilters`` decide whether it is this member's: ``step.picked_up`` when they
  match, ``step.passed`` (naming the filter) when they do not;
* a picked-up step is held durably in the session's event log, its instructions (the offer's,
  plus how to close this activation) go to the harness at a turn boundary, and a close through
  ``workflow_step_close`` is validated, persisted, reported to Forge and published as
  ``step.outcome``.

The session runs one step at a time. Skuld decides nothing about the work: it does not read the
harness's text, does not re-prompt a turn that ended without a close, and does not choose a
verdict. A turn that ends with the step still open is reported as ``unclosed`` and left to
Ting's stuck detection.

Everything this session did is reported to Forge as session activity (``listening``,
``picked_up``, ``passed``, ``offer_refused``, ``closed``, ``unclosed``; the root's
``offer_published``); Ting records the run from Forge's activity stream and never joins the mesh.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Coroutine, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from niuu.cap_telemetry import record_degraded
from niuu.domain.outcome import outcome_errors, outcome_schema_from_mapping
from niuu.domain.workflow_step import (
    ENDING_FAILED,
    ENDING_VERDICT,
    ENDING_WAITING,
    KIND_STEP_OFFERED,
    STEP_EVENT_CLOSED,
    STEP_EVENT_UNCLOSED,
    STEP_STATE_ACTIVE,
    STEP_STATE_CLOSED,
    STEP_STATE_WAITING,
    WORKFLOW_STEP_FRAME,
    OfferInput,
    PersonaPin,
    StepContract,
    StepOffered,
    StepOutcome,
    StepPassed,
    StepPickedUp,
    WorkflowEventBody,
    WorkflowEventError,
    WorkflowStepCloseArgs,
    WorkflowStepCloseError,
    persona_contract_of,
    render_close_howto,
)
from niuu.mesh.attestation import AttestationRefusedError
from niuu.mesh.events import MeshEvent
from niuu.mesh.platform_events import ATTESTATION_KEY, WorkflowOfferVerifier
from niuu.observability import get_observability
from personas.domain.persona_document import PortablePersonaDefinition
from skuld.mesh_topics import thread_topic

if TYPE_CHECKING:
    from skuld.peer_inbox import SenderIdentity

logger = logging.getLogger("skuld.broker")

#: Namespace of the deterministic id of the user turn that carries a step's instructions.
_STEP_TURN_NAMESPACE = "workflow-step"

#: Namespace of the activation id this session mints for an offer it picks up. One offer, one
#: session and one attempt are one activation, so an offer heard twice is the same activation.
_ACTIVATION_NAMESPACE = uuid.UUID("c3a1f0d2-6b7e-4e58-8d49-2f5a9b1c7e30")

#: Where the instructions turn came from, on the turn and its trace span.
STEP_TURN_SOURCE = "workflow_step"

#: ``metadata.workflow_step.event`` values this session reports besides ``closed`` and
#: ``unclosed`` (which ``niuu.domain.workflow_step`` names). Each report is this session's own act.
REPORT_LISTENING = "listening"
REPORT_OFFER_PUBLISHED = "offer_published"
REPORT_PICKED_UP = "picked_up"
REPORT_PASSED = "passed"
REPORT_OFFER_REFUSED = "offer_refused"

#: Why an offer was refused (``reason`` of ``offer_refused`` and of the refusals counter).
REFUSED_MALFORMED = "malformed"
REFUSED_OTHER_RUN = "other_run"
REFUSED_UNVERIFIED = "unverified"
REFUSED_NOT_ORCHESTRATOR = "not_orchestrator"
REFUSED_BAD_SIGNATURE = "bad_signature"
REFUSED_PIN = "pin"
REFUSED_CONTRACT = "contract"
REFUSED_BUSY = "busy"
REFUSED_NOT_DURABLE = "not_durable"
REFUSED_BROKER_FAILED = "broker_failed"

#: What ``accept_workflow_offer`` decided.
OFFER_PICKED_UP = "picked_up"
OFFER_PASSED = "passed"
OFFER_REPEAT = "repeat"
OFFER_IGNORED = "ignored"
OFFER_REFUSED = "refused"

#: The counter of offers this session refused, by ``reason`` (a mesh signature that did not
#: verify is counted by the mesh itself, on ``niuu.mesh.attestation.refusals``).
OFFER_REFUSALS_COUNTER = "skuld.workflow.offer_refusals"

#: A frame that records an offer this member passed, so Ting can read it from the durable log.
_PASSED_KEY = "passed"
#: The durable frame of a ``listening`` report (read back by Ting's catch-up).
_LISTENING_KEY = "listening"

_RUNNING_STATES = ("active", "tool_executing")
_TURN_END_STATES = ("idle", "stopped", "error")


_NO_OPEN_STEP = (
    "no workflow step is open on this session. A step is open from its activation until you "
    "close it; you can only close the step you were given."
)


class WorkflowStepNotHostedError(Exception):
    """This session is not a workflow member or root for the thing asked of it."""


class WorkflowOfferRefusedError(Exception):
    """The root refused to publish an offer. ``status`` is the HTTP status that says why."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class WorkflowStep:
    """One step activation as this session holds it. This is what the durable frame carries."""

    activation_id: str
    run_id: str
    attempt: int
    generation: int
    node_id: str
    node_label: str
    member: str
    valid_endings: list[str]
    valid_verdicts: list[str]
    outcome_schema: dict[str, Any] | None
    deadline_at: str | None
    instructions: str
    opened_at: str
    state: str = STEP_STATE_ACTIVE
    ending: dict[str, Any] | None = None
    closed_at: str | None = None
    #: The instructions turn has been handed to the harness (so a restart must not repeat it).
    injected: bool = False
    #: The offer this activation took up, and what its closes hand off: verdict -> event, or
    #: the one event of a persona that names no map.
    offer_id: str = ""
    topic: str = ""
    verdict_events: dict[str, str] = field(default_factory=dict)
    default_event: str = ""
    #: A turn has run since the instructions were handed over (memory only, never persisted).
    turn_seen: bool = field(default=False, compare=False)

    @classmethod
    def from_offer(
        cls, offer: StepOffered, contract: StepContract, *, session_id: str, member: str
    ) -> WorkflowStep:
        """The step this session opens for ``offer``, under its own activation id.

        The offer carries the node's task without the how-to-close; this session writes that
        for the activation it mints.
        """
        activation_id = str(
            uuid.uuid5(_ACTIVATION_NAMESPACE, f"{offer.offer_id}:{session_id}:{offer.attempt}")
        )
        return cls(
            activation_id=activation_id,
            run_id=offer.run_id,
            attempt=offer.attempt,
            generation=offer.generation,
            node_id=offer.node_id,
            node_label=offer.node_label,
            member=member,
            valid_endings=list(contract.valid_endings),
            valid_verdicts=list(contract.valid_verdicts),
            outcome_schema=dict(contract.outcome_schema) if contract.outcome_schema else None,
            deadline_at=offer.deadline_at.isoformat() if offer.deadline_at else None,
            instructions=(
                f"{offer.instructions}\n\n"
                f"{render_close_howto(activation_id, contract.valid_verdicts)}"
            ),
            opened_at=datetime.now(UTC).isoformat(),
            offer_id=offer.offer_id,
            topic=offer.topic,
            verdict_events=dict(contract.verdict_events),
            default_event=contract.default_event,
        )

    @classmethod
    def from_frame(cls, payload: dict[str, Any]) -> WorkflowStep:
        step = dict(payload["step"])
        step.pop("turn_seen", None)
        return cls(**step)

    @property
    def is_open(self) -> bool:
        return self.state in (STEP_STATE_ACTIVE, STEP_STATE_WAITING)

    def frame(self) -> dict[str, Any]:
        """The durable broker frame for the step's current state."""
        step = asdict(self)
        step.pop("turn_seen")
        return {"type": WORKFLOW_STEP_FRAME, "step": step}

    def produced_event(self, verdict: str | None) -> str:
        """The event a verdict hands off; empty when the persona does not name one."""
        if self.verdict_events:
            return self.verdict_events.get(verdict or "", "")
        return self.default_event


def step_turn_id(session_id: str, activation_id: str) -> str:
    """The user turn that carries a step's instructions has one id per activation."""
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}:{_STEP_TURN_NAMESPACE}:{activation_id}")
    )


def ending_from_close(step: WorkflowStep, args: WorkflowStepCloseArgs) -> dict[str, Any]:
    """Check a close against the open step and return the ending to record.

    Raises :class:`WorkflowStepCloseError` naming the exact reason and what to do instead.
    Nothing about the step changes.
    """
    if args.activation_id != step.activation_id:
        raise WorkflowStepCloseError(
            f"activation {args.activation_id} is not the open step {step.activation_id} "
            f"({step.node_label or step.node_id}); close that one."
        )
    if not step.is_open:
        closed_as = (step.ending or {}).get("kind", "closed")
        raise WorkflowStepCloseError(
            f"step {step.activation_id} is already closed ({closed_as}) and cannot be closed "
            "again. If you were given a new step, use the activation_id in its instructions."
        )
    if args.ending not in step.valid_endings:
        raise WorkflowStepCloseError(
            f"ending {args.ending!r} is not allowed for this step. Allowed endings: "
            f"{', '.join(step.valid_endings)}."
        )
    _check_ending_fields(step, args)
    outcome = args.outcome
    if step.outcome_schema is not None:
        schema = outcome_schema_from_mapping(step.outcome_schema)
        if outcome is None and args.ending == ENDING_VERDICT:
            required = (
                ", ".join(name for name, spec in schema.fields.items() if spec.required)
                or "none required"
            )
            raise WorkflowStepCloseError(
                "this step defines an outcome schema, so a verdict ending needs `outcome` "
                f"(required fields: {required}). Add it and call again."
            )
        if outcome is not None:
            errors = outcome_errors(outcome, schema)
            if errors:
                raise WorkflowStepCloseError(
                    f"outcome does not match the step's outcome schema: {'; '.join(errors)}. "
                    "Fix `outcome` and call again."
                )
    return {
        "kind": args.ending,
        "verdict": args.verdict,
        "outcome": outcome if outcome is not None else {},
        "note": args.note,
        "evidence": [item.model_dump() for item in args.evidence],
        "waiting_on": args.waiting_on.model_dump() if args.waiting_on else None,
        "failure_reason": args.failure_reason,
    }


def _check_ending_fields(step: WorkflowStep, args: WorkflowStepCloseArgs) -> None:
    """Each ending takes exactly its own fields: one that would be dropped is refused."""
    if args.ending == ENDING_VERDICT:
        if not (args.verdict or "").strip():
            choices = ", ".join(step.valid_verdicts) or "any non-blank verdict"
            raise WorkflowStepCloseError(f"ending 'verdict' needs `verdict`: {choices}.")
        if step.valid_verdicts and args.verdict not in step.valid_verdicts:
            raise WorkflowStepCloseError(
                f"verdict {args.verdict!r} is not valid for this step. Valid verdicts: "
                f"{', '.join(step.valid_verdicts)}."
            )
    elif args.verdict is not None:
        raise WorkflowStepCloseError(
            f"`verdict` is only for ending 'verdict', not {args.ending!r}. Remove it, or end "
            "with a verdict."
        )
    if args.ending == ENDING_WAITING:
        if args.waiting_on is None:
            raise WorkflowStepCloseError(
                "ending 'waiting' needs `waiting_on` ({kind: person|session|event|time|agent, "
                "ref, reason}) saying what you are waiting for."
            )
    elif args.waiting_on is not None:
        raise WorkflowStepCloseError(
            f"`waiting_on` is only for ending 'waiting', not {args.ending!r}. Remove it."
        )
    if args.ending == ENDING_FAILED:
        if not (args.failure_reason or "").strip():
            raise WorkflowStepCloseError(
                "ending 'failed' needs `failure_reason` saying why the step cannot be completed."
            )
    elif args.failure_reason is not None:
        raise WorkflowStepCloseError(
            f"`failure_reason` is only for ending 'failed', not {args.ending!r}. Remove it."
        )


class WorkflowStepMixin:
    """Broker behavior for taking a workflow run's offers and publishing its own acts."""

    _workflow_steps: dict[str, WorkflowStep]
    _pending_step_reports: list[dict[str, Any]]
    _workflow_step_lock: asyncio.Lock
    _workflow_step_tasks: set[asyncio.Task[None]]
    #: What this member did with each offer it received: ``picked_up`` or ``passed``.
    _offer_decisions: dict[str, str]
    #: The contract this member's own pinned persona derives (None unless a member).
    _own_contract: StepContract | None
    #: Identifies this start of the process; ``listening`` is reported once per start.
    _workflow_start_id: str

    def _init_workflow_steps(self) -> None:
        self._workflow_steps = {}
        self._pending_step_reports = []
        self._workflow_step_lock = asyncio.Lock()
        self._workflow_step_tasks = set()
        self._offer_decisions = {}
        self._own_contract = None
        self._workflow_start_id = uuid.uuid4().hex
        settings = self._settings
        config = settings.workflow_steps
        if not (config.enabled or config.root):
            return
        role = "workflow_steps.enabled" if config.enabled else "workflow_steps.root"
        if not settings.mesh.enabled:
            raise ValueError(
                f"{role} needs the run's mesh: offers and outcomes travel on it. Set "
                "mesh.enabled: true, or turn workflow_steps off."
            )
        if not config.root:
            self._init_workflow_member(settings)

    def _init_workflow_member(self, settings: Any) -> None:
        if not (
            settings.event_log_enabled
            and settings.volundr_api_url
            and settings.history_hydration_enabled
        ):
            raise ValueError(
                "workflow_steps.enabled needs the durable event log and history hydration: a "
                "held step is restored from the log after a restart. Set volundr_api_url, "
                "event_log_enabled: true and history_hydration_enabled: true, or turn "
                "workflow_steps.enabled off."
            )
        raw = settings.session.persona
        if raw is None:
            raise ValueError(
                "workflow_steps.enabled needs the member's pinned persona (session.persona): "
                "the contract of every offer is checked against it. Pin one, or turn "
                "workflow_steps.enabled off."
            )
        persona = PortablePersonaDefinition.from_dict(raw)
        pin = PersonaPin(
            alias=settings.workflow_steps.persona_alias,
            id=persona.id,
            revision=persona.revision,
            digest=persona.digest,
        )
        self._own_contract = StepContract.from_persona(pin, persona_contract_of(raw))

    def _spawn_workflow_step_task(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        """Run step work in the background, keeping it alive and logging how it ends."""
        task = asyncio.create_task(coro, name=name)
        self._workflow_step_tasks.add(task)
        task.add_done_callback(self._workflow_step_task_done)

    def _workflow_step_task_done(self, task: asyncio.Task[None]) -> None:
        self._workflow_step_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("workflow step task %s failed", task.get_name(), exc_info=task.exception())

    def _open_workflow_step(self) -> WorkflowStep | None:
        if not self._settings.workflow_steps.enabled:
            return None
        return next((step for step in self._workflow_steps.values() if step.is_open), None)

    # -- the mesh is up

    async def workflow_mesh_ready(self) -> None:
        """The mesh adapter started: say so, and announce again what this member already holds.

        ``listening`` tells Ting this member's subscriptions are up (it waits for every member
        that consumes an offer's topic before it offers). A step restored from the log after a
        restart was announced before the restart; it is announced again, which is the same event
        (same id) and a report Ting treats as a repeat, in case the first never arrived.
        """
        config = self._settings.workflow_steps
        if not config.enabled:
            return
        listening = {
            "run_id": config.run_id,
            "member": config.member,
            "start_id": self._workflow_start_id,
            "topics": list(self._mesh_topics.consumes),
        }
        # Durable first: Ting offers nothing until every member that takes a topic listens,
        # and a report its activity stream missed (Ting restarted, the stream reconnected) is
        # read back from this log. A listening report that is not stored would leave the run
        # waiting on this member until the step's deadline, so not storing it fails the start.
        await self._record_workflow_frame(
            {"type": WORKFLOW_STEP_FRAME, _LISTENING_KEY: listening},
            f"listening report of member {config.member}",
        )
        await self._report_workflow_step({"event": REPORT_LISTENING, **listening})
        for step in list(self._workflow_steps.values()):
            if step.is_open:
                await self._announce_pickup(step)

    # -- offers: a member takes them from the mesh

    async def accept_workflow_offer(
        self, event: MeshEvent, *, verified: SenderIdentity | None
    ) -> str:
        """Decide what to do with a ``workflow`` event that reached this member's topics.

        Only ``step.offered`` is acted on; the other kinds are information for the room and
        are ignored here. Returns what was decided (``OFFER_*``). A refusal is returned, never
        raised: it is logged at error, counted and reported to Forge, naming the reason.
        """
        config = self._settings.workflow_steps
        if not config.enabled:
            return OFFER_IGNORED
        if event.payload.get("kind") != KIND_STEP_OFFERED:
            return OFFER_IGNORED
        try:
            offer = event.as_workflow()
        except WorkflowEventError as exc:
            return await self._refuse_offer(
                offer_id=str(event.payload.get("offer_id") or ""),
                node_id=str(event.payload.get("node_id") or ""),
                reason=REFUSED_MALFORMED,
                detail=str(exc),
            )
        if not isinstance(offer, StepOffered):  # pragma: no cover - kind was checked above
            return OFFER_IGNORED
        if offer.run_id != config.run_id:
            return await self._refuse_offer(
                offer=offer,
                reason=REFUSED_OTHER_RUN,
                detail=f"offer is for run {offer.run_id}, this member belongs to {config.run_id}",
            )
        if offer.topic not in self._mesh_topics.consumes:
            # Heard on a topic followed at runtime (or the thread): only a persona that
            # declares the topic takes the offer, and a follower is not refusing anything.
            logger.debug(
                "workflow offer %s is not for a topic this persona consumes", offer.offer_id
            )
            return OFFER_IGNORED
        refusal = self._offer_origin_refusal(event, offer, verified)
        if refusal is not None:
            return await self._refuse_offer(offer=offer, reason=refusal[0], detail=refusal[1])
        async with self._workflow_step_lock:
            return await self._decide_offer(offer)

    def _offer_origin_refusal(
        self, event: MeshEvent, offer: StepOffered, verified: SenderIdentity | None
    ) -> tuple[str, str] | None:
        """Why the offer did not come from the run's orchestrator, or ``None`` when it did."""
        config = self._settings.workflow_steps
        required = self._settings.mesh.attestation.required
        if required:
            if verified is None:
                return (
                    REFUSED_UNVERIFIED,
                    "the mesh did not verify who published this offer, so it cannot be told "
                    "from one anybody could have sent",
                )
            publisher = verified.peer_id
        else:
            # An unauthenticated mesh (the operator's decision): the publisher is only claimed.
            publisher = event.source.split(":", 1)[-1]
        if publisher != config.orchestrator_peer_id:
            return (
                REFUSED_NOT_ORCHESTRATOR,
                f"offer was published by {publisher!r}, not by this run's root "
                f"{config.orchestrator_peer_id!r}",
            )
        if config.orchestration != "engine" or not required:
            return None
        return self._engine_signature_refusal(event)

    def _engine_signature_refusal(self, event: MeshEvent) -> tuple[str, str] | None:
        attested = getattr(self, "_mesh_attested", None)
        if attested is None:
            return (
                REFUSED_UNVERIFIED,
                "this session has no mesh verifier, so the engine's signature on the offer "
                "cannot be checked",
            )
        config = self._settings.workflow_steps
        verifier = WorkflowOfferVerifier(
            attested.verifier,
            event_max_age_s=self._settings.mesh.attestation.event_max_age_s,
            signer_peer_id=config.offer_signer,
        )
        try:
            verifier.verify(event.payload, event.payload.get(ATTESTATION_KEY))
        except AttestationRefusedError as exc:
            return REFUSED_BAD_SIGNATURE, f"the engine's signature on the offer: {exc}"
        return None

    async def _decide_offer(self, offer: StepOffered) -> str:
        """Pick the offer up, pass it, or refuse it. The caller holds the step lock."""
        known = self._offer_decisions.get(offer.offer_id)
        if known is not None:
            return await self._repeat_offer(offer, known)
        if self._startup_state == "failed":
            return await self._refuse_offer(
                offer=offer,
                reason=REFUSED_BROKER_FAILED,
                detail=f"this session's broker failed to start: {self._startup_failure}",
            )
        alias = self._settings.workflow_steps.persona_alias
        contract = offer.contract_for(alias)
        if contract is None:
            # The engine addresses an offer to the members it names a contract for; another
            # member that listens on the same topic (a reviewer hearing code.requested) is not
            # asked, so it neither takes, passes nor refuses it.
            logger.info(
                "workflow offer %s (%s) is for [%s], not this member's persona %r",
                offer.offer_id,
                offer.node_id,
                ", ".join(c.persona.alias for c in offer.contracts),
                alias,
            )
            return OFFER_IGNORED
        own = self._own_contract
        if own is None:
            return await self._refuse_offer(
                offer=offer,
                reason=REFUSED_PIN,
                detail=(
                    f"offer names a contract for this member's persona {alias!r}, but this "
                    "member derives no contract of its own from its pinned persona"
                ),
            )
        if contract.persona != own.persona:
            return await self._refuse_offer(
                offer=offer,
                reason=REFUSED_PIN,
                detail=(
                    f"offer pins {contract.persona.describe()}, this member runs "
                    f"{own.persona.describe()}"
                ),
            )
        mismatch = contract.mismatch(own)
        if mismatch is not None:
            return await self._refuse_offer(offer=offer, reason=REFUSED_CONTRACT, detail=mismatch)
        passed = filter_mismatch(offer.inputs, self._settings.workflow_steps.event_filters)
        if passed is not None:
            return await self._pass_offer(offer, passed)
        open_step = self._open_workflow_step()
        if open_step is not None:
            return await self._refuse_offer(
                offer=offer,
                reason=REFUSED_BUSY,
                detail=(
                    f"this session holds step {open_step.activation_id} "
                    f"({open_step.node_label or open_step.node_id}, state {open_step.state}) and "
                    "runs one step at a time; the offer is not taken, and offering it again "
                    "after that step closes takes it"
                ),
            )
        return await self._pick_up_offer(offer, contract)

    async def _repeat_offer(self, offer: StepOffered, decision: str) -> str:
        """An offer heard again changes nothing, and is answered again (the same event)."""
        if decision == OFFER_PASSED:
            await self._publish_workflow_body(self._passed_body(offer, ""))
            return OFFER_REPEAT
        step = next(
            (s for s in self._workflow_steps.values() if s.offer_id == offer.offer_id), None
        )
        if step is not None:
            await self._publish_workflow_body(self._picked_up_body(step))
        return OFFER_REPEAT

    async def _pass_offer(self, offer: StepOffered, reason: str) -> str:
        member = self._settings.workflow_steps.member
        frame = {
            "type": WORKFLOW_STEP_FRAME,
            _PASSED_KEY: {
                "offer_id": offer.offer_id,
                "run_id": offer.run_id,
                "node_id": offer.node_id,
                "member": member,
                "reason": reason,
            },
        }
        try:
            await self._record_workflow_frame(frame, f"pass of offer {offer.offer_id}")
        except RuntimeError as exc:
            return await self._refuse_offer(
                offer=offer, reason=REFUSED_NOT_DURABLE, detail=str(exc)
            )
        self._offer_decisions[offer.offer_id] = OFFER_PASSED
        await self._report_workflow_step(
            {
                "event": REPORT_PASSED,
                "run_id": offer.run_id,
                "offer_id": offer.offer_id,
                "node_id": offer.node_id,
                "member": member,
                "reason": reason,
            }
        )
        await self._publish_workflow_body(self._passed_body(offer, reason))
        return OFFER_PASSED

    def _passed_body(self, offer: StepOffered, reason: str) -> StepPassed:
        return StepPassed(
            run_id=offer.run_id,
            offer_id=offer.offer_id,
            node_id=offer.node_id,
            member=self._settings.workflow_steps.member,
            session_id=str(self.session_id),
            reason=reason,
        )

    async def _pick_up_offer(self, offer: StepOffered, contract: StepContract) -> str:
        step = WorkflowStep.from_offer(
            offer,
            contract,
            session_id=str(self.session_id),
            member=self._settings.workflow_steps.member,
        )
        try:
            await self._record_workflow_step(step, confirm=True)
        except RuntimeError as exc:
            return await self._refuse_offer(
                offer=offer, reason=REFUSED_NOT_DURABLE, detail=str(exc)
            )
        self._workflow_steps[step.activation_id] = step
        self._offer_decisions[offer.offer_id] = OFFER_PICKED_UP
        await self._announce_pickup(step)
        self._spawn_boundary_injection(f"workflow-step-inject-{step.activation_id}")
        return OFFER_PICKED_UP

    def _picked_up_body(self, step: WorkflowStep) -> StepPickedUp:
        return StepPickedUp(
            run_id=step.run_id,
            offer_id=step.offer_id,
            node_id=step.node_id,
            member=step.member,
            session_id=str(self.session_id),
            activation_id=step.activation_id,
            attempt=step.attempt,
        )

    async def _announce_pickup(self, step: WorkflowStep) -> None:
        """Tell Forge and the run's mesh that this member holds the step."""
        await self._report_workflow_step(
            {
                "event": REPORT_PICKED_UP,
                "run_id": step.run_id,
                "offer_id": step.offer_id,
                "node_id": step.node_id,
                "member": step.member,
                "activation_id": step.activation_id,
                "attempt": step.attempt,
            }
        )
        await self._publish_workflow_body(self._picked_up_body(step))

    async def _refuse_offer(
        self,
        *,
        reason: str,
        detail: str,
        offer: StepOffered | None = None,
        offer_id: str = "",
        node_id: str = "",
    ) -> str:
        """An offer that is not taken and not passed: loud, counted, and reported to Forge."""
        offer_id = offer.offer_id if offer is not None else offer_id
        node_id = offer.node_id if offer is not None else node_id
        logger.error("workflow offer %s refused (%s): %s", offer_id or "<no id>", reason, detail)
        get_observability().count(
            OFFER_REFUSALS_COUNTER,
            attributes={"reason": reason, "session_id": str(self.session_id)},
            description="Workflow offers this session refused, by reason.",
        )
        await self._report_workflow_step(
            {
                "event": REPORT_OFFER_REFUSED,
                "run_id": self._settings.workflow_steps.run_id,
                "offer_id": offer_id,
                "node_id": node_id,
                "member": self._settings.workflow_steps.member,
                "reason": reason,
                "detail": detail,
            }
        )
        return OFFER_REFUSED

    # -- publishing on the run's mesh

    async def _publish_workflow_body(
        self, body: WorkflowEventBody, *, payload: Mapping[str, Any] | None = None
    ) -> None:
        """Publish a workflow event on the run's thread, and on its own topic if it has one.

        Ting records from Forge, not from the mesh, so a publication that fails after the
        step's state is durable and reported does not undo it: it is an error log and a count
        on the degraded surface naming what the room and the coordinator will miss.
        """
        adapter = self._mesh_adapter
        try:
            if adapter is None:
                raise RuntimeError("the mesh adapter is not running")
            event = MeshEvent.workflow(
                source=adapter.peer_id, body=body, session_id=str(self.session_id)
            )
            if payload is not None:
                event = replace(event, payload=dict(payload))
            topics = [t for t in (body.publish_topic, thread_topic(body.run_id)) if t]
            for topic in dict.fromkeys(topics):
                await adapter.publish_event(event, topic)
        except Exception as exc:
            record_degraded(
                f"skuld.workflow.{body.KIND}",
                f"could not publish {body.KIND} ({body.dedup_key()}) on the run's mesh "
                f"({type(exc).__name__}: {exc}); the room and a coordinator miss it, Forge "
                "has the report",
            )

    # -- the root: publishes the offers its engine hands it

    async def publish_workflow_offer(
        self, offer: dict[str, Any], attestation: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Publish the run's engine's offer on the mesh, verbatim, with its signature.

        Raises :class:`WorkflowStepNotHostedError` (this session is not a run's root) or
        :class:`WorkflowOfferRefusedError` with the HTTP status that says why. Publishing the
        same offer again is the same event.
        """
        config = self._settings.workflow_steps
        if not config.root:
            raise WorkflowStepNotHostedError(
                "this session is not a workflow run's root (workflow_steps.root is off)"
            )
        if config.orchestration != "engine":
            raise WorkflowOfferRefusedError(
                "this run is orchestrated by a coordinator session: it publishes its own "
                "offers, and the engine's are not taken",
                status=409,
            )
        try:
            body = _parse_offer(offer)
        except ValueError as exc:
            raise WorkflowOfferRefusedError(f"not an offer: {exc}", status=400) from exc
        if body.run_id != config.run_id:
            raise WorkflowOfferRefusedError(
                f"offer is for run {body.run_id}, this is the root of run {config.run_id}",
                status=409,
            )
        self._check_engine_signature(offer, attestation)
        if self._mesh_adapter is None or not self._mesh_adapter.is_running:
            raise WorkflowOfferRefusedError(
                "the mesh is not running on this root, so the offer was not published; check "
                "the broker log for why the mesh adapter did not start",
                status=503,
            )
        payload = {**offer, **({ATTESTATION_KEY: attestation} if attestation else {})}
        event = replace(
            MeshEvent.workflow(
                source=self._mesh_adapter.peer_id, body=body, session_id=str(self.session_id)
            ),
            payload=payload,
        )
        topics = list(dict.fromkeys([body.topic, thread_topic(body.run_id)]))
        try:
            for topic in topics:
                await self._mesh_adapter.publish_event(event, topic)
        except Exception as exc:
            raise WorkflowOfferRefusedError(
                f"the mesh refused the offer ({type(exc).__name__}: {exc}); nothing was "
                "taken, and posting it again publishes it again",
                status=503,
            ) from exc
        await self._report_workflow_step(
            {
                "event": REPORT_OFFER_PUBLISHED,
                "run_id": body.run_id,
                "offer_id": body.offer_id,
                "node_id": body.node_id,
                "topic": body.topic,
            }
        )
        return {"offer_id": body.offer_id, "event_id": event.event_id, "topics": topics}

    def _check_engine_signature(
        self, offer: Mapping[str, Any], attestation: dict[str, Any] | None
    ) -> None:
        """The root does not publish an offer its engine did not sign (when the mesh signs)."""
        if not self._settings.mesh.attestation.required:
            return
        attested = getattr(self, "_mesh_attested", None)
        if attested is None:
            raise WorkflowOfferRefusedError(
                "this root has no mesh verifier, so it cannot check the engine's signature",
                status=503,
            )
        config = self._settings.workflow_steps
        verifier = WorkflowOfferVerifier(
            attested.verifier,
            event_max_age_s=self._settings.mesh.attestation.event_max_age_s,
            signer_peer_id=config.offer_signer,
        )
        try:
            verifier.verify(offer, attestation)
        except AttestationRefusedError as exc:
            raise WorkflowOfferRefusedError(
                f"the engine's signature on the offer is not valid: {exc}", status=403
            ) from exc

    # -- durable state

    async def _record_workflow_step(self, step: WorkflowStep, *, confirm: bool) -> None:
        """Write the step's state to the session event log; wait until Forge stored it."""
        await self._record_workflow_frame(
            step.frame(), f"workflow step {step.activation_id}", confirm=confirm
        )

    async def _record_workflow_frame(
        self, frame: dict[str, Any], what: str, *, confirm: bool = True
    ) -> None:
        """Write a workflow frame to the session event log; wait until Forge stored it.

        A frame that is not durable would be lost by a restart, so one Forge did not store is
        an error, never state that exists only in memory.
        """
        entry = self._enqueue_event_log(frame)
        if entry is None:
            raise RuntimeError(
                f"the durable event log refused the {what}; it would not survive a restart"
            )
        receipt = self._watch_event_log_entry(entry)
        await self._channels.broadcast(frame)
        if not confirm:
            return
        stored = await self._await_event_log_receipt(
            receipt, self._settings.forge_mcp.notify_confirm_timeout_s
        )
        if stored is False:
            raise RuntimeError(
                f"the {what} was not stored (it was dropped while Forge was unreachable, or "
                "its sequence conflicted)"
            )
        if stored is None:
            logger.warning(
                "%s: Forge has not yet acknowledged the log flush within "
                "forge_mcp.notify_confirm_timeout_s; it will be stored when Forge is reachable",
                what,
            )

    # -- injection at a turn boundary

    async def _maybe_inject_workflow_step(self) -> bool:
        """Hand the open step's instructions to the harness if it is between turns.

        True when it did, so the caller leaves any peer messages for the next boundary: the
        step goes in first. The gate is the one peer messages share (``_turn_boundary_open``).
        """
        async with self._workflow_step_lock:
            step = self._open_workflow_step()
            if step is None or step.injected or step.state != STEP_STATE_ACTIVE:
                return False
            if self._transport is None or self._activity_state in ("stopped", "error"):
                return False
            if not self._turn_boundary_open():
                return False
            step.injected = True
            step.turn_seen = False
            try:
                await self._record_workflow_step(step, confirm=True)
                await self._ensure_transport_started()
                task = await self._submit_user_turn(
                    step.instructions,
                    msg_id=step_turn_id(self.session_id, step.activation_id),
                    request_id=None,
                    source=STEP_TURN_SOURCE,
                    extra_metadata={
                        "workflow_step": {
                            "activation_id": step.activation_id,
                            "run_id": step.run_id,
                            "offer_id": step.offer_id,
                            "node_id": step.node_id,
                            "attempt": step.attempt,
                        }
                    },
                )
            except Exception as exc:
                logger.exception("workflow step %s: could not start delivery", step.activation_id)
                await self._fail_workflow_step(
                    step, f"could not hand the step to the harness: {exc}"
                )
                return False
        self._spawn_workflow_step_task(
            self._finish_step_delivery(step, task), f"workflow-step-delivered-{step.activation_id}"
        )
        return True

    async def _finish_step_delivery(self, step: WorkflowStep, task: asyncio.Task[None]) -> None:
        """A step whose instructions never reached the harness fails loudly, never waits."""
        await task
        msg_id = step_turn_id(self.session_id, step.activation_id)
        turn = next((t for t in self._conversation_turns if t.id == msg_id), None)
        if turn is None or turn.metadata.get("steering_state") != "failed":
            return
        async with self._workflow_step_lock:
            if not step.is_open or step.state != STEP_STATE_ACTIVE:
                return
            await self._fail_workflow_step(
                step,
                "the harness did not accept the step's instructions after the delivery retries "
                "(delivery.max_attempts); see the session's user_delivery_failed frame",
            )

    async def _fail_workflow_step(self, step: WorkflowStep, reason: str) -> None:
        """Close a step as failed on Skuld's own account, and say so."""
        ending = {
            "kind": ENDING_FAILED,
            "verdict": None,
            "outcome": {},
            "note": None,
            "evidence": [],
            "waiting_on": None,
            "failure_reason": reason,
        }
        logger.error("workflow step %s failed: %s", step.activation_id, reason)
        await self._close_step(step, ending)

    async def _close_step(self, step: WorkflowStep, ending: dict[str, Any]) -> None:
        """Persist the step's close, report it to Forge, then publish it on the run's mesh.

        The caller holds the step lock. The report is what Ting records; the mesh event is the
        run's record for the room and a coordinator, and carries the same ending.
        """
        step.state = STEP_STATE_WAITING if ending["kind"] == ENDING_WAITING else STEP_STATE_CLOSED
        step.ending = ending
        step.closed_at = datetime.now(UTC).isoformat() if step.state == STEP_STATE_CLOSED else None
        step.turn_seen = False
        await self._record_workflow_step(step, confirm=True)
        await self._report_workflow_step(
            {
                "event": STEP_EVENT_CLOSED,
                "run_id": step.run_id,
                "offer_id": step.offer_id,
                "activation_id": step.activation_id,
                "attempt": step.attempt,
                "ending": ending,
            }
        )
        await self._publish_workflow_body(self._outcome_body(step, ending))

    def _outcome_body(self, step: WorkflowStep, ending: dict[str, Any]) -> StepOutcome:
        verdict = ending.get("verdict")
        return StepOutcome(
            run_id=step.run_id,
            offer_id=step.offer_id,
            node_id=step.node_id,
            member=step.member,
            session_id=str(self.session_id),
            activation_id=step.activation_id,
            attempt=step.attempt,
            ending=ending["kind"],
            verdict=verdict,
            outcome=ending.get("outcome"),
            note=ending.get("note"),
            evidence=ending.get("evidence") or [],
            waiting_on=ending.get("waiting_on"),
            failure_reason=ending.get("failure_reason"),
            produced_event=step.produced_event(verdict) if ending["kind"] == ENDING_VERDICT else "",
        )

    # -- the close tool

    async def close_workflow_step(self, args: WorkflowStepCloseArgs) -> dict[str, Any]:
        """``workflow_step_close``: validate against the open step, persist, report, answer."""
        async with self._workflow_step_lock:
            step = self._open_workflow_step()
            if step is None:
                raise WorkflowStepCloseError(_NO_OPEN_STEP)
            ending = ending_from_close(step, args)
            previous = (step.state, step.ending, step.closed_at)
            try:
                await self._close_step(step, ending)
            except RuntimeError:
                step.state, step.ending, step.closed_at = previous
                raise
        return {
            "activation_id": step.activation_id,
            "state": step.state,
            "ending": ending["kind"],
            "reported": True,
        }

    # -- turn boundaries

    async def _observe_workflow_step_activity(self, state: str) -> None:
        """Track the harness's turns against the open step.

        Handing instructions over at an idle boundary is ``_observe_turn_boundary``'s job.
        """
        step = self._open_workflow_step()
        if step is None:
            return
        if state in _RUNNING_STATES:
            if step.injected:
                step.turn_seen = True
            return
        if state not in _TURN_END_STATES:
            return
        if step.injected and step.turn_seen and step.state == STEP_STATE_ACTIVE:
            step.turn_seen = False
            logger.warning(
                "workflow step %s: the turn ended without workflow_step_close", step.activation_id
            )
            await self._report_workflow_step(
                {
                    "event": STEP_EVENT_UNCLOSED,
                    "run_id": step.run_id,
                    "offer_id": step.offer_id,
                    "activation_id": step.activation_id,
                    "attempt": step.attempt,
                }
            )

    # -- restore

    def _restore_durable_workflow_steps(self, frames: list) -> None:
        """Rebuild the steps this session held, and the offers it passed, from the log."""
        for frame in frames:
            if getattr(frame, "kind", "") != WORKFLOW_STEP_FRAME:
                continue
            if _PASSED_KEY in frame.payload:
                self._offer_decisions[frame.payload[_PASSED_KEY]["offer_id"]] = OFFER_PASSED
                continue
            if _LISTENING_KEY in frame.payload:
                # A start's listening report holds no step; this start reports its own.
                continue
            step = WorkflowStep.from_frame(frame.payload)
            self._workflow_steps[step.activation_id] = step
            if step.offer_id:
                self._offer_decisions[step.offer_id] = OFFER_PICKED_UP


def _parse_offer(offer: Mapping[str, Any]) -> StepOffered:
    """The offer a request carries, or ``ValueError`` saying what is wrong."""
    if offer.get("kind") != KIND_STEP_OFFERED:
        raise ValueError(f"the payload's kind is {offer.get('kind')!r}, not {KIND_STEP_OFFERED!r}")
    body = {key: value for key, value in offer.items() if key not in ("kind", ATTESTATION_KEY)}
    try:
        return StepOffered.model_validate(body)
    except Exception as exc:
        raise ValueError(f"malformed step.offered: {exc}") from exc


def filter_mismatch(inputs: list[OfferInput], filters: Mapping[str, str]) -> str | None:
    """Why this member's ``eventFilters`` do not match the offer, or ``None`` when they do.

    Each filter key is looked up in an input's payload, then its ``fields``, then its
    ``outcome``; a list value matches when it contains the expected value, anything else when
    its text equals it. The offer matches when one of its inputs satisfies every filter. The
    reason names the filter that did not, so ``step.passed`` says why.
    """
    if not filters:
        return None
    if not inputs:
        key = next(iter(filters))
        return f"filter {key}={filters[key]!r}: the offer carries no inputs to match it against"
    reasons: list[str] = []
    for item in inputs:
        reason = _input_filter_mismatch(item.payload, filters)
        if reason is None:
            return None
        reasons.append(f"{item.event_type}: {reason}")
    return "; ".join(reasons)


def _input_filter_mismatch(payload: Mapping[str, Any], filters: Mapping[str, str]) -> str | None:
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
                return f"filter {key}={expected!r} not in {actual!r}"
            continue
        if str(actual or "").strip() != expected:
            return f"filter {key}={expected!r} but the input has {actual!r}"
    return None
