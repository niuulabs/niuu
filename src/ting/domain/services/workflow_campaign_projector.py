"""Background projector that keeps workflow campaigns in sync with Volundr."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository

logger = logging.getLogger(__name__)


class WorkflowCampaignProjector:
    """Poll active workflow campaigns and emit generic campaign events."""

    def __init__(
        self,
        *,
        repo: WorkflowCampaignRepository,
        volundr_factory: VolundrFactory,
        event_bus: EventBusPort,
        interval_seconds: float = 15.0,
    ) -> None:
        self._repo = repo
        self._volundr_factory = volundr_factory
        self._event_bus = event_bus
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="workflow-campaign-projector")
        logger.info("Workflow campaign projector started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Workflow campaign projector stopped")

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._tick()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    async def _tick(self) -> None:
        campaigns = await self._repo.list_active_campaigns()
        for campaign in campaigns:
            try:
                await self._refresh_campaign(campaign)
            except Exception:
                logger.warning(
                    "Workflow campaign projector failed for campaign %s",
                    campaign.slug,
                    exc_info=True,
                )

    async def _refresh_campaign(self, campaign: WorkflowCampaign) -> None:
        adapter = await self._volundr_factory.primary_for_owner(campaign.owner_id)
        if adapter is None:
            return
        session = await adapter.get_session(campaign.session_id)
        if session is None:
            return
        next_status = _status_from_session(session.status, fallback=campaign.status)
        if next_status == campaign.status and session.name == campaign.session_name:
            return

        now = datetime.now(UTC)
        updated = WorkflowCampaign(
            **{
                **campaign.__dict__,
                "session_name": session.name,
                "status": next_status,
                "updated_at": now,
                "last_activity_at": now,
                "completed_at": now
                if next_status == WorkflowCampaignStatus.COMPLETED and campaign.completed_at is None
                else campaign.completed_at,
            }
        )
        saved = await self._repo.save_campaign(updated)
        event = "workflow.campaign.updated"
        if next_status == WorkflowCampaignStatus.COMPLETED:
            event = "workflow.campaign.completed"
        elif next_status == WorkflowCampaignStatus.FAILED:
            event = "workflow.campaign.failed"
        await self._event_bus.emit(
            TingEvent(
                event=event,
                owner_id=saved.owner_id,
                data={
                    "campaign_id": str(saved.id),
                    "slug": saved.slug,
                    "name": saved.name,
                    "status": saved.status.value,
                    "session_id": saved.session_id,
                    "workflow_id": str(saved.workflow_id),
                    "active_stage_id": saved.active_stage_id,
                },
            )
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
    if normalized in {"blocked", "waiting", "paused"}:
        return WorkflowCampaignStatus.BLOCKED
    if normalized in {"stopped", "completed", "complete", "succeeded", "success"}:
        return WorkflowCampaignStatus.COMPLETED
    if normalized in {"failed", "error", "cancelled", "canceled"}:
        return WorkflowCampaignStatus.FAILED
    return fallback
