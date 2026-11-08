"""Projection of a workflow run's ledger onto its campaign, and the stale-run sweep.

A campaign is the record of a run. Its state is not inferred from session activity:
the step engine's ledger is the truth, and ``WorkflowRuns`` hands this projector the
projection (``ting.domain.campaign_run_projection``) of the ledger after every
engine call. This projector writes it: it saves the transition, tells the event bus
and A2A push subscribers, and advances a ticket's next workflow when a run completes.

It also sweeps two cases no engine call ever reports: a run whose launch died before
the ledger recorded it, and a run waiting on a person for longer than the operator
allows.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from niuu.observability import get_observability
from ting.domain.campaign_run_projection import RunProjection
from ting.domain.campaign_stages import FAILURE_STAGE_KEY
from ting.domain.models import (
    CampaignStageState,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.services.ticket_advance import TicketAdvancer
from ting.domain.ticket_coordinator import ticket_of
from ting.ports.a2a_push import A2APushDispatcherPort
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.workflow_campaign_repository import (
    CampaignAlreadyTerminalError,
    WorkflowCampaignRepository,
)

if TYPE_CHECKING:
    from ting.domain.services.workflow_runs import WorkflowRuns

logger = logging.getLogger(__name__)

#: Reads whether a run's ledger has been launched.
RunLaunched = Callable[[UUID], Awaitable[bool]]

#: Told when a run ends (completed or failed), with its campaign as saved.
RunFinished = Callable[[WorkflowCampaign], Awaitable[None]]


class WorkflowCampaignProjector:
    """Write the projection of a run's ledger onto its campaign."""

    def __init__(
        self,
        *,
        repo: WorkflowCampaignRepository,
        event_bus: EventBusPort,
        run_launched: RunLaunched,
        push_dispatcher: A2APushDispatcherPort | None = None,
        sweep_interval_seconds: float = 0.0,
        input_wait_timeout_seconds: float = 0.0,
        launch_lease_seconds: float = 0.0,
        coordinator_repo: TicketCoordinatorRepository | None = None,
        ticket_advancer: TicketAdvancer | None = None,
        on_run_finished: tuple[RunFinished, ...] = (),
    ) -> None:
        self._repo = repo
        self._on_run_finished = on_run_finished
        self._ticket_advancer = ticket_advancer
        self._coordinator_repo = coordinator_repo
        self._event_bus = event_bus
        self._run_launched = run_launched
        self._push_dispatcher = push_dispatcher
        self._sweep_interval = sweep_interval_seconds
        self._input_wait_timeout = input_wait_timeout_seconds
        self._launch_lease = launch_lease_seconds
        self._runs: WorkflowRuns | None = None
        self._sweep_task: asyncio.Task[None] | None = None
        self._running = False

    def bind_runs(self, runs: WorkflowRuns) -> None:
        """The run service the sweep cancels expired runs through."""
        self._runs = runs

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        if self._sweep_interval > 0 and (self._input_wait_timeout > 0 or self._launch_lease > 0):
            self._sweep_task = asyncio.create_task(self._sweep_forever())
        logger.info("Workflow campaign projector started")

    async def stop(self) -> None:
        self._running = False
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            self._sweep_task = None
        logger.info("Workflow campaign projector stopped")

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval)
            await self.expire_stale_campaigns(now=datetime.now(UTC))

    async def list_active_owner_ids(self) -> list[str]:
        owners = set(await self._repo.list_active_owner_ids())
        if self._coordinator_repo is not None:
            owners.update(await self._coordinator_repo.list_active_owner_ids())
        return sorted(owners)

    async def reconcile_owner(self, owner_id: str) -> None:
        """Start the next workflow of tickets whose run finished while Ting was away."""
        if self._ticket_advancer is not None:
            await self._ticket_advancer.advance_finished(owner_id)

    async def apply_run_projection(
        self, campaign: WorkflowCampaign, projection: RunProjection
    ) -> None:
        """Save what the run's ledger says the campaign now is."""
        await self._save_transition(
            campaign,
            projection.status,
            metadata=projection.metadata,
            stage_state=projection.stage_state,
            active_stage_id=projection.active_stage_id,
            clear_active_stage=projection.active_stage_id is None,
            force=projection.differs_from(campaign),
        )

    # -- expiry -----------------------------------------------------------------

    async def expire_stale_campaigns(self, *, now: datetime) -> int:
        """Fail runs whose launch died or that wait on a person for too long.

        Events drive every other transition. Nothing reports a run nobody answers, or
        a launch that died before the ledger recorded it, so those were never closed.
        """
        expired = 0
        for campaign in await self._repo.list_active_campaigns():
            try:
                expired += await self._expire_if_stale(campaign, now=now)
            except Exception:
                logger.error(
                    "Could not check workflow campaign %s for expiry",
                    campaign.slug,
                    exc_info=True,
                )
        return expired

    async def _expire_if_stale(self, campaign: WorkflowCampaign, *, now: datetime) -> bool:
        """Fail one stale campaign; return whether it was.

        Each expiry is an error: it names the bound that was hit, is counted, and
        reaches the caller through the task's failure and its A2A push.
        """
        expiry = await self._stale_expiry(campaign, now=now)
        if expiry is None:
            return False
        logger.error(
            "Expiring workflow campaign %s at %s=%gs: %s Raise the bound if this work "
            "legitimately takes longer.",
            campaign.slug,
            expiry.knob,
            expiry.bound_seconds,
            expiry.reason,
        )
        get_observability().count(
            "ting_a2a_campaigns_expired_total",
            attributes={"reason": expiry.kind},
            description=(
                "Workflow campaigns (A2A tasks) failed by the stale-campaign sweep, "
                "by the bound that expired them."
            ),
        )
        if expiry.cancel_run:
            if self._runs is None:
                raise RuntimeError("the stale-campaign sweep has no run service to cancel with")
            await self._runs.cancel(campaign, expiry.reason)
            return True
        await self._save_failed(campaign, expiry.reason)
        return True

    async def _stale_expiry(
        self, campaign: WorkflowCampaign, *, now: datetime
    ) -> _CampaignExpiry | None:
        since = campaign.last_activity_at or campaign.updated_at
        if campaign.status == WorkflowCampaignStatus.PENDING:
            return await self._abandoned_launch_expiry(campaign, since=since, now=now)
        if campaign.status != WorkflowCampaignStatus.BLOCKED:
            return None
        if ticket_of(campaign.metadata) is not None:
            # A ticket waiting at a gate is waiting on a person; its board shows it,
            # and a review can take days.
            return None
        wait = timedelta(seconds=self._input_wait_timeout)
        if self._input_wait_timeout <= 0 or now - since <= wait:
            return None
        return _CampaignExpiry(
            kind="input_wait",
            knob="a2a.stale_campaigns.input_wait_timeout_seconds",
            bound_seconds=self._input_wait_timeout,
            reason=(
                f"No answer to the input request within {_hours(wait)}; the task was closed "
                "and its sessions stopped. Start a new task to retry."
            ),
            cancel_run=True,
        )

    async def _abandoned_launch_expiry(
        self, campaign: WorkflowCampaign, *, since: datetime, now: datetime
    ) -> _CampaignExpiry | None:
        """Expire a run whose launch ended before the ledger recorded it.

        A launch saves its campaign before it starts the run, so a retry cannot launch
        twice. A launch that dies in between leaves the campaign ``pending`` with
        nothing to report on it, and no retry may come. The launch lease is the bound:
        past it no attempt holds the launch.
        """
        lease = timedelta(seconds=self._launch_lease)
        if self._launch_lease <= 0 or now - since <= lease:
            return None
        if await self._run_launched(campaign.id):
            return None
        return _CampaignExpiry(
            kind="launch_abandoned",
            knob="a2a.launch_lease_seconds",
            bound_seconds=self._launch_lease,
            reason="The launch ended before the run started. Start a new task to retry.",
        )

    async def _save_failed(self, campaign: WorkflowCampaign, reason: str) -> None:
        await self._save_transition(
            campaign,
            WorkflowCampaignStatus.FAILED,
            metadata={**campaign.metadata, "failure_error": reason},
        )

    # -- saving -----------------------------------------------------------------

    async def _save_transition(
        self,
        campaign: WorkflowCampaign,
        next_status: WorkflowCampaignStatus,
        *,
        metadata: dict | None = None,
        stage_state: list[CampaignStageState] | None = None,
        active_stage_id: str | None = None,
        clear_active_stage: bool = False,
        force: bool = False,
    ) -> bool:
        """Persist a transition; return whether it was written."""
        if not force and next_status == campaign.status:
            return False

        now = datetime.now(UTC)
        resolved_metadata = dict(campaign.metadata) if metadata is None else metadata
        if clear_active_stage:
            resolved_active = None
        else:
            resolved_active = active_stage_id or campaign.active_stage_id
        try:
            saved = await self._repo.save_campaign(
                replace(
                    campaign,
                    status=next_status,
                    metadata=resolved_metadata,
                    stage_state=stage_state if stage_state is not None else campaign.stage_state,
                    active_stage_id=resolved_active,
                    updated_at=now,
                    last_activity_at=now,
                    completed_at=(
                        now
                        if next_status == WorkflowCampaignStatus.COMPLETED
                        and campaign.completed_at is None
                        else campaign.completed_at
                    ),
                )
            )
        except CampaignAlreadyTerminalError:
            # The campaign finished (e.g. CancelTask) after this copy was read.
            # The terminal state stands; this event no longer applies.
            logger.info(
                "Workflow campaign %s already finished; ignoring %s from a stale read",
                campaign.slug,
                next_status.value,
            )
            return False
        event_name = "workflow.campaign.updated"
        if next_status == WorkflowCampaignStatus.COMPLETED:
            event_name = "workflow.campaign.completed"
        elif next_status == WorkflowCampaignStatus.FAILED:
            event_name = "workflow.campaign.failed"
        try:
            await self._event_bus.emit(
                TingEvent(
                    event=event_name,
                    owner_id=saved.owner_id,
                    data={
                        "campaign_id": str(saved.id),
                        "slug": saved.slug,
                        "name": saved.name,
                        "status": saved.status.value,
                        "workflow_id": str(saved.workflow_id),
                        "active_stage_id": saved.active_stage_id,
                        **(
                            {"error": str(saved.metadata["failure_error"])}
                            if saved.metadata.get("failure_error")
                            else {}
                        ),
                        **(
                            {"failed_stage_id": str(saved.metadata[FAILURE_STAGE_KEY])}
                            if next_status == WorkflowCampaignStatus.FAILED
                            and saved.metadata.get(FAILURE_STAGE_KEY)
                            else {}
                        ),
                    },
                )
            )
            if self._push_dispatcher is not None:
                await self._push_dispatcher.queue_campaign(saved)
        finally:
            if next_status == WorkflowCampaignStatus.COMPLETED and self._ticket_advancer:
                await self._ticket_advancer.advance(saved)
            if next_status in _TERMINAL_STATUSES:
                for finished in self._on_run_finished:
                    await finished(saved)
        _record_a2a_state_transition(saved)
        return True


_TERMINAL_STATUSES = frozenset({WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED})


@dataclass(frozen=True)
class _CampaignExpiry:
    """Why the stale-campaign sweep fails a campaign, and which bound it hit."""

    kind: str
    knob: str
    bound_seconds: float
    reason: str
    #: Whether the run has a ledger to cancel (its sessions are stopped with it).
    cancel_run: bool = False


def _hours(span: timedelta) -> str:
    hours = span.total_seconds() / 3600
    return f"{hours:g} hour" + ("" if hours == 1 else "s")


_A2A_STATE_BY_STATUS = {
    WorkflowCampaignStatus.PENDING: "TASK_STATE_SUBMITTED",
    WorkflowCampaignStatus.RUNNING: "TASK_STATE_WORKING",
    WorkflowCampaignStatus.BLOCKED: "TASK_STATE_INPUT_REQUIRED",
    WorkflowCampaignStatus.COMPLETED: "TASK_STATE_COMPLETED",
    WorkflowCampaignStatus.FAILED: "TASK_STATE_FAILED",
}


def _record_a2a_state_transition(campaign: WorkflowCampaign) -> None:
    telemetry = get_observability()
    attributes = {"state": _A2A_STATE_BY_STATUS[campaign.status]}
    telemetry.count(
        "a2a_task_state_transitions_total",
        attributes=attributes,
        description="A2A task state transitions observed by Ting.",
    )
    if campaign.status not in {WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED}:
        return
    finished_at = campaign.completed_at or campaign.updated_at
    telemetry.duration(
        "a2a_send_to_terminal_duration_seconds",
        max(0.0, (finished_at - campaign.created_at).total_seconds()),
        attributes=attributes,
        description="Time from A2A SendMessage campaign creation to terminal state.",
    )
