"""Persistence port for trackerless workflow campaign records."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from ting.domain.models import WorkflowCampaign
from ting.domain.ticket_coordinator import ticket_of


class CampaignAlreadyTerminalError(RuntimeError):
    """A write tried to reopen a campaign that already completed or failed.

    Terminal states are final. A writer holding an older copy (an activity
    event racing CancelTask, say) loses: its write is refused, not merged.
    """


class WorkflowCampaignRepository(ABC):
    """Persistence interface for launched workflow campaigns."""

    async def save_ticket_campaign(
        self,
        campaign: WorkflowCampaign,
        *,
        saga_id: UUID,
        issue_id: str,
    ) -> str | None:
        """Save a ticket's new run unless another is active; return that other run's slug.

        The check and the save are one atomic step, so two launches for the same
        ticket cannot both pass.
        """
        raise NotImplementedError(f"{type(self).__name__} cannot guard ticket launches")

    @abstractmethod
    async def list_campaigns(self, *, owner_id: str) -> list[WorkflowCampaign]:
        """List campaigns visible to the owner."""

    async def list_ticket_campaigns(
        self,
        *,
        owner_id: str,
        saga_id: UUID | None = None,
        issue_id: str | None = None,
    ) -> list[WorkflowCampaign]:
        """List the owner's campaigns tied to a ticket, oldest first, narrowed when asked.

        An owner can hold hundreds of campaigns, each with its workflow snapshot,
        so a board read must not load them all.
        """
        campaigns = [
            campaign
            for campaign in await self.list_campaigns(owner_id=owner_id)
            if (ticket := ticket_of(campaign.metadata)) is not None
            and (saga_id is None or ticket[0] == str(saga_id))
            and (issue_id is None or ticket[1] == issue_id)
        ]
        return sorted(campaigns, key=lambda campaign: campaign.created_at)

    @abstractmethod
    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        """List campaigns that may still change at runtime."""

    async def list_active_owner_ids(self) -> list[str]:
        """List owners whose campaigns require an activity subscription."""
        campaigns = await self.list_active_campaigns()
        return sorted({campaign.owner_id for campaign in campaigns})

    async def get_active_campaign_by_session(
        self,
        *,
        owner_id: str,
        session_id: str,
        connection_id: str | None = None,
    ) -> WorkflowCampaign | None:
        """Fetch the active campaign associated with one Volundr session."""
        campaigns = await self.list_active_campaigns()
        matches = [
            campaign
            for campaign in campaigns
            if campaign.owner_id == owner_id
            and campaign.session_id == session_id
            and (connection_id is None or campaign.connection_id == connection_id)
        ]
        return matches[0] if len(matches) == 1 else None

    @abstractmethod
    async def get_campaign(self, campaign_id: UUID) -> WorkflowCampaign | None:
        """Fetch a campaign by UUID."""

    @abstractmethod
    async def get_campaign_by_slug(
        self,
        slug: str,
        *,
        owner_id: str | None = None,
    ) -> WorkflowCampaign | None:
        """Fetch a campaign by slug, optionally scoped to an owner."""

    @abstractmethod
    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        """Insert or update a campaign.

        Raises :class:`CampaignAlreadyTerminalError` when the stored campaign is
        completed or failed and this write would make it active again.
        """

    @abstractmethod
    async def delete_campaign(self, campaign_id: UUID) -> bool:
        """Delete a campaign record by UUID."""
