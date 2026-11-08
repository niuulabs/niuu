"""Stuck detection: which open steps of a run are stuck, and why.

``detect_stuck`` is a pure function of a run's ledger, what its steps' sessions
reported and the clock. It decides nothing and changes nothing: the sweep
(``ting.domain.services.workflow_step_watch``) raises the flags, takes the safe
automatic actions and tells a person.

A step is stuck in one of five ways (``FlagKind``):

* **overdue**: an ``active`` or ``waiting`` step is past its ``deadline_at``;
* **not started**: an ``active`` stage-member step has no session after the start
  grace, so the engine committed it and died before (or while) starting the session;
* **undelivered**: its session is running but does not know the activation, so the
  delivery never landed;
* **unclosed**: a turn ended with the step still open (the engine logged it when the
  session said so);
* **session gone**: the session failed, stopped or no longer exists.

A session that has not come up yet (created, starting) is neither undelivered nor
gone: nothing can be asked of it, so nothing is claimed about the step.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from ting.domain.workflow_steps import (
    EVENT_STEP_UNCLOSED,
    FlagKind,
    RunState,
    StepKind,
    StepState,
    WorkflowStep,
)
from ting.ports.workflow_steps import SessionLiveness, StepObservation

_SESSION_ENDED = frozenset({SessionLiveness.STOPPED, SessionLiveness.FAILED, SessionLiveness.GONE})
_WATCHED_STATES = frozenset({StepState.ACTIVE, StepState.WAITING})


@dataclass(frozen=True)
class StuckSettings:
    """Stuck detection knobs, from Ting's configuration."""

    start_grace_seconds: float

    def __post_init__(self) -> None:
        if not self.start_grace_seconds > 0:
            raise ValueError("start_grace_seconds must be positive")


@dataclass(frozen=True)
class Condition:
    """One way one step is stuck right now."""

    step: WorkflowStep
    kind: FlagKind
    detail: str
    since: datetime


def detect_stuck(
    state: RunState,
    observations: Mapping[UUID, StepObservation],
    *,
    now: datetime,
    settings: StuckSettings,
) -> list[Condition]:
    """Every way the run's open steps are stuck.

    ``observations`` holds, per activation, what its session reported; a step with no
    entry could not be asked, so only the conditions that need no question are
    judged for it.
    """
    grace = timedelta(seconds=settings.start_grace_seconds)
    unclosed = _unclosed_since(state)
    conditions: list[Condition] = []
    for step in state.steps:
        if step.state not in _WATCHED_STATES:
            continue
        conditions.extend(_overdue(step, now))
        if step.kind is not StepKind.STAGE_MEMBER:
            continue
        conditions.extend(_session_conditions(step, observations.get(step.id), now, grace))
        if step.state is StepState.ACTIVE and step.id in unclosed:
            conditions.append(
                Condition(
                    step,
                    FlagKind.UNCLOSED,
                    f"its session {step.session_id} ended a turn without closing the step",
                    unclosed[step.id],
                )
            )
    return conditions


def _overdue(step: WorkflowStep, now: datetime) -> list[Condition]:
    deadline = step.deadline_at
    if deadline is None or now <= deadline:
        return []
    return [
        Condition(
            step,
            FlagKind.OVERDUE,
            f"still {step.state.value} past its deadline of {deadline.isoformat()}",
            deadline,
        )
    ]


def _session_conditions(
    step: WorkflowStep,
    observed: StepObservation | None,
    now: datetime,
    grace: timedelta,
) -> list[Condition]:
    started = step.activated_at or step.created_at
    waited = now - started
    if not step.session_id:
        if step.state is StepState.ACTIVE and waited > grace:
            return [
                Condition(
                    step,
                    FlagKind.NOT_STARTED,
                    f"active for {_seconds(waited)}s with no session; the start grace is "
                    f"{_seconds(grace)}s",
                    started,
                )
            ]
        return []
    if observed is None:
        return []
    if observed.liveness in _SESSION_ENDED:
        return [
            Condition(
                step,
                FlagKind.SESSION_GONE,
                f"its session {step.session_id} is {observed.liveness.value}",
                now,
            )
        ]
    undelivered = (
        observed.liveness is SessionLiveness.RUNNING
        and step.state is StepState.ACTIVE
        and observed.reported is None
        and waited > grace
    )
    if not undelivered:
        return []
    return [
        Condition(
            step,
            FlagKind.UNDELIVERED,
            f"its session {step.session_id} is running but does not know the activation "
            f"{_seconds(waited)}s after it started",
            started,
        )
    ]


def _unclosed_since(state: RunState) -> dict[UUID, datetime]:
    since: dict[UUID, datetime] = {}
    for event in state.events:
        if event.event_type != EVENT_STEP_UNCLOSED or event.source_activation_id is None:
            continue
        since.setdefault(event.source_activation_id, event.created_at)
    return since


def _seconds(span: timedelta) -> int:
    return int(span.total_seconds())


__all__ = ["Condition", "StuckSettings", "detect_stuck"]
