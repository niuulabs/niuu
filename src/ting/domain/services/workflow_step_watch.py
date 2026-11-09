"""The stuck-step sweep: finds workflow steps that stopped making progress (NIU-1181).

A periodic task, one pass over every active workflow run. For each run it

1. publishes the offers that were waiting for their members to listen, and reads what the
   run's sessions hold in their durable logs, recording any report the activity stream
   missed (a pickup, a pass, a close);
2. asks whether the session of every open stage-member step is still up, judges which steps
   are stuck (``detect_stuck``) and raises a durable flag, once per step and kind, counted
   and logged;
3. tells a person about each flag through the notification feed, once;
4. takes the automatic actions that cannot change the outcome: it publishes an unclaimed
   offer again (the same offer, counted: the pickup grace is when to look again, never a
   cap), and fails a step whose session is gone, an offer whose members' sessions ended
   before they listened, and an offer that reached its deadline untaken, naming the knob. An
   overdue or unclosed step is only flagged;
5. resolves flags whose step closed or whose condition cleared, and projects the run's
   open flags onto its campaign for the web to show.

Every failure is loud: a session that cannot be asked, an action that cannot be taken,
a run that cannot be swept is logged at error and counted, and the step stays flagged.
The sweep never guesses that a step recovered because it could not look.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

from niuu.observability import get_observability
from ting.domain.models import WorkflowCampaign
from ting.domain.services.workflow_runs import WorkflowRuns
from ting.domain.services.workflow_step_engine import (
    RunSessionsNotStoppedError,
    WorkflowStepEngine,
)
from ting.domain.step_engine import listening_members, offer_members, unit_sessions
from ting.domain.step_graph import compile_step_graph
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
from ting.ports.workflow_steps import (
    SessionLiveness,
    StepRunDefinitions,
    StepSessionPort,
    WorkflowStepRepository,
)

logger = logging.getLogger(__name__)

#: What happens next to a flagged step, for the person who reads the notification.
_NEXT_BY_KIND = {
    FlagKind.OVERDUE: ("Ting only flags an overdue step. Retry or reassign it, or abort the run."),
    FlagKind.UNCLAIMED: (
        "Ting publishes the same offer again, and keeps doing so until a member takes it up "
        "or the step deadline fails it. Check that the members that take this kind of work "
        "are running."
    ),
    FlagKind.UNCLOSED: (
        "Ting only flags a step whose turn ended without a close. Retry or reassign it, or "
        "abort the run."
    ),
    FlagKind.SESSION_GONE: (
        "Ting is failing the step; the run's other branches carry on. Retry or reassign "
        "it if the run is still open."
    ),
}


def _next_for(flag: StepFlag) -> str:
    """What happens next to a flagged step, as the notification tells the owner."""
    return _NEXT_BY_KIND[flag.kind]


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
        definitions: StepRunDefinitions,
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
        self._definitions = definitions
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
        try:
            await self._runs.catch_up_run(campaign)
        except Exception:
            # An unreadable session log must not hide the other conditions of the run
            # (a deleted session, a passed deadline): it is seen, counted, and tried again.
            logger.error(
                "Reading the session logs of run %s failed; judging its steps without them",
                campaign.slug,
                exc_info=True,
            )
            self._count_error("catch_up")
        await self._engine.publish_ready_offers(campaign.id)
        state = await self._steps.load(campaign.id)
        if state.finished:
            return ()
        liveness, unobserved = await self._observe(campaign, state)
        conditions = detect_stuck(state, liveness, now=self._clock(), settings=self._settings)
        await self._engine.raise_flags(campaign.id, conditions)
        for condition in conditions:
            if condition.kind is FlagKind.UNCLAIMED:
                await self._republish(campaign, condition)
        # Told after the offer went out again, and before a step that is failed below closes
        # it and its flag.
        await self._notify(campaign, await self._steps.load(campaign.id))
        for condition in conditions:
            if condition.kind is FlagKind.SESSION_GONE:
                await self._fail_gone(campaign, condition)
            elif condition.kind is FlagKind.OVERDUE and condition.step.kind is StepKind.OFFER:
                await self._fail_expired_offer(campaign, condition)
        await self._fail_unreachable_offers(campaign, await self._steps.load(campaign.id))
        await self._engine.resolve_flags(
            campaign.id,
            {(condition.step.id, condition.kind) for condition in conditions},
            unobserved=unobserved,
        )
        return (await self._runs.refresh(campaign.id)).live_flags()

    async def _observe(
        self, campaign: WorkflowCampaign, state: RunState
    ) -> tuple[dict[UUID, SessionLiveness], set[UUID]]:
        """Whether each open step's session is up; the steps that could not be asked."""
        liveness: dict[UUID, SessionLiveness] = {}
        unobserved: set[UUID] = set()
        for step in state.steps:
            if (
                step.kind is not StepKind.STAGE_MEMBER
                or step.state not in (StepState.ACTIVE, StepState.WAITING)
                or not step.session_id
            ):
                continue
            try:
                liveness[step.id] = await self._sessions.session_liveness(
                    campaign.id, step.session_id
                )
            except Exception:
                logger.error(
                    "Could not ask whether session %s is up for step %s (%s/%s) of run %s",
                    step.session_id,
                    step.id,
                    step.node_id,
                    step.member,
                    campaign.slug,
                    exc_info=True,
                )
                self._count_error("observe")
                unobserved.add(step.id)
        return liveness, unobserved

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
                        "next": _next_for(flag),
                        "ui_path": f"/ting/review/run/{campaign.slug}",
                    },
                )
            )
            told.append(flag)
        await self._engine.mark_notified(campaign.id, told)

    # -- the safe automatic actions ---------------------------------------------

    async def _republish(self, campaign: WorkflowCampaign, condition: Condition) -> None:
        """Publish an unclaimed offer again. The same offer is the same event on the mesh."""
        offer = condition.step
        try:
            await self._engine.republish_offer(campaign.id, offer.id)
        except Exception as exc:
            logger.error(
                "Republishing offer %s (%s) of run %s failed (%s: %s); it stays unclaimed and "
                "the next pass tries again",
                offer.id,
                offer.node_id,
                campaign.slug,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            get_observability().count(
                "ting_workflow_step_auto_recovery_failures_total",
                attributes={"action": "republish_offer"},
                description="Automatic recoveries of stuck workflow steps that failed.",
            )

    async def _fail_expired_offer(self, campaign: WorkflowCampaign, condition: Condition) -> None:
        """The step deadline is the hard stop of an offer nobody took up: it fails."""
        offer = condition.step
        deadline = offer.deadline_at.isoformat() if offer.deadline_at else "its deadline"
        await self._fail(
            campaign,
            condition,
            f"nobody took up the offer of {offer.node_id!r} before {deadline}; the members that "
            "take its work may not be running or never listened. Raise "
            "workflow_steps.step_deadline_seconds (or the node's deadlineSeconds) if members "
            "start slower than that, or fix the member",
        )

    async def _fail_unreachable_offers(self, campaign: WorkflowCampaign, state: RunState) -> None:
        """Fail an unpublished offer whose member's session ended before it listened.

        Waiting for a member that can never listen is waiting for the deadline for nothing.
        """
        waiting = [
            offer
            for offer in state.steps
            if offer.kind is StepKind.OFFER and offer.state is StepState.ACTIVE
        ]
        if not waiting:
            return
        definition = await self._definitions.get(campaign.id)
        graph = compile_step_graph(definition.graph, definition.persona_definitions)
        listening = listening_members(state)
        unit = unit_sessions(state)
        for offer in waiting:
            for member in offer_members(graph, offer):
                session_id = unit.get(member.alias)
                if member.alias in listening or not session_id:
                    continue
                gone = await self._ended_reason(campaign, session_id)
                if not gone:
                    continue
                await self._fail(
                    campaign,
                    Condition(offer, FlagKind.SESSION_GONE, gone, self._clock()),
                    f"member {member.alias!r} cannot take the offer of {offer.node_id!r}: {gone}",
                )
                break

    async def _ended_reason(self, campaign: WorkflowCampaign, session_id: str) -> str:
        """Why a member's session can no longer listen; empty while it is up or coming up."""
        liveness = await self._sessions.session_liveness(campaign.id, session_id)
        if liveness not in (SessionLiveness.FAILED, SessionLiveness.STOPPED, SessionLiveness.GONE):
            return ""
        detail = f"session {session_id} is {liveness.value}"
        if liveness is not SessionLiveness.FAILED:
            return detail
        failure = await self._sessions.session_failure(campaign.id, session_id)
        return f"{detail}: {failure}" if failure else detail

    async def _fail(self, campaign: WorkflowCampaign, condition: Condition, reason: str) -> None:
        step = condition.step
        try:
            failed = await self._engine.fail_step(campaign.id, step.id, reason)
        except RunSessionsNotStoppedError:
            # The step and the run are recorded as failed; only ending the run's sessions
            # failed. That is its own error (the adapter logged each session, the engine
            # counted it), not a step that "stays flagged": the step is closed.
            logger.error(
                "Failed step %s (%s/%s) of run %s (%s) and recorded the run's end, but its "
                "sessions could not all be stopped",
                step.id,
                step.node_id,
                step.member,
                campaign.slug,
                condition.kind.value,
                exc_info=True,
            )
            get_observability().count(
                "ting_workflow_step_auto_recovery_failures_total",
                attributes={"action": "stop_run_sessions"},
                description="Automatic recoveries of stuck workflow steps that failed.",
            )
            failed = True
        except Exception:
            logger.error(
                "Could not fail step %s (%s/%s) of run %s (%s); it stays flagged",
                step.id,
                step.node_id,
                step.member,
                campaign.slug,
                condition.kind.value,
                exc_info=True,
            )
            get_observability().count(
                "ting_workflow_step_auto_recovery_failures_total",
                attributes={"action": "fail_step"},
                description="Automatic recoveries of stuck workflow steps that failed.",
            )
            return
        if not failed:
            return
        logger.warning(
            "Stuck step %s (%s/%s) of run %s (%s): fail_step done",
            step.id,
            step.node_id,
            step.member,
            campaign.slug,
            condition.kind.value,
        )
        get_observability().count(
            "ting_workflow_step_auto_recoveries_total",
            attributes={"action": "fail_step", "kind": condition.kind.value},
            description="Automatic recoveries of stuck workflow steps, by action and flag kind.",
        )

    async def _fail_gone(self, campaign: WorkflowCampaign, condition: Condition) -> None:
        await self._fail(
            campaign,
            condition,
            f"session gone: {condition.detail}; Ting failed the step because nothing can close it",
        )

    @staticmethod
    def _count_error(stage: str) -> None:
        get_observability().count(
            "ting_workflow_step_sweep_errors_total",
            attributes={"stage": stage},
            description="Stuck-step sweep work that failed, by stage; each is also logged.",
        )


__all__ = ["StuckStepSweeper"]
