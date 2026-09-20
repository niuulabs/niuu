"""Runtime lifecycle helpers for A2A workflow campaigns."""

from __future__ import annotations

import logging
from dataclasses import replace

from niuu.domain.models import Principal
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.ports.volundr import VolundrPort
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository

logger = logging.getLogger(__name__)

TERMINAL_SESSION_STOPPED_KEY = "terminal_session_stopped"
_TERMINAL_STATUSES = frozenset({WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED})


def terminal_session_cleanup_needed(campaign: WorkflowCampaign) -> bool:
    """Return whether a terminal A2A campaign still owns a runtime session."""
    return campaign.status in _TERMINAL_STATUSES and not campaign.metadata.get(
        TERMINAL_SESSION_STOPPED_KEY
    )


async def stop_terminal_campaign_session(
    campaign: WorkflowCampaign,
    *,
    adapter: VolundrPort,
    repo: WorkflowCampaignRepository,
    auth_token: str | None = None,
    principal: Principal | None = None,
) -> WorkflowCampaign | None:
    """Stop a terminal campaign session and durably record the idempotent cleanup.

    The terminal campaign, including its result and review envelope, must already
    be persisted before this helper is called.  ``None`` means cleanup remains
    pending and callers that expose terminal state should ask the client to retry.
    """
    if not terminal_session_cleanup_needed(campaign):
        return campaign

    try:
        await adapter.stop_session(
            campaign.session_id,
            auth_token=auth_token,
            principal=principal,
        )
    except Exception:
        logger.warning(
            "Could not stop terminal A2A campaign session %s",
            campaign.session_id,
            exc_info=True,
        )
        return None

    cleaned = replace(
        campaign,
        metadata={**campaign.metadata, TERMINAL_SESSION_STOPPED_KEY: True},
    )
    try:
        return await repo.save_campaign(cleaned)
    except Exception:
        # Repeating the non-destructive stop request is safe: the HTTP adapter
        # confirms an already-stopped conflict with an authoritative GET. Leave
        # the receipt absent and let a later GetTask persist it.
        logger.warning(
            "Could not record terminal A2A campaign cleanup for %s",
            campaign.session_id,
            exc_info=True,
        )
        return None
