"""Event-driven projection of Volundr workflow sessions into A2A campaigns."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime

from niuu.domain.models import Principal
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.workflow_campaign_lifecycle import (
    stop_terminal_campaign_session,
    terminal_session_cleanup_needed,
)
from ting.ports.a2a_push import A2APushDispatcherPort
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.volundr import ActivityEvent, VolundrFactory
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository

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
    ) -> None:
        self._repo = repo
        self._volundr_factory = volundr_factory
        self._event_bus = event_bus
        self._push_dispatcher = push_dispatcher
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        logger.info("Event-driven workflow campaign projector started")

    async def stop(self) -> None:
        self._running = False
        logger.info("Event-driven workflow campaign projector stopped")

    async def list_active_owner_ids(self) -> list[str]:
        return await self._repo.list_active_owner_ids()

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

    async def handle_activity(self, event: ActivityEvent, owner_id: str) -> bool:
        """Apply one Volundr stream event; return whether it was a campaign event."""
        campaign = await self._active_campaign(owner_id, event.session_id)
        if campaign is None:
            return False

        delivery = _authoritative_delivery(
            event.metadata, completion_event=_campaign_completion_event(campaign)
        )
        if event.state == "error" or event.session_status in {"failed", "cancelled", "canceled"}:
            error = str(event.metadata.get("error") or event.metadata.get("message") or "").strip()
            if not error:
                error = f"Session {event.session_status or event.state or 'failed'}"
            await self._save_transition(
                campaign,
                WorkflowCampaignStatus.FAILED,
                failure_error=error,
            )
        elif delivery is not None:
            await self._save_transition(
                campaign,
                WorkflowCampaignStatus.COMPLETED,
                metadata={**campaign.metadata, "delivery": delivery},
            )
        elif event.session_status == "stopped":
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
        return True

    async def record_help_needed(
        self,
        payload: dict[str, object],
        owner_id: str,
        *,
        session_id: str,
        gate: dict[str, object],
    ) -> bool:
        """Persist an input-required event and immediately notify A2A callbacks."""
        campaign = await self._active_campaign(owner_id, session_id)
        if campaign is None:
            return False

        metadata = dict(campaign.metadata)
        metadata["pending_workflow_gates"] = [gate]
        metadata["latest_help_needed"] = dict(payload)
        await self._save_transition(
            campaign,
            WorkflowCampaignStatus.BLOCKED,
            metadata=metadata,
            active_stage_id=str(gate.get("node_id") or campaign.active_stage_id or "") or None,
            force=True,
        )
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

    async def _active_campaign(self, owner_id: str, session_id: str) -> WorkflowCampaign | None:
        return await self._repo.get_active_campaign_by_session(
            owner_id=owner_id,
            session_id=session_id,
        )

    async def _refresh_campaign(self, campaign: WorkflowCampaign) -> None:
        """One-shot reconnect reconciliation; never called on a timer."""
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
        if (
            next_status != WorkflowCampaignStatus.FAILED
            and _authoritative_delivery(
                activity_metadata, completion_event=_campaign_completion_event(campaign)
            )
            is not None
        ):
            next_status = WorkflowCampaignStatus.COMPLETED
        elif next_status == WorkflowCampaignStatus.RUNNING and await self._session_awaits_input(
            adapter, campaign.session_id
        ):
            next_status = WorkflowCampaignStatus.BLOCKED

        failure_error = ""
        if next_status == WorkflowCampaignStatus.FAILED:
            failure_error = str(
                activity_metadata.get("error") or activity_metadata.get("message") or ""
            ).strip()
        await self._save_transition(
            campaign,
            next_status,
            session_name=session.name,
            failure_error=failure_error,
            metadata=_with_delivery(campaign, activity_metadata),
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

    async def _save_transition(
        self,
        campaign: WorkflowCampaign,
        next_status: WorkflowCampaignStatus,
        *,
        session_name: str | None = None,
        failure_error: str = "",
        metadata: dict | None = None,
        active_stage_id: str | None = None,
        force: bool = False,
    ) -> None:
        resolved_name = session_name or campaign.session_name
        if not force and next_status == campaign.status and resolved_name == campaign.session_name:
            return

        now = datetime.now(UTC)
        resolved_metadata = dict(campaign.metadata) if metadata is None else metadata
        if failure_error:
            resolved_metadata["failure_error"] = failure_error
        saved = await self._repo.save_campaign(
            replace(
                campaign,
                session_name=resolved_name,
                status=next_status,
                metadata=resolved_metadata,
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
                    },
                )
            )
            if self._push_dispatcher is not None:
                await self._push_dispatcher.queue_campaign(saved)
        finally:
            if terminal_session_cleanup_needed(saved):
                await self.cleanup_terminal_session(saved)


_PENDING_BLOCKER_STATUSES = frozenset({"", "pending", "open", "waiting", "help_needed", "blocked"})


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


def _authoritative_delivery(activity_metadata: dict, *, completion_event: str) -> dict | None:
    """Extract Skuld's terminal result without trusting generic idle metadata."""
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
