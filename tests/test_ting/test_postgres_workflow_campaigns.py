"""PostgreSQL workflow campaign repository contract tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ting.adapters.postgres_workflow_campaigns import PostgresWorkflowCampaignRepository
from ting.domain.models import CampaignStageState, WorkflowCampaign, WorkflowCampaignStatus


def _campaign() -> WorkflowCampaign:
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug="tool-build-test",
        name="Tool build test",
        owner_id="owner-1",
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="Tool Builder",
        workflow_snapshot={"nodes": []},
        session_id="session-1",
        session_name="tool-build-test",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id="build",
        stage_state=[
            CampaignStageState(
                stage_id="build",
                label="Build",
                status="running",
                started_at=now,
                completed_at=None,
                reason=None,
            )
        ],
        metadata={"source": "a2a"},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        completed_at=None,
        connection_id="Noatun",
    )


def _row(campaign: WorkflowCampaign) -> dict:
    stage = campaign.stage_state[0]
    return {
        "id": campaign.id,
        "slug": campaign.slug,
        "name": campaign.name,
        "owner_id": campaign.owner_id,
        "workflow_id": campaign.workflow_id,
        "workflow_version": campaign.workflow_version,
        "workflow_name": campaign.workflow_name,
        "workflow_snapshot": json.dumps(campaign.workflow_snapshot),
        "session_id": campaign.session_id,
        "session_name": campaign.session_name,
        "status": campaign.status.value,
        "active_stage_id": campaign.active_stage_id,
        "stage_state": json.dumps(
            [
                {
                    "stage_id": stage.stage_id,
                    "label": stage.label,
                    "status": stage.status,
                    "started_at": stage.started_at.isoformat() if stage.started_at else None,
                    "completed_at": None,
                    "reason": None,
                }
            ]
        ),
        "metadata": json.dumps(campaign.metadata),
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
        "last_activity_at": campaign.last_activity_at,
        "completed_at": campaign.completed_at,
        "connection_id": campaign.connection_id,
    }


@pytest.mark.asyncio
async def test_lists_campaigns_and_active_owners() -> None:
    campaign = _campaign()
    pool = AsyncMock()
    pool.fetch.side_effect = [[_row(campaign)], [_row(campaign)], [{"owner_id": "owner-1"}]]
    repo = PostgresWorkflowCampaignRepository(pool)

    listed = await repo.list_campaigns(owner_id="owner-1")
    active = await repo.list_active_campaigns()
    owners = await repo.list_active_owner_ids()

    assert listed == [campaign]
    assert active == [campaign]
    assert owners == ["owner-1"]


@pytest.mark.asyncio
async def test_gets_active_campaign_by_owner_and_session() -> None:
    campaign = _campaign()
    pool = AsyncMock()
    pool.fetchrow.side_effect = [_row(campaign), None]
    repo = PostgresWorkflowCampaignRepository(pool)

    found = await repo.get_active_campaign_by_session(
        owner_id=campaign.owner_id,
        session_id=campaign.session_id,
    )
    missing = await repo.get_active_campaign_by_session(
        owner_id=campaign.owner_id,
        session_id="missing",
    )

    assert found == campaign
    assert missing is None
    assert pool.fetchrow.await_args_list[0].args[-2:] == (
        campaign.owner_id,
        campaign.session_id,
    )


@pytest.mark.asyncio
async def test_gets_campaign_by_id_and_scoped_or_unscoped_slug() -> None:
    campaign = _campaign()
    pool = AsyncMock()
    pool.fetchrow.side_effect = [
        _row(campaign),
        None,
        _row(campaign),
        _row(campaign),
        None,
    ]
    repo = PostgresWorkflowCampaignRepository(pool)

    assert await repo.get_campaign(campaign.id) == campaign
    assert await repo.get_campaign(uuid4()) is None
    assert await repo.get_campaign_by_slug(campaign.slug) == campaign
    assert await repo.get_campaign_by_slug(campaign.slug, owner_id=campaign.owner_id) == campaign
    assert await repo.get_campaign_by_slug("missing", owner_id=campaign.owner_id) is None


@pytest.mark.asyncio
async def test_saves_and_deletes_campaign() -> None:
    campaign = _campaign()
    pool = AsyncMock()
    pool.execute.side_effect = ["INSERT 0 1", "DELETE 1", "DELETE 0"]
    repo = PostgresWorkflowCampaignRepository(pool)

    assert await repo.save_campaign(campaign) == campaign
    assert await repo.delete_campaign(campaign.id) is True
    assert await repo.delete_campaign(uuid4()) is False

    save_args = pool.execute.await_args_list[0].args
    assert save_args[1] == campaign.id
    assert json.loads(save_args[8]) == campaign.workflow_snapshot
    assert json.loads(save_args[13])[0]["stage_id"] == "build"
    assert json.loads(save_args[14]) == campaign.metadata
