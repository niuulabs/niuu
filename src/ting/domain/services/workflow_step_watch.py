"""The stuck-step sweep: finds workflow steps that stopped making progress (NIU-1181).

A periodic task, one pass over every active workflow run. For each run it

1. asks the session of every open stage-member step what it holds (its liveness and its
   durable record of the step), and records any close the activity stream missed;
2. judges which steps are stuck (``detect_stuck``) and raises a durable flag, once per
   step and kind, counted and logged;
3. tells a person about each flag through the notification feed, once;
4. takes the two automatic actions that finish an interrupted operation and cannot
   change the outcome: it starts or re-delivers a step that never reached its session,
   and fails a step whose session is gone (the engine's sibling and run-failure rules
   then apply). An overdue or unclosed step is only flagged, never acted on;
5. resolves flags whose step closed or whose condition cleared, and projects the run's
   open flags onto its campaign for the web to show.

Every failure is loud: a session that cannot be asked, an action that cannot be taken,
a run that cannot be swept is logged at error and counted, and the step stays flagged.
The sweep never guesses that a step recovered because it could not look.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

from niuu.observability import get_observability
from ting.domain.models import WorkflowCampaign
from ting.domain.services.workflow_runs import WorkflowRuns
from ting.domain.services.workflow_step_engine import WorkflowStepEngine
from ting.domain.step_stuck import Condition, StuckSettings, detect_stuck
from ting.domain.workflow_steps import (
    NOTIFY_STEP_FLAGGED,
    FlagKind,
    RunState,
    StepFlag,
    StepKind,
    StepState,
)
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_steps import StepObservation, StepSessionPort, WorkflowStepRepository

logger = logging.getLogger(__name__)

#: What happens next to a flagged step, for the person who reads the notification.
_NEXT_BY_KIND = {
    FlagKind.OVERDUE: ("Ting only flags an overdue step. Retry or reassign it, or abort the run."),
    FlagKind.UNCLOSED: (
        "Ting only flags a step whose turn ended without a close. Retry or reassign it, or "
        "abort the run."
    ),
    FlagKind.NOT_STARTED: "Ting is starting the step on its session again.",
    FlagKind.UNDELIVERED: "Ting is delivering the step to its session again.",
    FlagKind.SESSION_GONE: (
        "Ting is failing the step; the run's other branches carry on. Retry or reassign "
        "it if the run is still open."
    ),
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StuckStepSweeper:
    """Sweeps active runs for stuck steps on an interval."""

    def __init__(
        self,
        *,
        engine: WorkflowStepEngine,
        runs: WorkflowRuns,
        steps: WorkflowStepRepository,
        campaigns: WorkflowCampaignRepository,
        sessions: StepSessionPort,
        event_bus: EventBusPort,
        settings: StuckSettings,
        interval_seconds: float,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not interval_seconds > 0:
            raise ValueError("the stuck-step sweep needs a positive interval_seconds")
        self._engine = engine
        self._runs = runs
        self._steps = steps
        self._campaigns = campaigns
        self._sessions = sessions
        self._event_bus = event_bus
        self._settings = settings
        self._interval = interval_seconds
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._sweep_forever(), name="stuck-step-sweep")
        logger.info("Stuck-step sweep started (every %gs)", self._interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("Stuck-step sweep stopped")

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.sweep_once()
            except Exception:
                logger.error("The stuck-step sweep failed", exc_info=True)
                self._count_error("sweep")

    async def sweep_once(self) -> dict[FlagKind, int]:
        """One pass over every active run; returns how many steps are flagged, by kind."""
        flagged = dict.fromkeys(FlagKind, 0)
        for campaign in await self._campaigns.list_active_campaigns():
            try:
                live = await self._sweep_run(campaign)
            except Exception:
                logger.error(
                    "Sweeping workflow run %s for stuck steps failed", campaign.slug, exc_info=True
                )
                self._count_error("run")
                continue
            for flag in live:
                flagged[flag.kind] += 1
        for kind, count in flagged.items():
            get_observability().gauge(
                "ting_workflow_steps_flagged",
                count,
                attributes={"kind": kind.value},
                description="Open workflow steps currently flagged as stuck, by kind.",
            )
        return flagged

    # -- one run ----------------------------------------------------------------

    async def _sweep_run(self, campaign: WorkflowCampaign) -> tuple[StepFlag, ...]:
        state = await self._steps.load(campaign.id)
        if not state.launched or state.finished:
            return ()
        observations, unobserved = await self._observe(campaign, state)
        if await self._record_missed_closes(campaign, state, observations):
            state = await self._steps.load(campaign.id)
            if state.finished:
                return ()
        conditions = detect_stuck(state, observations, now=self._clock(), settings=self._settings)
        await self._engine.raise_flags(campaign.id, conditions)
        await self._notify(campaign, await self._steps.load(campaign.id))
        for condition in conditions:
            await self._act(campaign, condition)
        await self._engine.resolve_flags(
            campaign.id,
            {(condition.step.id, condition.kind) for condition in conditions},
            unobserved=unobserved,
        )
        return (await self._runs.refresh(campaign.id)).live_flags()

    async def _observe(
        self, campaign: WorkflowCampaign, state: RunState
    ) -> tuple[dict[UUID, StepObservation], set[UUID]]:
        """What each open step's session holds; the steps that could not be asked."""
        observations: dict[UUID, StepObservation] = {}
        unobserved: set[UUID] = set()
        for step in state.steps:
            if (
                step.kind is not StepKind.STAGE_MEMBER
                or step.state not in (StepState.ACTIVE, StepState.WAITING)
                or not step.session_id
            ):
                continue
            try:
                observations[step.id] = await self._sessions.observe(
                    campaign.id, step.session_id, step.id
                )
            except Exception:
                logger.error(
                    "Could not ask session %s about step %s (%s/%s) of run %s",
                    step.session_id,
                    step.id,
                    step.node_id,
                    step.member,
                    campaign.slug,
                    exc_info=True,
                )
                self._count_error("observe")
                unobserved.add(step.id)
        return observations, unobserved

    async def _record_missed_closes(
        self,
        campaign: WorkflowCampaign,
        state: RunState,
        observations: dict[UUID, StepObservation],
    ) -> bool:
        """Apply the closes sessions hold that the activity stream never delivered."""
        applied = False
        for step in state.steps:
            observed = observations.get(step.id)
            if observed is None or observed.reported is None:
                continue
            if await self._runs.apply_reported_close(campaign.id, step, observed.reported):
                logger.warning(
                    "Recorded the close of step %s (%s/%s) of run %s that its session held "
                    "but the activity stream never delivered",
                    step.id,
                    step.node_id,
                    step.member,
                    campaign.slug,
                )
                get_observability().count(
                    "ting_workflow_step_closes_reconciled_total",
                    description=(
                        "Step closes recorded by the sweep because the session held them but "
                        "the activity stream never delivered them."
                    ),
                )
                applied = True
        return applied

    # -- telling a person -------------------------------------------------------

    async def _notify(self, campaign: WorkflowCampaign, state: RunState) -> None:
        """Tell the owner about every live flag they have not been told about yet.

        A flag is marked told only after the event was emitted, so one that could not be
        emitted is tried again on the next pass.
        """
        steps = {step.id: step for step in state.steps}
        told: list[StepFlag] = []
        for flag in state.live_flags():
            if flag.notified_at is not None:
                continue
            step = steps[flag.activation_id]
            await self._event_bus.emit(
                TingEvent(
                    event=NOTIFY_STEP_FLAGGED,
                    owner_id=campaign.owner_id,
                    data={
                        "owner_id": campaign.owner_id,
                        "run_id": str(campaign.id),
                        "run_slug": campaign.slug,
                        "run_name": campaign.name,
                        "activation_id": str(step.id),
                        "node_id": step.node_id,
                        "member": step.member or "",
                        "attempt": step.attempt,
                        "kind": flag.kind.value,
                        "since": flag.since.isoformat(),
                        "detail": flag.detail,
                        "next": _NEXT_BY_KIND[flag.kind],
                        "ui_path": f"/ting/review/run/{campaign.slug}",
                    },
                )
            )
            told.append(flag)
        await self._engine.mark_notified(campaign.id, told)

    # -- the safe automatic actions ---------------------------------------------

    async def _act(self, campaign: WorkflowCampaign, condition: Condition) -> None:
        step = condition.step
        if condition.kind in (FlagKind.NOT_STARTED, FlagKind.UNDELIVERED):
            await self._recover(
                campaign,
                condition,
                "resume_start",
                self._engine.resume_start(
                    campaign.id, step.id, f"stuck sweep: {condition.kind.value}: {condition.detail}"
                ),
            )
            return
        if condition.kind is FlagKind.SESSION_GONE:
            await self._recover(
                campaign,
                condition,
                "fail_step",
                self._engine.fail_step(
                    campaign.id,
                    step.id,
                    f"session gone: {condition.detail}; Ting failed the step because nothing "
                    "can close it",
                ),
            )

    async def _recover(
        self,
        campaign: WorkflowCampaign,
        condition: Condition,
        action: str,
        attempt: Awaitable[bool],
    ) -> None:
        step = condition.step
        try:
            done = await attempt
        except Exception:
            logger.error(
                "Could not %s step %s (%s/%s) of run %s; it stays flagged %s",
                action,
                step.id,
                step.node_id,
                step.member,
                campaign.slug,
                condition.kind.value,
                exc_info=True,
            )
            get_observability().count(
                "ting_workflow_step_auto_recovery_failures_total",
                attributes={"action": action},
                description="Automatic recoveries of stuck workflow steps that failed.",
            )
            return
        if not done:
            return
        logger.warning(
            "Stuck step %s (%s/%s) of run %s (%s): %s done",
            step.id,
            step.node_id,
            step.member,
            campaign.slug,
            condition.kind.value,
            action,
        )
        get_observability().count(
            "ting_workflow_step_auto_recoveries_total",
            attributes={"action": action, "kind": condition.kind.value},
            description="Automatic recoveries of stuck workflow steps, by action and flag kind.",
        )

    @staticmethod
    def _count_error(stage: str) -> None:
        get_observability().count(
            "ting_workflow_step_sweep_errors_total",
            attributes={"stage": stage},
            description="Stuck-step sweep work that failed, by stage; each is also logged.",
        )


__all__ = ["StuckStepSweeper"]
