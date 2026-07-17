"""Tests for the workflow campaign projector's blocked-state derivation."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector


def _campaign(status: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING) -> WorkflowCampaign:
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug="tool-build-test",
        name="tool-build-test",
        owner_id="user-1",
        workflow_id=uuid4(),
        workflow_name="Tool & Skill Builder",
        workflow_version="1.0.0",
        workflow_snapshot={},
        session_id="session-123",
        session_name="tool-build-test",
        status=status,
        active_stage_id=None,
        stage_state=[],
        metadata={},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        completed_at=None,
    )


class _Adapter:
    def __init__(
        self,
        *,
        session_status: str = "running",
        help_requests: list | None = None,
        gates: list | None = None,
        blocker_error: Exception | None = None,
    ) -> None:
        self._session_status = session_status
        self._help_requests = help_requests or []
        self._gates = gates or []
        self._blocker_error = blocker_error

    async def get_session(self, session_id, *, auth_token=None, principal=None):
        return SimpleNamespace(status=self._session_status, name="tool-build-test")

    async def get_help_requests(self, session_id, *, auth_token=None, principal=None):
        if self._blocker_error is not None:
            raise self._blocker_error
        return list(self._help_requests)

    async def get_workflow_gates(self, session_id, *, auth_token=None, principal=None):
        if self._blocker_error is not None:
            raise self._blocker_error
        return list(self._gates)


class _Factory:
    def __init__(self, adapter: _Adapter) -> None:
        self._adapter = adapter

    async def primary_for_owner(self, owner_id: str):
        return self._adapter


def _projector(adapter: _Adapter) -> tuple[WorkflowCampaignProjector, AsyncMock, AsyncMock]:
    repo = AsyncMock()
    repo.save_campaign = AsyncMock(side_effect=lambda campaign: campaign)
    event_bus = AsyncMock()
    projector = WorkflowCampaignProjector(
        repo=repo,
        volundr_factory=_Factory(adapter),
        event_bus=event_bus,
    )
    return projector, repo, event_bus


@pytest.mark.asyncio
async def test_pending_help_request_flips_campaign_to_blocked() -> None:
    adapter = _Adapter(help_requests=[{"id": "help-1", "status": "pending"}])
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.BLOCKED


@pytest.mark.asyncio
async def test_pending_gate_flips_campaign_to_blocked() -> None:
    adapter = _Adapter(gates=[{"id": "gate-1", "status": "pending"}])
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.BLOCKED


@pytest.mark.asyncio
async def test_answered_blockers_resume_campaign() -> None:
    adapter = _Adapter(
        help_requests=[{"id": "help-1", "status": "answered"}],
        gates=[{"id": "gate-1", "status": "resolved"}],
    )
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign(WorkflowCampaignStatus.BLOCKED))

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.RUNNING


@pytest.mark.asyncio
async def test_no_blockers_keeps_running_without_save() -> None:
    adapter = _Adapter()
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    repo.save_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocker_fetch_failure_reads_as_not_blocked() -> None:
    adapter = _Adapter(blocker_error=RuntimeError("session pod unreachable"))
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    repo.save_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_session_completes_without_blocker_checks() -> None:
    adapter = _Adapter(
        session_status="stopped",
        blocker_error=RuntimeError("must not be called"),
    )
    projector, repo, _ = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.COMPLETED
