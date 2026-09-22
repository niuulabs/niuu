"""Coverage for the WorkflowCampaignRepository port's default mixin method.

``list_active_owner_ids`` has a real default implementation on the port
(derive distinct owners from ``list_active_campaigns``); concrete adapters in
this codebase override it with a direct query, so the base implementation
itself is otherwise never exercised. This test uses a minimal fake that
implements only the abstract methods and leaves the default in place.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository


def _campaign(owner_id: str) -> WorkflowCampaign:
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug=f"slug-{owner_id}",
        name="campaign",
        owner_id=owner_id,
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="workflow",
        workflow_snapshot={},
        session_id="session-1",
        session_name="campaign",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id=None,
        stage_state=[],
        metadata={},
        created_at=now,
        updated_at=now,
    )


class _FakeWorkflowCampaignRepository(WorkflowCampaignRepository):
    """Implements only the abstract methods, so defaults are exercised as written."""

    def __init__(self, active_campaigns: list[WorkflowCampaign]) -> None:
        self._active_campaigns = active_campaigns

    async def list_campaigns(self, *, owner_id: str) -> list[WorkflowCampaign]:
        raise NotImplementedError

    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        return self._active_campaigns

    async def get_campaign(self, campaign_id: UUID) -> WorkflowCampaign | None:
        raise NotImplementedError

    async def get_campaign_by_slug(
        self, slug: str, *, owner_id: str | None = None
    ) -> WorkflowCampaign | None:
        raise NotImplementedError

    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        raise NotImplementedError

    async def delete_campaign(self, campaign_id: UUID) -> bool:
        raise NotImplementedError


async def test_list_active_owner_ids_dedupes_and_sorts_owners() -> None:
    repo = _FakeWorkflowCampaignRepository(
        [_campaign("zed"), _campaign("alice"), _campaign("alice")]
    )

    owner_ids = await repo.list_active_owner_ids()

    assert owner_ids == ["alice", "zed"]


async def test_list_active_owner_ids_returns_empty_list_without_active_campaigns() -> None:
    repo = _FakeWorkflowCampaignRepository([])

    assert await repo.list_active_owner_ids() == []
