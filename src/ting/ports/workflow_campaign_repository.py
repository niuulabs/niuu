"""Persistence port for trackerless workflow campaign records."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from ting.domain.models import WorkflowCampaign


class WorkflowCampaignRepository(ABC):
    """Persistence interface for launched workflow campaigns."""

    @abstractmethod
    async def list_campaigns(self, *, owner_id: str) -> list[WorkflowCampaign]:
        """List campaigns visible to the owner."""

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
    ) -> WorkflowCampaign | None:
        """Fetch the active campaign associated with one Volundr session."""
        campaigns = await self.list_active_campaigns()
        return next(
            (
                campaign
                for campaign in campaigns
                if campaign.owner_id == owner_id and campaign.session_id == session_id
            ),
            None,
        )

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
        """Insert or update a campaign."""

    @abstractmethod
    async def delete_campaign(self, campaign_id: UUID) -> bool:
        """Delete a campaign record by UUID."""
