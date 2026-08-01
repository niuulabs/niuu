"""Tests for the workflow campaign projector's blocked-state derivation."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.ports.volundr import ActivityEvent


def _campaign(
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
    connection_id: str | None = None,
) -> WorkflowCampaign:
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
        connection_id=connection_id,
    )


class _Adapter:
    def __init__(
        self,
        *,
        session_status: str = "running",
        help_requests: list | None = None,
        gates: list | None = None,
        blocker_error: Exception | None = None,
        activity_state: str | None = None,
        activity_metadata: dict | None = None,
    ) -> None:
        self._session_status = session_status
        self._help_requests = help_requests or []
        self._gates = gates or []
        self._blocker_error = blocker_error
        self._activity_state = activity_state
        self._activity_metadata = activity_metadata or {}

    async def get_session(self, session_id, *, auth_token=None, principal=None):
        return SimpleNamespace(
            status=self._session_status,
            name="tool-build-test",
            activity_state=self._activity_state,
            activity_metadata=self._activity_metadata,
        )

    async def get_help_requests(self, session_id, *, auth_token=None, principal=None):
        if self._blocker_error is not None:
            raise self._blocker_error
        return list(self._help_requests)

    async def get_workflow_gates(self, session_id, *, auth_token=None, principal=None):
        if self._blocker_error is not None:
            raise self._blocker_error
        return list(self._gates)


class _Factory:
    def __init__(
        self,
        adapter: _Adapter,
        *,
        connections: dict[str, _Adapter] | None = None,
    ) -> None:
        self._adapter = adapter
        self._connections = connections or {}
        self.primary_calls = 0
        self.connection_calls: list[str] = []

    async def primary_for_owner(self, owner_id: str):
        self.primary_calls += 1
        return self._adapter

    async def for_connection(self, owner_id: str, connection_id: str):
        self.connection_calls.append(connection_id)
        return self._connections.get(connection_id)


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


@pytest.mark.asyncio
async def test_terminal_activity_error_fails_running_campaign() -> None:
    adapter = _Adapter(
        session_status="running",
        activity_state="error",
        activity_metadata={"error": "refresh token was already used"},
        blocker_error=RuntimeError("must not be called"),
    )
    projector, repo, event_bus = _projector(adapter)

    await projector._refresh_campaign(_campaign())

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.FAILED
    assert saved.metadata["failure_error"] == "refresh token was already used"
    emitted = event_bus.emit.await_args.args[0]
    assert emitted.event == "workflow.campaign.failed"
    assert emitted.data["error"] == "refresh token was already used"


@pytest.mark.asyncio
async def test_sse_terminal_event_completes_and_queues_push_without_session_read() -> None:
    adapter = _Adapter()
    campaign = _campaign()
    repo = AsyncMock()
    repo.get_active_campaign_by_session.return_value = campaign
    repo.save_campaign = AsyncMock(side_effect=lambda value: value)
    push_dispatcher = AsyncMock()
    projector = WorkflowCampaignProjector(
        repo=repo,
        volundr_factory=_Factory(adapter),
        event_bus=AsyncMock(),
        push_dispatcher=push_dispatcher,
    )

    handled = await projector.handle_activity(
        ActivityEvent(
            session_id=campaign.session_id,
            state="",
            metadata={},
            owner_id=campaign.owner_id,
            session_status="stopped",
        ),
        campaign.owner_id,
    )

    assert handled is True
    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.COMPLETED
    push_dispatcher.queue_campaign.assert_awaited_once_with(saved)


@pytest.mark.asyncio
async def test_sse_error_event_fails_and_queues_error_push() -> None:
    campaign = _campaign()
    repo = AsyncMock()
    repo.get_active_campaign_by_session.return_value = campaign
    repo.save_campaign = AsyncMock(side_effect=lambda value: value)
    event_bus = AsyncMock()
    push_dispatcher = AsyncMock()
    projector = WorkflowCampaignProjector(
        repo=repo,
        volundr_factory=_Factory(_Adapter()),
        event_bus=event_bus,
        push_dispatcher=push_dispatcher,
    )

    await projector.handle_activity(
        ActivityEvent(
            session_id=campaign.session_id,
            state="error",
            metadata={"error": "refresh token was already used"},
            owner_id=campaign.owner_id,
        ),
        campaign.owner_id,
    )

    saved = repo.save_campaign.await_args.args[0]
    assert saved.status == WorkflowCampaignStatus.FAILED
    assert saved.metadata["failure_error"] == "refresh token was already used"
    assert event_bus.emit.await_args.args[0].event == "workflow.campaign.failed"
    push_dispatcher.queue_campaign.assert_awaited_once_with(saved)


class TestConnectionAffinity:
    """Campaign reads must target the connection the session launched on."""

    @staticmethod
    def _build(factory) -> tuple[WorkflowCampaignProjector, AsyncMock]:
        repo = AsyncMock()
        repo.save_campaign = AsyncMock(side_effect=lambda campaign: campaign)
        return (
            WorkflowCampaignProjector(repo=repo, volundr_factory=factory, event_bus=AsyncMock()),
            repo,
        )

    @pytest.mark.asyncio
    async def test_campaign_connection_is_preferred_over_primary(self) -> None:
        valhalla = _Adapter(session_status="running")
        primary = _Adapter(session_status="stopped")  # would wrongly complete the campaign
        factory = _Factory(primary, connections={"conn-valhalla": valhalla})
        projector, repo = self._build(factory)

        await projector._refresh_campaign(
            _campaign(status=WorkflowCampaignStatus.PENDING, connection_id="conn-valhalla")
        )

        assert factory.connection_calls == ["conn-valhalla"]
        assert factory.primary_calls == 0
        saved = repo.save_campaign.await_args.args[0]
        assert saved.status == WorkflowCampaignStatus.RUNNING

    @pytest.mark.asyncio
    async def test_vanished_connection_does_not_retarget_primary(self) -> None:
        primary = _Adapter(session_status="running")
        factory = _Factory(primary, connections={})
        projector, _ = self._build(factory)

        await projector._refresh_campaign(_campaign(connection_id="conn-gone"))

        assert factory.connection_calls == ["conn-gone"]
        assert factory.primary_calls == 0

    @pytest.mark.asyncio
    async def test_legacy_campaign_without_connection_uses_primary(self) -> None:
        primary = _Adapter(session_status="running")
        factory = _Factory(primary)
        projector, _ = self._build(factory)

        await projector._refresh_campaign(_campaign())

        assert factory.connection_calls == []
        assert factory.primary_calls == 1
