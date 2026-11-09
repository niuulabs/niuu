"""The shapes a workflow step host and its harness exchange, and the `workflow` mesh event.

A workflow run activates a step on a harness session (Forge relays the activation to the
session's Skuld), and the harness closes it through the ``workflow_step_close`` tool of the
session's built-in ``forge`` MCP server. Skuld holds the step durably and reports its close
to Ting through session activity. These are the tool's arguments and the report's ending, so
the tool definition (niuu), the broker (Skuld) and the contract tests agree on one shape.

The same module holds what a step handoff on the run's mesh shares: the persona contract every
party derives the same way, the text that tells a member how to close a step, and the
`workflow` event (``step.offered`` … ``run.ended``) with its open set of kinds. Ting and Skuld
both import it, so neither owns the other's shapes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

#: The ways a step activation can end.
ENDING_VERDICT = "verdict"
ENDING_WAITING = "waiting"
ENDING_DONE = "done"
ENDING_FAILED = "failed"
STEP_ENDINGS = (ENDING_VERDICT, ENDING_WAITING, ENDING_DONE, ENDING_FAILED)

#: What a waiting step can be waiting for.
WAITING_ON_KINDS = ("person", "session", "event", "time", "agent")

#: The durable broker frame that carries a step's state in the session event log.
WORKFLOW_STEP_FRAME = "workflow_step"

#: A step's state in Skuld: ``active`` until its harness closes it, ``waiting`` when it closed
#: with a ``waiting`` ending (the step stays open and may be closed again), ``closed`` after.
STEP_STATE_ACTIVE = "active"
STEP_STATE_WAITING = "waiting"
STEP_STATE_CLOSED = "closed"

#: Session-activity ``metadata.workflow_step.event`` values.
STEP_EVENT_CLOSED = "closed"
STEP_EVENT_UNCLOSED = "unclosed"

#: The tool the harness closes a step with.
WORKFLOW_STEP_CLOSE_TOOL = "workflow_step_close"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepEvidence(_Strict):
    """One piece of evidence the closing agent attaches to its ending."""

    kind: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    summary: str = ""


class StepWaitingOn(_Strict):
    """What a waiting step is waiting for."""

    kind: Literal["person", "session", "event", "time", "agent"]
    ref: str = Field(min_length=1)
    reason: str = ""


class WorkflowStepCloseArgs(_Strict):
    """Arguments of the ``workflow_step_close`` tool, before they are checked against the step."""

    activation_id: str = Field(min_length=1)
    ending: Literal["verdict", "waiting", "done", "failed"]
    verdict: str | None = None
    outcome: dict[str, Any] | None = None
    note: str | None = None
    evidence: list[StepEvidence] = Field(default_factory=list)
    waiting_on: StepWaitingOn | None = None
    failure_reason: str | None = None


class WorkflowStepCloseError(Exception):
    """A close the open step does not allow. The message names the exact reason and the fix.

    It is shown to the agent as a tool error; nothing is recorded.
    """


# ---------------------------------------------------------------------------------------------
# The persona contract, derived the same way by whoever needs it.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonaContract:
    """What a persona says about the events it takes in and gives out.

    ``produces_map`` maps a verdict to the event it hands off; when a persona has
    one, a verdict outside it is refused. ``produces_event`` is the event of a
    persona that declares no map, whatever its verdict.
    """

    produces_map: Mapping[str, str] = field(default_factory=dict)
    produces_event: str = ""
    consumes: tuple[str, ...] = ()
    #: The persona's ``produces.schema`` exactly as its document states it; ``None`` when absent.
    outcome_schema: Mapping[str, Any] | None = None

    @property
    def produced_events(self) -> frozenset[str]:
        if self.produces_map:
            return frozenset(self.produces_map.values())
        return frozenset({self.produces_event}) if self.produces_event else frozenset()

    @property
    def valid_verdicts(self) -> tuple[str, ...]:
        """The verdicts a step may close with, sorted; empty when any verdict is accepted."""
        return tuple(sorted(self.produces_map))

    def event_for(self, verdict: str) -> str | None:
        """The event a verdict hands off, or ``None`` when the persona does not name it."""
        if self.produces_map:
            return self.produces_map.get(verdict)
        return self.produces_event or None


def persona_contract_of(definition: Mapping[str, Any]) -> PersonaContract:
    """Read a persona's event contract from its portable document or its bare definition.

    Ting derives it to route a run; a member's Skuld derives it from its own pinned persona
    to check an offer. Both must read the same document the same way, so there is one reader.
    """
    body = definition.get("definition")
    persona = body if isinstance(body, Mapping) else definition
    produces = persona.get("produces")
    produces = produces if isinstance(produces, Mapping) else {}
    consumes = persona.get("consumes")
    consumes = consumes if isinstance(consumes, Mapping) else {}
    raw_map = produces.get("event_type_map")
    event_map = (
        {str(verdict): str(event) for verdict, event in raw_map.items()}
        if isinstance(raw_map, Mapping)
        else {}
    )
    raw_consumes = consumes.get("event_types")
    raw_schema = produces.get("schema")
    return PersonaContract(
        produces_map=event_map,
        produces_event=str(produces.get("event_type") or ""),
        consumes=tuple(str(item) for item in raw_consumes)
        if isinstance(raw_consumes, list)
        else (),
        outcome_schema=dict(raw_schema) if isinstance(raw_schema, Mapping) and raw_schema else None,
    )


# ---------------------------------------------------------------------------------------------
# How the member is told to close a step.
# ---------------------------------------------------------------------------------------------


def json_block(value: Any) -> str:
    """A value as a fenced JSON block, keys sorted so the same value is the same text."""
    return "```json\n" + json.dumps(value, indent=2, sort_keys=True, default=str) + "\n```"


def render_close_howto(activation_id: str, valid_verdicts: Sequence[str]) -> str:
    """The "Closing the step" section: how a member closes the step it holds.

    The same text whoever puts it in front of the harness. It states this activation's id as
    the step's id, apart from the call, and says the run id is not it: shown only inside an
    example, the id read as a placeholder, and the run id named in the step's header was
    passed in its place. The call uses the first valid verdict.
    """
    call = {
        "activation_id": activation_id,
        "ending": ENDING_VERDICT,
        "verdict": valid_verdicts[0] if valid_verdicts else "<verdict>",
        "outcome": {},
        "note": "<what you did, in a sentence or two>",
    }
    return (
        "## Closing the step\n\n"
        f"This step's activation id is `{activation_id}`. It is the `activation_id` the "
        f"`{WORKFLOW_STEP_CLOSE_TOOL}` tool takes; the run id is not it.\n\n"
        f"When you are done, call `{WORKFLOW_STEP_CLOSE_TOOL}` exactly once with that id, "
        "filling in your own verdict, outcome and note:\n\n"
        f"{json_block(call)}\n\n"
        "`evidence` (a list of `{kind, ref, summary}`) and `note` are optional on every ending. "
        "If the call is refused, the error says why and nothing was recorded; fix the call "
        "and make it again. Ending your turn without closing the step does not close it: "
        "the step stays open and is flagged. Do not close it by writing the answer in "
        "your reply."
    )


# ---------------------------------------------------------------------------------------------
# The ``workflow`` mesh event: what Ting records of a run, as it travels on the run's mesh.
# ---------------------------------------------------------------------------------------------

#: The kinds Ting knows. The set is open: an event of any other kind is kept and carried.
KIND_STEP_OFFERED = "step.offered"
KIND_STEP_PICKED_UP = "step.picked_up"
KIND_STEP_PASSED = "step.passed"
KIND_STEP_OUTCOME = "step.outcome"
KIND_GATE_OPENED = "gate.opened"
KIND_GATE_DECIDED = "gate.decided"
KIND_RUN_ENDED = "run.ended"

#: A workflow event's id derives from its kind and key, so a republish is the same event.
_EVENT_ID_NAMESPACE = uuid.UUID("5d2c6f0e-8a4b-4c58-9e1a-0f3b7c9d1e24")

#: What a gate decision can be.
GATE_DECISIONS = ("approved", "changes_requested")

#: How a run can end.
RUN_STATUSES = ("completed", "failed", "canceled")

EndingName = Literal["verdict", "waiting", "done", "failed"]


class WorkflowEventError(ValueError):
    """A ``workflow`` event payload is not what its kind says. The message names the reason."""


class _Open(BaseModel):
    """A shape that keeps fields it does not know, so a newer sender's data is carried on."""

    model_config = ConfigDict(extra="allow", frozen=True)


class PersonaPin(_Open):
    """The persona a member runs as, pinned to one revision of one document."""

    alias: str = Field(min_length=1)
    id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    digest: str = Field(min_length=1)

    def describe(self) -> str:
        return f"{self.alias} ({self.id} revision {self.revision}, digest {self.digest})"


class StepContract(_Open):
    """What one persona's member may close an offered step with.

    It is a function of the pinned persona: Ting derives it with ``from_persona`` to write the
    offer, and the member's Skuld derives it again from its own pin and refuses an offer whose
    contract ``mismatch`` reports.
    """

    persona: PersonaPin
    valid_endings: list[EndingName] = Field(default_factory=lambda: list(STEP_ENDINGS))
    #: Empty with ``verdict`` allowed means every verdict is accepted and hands off
    #: ``default_event`` (the persona maps no verdict to an event).
    valid_verdicts: list[str] = Field(default_factory=list)
    #: What each valid verdict hands off: verdict -> the event it produces.
    verdict_events: dict[str, str] = Field(default_factory=dict)
    #: The event every verdict produces when the persona names no per-verdict map.
    default_event: str = ""
    #: The persona's ``produces.schema`` verbatim, or ``None`` when it declares none.
    outcome_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _coherent(self) -> StepContract:
        if len(set(self.valid_endings)) != len(self.valid_endings):
            raise ValueError("valid_endings must not repeat an ending")
        if not self.valid_endings:
            raise ValueError("valid_endings must name at least one ending")
        if len(set(self.valid_verdicts)) != len(self.valid_verdicts):
            raise ValueError("valid_verdicts must not repeat a verdict")
        if any(not verdict.strip() for verdict in self.valid_verdicts):
            raise ValueError("valid_verdicts must not contain a blank verdict")
        return self

    @classmethod
    def from_persona(
        cls,
        persona: PersonaPin,
        contract: PersonaContract,
        valid_endings: Sequence[EndingName] = STEP_ENDINGS,
    ) -> StepContract:
        """The contract a pinned persona's document gives its member."""
        return cls(
            persona=persona,
            valid_endings=list(valid_endings),
            valid_verdicts=list(contract.valid_verdicts),
            verdict_events=dict(contract.produces_map),
            default_event=contract.produces_event,
            outcome_schema=dict(contract.outcome_schema) if contract.outcome_schema else None,
        )

    def mismatch(self, derived: StepContract) -> str | None:
        """Why this (offered) contract differs from the one derived from the member's own pin.

        ``None`` when they agree. The reason names both sides, so a refusal can say what the
        offer claimed and what the pinned persona says.
        """
        reasons = []
        if self.persona != derived.persona:
            reasons.append(
                f"persona: offer pins {self.persona.describe()}, "
                f"this member runs {derived.persona.describe()}"
            )
        if sorted(self.valid_endings) != sorted(derived.valid_endings):
            reasons.append(
                f"valid_endings: offer says {sorted(self.valid_endings)}, "
                f"persona derives {sorted(derived.valid_endings)}"
            )
        if sorted(self.valid_verdicts) != sorted(derived.valid_verdicts):
            reasons.append(
                f"valid_verdicts: offer says {sorted(self.valid_verdicts)}, "
                f"persona derives {sorted(derived.valid_verdicts)}"
            )
        if self.verdict_events != derived.verdict_events:
            reasons.append(
                f"verdict_events: offer says {self.verdict_events}, "
                f"persona derives {derived.verdict_events}"
            )
        if self.default_event != derived.default_event:
            reasons.append(
                f"default_event: offer says {self.default_event!r}, "
                f"persona derives {derived.default_event!r}"
            )
        if self.outcome_schema != derived.outcome_schema:
            reasons.append(
                f"outcome_schema: offer says {self.outcome_schema}, "
                f"persona derives {derived.outcome_schema}"
            )
        return "; ".join(reasons) or None


class OfferInput(_Open):
    """An outcome the offered step consumed, as the member sees it (oldest first in the offer)."""

    seq: int
    event_type: str = Field(min_length=1)
    edge_id: str = ""
    node_id: str = Field(min_length=1)
    member: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowEventBody(_Open):
    """What every workflow event of a known kind carries.

    ``to_payload`` is the ``payload`` of the ``workflow`` mesh event. The event's
    ``correlation_id`` is the run id, so every workflow event is on the run's thread.
    """

    KIND: ClassVar[str]
    #: The urgency hint of the mesh event that carries it.
    URGENCY: ClassVar[float] = 0.3

    run_id: str = Field(min_length=1)

    def dedup_key(self) -> str:
        """What makes two events the same event: kind plus this key."""
        raise NotImplementedError

    @property
    def event_id(self) -> str:
        """The mesh event id, derived so a republish of the same fact is the same event."""
        return workflow_event_id(self.KIND, self.dedup_key())

    @property
    def publish_topic(self) -> str | None:
        """The topic the event also goes on besides the run's thread, if it has one."""
        return None

    def to_payload(self) -> dict[str, Any]:
        """The mesh event payload: ``kind`` and every field, JSON-safe."""
        return {"kind": self.KIND, **self.model_dump(mode="json")}


def workflow_event_id(kind: str, key: str) -> str:
    """The deterministic mesh event id of a workflow event of ``kind`` with this dedup ``key``."""
    return uuid.uuid5(_EVENT_ID_NAMESPACE, f"{kind}:{key}").hex


class StepOffered(WorkflowEventBody):
    """``step.offered``: work for whoever does this kind of work, and the contract to do it by.

    One offer is what one event produced for one node. Every member whose pinned persona
    consumes ``topic`` receives it; each finds its own ``contracts`` entry by persona.
    """

    KIND: ClassVar[str] = KIND_STEP_OFFERED
    URGENCY: ClassVar[float] = 0.3

    offer_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    node_label: str = ""
    workflow_name: str = ""
    #: The topic members consume to take the offer (e.g. ``code.requested``).
    topic: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    generation: int = Field(default=0, ge=0)
    inputs: list[OfferInput] = Field(default_factory=list)
    deadline_at: datetime | None = None
    #: The node's task, as the member's harness is told it (without the closing how-to, which
    #: ``render_close_howto`` writes for the activation the member opens).
    instructions: str = Field(min_length=1)
    contracts: list[StepContract] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_contract_per_persona(self) -> StepOffered:
        aliases = [contract.persona.alias for contract in self.contracts]
        if len(set(aliases)) != len(aliases):
            raise ValueError("contracts must not repeat a persona alias")
        return self

    def dedup_key(self) -> str:
        return self.offer_id

    @property
    def publish_topic(self) -> str | None:
        return self.topic

    def contract_for(self, alias: str) -> StepContract | None:
        """The contract of the member that runs persona ``alias``, if the offer names it."""
        for contract in self.contracts:
            if contract.persona.alias == alias:
                return contract
        return None


class StepPickedUp(WorkflowEventBody):
    """``step.picked_up``: a member is doing the offered work."""

    KIND: ClassVar[str] = KIND_STEP_PICKED_UP
    URGENCY: ClassVar[float] = 0.2

    offer_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    member: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    activation_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)

    def dedup_key(self) -> str:
        return f"{self.offer_id}:{self.session_id}"


class StepPassed(WorkflowEventBody):
    """``step.passed``: a member received the offer and it is not its to take."""

    KIND: ClassVar[str] = KIND_STEP_PASSED
    URGENCY: ClassVar[float] = 0.1

    offer_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    member: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    #: Which of the member's filters did not match, for the room and the log.
    reason: str = ""

    def dedup_key(self) -> str:
        return f"{self.offer_id}:{self.session_id}"


class StepOutcome(WorkflowEventBody):
    """``step.outcome``: the member closed the step, and this is how it ended."""

    KIND: ClassVar[str] = KIND_STEP_OUTCOME
    URGENCY: ClassVar[float] = 0.4

    offer_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    member: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    activation_id: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    ending: EndingName
    verdict: str | None = None
    outcome: dict[str, Any] | None = None
    note: str | None = None
    evidence: list[StepEvidence] = Field(default_factory=list)
    waiting_on: StepWaitingOn | None = None
    failure_reason: str | None = None
    #: The event a verdict hands off, which the outcome also goes out on as information.
    produced_event: str = ""

    @model_validator(mode="after")
    def _ending_is_complete(self) -> StepOutcome:
        if self.ending == ENDING_VERDICT and not (self.verdict or "").strip():
            raise ValueError("a verdict ending needs a verdict")
        if self.ending == ENDING_WAITING and self.waiting_on is None:
            raise ValueError("a waiting ending needs waiting_on")
        if self.ending == ENDING_FAILED and not (self.failure_reason or "").strip():
            raise ValueError("a failed ending needs failure_reason")
        return self

    def dedup_key(self) -> str:
        # An activation may close more than once (waiting, then its real ending), so the
        # ending is part of the key.
        return f"{self.activation_id}:{self.ending}:{self.verdict or ''}"

    @property
    def publish_topic(self) -> str | None:
        return self.produced_event or None


class GateOpened(WorkflowEventBody):
    """``gate.opened``: a person must decide before the run goes on."""

    KIND: ClassVar[str] = KIND_GATE_OPENED
    URGENCY: ClassVar[float] = 0.8

    gate_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    node_label: str = ""
    #: ``human_approval`` or ``automated_approval``.
    mode: str = ""
    summary: str = ""

    def dedup_key(self) -> str:
        return self.gate_id


class GateDecided(WorkflowEventBody):
    """``gate.decided``: approved, or changes requested, by whom."""

    KIND: ClassVar[str] = KIND_GATE_DECIDED
    URGENCY: ClassVar[float] = 0.5

    gate_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    decision: Literal["approved", "changes_requested"]
    decided_by: str = Field(min_length=1)
    note: str = ""

    def dedup_key(self) -> str:
        return self.gate_id


class RunEnded(WorkflowEventBody):
    """``run.ended``: the run is over, and why."""

    KIND: ClassVar[str] = KIND_RUN_ENDED
    URGENCY: ClassVar[float] = 0.5

    status: Literal["completed", "failed", "canceled"]
    reason: str = ""

    def dedup_key(self) -> str:
        return self.run_id


#: The known kinds and their shapes. Adding a kind is adding a class here; nothing else knows
#: the set, and an event of a kind not in it is an ``UnknownWorkflowEvent``.
WORKFLOW_EVENT_KINDS: Mapping[str, type[WorkflowEventBody]] = {
    shape.KIND: shape
    for shape in (
        StepOffered,
        StepPickedUp,
        StepPassed,
        StepOutcome,
        GateOpened,
        GateDecided,
        RunEnded,
    )
}


@dataclass(frozen=True)
class UnknownWorkflowEvent:
    """A ``workflow`` event of a kind this build does not know.

    It is kept whole and carried on: the room shows it, a log keeps it, nothing is recorded for
    it and it is never rejected, so a newer sender can add kinds without breaking an older
    receiver.
    """

    kind: str
    run_id: str
    payload: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return dict(self.payload)


def parse_workflow_event(payload: Mapping[str, Any]) -> WorkflowEventBody | UnknownWorkflowEvent:
    """Read the payload of a ``workflow`` mesh event.

    A kind this module knows is checked against its shape and a payload that does not fit
    raises ``WorkflowEventError`` naming what is wrong. A kind it does not know is returned as
    an ``UnknownWorkflowEvent`` with the payload untouched.
    """
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise WorkflowEventError("a workflow event needs a `kind`")
    shape = WORKFLOW_EVENT_KINDS.get(kind)
    if shape is None:
        run_id = payload.get("run_id")
        return UnknownWorkflowEvent(
            kind=kind,
            run_id=run_id if isinstance(run_id, str) else "",
            payload=dict(payload),
        )
    body = {key: value for key, value in payload.items() if key != "kind"}
    try:
        return shape.model_validate(body)
    except ValidationError as exc:
        raise WorkflowEventError(f"workflow event `{kind}` is malformed: {exc}") from exc
