"""Hosting a workflow run's steps on this harness session.

A workflow run (Ting) activates a step on the session of the stage member that should do
it; Forge relays the activation here. This module holds that step durably, hands its
instructions to the harness at a turn boundary, validates how the harness closes it through
the ``workflow_step_close`` tool, and reports the close to Ting through session activity.

The session runs one step at a time. Skuld decides nothing about the step: it does not read
the harness's text, does not re-prompt a turn that ended without a close, and does not choose
a verdict. A turn that ends with the step still open is reported as ``unclosed`` and left to
Ting's stuck detection.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from niuu.domain.outcome import outcome_errors, outcome_schema_from_mapping
from niuu.domain.workflow_step import (
    ENDING_FAILED,
    ENDING_VERDICT,
    ENDING_WAITING,
    STEP_EVENT_CLOSED,
    STEP_EVENT_UNCLOSED,
    STEP_STATE_ACTIVE,
    STEP_STATE_CLOSED,
    STEP_STATE_WAITING,
    WORKFLOW_STEP_FRAME,
    WorkflowStepCloseArgs,
    WorkflowStepCloseError,
)

logger = logging.getLogger("skuld.broker")

#: Namespace of the deterministic id of the user turn that carries a step's instructions.
_STEP_TURN_NAMESPACE = "workflow-step"

#: Where the instructions turn came from, on the turn and its trace span.
STEP_TURN_SOURCE = "workflow_step"

_RUNNING_STATES = ("active", "tool_executing")
_BLOCKED_STATES = ("awaiting_input",)
_TURN_END_STATES = ("idle", "stopped", "error")


_NO_OPEN_STEP = (
    "no workflow step is open on this session. A step is open from its activation until you "
    "close it; you can only close the step you were given."
)


class WorkflowStepNotHostedError(Exception):
    """This session is not a workflow step host (``workflow_steps.enabled`` is off)."""


class WorkflowStepConflictError(Exception):
    """The session already has a different open step, or the activation's identity differs."""


class WorkflowStepUnavailableError(Exception):
    """The session cannot take a step now (its broker failed to start)."""


class WorkflowStepRequest(BaseModel):
    """``POST /api/workflow/steps``: the activation Ting rendered, as the contract has it."""

    model_config = ConfigDict(extra="forbid")

    activation_id: str
    run_id: str
    attempt: int = Field(ge=1)
    generation: int = Field(ge=0)
    node_id: str = Field(min_length=1)
    node_label: str
    member: str = Field(min_length=1)
    valid_endings: list[Literal["verdict", "waiting", "done", "failed"]] = Field(min_length=1)
    valid_verdicts: list[str]
    outcome_schema: dict[str, Any] | None = None
    deadline_at: datetime | None = None
    instructions: str = Field(min_length=1)

    @field_validator("activation_id", "run_id")
    @classmethod
    def _uuid_text(cls, value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("must be a UUID") from exc

    @model_validator(mode="after")
    def _coherent(self) -> WorkflowStepRequest:
        if len(set(self.valid_endings)) != len(self.valid_endings):
            raise ValueError("valid_endings must not repeat an ending")
        if len(set(self.valid_verdicts)) != len(self.valid_verdicts):
            raise ValueError("valid_verdicts must not repeat a verdict")
        if any(not verdict.strip() for verdict in self.valid_verdicts):
            raise ValueError("valid_verdicts must not contain a blank verdict")
        # An empty valid_verdicts with 'verdict' allowed means the persona maps every verdict
        # to one event (produces.event_type, no event_type_map): any non-blank verdict closes.
        if self.outcome_schema is not None:
            outcome_schema_from_mapping(self.outcome_schema)
        return self


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
    #: A turn has run since the instructions were handed over (memory only, never persisted).
    turn_seen: bool = field(default=False, compare=False)

    @classmethod
    def from_request(cls, request: WorkflowStepRequest) -> WorkflowStep:
        return cls(
            activation_id=request.activation_id,
            run_id=request.run_id,
            attempt=request.attempt,
            generation=request.generation,
            node_id=request.node_id,
            node_label=request.node_label,
            member=request.member,
            valid_endings=list(request.valid_endings),
            valid_verdicts=list(request.valid_verdicts),
            outcome_schema=request.outcome_schema,
            deadline_at=request.deadline_at.isoformat() if request.deadline_at else None,
            instructions=request.instructions,
            opened_at=datetime.now(UTC).isoformat(),
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

    def status(self) -> dict[str, Any]:
        """What ``POST`` and ``GET /api/workflow/steps`` answer."""
        return {
            "activation_id": self.activation_id,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "generation": self.generation,
            "node_id": self.node_id,
            "member": self.member,
            "state": self.state,
            "ending": self.ending,
            "deadline_at": self.deadline_at,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
        }

    def same_activation(self, request: WorkflowStepRequest) -> bool:
        return self.activation_id == request.activation_id and self.attempt == request.attempt


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
    """Broker behavior for hosting workflow steps."""

    _workflow_steps: dict[str, WorkflowStep]
    _pending_step_reports: list[dict[str, Any]]
    _workflow_step_lock: asyncio.Lock
    _workflow_step_tasks: set[asyncio.Task[None]]

    def _init_workflow_steps(self) -> None:
        self._workflow_steps = {}
        self._pending_step_reports = []
        self._workflow_step_lock = asyncio.Lock()
        self._workflow_step_tasks = set()
        if not self._settings.workflow_steps.enabled:
            return
        settings = self._settings
        if not (
            settings.event_log_enabled
            and settings.volundr_api_url
            and settings.history_hydration_enabled
        ):
            raise ValueError(
                "workflow_steps.enabled needs the durable event log and history hydration: a "
                "step is restored from the log after a restart. Set volundr_api_url, "
                "event_log_enabled: true and history_hydration_enabled: true, or turn "
                "workflow_steps.enabled off."
            )

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

    def _require_step_host(self) -> None:
        if not self._settings.workflow_steps.enabled:
            raise WorkflowStepNotHostedError(
                "this session is not a workflow step host (workflow_steps.enabled is off); "
                "create it with workflow_step_host: true"
            )

    # -- the step API

    async def accept_workflow_step(self, request: WorkflowStepRequest) -> tuple[bool, WorkflowStep]:
        """Hold the activation durably and hand its instructions to the harness.

        Returns ``(created, step)``: ``created`` is False when this exact activation is
        already here, which changes nothing. Raises :class:`WorkflowStepConflictError` when a
        different step is open or the activation id is known with a different attempt.
        """
        self._require_step_host()
        if self._startup_state == "failed":
            raise WorkflowStepUnavailableError(
                f"this session's broker failed to start: {self._startup_failure}"
            )
        async with self._workflow_step_lock:
            known = self._workflow_steps.get(request.activation_id)
            if known is not None:
                if known.same_activation(request):
                    return False, known
                raise WorkflowStepConflictError(
                    f"activation {request.activation_id} is already known with attempt "
                    f"{known.attempt}; a new attempt needs a new activation id"
                )
            open_step = self._open_workflow_step()
            if open_step is not None:
                raise WorkflowStepConflictError(
                    f"this session already has an open step ({open_step.activation_id}, state "
                    f"{open_step.state}); a session runs one step at a time"
                )
            step = WorkflowStep.from_request(request)
            await self._record_workflow_step(step, confirm=True)
            self._workflow_steps[step.activation_id] = step
        self._spawn_workflow_step_task(
            self._maybe_inject_workflow_step(), f"workflow-step-inject-{step.activation_id}"
        )
        return True, step

    def get_workflow_step(self, activation_id: str) -> WorkflowStep:
        self._require_step_host()
        step = self._workflow_steps.get(activation_id)
        if step is None:
            raise LookupError(f"this session holds no workflow step {activation_id}")
        return step

    async def _record_workflow_step(self, step: WorkflowStep, *, confirm: bool) -> None:
        """Write the step's state to the session event log; wait until Forge stored it.

        A step that is not durable would be lost by a restart, so a frame Forge did not store is
        an error, never a step that exists only in memory.
        """
        entry = self._enqueue_event_log(step.frame())
        if entry is None:
            raise RuntimeError(
                "the durable event log refused the workflow step frame; the step would not "
                "survive a restart"
            )
        receipt = self._watch_event_log_entry(entry)
        await self._channels.broadcast(step.frame())
        if not confirm:
            return
        stored = await self._await_event_log_receipt(
            receipt, self._settings.forge_mcp.notify_confirm_timeout_s
        )
        if stored is False:
            raise RuntimeError(
                f"the workflow step frame for {step.activation_id} was not stored (it was "
                "dropped while Forge was unreachable, or its sequence conflicted)"
            )
        if stored is None:
            logger.warning(
                "workflow step %s: Forge has not yet acknowledged the log flush within "
                "forge_mcp.notify_confirm_timeout_s; it will be stored when Forge is reachable",
                step.activation_id,
            )

    # -- injection at a turn boundary

    def _workflow_step_boundary_open(self) -> bool:
        """Whether the harness is between turns, so the instructions interrupt nothing.

        A turn in flight, a question or approval waiting on a person, or a transport that says
        its turn is active all mean no. The check is repeated at every turn end.
        """
        if self._activity_state in (*_RUNNING_STATES, *_BLOCKED_STATES):
            return False
        return not (self._transport_tracks_turns() and self._transport_turn_active())

    async def _maybe_inject_workflow_step(self) -> None:
        """Hand the open step's instructions to the harness if it is between turns."""
        async with self._workflow_step_lock:
            step = self._open_workflow_step()
            if step is None or step.injected or step.state != STEP_STATE_ACTIVE:
                return
            if self._transport is None or self._activity_state in ("stopped", "error"):
                return
            if not self._workflow_step_boundary_open():
                return
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
                return
        self._spawn_workflow_step_task(
            self._finish_step_delivery(step, task), f"workflow-step-delivered-{step.activation_id}"
        )

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
        """Persist the step's close, then report it. The caller holds the step lock."""
        step.state = STEP_STATE_WAITING if ending["kind"] == ENDING_WAITING else STEP_STATE_CLOSED
        step.ending = ending
        step.closed_at = datetime.now(UTC).isoformat() if step.state == STEP_STATE_CLOSED else None
        step.turn_seen = False
        await self._record_workflow_step(step, confirm=True)
        await self._report_workflow_step(
            {
                "event": STEP_EVENT_CLOSED,
                "run_id": step.run_id,
                "activation_id": step.activation_id,
                "attempt": step.attempt,
                "ending": ending,
            }
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
        """Track the harness's turns against the open step; inject at a boundary."""
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
                    "activation_id": step.activation_id,
                    "attempt": step.attempt,
                }
            )
            return
        if state == "idle":
            self._spawn_workflow_step_task(
                self._maybe_inject_workflow_step(), f"workflow-step-inject-{step.activation_id}"
            )

    # -- restore

    def _restore_durable_workflow_steps(self, frames: list) -> None:
        """Rebuild the steps this session held, each at its latest logged state."""
        for frame in frames:
            if getattr(frame, "kind", "") != WORKFLOW_STEP_FRAME:
                continue
            step = WorkflowStep.from_frame(frame.payload)
            self._workflow_steps[step.activation_id] = step
