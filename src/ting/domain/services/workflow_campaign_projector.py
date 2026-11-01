"""Event-driven projection of Volundr workflow sessions into A2A campaigns."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from niuu.domain.models import Principal
from niuu.observability import get_observability
from ting.domain.campaign_stages import FAILURE_STAGE_KEY, record_runtime_failure
from ting.domain.models import (
    PR_STATE_MERGED,
    CampaignStageState,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.services.ticket_advance import TicketAdvancer
from ting.domain.services.ticket_coordinator import coordinator_peer_id
from ting.domain.services.workflow_campaign_lifecycle import (
    stop_terminal_campaign_session,
    terminal_session_cleanup_needed,
)
from ting.domain.ticket_coordinator import (
    BRIEF_MESSAGE_METADATA,
    COORDINATOR_JOINED_KEY,
    PULL_REQUEST_KEY,
    TICKET_METADATA_KEY,
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_of,
)
from ting.domain.workflow_board import node_label
from ting.ports.a2a_push import A2APushDispatcherPort
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.volundr import ActivityEvent, VolundrFactory, VolundrPort, VolundrSession
from ting.ports.workflow_campaign_repository import (
    CampaignAlreadyTerminalError,
    WorkflowCampaignRepository,
)

logger = logging.getLogger(__name__)


class WorkflowCampaignProjector:
    """Project Volundr SSE events and reconnect reconciliation into campaigns.

    Normal campaign transitions are driven by Volundr's activity stream.  A
    one-shot read when an owner subscription starts closes the restart and
    disconnect gap without continuously polling every campaign.
    """

    def __init__(
        self,
        *,
        repo: WorkflowCampaignRepository,
        volundr_factory: VolundrFactory,
        event_bus: EventBusPort,
        push_dispatcher: A2APushDispatcherPort | None = None,
        sweep_interval_seconds: float = 0.0,
        input_wait_timeout_seconds: float = 0.0,
        idle_timeout_seconds: float = 0.0,
        launch_lease_seconds: float = 0.0,
        coordinator_repo: TicketCoordinatorRepository | None = None,
        coordinator_persona: str = "",
        ticket_advancer: TicketAdvancer | None = None,
    ) -> None:
        self._repo = repo
        self._ticket_advancer = ticket_advancer
        self._coordinator_repo = coordinator_repo
        self._coordinator_persona = coordinator_persona
        self._volundr_factory = volundr_factory
        self._event_bus = event_bus
        self._push_dispatcher = push_dispatcher
        self._sweep_interval = sweep_interval_seconds
        self._input_wait_timeout = input_wait_timeout_seconds
        self._idle_timeout = idle_timeout_seconds
        self._launch_lease = launch_lease_seconds
        self._sweep_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        if self._sweep_interval > 0 and (
            self._input_wait_timeout > 0 or self._idle_timeout > 0 or self._launch_lease > 0
        ):
            self._sweep_task = asyncio.create_task(self._sweep_forever())
        logger.info("Event-driven workflow campaign projector started")

    async def stop(self) -> None:
        self._running = False
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            self._sweep_task = None
        logger.info("Event-driven workflow campaign projector stopped")

    async def _sweep_forever(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval)
            await self.settle_merged_campaigns()
            await self.expire_stale_campaigns(now=datetime.now(UTC))

    async def settle_merged_campaigns(self) -> int:
        """Complete blocked ticket runs whose session is gone but whose work merged.

        A stopped session reports nothing more, and a person can merge its pull
        request afterwards, so no event would ever finish such a run.
        """
        settled = 0
        for campaign in await self._repo.list_active_campaigns():
            if (
                campaign.status != WorkflowCampaignStatus.BLOCKED
                or not campaign.session_id
                or not ticket_of(campaign.metadata)
            ):
                continue
            try:
                adapter = await self._campaign_adapter(campaign)
                session = await adapter.get_session(campaign.session_id) if adapter else None
                if session is not None and session.status not in _ENDED_SESSION_STATUSES:
                    continue
                settled += await self._complete_if_merged(campaign)
            except Exception:
                logger.error(
                    "Could not check whether campaign %s merged", campaign.slug, exc_info=True
                )
        return settled

    async def expire_stale_campaigns(self, *, now: datetime) -> int:
        """Fail campaigns stuck waiting for input or making no progress; return how many.

        Events drive every other transition. Nothing emits an event for a
        campaign nobody answers, a session that stops doing anything or a
        launch that died before it recorded a session, so those were never
        closed and their sessions held their nodes for good.
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

        Each expiry is an error: it names the bound that was hit, is counted,
        and reaches the caller through the task's failure and its A2A push.
        """
        expiry = await self._stale_expiry(campaign, now=now)
        if expiry is None:
            return False
        if expiry.session is not None:
            # Recorded so that failing the run stops the session it left behind.
            campaign = replace(
                campaign, session_id=expiry.session.id, session_name=expiry.session.name
            )
        logger.error(
            "Expiring workflow campaign %s (session %s) at %s=%gs: "
            "%s Raise the bound if this work legitimately takes longer.",
            campaign.slug,
            campaign.session_id or "none",
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
        await self._save_failure(campaign, {"error": expiry.reason})
        return True

    async def _stale_expiry(
        self, campaign: WorkflowCampaign, *, now: datetime
    ) -> _CampaignExpiry | None:
        if not campaign.session_id:
            return await self._abandoned_launch_expiry(campaign, now=now)
        since = campaign.last_activity_at or campaign.updated_at
        if campaign.status == WorkflowCampaignStatus.BLOCKED:
            if ticket_of(campaign.metadata) is not None:
                # A ticket waiting at a gate is waiting on a person; its board shows
                # it, and a review can take days.
                return None
            wait = timedelta(seconds=self._input_wait_timeout)
            if self._input_wait_timeout <= 0 or now - since <= wait:
                return None
            return _CampaignExpiry(
                kind="input_wait",
                knob="a2a.stale_campaigns.input_wait_timeout_seconds",
                bound_seconds=self._input_wait_timeout,
                reason=(
                    f"No answer to the input request within {_hours(wait)}; the task was "
                    "closed and its session stopped. Start a new task to retry."
                ),
            )
        if campaign.status not in {WorkflowCampaignStatus.PENDING, WorkflowCampaignStatus.RUNNING}:
            return None
        idle = timedelta(seconds=self._idle_timeout)
        if self._idle_timeout <= 0 or now - since <= idle:
            return None
        adapter = await self._campaign_adapter(campaign)
        if adapter is None:
            return None
        session = await adapter.get_session(campaign.session_id)
        if session is None:
            return self._idle_expiry(
                "session_gone",
                f"Session {campaign.session_id} no longer exists; the task cannot finish.",
            )
        if str(session.activity_state or "").lower() in _BUSY_ACTIVITY_STATES:
            # A busy report is re-sent as a heartbeat for as long as the broker
            # lives, so it proves the broker is alive, not that work is moving.
            # Progress is entering the busy state or recording work (a usage
            # report); a heartbeat moves neither.
            progress_at = _latest(session.activity_state_since, session.updated_at) or since
            if now - progress_at <= idle:
                return None
            return self._idle_expiry(
                "no_progress",
                f"Session reported busy with no recorded progress for over {_hours(idle)} "
                f"(last progress {progress_at.isoformat()}; only heartbeats since); the task "
                f"was closed and its session stopped.{_last_outcome_note(session)}",
            )
        last_active = session.last_active or since
        if now - last_active <= idle:
            return None
        return self._idle_expiry(
            "idle",
            f"Session idle for over {_hours(idle)} without finishing; the task was "
            f"closed and its session stopped.{_last_outcome_note(session)}",
        )

    async def _abandoned_launch_expiry(
        self, campaign: WorkflowCampaign, *, now: datetime
    ) -> _CampaignExpiry | None:
        """Expire a run whose launch ended without recording a session.

        A launch saves its run before it spawns the session, so a retry cannot
        launch twice. A launch that dies before it records the session leaves
        the run PENDING with nothing to report on it, and no retry may come.
        The launch lease is the bound: past it no attempt holds the launch.
        """
        lease = timedelta(seconds=self._launch_lease)
        since = campaign.last_activity_at or campaign.updated_at
        if self._launch_lease <= 0 or now - since <= lease:
            return None
        adapter = await self._campaign_adapter(campaign)
        if adapter is None:
            return self._launch_expiry(
                f"The launch ended without recording a session, and Forge connection "
                f"{campaign.connection_id!r} no longer resolves, so none could be looked up. "
                "Start a new task to retry."
            )
        tracker_key = f"workflow:{campaign.slug}"
        session = next(
            (s for s in await adapter.list_sessions() if s.tracker_issue_id == tracker_key),
            None,
        )
        if session is None:
            return self._launch_expiry(
                "The launch ended without starting a session. Start a new task to retry."
            )
        return self._launch_expiry(
            f"The launch ended without recording session {session.id}, which nothing tracks; "
            "the task was closed and its session stopped. Start a new task to retry.",
            session=session,
        )

    def _launch_expiry(
        self, reason: str, *, session: VolundrSession | None = None
    ) -> _CampaignExpiry:
        return _CampaignExpiry(
            kind="launch_abandoned",
            knob="a2a.launch_lease_seconds",
            bound_seconds=self._launch_lease,
            reason=reason,
            session=session,
        )

    def _idle_expiry(self, kind: str, reason: str) -> _CampaignExpiry:
        return _CampaignExpiry(
            kind=kind,
            knob="a2a.stale_campaigns.idle_timeout_seconds",
            bound_seconds=self._idle_timeout,
            reason=reason,
        )

    async def list_active_owner_ids(self) -> list[str]:
        owners = set(await self._repo.list_active_owner_ids())
        if self._coordinator_repo is not None:
            owners.update(await self._coordinator_repo.list_active_owner_ids())
        return sorted(owners)

    async def deliver_coordinator_brief(self, event: ActivityEvent, volundr: VolundrPort) -> None:
        """Hand a coordinator the brief that waited for its session to come up.

        An activity report is the proof that its broker is listening. A failure
        keeps the brief for the next report and the ticket stays retryable.
        """
        if self._coordinator_repo is None or event.state not in _LIVE_ACTIVITY_STATES:
            return
        coordinator = await self._coordinator_repo.get_by_session(event.session_id)
        if coordinator is None or coordinator.pending_brief is None:
            return
        try:
            await volundr.send_directed_room_message(
                coordinator.session_id,
                coordinator_peer_id(self._coordinator_persona),
                coordinator.pending_brief,
                metadata=BRIEF_MESSAGE_METADATA,
            )
        except Exception:
            logger.error(
                "Could not hand coordinator session %s its waiting brief",
                coordinator.session_id,
                exc_info=True,
            )
            return
        await self._coordinator_repo.save(replace(coordinator, pending_brief=None))

    async def reconcile_owner(self, owner_id: str) -> None:
        """Reconcile one owner's campaigns once before (re)subscribing to SSE."""
        campaigns = await self._repo.list_active_campaigns()
        for campaign in campaigns:
            if campaign.owner_id != owner_id:
                continue
            try:
                await self._refresh_campaign(campaign)
            except Exception:
                logger.warning(
                    "Workflow campaign reconnect reconciliation failed for %s",
                    campaign.slug,
                    exc_info=True,
                )
        if self._ticket_advancer is not None:
            await self._ticket_advancer.advance_finished(owner_id)

    async def handle_activity(
        self,
        event: ActivityEvent,
        owner_id: str,
        *,
        connection_id: str | None = None,
    ) -> bool:
        """Apply one Volundr stream event; return whether it was a campaign event."""
        campaign = await self._active_campaign(
            owner_id, event.session_id, connection_id=connection_id
        )
        if campaign is None:
            return False

        if event.state in _LIVE_ACTIVITY_STATES:
            campaign = await self._join_coordinator(campaign)
        completion_event = _campaign_completion_event(campaign)
        authoritative_completion = _authoritative_completion(
            event.metadata, completion_event=completion_event
        )
        delivery = _authoritative_delivery(event.metadata, completion_event=completion_event)
        if event.state == "error" or event.session_status in {"failed", "cancelled", "canceled"}:
            error = str(event.metadata.get("error") or event.metadata.get("message") or "").strip()
            if not error:
                error = f"Session {event.session_status or event.state or 'failed'}"
            await self._save_failure(campaign, {**event.metadata, "error": error})
        elif authoritative_completion is not None and (
            "delivery" not in event.metadata or delivery is not None
        ):
            metadata = await self._with_pull_request(campaign, dict(campaign.metadata))
            if delivery is not None:
                metadata["delivery"] = delivery
            await self._save_transition(
                campaign,
                WorkflowCampaignStatus.COMPLETED,
                metadata=metadata,
            )
        elif event.session_status == "stopped":
            if not await self._complete_if_merged(campaign):
                await self._save_transition(campaign, WorkflowCampaignStatus.BLOCKED)
        elif event.session_status in {"completed", "complete", "succeeded"}:
            metadata = _with_delivery(campaign, event.metadata)
            await self._save_transition(
                campaign,
                WorkflowCampaignStatus.COMPLETED,
                metadata=metadata,
            )
        elif campaign.status == WorkflowCampaignStatus.PENDING and event.state in {
            "active",
            "idle",
            "tool_executing",
        }:
            await self._save_transition(campaign, WorkflowCampaignStatus.RUNNING)
        elif (
            campaign.status == WorkflowCampaignStatus.BLOCKED
            and event.state in _BUSY_ACTIVITY_STATES
            and await self._nothing_awaits_input(campaign)
        ):
            await self._resume_campaign(campaign)
        return True

    async def _nothing_awaits_input(self, campaign: WorkflowCampaign) -> bool:
        """Whether a blocked campaign's session has moved on from what blocked it."""
        try:
            adapter = await self._campaign_adapter(campaign)
            if adapter is None:
                return False
            if _has_pending_entry(await adapter.get_help_requests(campaign.session_id)):
                return False
            return not _has_pending_entry(await adapter.get_workflow_gates(campaign.session_id))
        except Exception:
            # Unread stays blocked; the session's next report tries again.
            logger.warning("Could not read what blocked campaign %s", campaign.slug, exc_info=True)
            return False

    async def _resume_campaign(self, campaign: WorkflowCampaign) -> None:
        metadata = dict(campaign.metadata)
        metadata.pop("pending_workflow_gates", None)
        await self._save_transition(campaign, WorkflowCampaignStatus.RUNNING, metadata=metadata)

    async def _coordinator_of(self, campaign: WorkflowCampaign) -> TicketCoordinator | None:
        """The active coordinator of the ticket a campaign runs for, if it has one."""
        ticket = ticket_of(campaign.metadata)
        if ticket is None or self._coordinator_repo is None:
            return None
        coordinator = await self._coordinator_repo.get(UUID(ticket[0]), ticket[1])
        if coordinator is None or coordinator.status != TicketCoordinatorStatus.ACTIVE:
            return None
        return coordinator

    async def _join_coordinator(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        """Seat a ticket's coordinator in its run's room once that room is up.

        The seat is an observer's: it hears gates open and the run finish, and it
        holds no authority to answer them. A failure is retried on the next report.
        """
        if campaign.metadata.get(COORDINATOR_JOINED_KEY):
            return campaign
        coordinator = await self._coordinator_of(campaign)
        if coordinator is None:
            return campaign
        try:
            adapter = await self._campaign_adapter(campaign)
            if adapter is None:
                return campaign
            await adapter.join_room(
                campaign.session_id,
                participant_id=_room_member_id(coordinator),
                display_name="Ticket coordinator",
                member_session_id=coordinator.session_id,
                notice_context=_notice_context(campaign),
            )
        except Exception:
            logger.error(
                "Coordinator session %s could not join the room of workflow session %s",
                coordinator.session_id,
                campaign.session_id,
                exc_info=True,
            )
            return campaign
        return await self._repo.save_campaign(
            replace(campaign, metadata={**campaign.metadata, COORDINATOR_JOINED_KEY: True})
        )

    async def _leave_coordinator(self, campaign: WorkflowCampaign) -> None:
        """End the coordinator's seat with the run, and say so if the run failed."""
        if not campaign.metadata.get(COORDINATOR_JOINED_KEY):
            return
        coordinator = await self._coordinator_of(campaign)
        if coordinator is None:
            return
        try:
            adapter = await self._campaign_adapter(campaign)
            if adapter is None:
                return
            if campaign.status == WorkflowCampaignStatus.FAILED:
                await adapter.send_directed_room_message(
                    coordinator.session_id,
                    coordinator_peer_id(self._coordinator_persona),
                    f"[{_notice_context(campaign)}] This run failed: "
                    f"{campaign.metadata.get('failure_error') or 'no reason was reported'}",
                )
            await adapter.leave_room(
                campaign.session_id,
                _room_member_id(coordinator),
                reason="run_finished",
            )
        except LookupError:
            # The session is already gone, and its room with it.
            return
        except Exception:
            logger.error(
                "Coordinator session %s could not leave the room of workflow session %s",
                coordinator.session_id,
                campaign.session_id,
                exc_info=True,
            )

    async def _complete_if_merged(self, campaign: WorkflowCampaign) -> bool:
        """Finish a run whose pull request merged, however its session ended."""
        metadata = await self._with_pull_request(campaign, dict(campaign.metadata))
        pull_request = metadata.get(PULL_REQUEST_KEY) or {}
        if pull_request.get("state") != PR_STATE_MERGED:
            return False
        metadata.pop("pending_workflow_gates", None)
        return await self._save_transition(
            campaign, WorkflowCampaignStatus.COMPLETED, metadata=metadata
        )

    async def _with_pull_request(
        self, campaign: WorkflowCampaign, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """Add the pull request Forge sees for a run, so the board can link to it."""
        try:
            adapter = await self._campaign_adapter(campaign)
            if adapter is None:
                return metadata
            pr = await adapter.get_pr_status(campaign.session_id)
        except Exception:
            logger.error(
                "Could not read the pull request of campaign %s", campaign.slug, exc_info=True
            )
            return metadata
        if pr is None:
            return metadata
        return {**metadata, PULL_REQUEST_KEY: {"id": pr.pr_id, "url": pr.url, "state": pr.state}}

    async def record_help_needed(
        self,
        payload: dict[str, object],
        owner_id: str,
        *,
        session_id: str,
        gate: dict[str, object],
        connection_id: str | None = None,
    ) -> bool:
        """Persist an input-required event and immediately notify A2A callbacks."""
        campaign = await self._active_campaign(owner_id, session_id, connection_id=connection_id)
        if campaign is None:
            return False

        metadata = await self._with_pull_request(campaign, dict(campaign.metadata))
        metadata["pending_workflow_gates"] = [
            {
                **gate,
                "label": gate.get("label")
                or node_label(campaign.workflow_snapshot, str(gate.get("node_id") or "")),
            }
        ]
        metadata["latest_help_needed"] = dict(payload)
        if not await self._save_transition(
            campaign,
            WorkflowCampaignStatus.BLOCKED,
            metadata=metadata,
            active_stage_id=str(gate.get("node_id") or campaign.active_stage_id or "") or None,
            force=True,
        ):
            return True
        await self._event_bus.emit(
            TingEvent(
                event="workflow.campaign.feedback_requested",
                owner_id=owner_id,
                data={
                    "owner_id": owner_id,
                    "campaign_id": str(campaign.id),
                    "slug": campaign.slug,
                    "session_id": session_id,
                    "summary": payload.get("summary", ""),
                    "reason": payload.get("reason", ""),
                    "recommendation": payload.get("recommendation", ""),
                },
            )
        )
        logger.info(
            "Recorded workflow campaign help-needed request for campaign=%s session=%s",
            campaign.slug,
            session_id[:8],
        )
        return True

    async def _active_campaign(
        self,
        owner_id: str,
        session_id: str,
        *,
        connection_id: str | None = None,
    ) -> WorkflowCampaign | None:
        return await self._repo.get_active_campaign_by_session(
            owner_id=owner_id,
            session_id=session_id,
            connection_id=connection_id,
        )

    async def _refresh_campaign(self, campaign: WorkflowCampaign) -> None:
        """One-shot reconnect reconciliation; never called on a timer."""
        if not campaign.session_id:
            await self._expire_if_stale(campaign, now=datetime.now(UTC))
            return
        adapter = await self._campaign_adapter(campaign)
        if adapter is None:
            return
        session = await adapter.get_session(campaign.session_id)
        if session is None:
            return
        activity_state = str(getattr(session, "activity_state", "") or "").strip().lower()
        next_status = (
            WorkflowCampaignStatus.FAILED
            if activity_state == "error"
            else _status_from_session(session.status, fallback=campaign.status)
        )
        activity_metadata = getattr(session, "activity_metadata", {}) or {}
        completion_event = _campaign_completion_event(campaign)
        authoritative_completion = _authoritative_completion(
            activity_metadata, completion_event=completion_event
        )
        delivery = _authoritative_delivery(activity_metadata, completion_event=completion_event)
        if (
            next_status != WorkflowCampaignStatus.FAILED
            and authoritative_completion is not None
            and ("delivery" not in activity_metadata or delivery is not None)
        ):
            next_status = WorkflowCampaignStatus.COMPLETED
        elif next_status == WorkflowCampaignStatus.RUNNING and await self._session_awaits_input(
            adapter, campaign.session_id
        ):
            next_status = WorkflowCampaignStatus.BLOCKED

        if next_status == WorkflowCampaignStatus.FAILED:
            await self._save_failure(campaign, activity_metadata, session_name=session.name)
            return
        metadata = _with_delivery(campaign, activity_metadata)
        if next_status == WorkflowCampaignStatus.RUNNING:
            metadata.pop("pending_workflow_gates", None)
        await self._save_transition(
            campaign,
            next_status,
            session_name=session.name,
            metadata=metadata,
        )

    async def _campaign_adapter(self, campaign: WorkflowCampaign):
        if campaign.connection_id:
            adapter = await self._volundr_factory.for_connection(
                campaign.owner_id, campaign.connection_id
            )
            if adapter is not None:
                return adapter
            logger.warning(
                "Campaign %s connection %s no longer resolves; skipping reconciliation",
                campaign.slug,
                campaign.connection_id,
            )
            return None
        return await self._volundr_factory.primary_for_owner(campaign.owner_id)

    async def cleanup_terminal_session(
        self,
        campaign: WorkflowCampaign,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> WorkflowCampaign | None:
        """Release one terminal A2A runtime without changing its task outcome."""
        if not terminal_session_cleanup_needed(campaign):
            return campaign
        try:
            adapter = await self._campaign_adapter(campaign)
        except Exception:
            logger.warning(
                "Could not resolve terminal campaign session %s for cleanup",
                campaign.session_id,
                exc_info=True,
            )
            return None
        if adapter is None:
            return None
        return await stop_terminal_campaign_session(
            campaign,
            adapter=adapter,
            repo=self._repo,
            auth_token=auth_token,
            principal=principal,
        )

    async def _session_awaits_input(self, adapter, session_id: str) -> bool:
        try:
            if _has_pending_entry(await adapter.get_help_requests(session_id)):
                return True
            return _has_pending_entry(await adapter.get_workflow_gates(session_id))
        except Exception:
            logger.warning(
                "Could not reconcile session blockers for %s",
                session_id,
                exc_info=True,
            )
            return False

    async def _save_failure(
        self,
        campaign: WorkflowCampaign,
        activity_metadata: dict,
        *,
        session_name: str | None = None,
    ) -> None:
        """Fail a campaign, recording the error and the stage it failed in."""
        metadata, stage_state, active_stage_id = record_runtime_failure(
            campaign, activity_metadata, now=datetime.now(UTC)
        )
        await self._save_transition(
            campaign,
            WorkflowCampaignStatus.FAILED,
            session_name=session_name,
            metadata=metadata,
            stage_state=stage_state,
            active_stage_id=active_stage_id,
        )

    async def _save_transition(
        self,
        campaign: WorkflowCampaign,
        next_status: WorkflowCampaignStatus,
        *,
        session_name: str | None = None,
        failure_error: str = "",
        metadata: dict | None = None,
        stage_state: list[CampaignStageState] | None = None,
        active_stage_id: str | None = None,
        force: bool = False,
    ) -> bool:
        """Persist a transition; return whether it was written."""
        resolved_name = session_name or campaign.session_name
        if not force and next_status == campaign.status and resolved_name == campaign.session_name:
            return False

        now = datetime.now(UTC)
        resolved_metadata = dict(campaign.metadata) if metadata is None else metadata
        if failure_error:
            resolved_metadata["failure_error"] = failure_error
        try:
            saved = await self._repo.save_campaign(
                replace(
                    campaign,
                    session_name=resolved_name,
                    status=next_status,
                    metadata=resolved_metadata,
                    stage_state=stage_state if stage_state is not None else campaign.stage_state,
                    active_stage_id=(
                        active_stage_id if active_stage_id is not None else campaign.active_stage_id
                    ),
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
                        "session_id": saved.session_id,
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
            if next_status in _TERMINAL_STATUSES:
                await self._leave_coordinator(saved)
            if terminal_session_cleanup_needed(saved):
                await self.cleanup_terminal_session(saved)
            if next_status == WorkflowCampaignStatus.COMPLETED and self._ticket_advancer:
                await self._ticket_advancer.advance(saved)
        _record_a2a_state_transition(saved)
        return True


_BUSY_ACTIVITY_STATES = frozenset({"active", "tool_executing"})
_LIVE_ACTIVITY_STATES = frozenset({"idle", *_BUSY_ACTIVITY_STATES})
_ENDED_SESSION_STATUSES = frozenset({"stopped", "failed"})
_TERMINAL_STATUSES = frozenset({WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED})


@dataclass(frozen=True)
class _CampaignExpiry:
    """Why the stale-campaign sweep fails a campaign, and which bound it hit."""

    kind: str
    knob: str
    bound_seconds: float
    reason: str
    session: VolundrSession | None = None


def _notice_context(campaign: WorkflowCampaign) -> str:
    """Names the ticket and the run, so a notice read much later still stands on its own."""
    ticket = campaign.metadata.get(TICKET_METADATA_KEY)
    identifier = ticket.get("identifier") if isinstance(ticket, dict) else None
    return f"Ticket {identifier}, {campaign.workflow_name}, run {campaign.slug}"


def _room_member_id(coordinator: TicketCoordinator) -> str:
    return f"coordinator:{coordinator.session_id}"


def _hours(span: timedelta) -> str:
    hours = span.total_seconds() / 3600
    return f"{hours:g} hour" + ("" if hours == 1 else "s")


def _latest(*moments: datetime | None) -> datetime | None:
    present = [moment for moment in moments if moment is not None]
    return max(present) if present else None


def _last_outcome_note(session: object) -> str:
    """Name the last workflow outcome a flock session reported before going quiet."""
    metadata = getattr(session, "activity_metadata", None) or {}
    last = metadata.get("flock_idle") if isinstance(metadata, dict) else None
    if not isinstance(last, dict) or not last.get("event_type"):
        return ""
    verdict = f" (verdict {last['verdict']})" if last.get("verdict") else ""
    persona = f" from {last['persona']}" if last.get("persona") else ""
    return (
        f" The last workflow outcome was {last['event_type']}{verdict}{persona}; "
        "no stage was working after it."
    )


_PENDING_BLOCKER_STATUSES = frozenset({"", "pending", "open", "waiting", "help_needed", "blocked"})

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


def _with_delivery(campaign: WorkflowCampaign, activity_metadata: dict) -> dict:
    metadata = dict(campaign.metadata)
    envelope = activity_metadata.get("delivery")
    if isinstance(envelope, dict):
        metadata["delivery"] = envelope
    return metadata


def _campaign_completion_event(campaign: WorkflowCampaign) -> str:
    """Return the event type the campaign's own pinned graph names for completion.

    Read from the graph's ``end`` node rather than assumed, so this projector
    works for whatever workflow the campaign is running. An empty result means
    the campaign's workflow declares no completion event, so the authoritative
    fast path simply does not apply to it.
    """
    snapshot = campaign.workflow_snapshot
    graph = snapshot.get("graph") if isinstance(snapshot, dict) else None
    if not isinstance(graph, dict):
        return ""
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and node.get("kind") == "end":
            event_type = str(node.get("completionEvent") or "").strip()
            if event_type:
                return event_type
    return ""


def _authoritative_completion(activity_metadata: dict, *, completion_event: str) -> dict | None:
    """Validate a workflow terminal outcome before completing its campaign."""
    if not completion_event:
        return None
    if activity_metadata.get("completion_source") != "ravn_flock":
        return None
    if activity_metadata.get("completion_event_type") != completion_event:
        return None
    if not str(activity_metadata.get("completion_peer_id") or "").startswith("workflow-stop:"):
        return None

    structured = activity_metadata.get("structured_outcome")
    if not isinstance(structured, dict) or not structured:
        return None
    structured_payload = (
        structured["outcome"] if isinstance(structured.get("outcome"), dict) else structured
    )
    if not isinstance(structured_payload, dict) or not structured_payload:
        return None
    if not bool(structured_payload.get("authoritative") or activity_metadata.get("outcome_valid")):
        return None
    return structured_payload


def _authoritative_delivery(activity_metadata: dict, *, completion_event: str) -> dict | None:
    """Extract an optional attested delivery result from a terminal outcome."""
    structured_payload = _authoritative_completion(
        activity_metadata, completion_event=completion_event
    )
    if structured_payload is None:
        return None

    envelope = activity_metadata.get("delivery")
    if not isinstance(envelope, dict) or envelope.get("schemaVersion") != 1:
        return None
    result = envelope.get("result")
    reviews = envelope.get("reviews")
    if not isinstance(result, dict) or not result or not isinstance(reviews, list):
        return None
    if structured_payload.get("result") != result:
        return None
    return envelope


def _has_pending_entry(entries: list, status_key: str = "status") -> bool:
    return any(
        isinstance(entry, dict)
        and str(entry.get(status_key) or "").strip().lower() in _PENDING_BLOCKER_STATUSES
        for entry in entries
    )


def _status_from_session(
    session_status: str,
    *,
    fallback: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
) -> WorkflowCampaignStatus:
    normalized = session_status.strip().lower()
    if normalized in {"creating", "starting", "queued"}:
        return WorkflowCampaignStatus.PENDING
    if normalized in {"running", "active", "busy"}:
        return WorkflowCampaignStatus.RUNNING
    if normalized in {"blocked", "waiting", "paused", "stopped"}:
        return WorkflowCampaignStatus.BLOCKED
    if normalized in {"completed", "complete", "succeeded", "success"}:
        return WorkflowCampaignStatus.COMPLETED
    if normalized in {"failed", "error", "cancelled", "canceled"}:
        return WorkflowCampaignStatus.FAILED
    return fallback
