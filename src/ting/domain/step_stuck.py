"""Stuck detection: which open steps of a run are stuck, and why.

``detect_stuck`` is a pure function of a run's ledger, whether its steps' sessions are up and
the clock. It decides nothing and changes nothing: the sweep
(``ting.domain.services.workflow_step_watch``) raises the flags, takes the safe automatic
actions and tells a person.

A step is stuck in one of four ways (``FlagKind``):

* **overdue**: an ``active`` or ``waiting`` step, or an offer nobody took up, is past its
  ``deadline_at``;
* **unclaimed**: an offer was published and no member took it up (or passed it) within the
  pickup grace, so the same offer is published again; the grace is when to look again, not
  a cap;
* **unclosed**: a turn ended with the step still open (the engine logged it when the
  session said so);
* **session gone**: the session failed, stopped or no longer exists.

A session that has not come up yet (created, starting) is not gone: nothing can be asked of
it, so nothing is claimed about the step. An offer that is not published yet is waiting for
its members to listen, which is neither unclaimed nor stuck until its deadline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from ting.domain.step_engine import offer_publications
from ting.domain.workflow_steps import (
    EVENT_STEP_UNCLOSED,
    FlagKind,
    RunState,
    StepKind,
    StepState,
    WorkflowStep,
)
from ting.ports.workflow_steps import SessionLiveness

_SESSION_ENDED = frozenset({SessionLiveness.STOPPED, SessionLiveness.FAILED, SessionLiveness.GONE})
_WATCHED_STATES = frozenset({StepState.ACTIVE, StepState.WAITING})


@dataclass(frozen=True)
class StuckSettings:
    """Stuck detection knobs, from Ting's configuration."""

    #: How long a published offer may go without a pickup or a pass before it is published
    #: again.
    pickup_grace_seconds: float

    def __post_init__(self) -> None:
        if not self.pickup_grace_seconds > 0:
            raise ValueError("pickup_grace_seconds must be positive")


@dataclass(frozen=True)
class Condition:
    """One way one step is stuck right now."""

    step: WorkflowStep
    kind: FlagKind
    detail: str
    since: datetime


def detect_stuck(
    state: RunState,
    liveness: Mapping[UUID, SessionLiveness],
    *,
    now: datetime,
    settings: StuckSettings,
) -> list[Condition]:
    """Every way the run's open steps are stuck.

    ``liveness`` holds, per activation, whether its session is up; a step with no entry
    could not be asked, so only the conditions that need no question are judged for it.
    """
    grace = timedelta(seconds=settings.pickup_grace_seconds)
    unclosed = _unclosed_since(state)
    published = offer_publications(state)
    conditions: list[Condition] = []
    for step in state.steps:
        if step.state not in _WATCHED_STATES:
            continue
        conditions.extend(_overdue(step, now))
        if step.kind is StepKind.OFFER:
            conditions.extend(_unclaimed(step, published.get(step.offer_id or "", ()), now, grace))
            continue
        if step.kind is not StepKind.STAGE_MEMBER:
            continue
        conditions.extend(_session_gone(step, liveness.get(step.id), now))
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


def _unclaimed(
    offer: WorkflowStep,
    publications: list[datetime] | tuple[()],
    now: datetime,
    grace: timedelta,
) -> list[Condition]:
    if not publications:
        return []
    last = max(publications)
    if now - last <= grace:
        return []
    return [
        Condition(
            offer,
            FlagKind.UNCLAIMED,
            f"offered {len(publications)} time(s), last at {last.isoformat()}, and no member "
            f"took it up or passed it within the pickup grace of {int(grace.total_seconds())}s",
            publications[0],
        )
    ]


def _session_gone(
    step: WorkflowStep, liveness: SessionLiveness | None, now: datetime
) -> list[Condition]:
    if liveness is None or liveness not in _SESSION_ENDED:
        return []
    return [
        Condition(
            step,
            FlagKind.SESSION_GONE,
            f"its session {step.session_id} is {liveness.value}",
            now,
        )
    ]


def _unclosed_since(state: RunState) -> dict[UUID, datetime]:
    since: dict[UUID, datetime] = {}
    for event in state.events:
        if event.event_type != EVENT_STEP_UNCLOSED or event.source_activation_id is None:
            continue
        since.setdefault(event.source_activation_id, event.created_at)
    return since


__all__ = ["Condition", "StuckSettings", "detect_stuck"]
