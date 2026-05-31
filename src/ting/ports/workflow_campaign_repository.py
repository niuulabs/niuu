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
