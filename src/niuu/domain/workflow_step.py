"""The shapes a workflow step host and its harness exchange.

A workflow run activates a step on a harness session (Forge relays the activation to the
session's Skuld), and the harness closes it through the ``workflow_step_close`` tool of the
session's built-in ``forge`` MCP server. Skuld holds the step durably and reports its close
to Ting through session activity. These are the tool's arguments and the report's ending, so
the tool definition (niuu), the broker (Skuld) and the contract tests agree on one shape.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
