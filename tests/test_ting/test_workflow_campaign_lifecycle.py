"""Tests for the A2A workflow campaign runtime lifecycle helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.workflow_campaign_lifecycle import (
    TERMINAL_SESSION_STOPPED_KEY,
    stop_terminal_campaign_session,
    terminal_session_cleanup_needed,
)


def _campaign(
    *,
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.COMPLETED,
    metadata: dict[str, Any] | None = None,
) -> WorkflowCampaign:
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug="task-1",
        name="task-1",
        owner_id="user-1",
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="workflow",
        workflow_snapshot={"graph": {"nodes": [], "edges": []}},
        session_id="session-123",
        session_name="task-1",
        status=status,
        active_stage_id=None,
        stage_state=[],
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )


def test_terminal_session_cleanup_needed_true_for_completed_without_stopped_flag() -> None:
    campaign = _campaign(status=WorkflowCampaignStatus.COMPLETED)
    assert terminal_session_cleanup_needed(campaign) is True


def test_terminal_session_cleanup_needed_true_for_failed_without_stopped_flag() -> None:
    campaign = _campaign(status=WorkflowCampaignStatus.FAILED)
    assert terminal_session_cleanup_needed(campaign) is True


def test_terminal_session_cleanup_needed_false_once_flag_recorded() -> None:
    campaign = _campaign(
        status=WorkflowCampaignStatus.COMPLETED,
        metadata={TERMINAL_SESSION_STOPPED_KEY: True},
    )
    assert terminal_session_cleanup_needed(campaign) is False


def test_terminal_session_cleanup_needed_false_for_non_terminal_status() -> None:
    campaign = _campaign(status=WorkflowCampaignStatus.RUNNING)
    assert terminal_session_cleanup_needed(campaign) is False


@pytest.mark.asyncio
async def test_stop_terminal_campaign_session_returns_campaign_unchanged_when_not_needed() -> None:
    campaign = _campaign(
        status=WorkflowCampaignStatus.RUNNING,
    )
    adapter = AsyncMock()
    repo = AsyncMock()

    result = await stop_terminal_campaign_session(campaign, adapter=adapter, repo=repo)

    assert result is campaign
    adapter.stop_session.assert_not_called()
    repo.save_campaign.assert_not_called()


@pytest.mark.asyncio
async def test_stop_terminal_campaign_session_stops_and_records_cleanup() -> None:
    campaign = _campaign(status=WorkflowCampaignStatus.COMPLETED)
    adapter = AsyncMock()
    repo = AsyncMock()
    repo.save_campaign.side_effect = lambda saved: saved

    result = await stop_terminal_campaign_session(
        campaign,
        adapter=adapter,
        repo=repo,
        auth_token="token-1",
    )

    adapter.stop_session.assert_awaited_once_with(
        campaign.session_id,
        auth_token="token-1",
        principal=None,
    )
    assert result is not None
    assert result.metadata[TERMINAL_SESSION_STOPPED_KEY] is True
    saved_arg = repo.save_campaign.await_args.args[0]
    assert saved_arg.metadata[TERMINAL_SESSION_STOPPED_KEY] is True


@pytest.mark.asyncio
async def test_stop_terminal_campaign_session_returns_none_when_adapter_raises() -> None:
    campaign = _campaign(status=WorkflowCampaignStatus.FAILED)
    adapter = AsyncMock()
    adapter.stop_session.side_effect = RuntimeError("connection refused")
    repo = AsyncMock()

    result = await stop_terminal_campaign_session(campaign, adapter=adapter, repo=repo)

    assert result is None
    repo.save_campaign.assert_not_called()


@pytest.mark.asyncio
async def test_stop_terminal_campaign_session_returns_none_when_save_raises() -> None:
    campaign = _campaign(status=WorkflowCampaignStatus.COMPLETED)
    adapter = AsyncMock()
    repo = AsyncMock()
    repo.save_campaign.side_effect = RuntimeError("db unavailable")

    result = await stop_terminal_campaign_session(campaign, adapter=adapter, repo=repo)

    assert result is None
    adapter.stop_session.assert_awaited_once()
