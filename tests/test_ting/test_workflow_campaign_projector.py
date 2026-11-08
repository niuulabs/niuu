"""The campaign projector writes a run's ledger projection onto its campaign."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from ting.domain.campaign_run_projection import RunProjection
from ting.domain.models import CampaignStageState, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.ticket_coordinator import TICKET_METADATA_KEY
from ting.ports.workflow_campaign_repository import CampaignAlreadyTerminalError

_HOUR = 3600.0


def _campaign(
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
    *,
    age_seconds: float = 0.0,
    metadata: dict | None = None,
) -> WorkflowCampaign:
    moment = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return WorkflowCampaign(
        id=uuid4(),
        slug="build-test",
        name="build-test",
        owner_id="user-1",
        workflow_id=uuid4(),
        workflow_name="Build",
        workflow_version="1.0.0",
        workflow_snapshot={},
        session_id="",
        session_name="",
        status=status,
        active_stage_id=None,
        stage_state=[],
        metadata=metadata or {},
        created_at=moment,
        updated_at=moment,
        last_activity_at=moment,
        completed_at=None,
        connection_id="cluster-1",
    )


def _projection(campaign: WorkflowCampaign, **changes) -> RunProjection:
    values = {
        "status": campaign.status,
        "active_stage_id": campaign.active_stage_id,
        "stage_state": campaign.stage_state,
        "metadata": dict(campaign.metadata),
    }
    values.update(changes)
    return RunProjection(**values)


class _Launched:
    def __init__(self, launched: bool) -> None:
        self.launched = launched
        self.asked: list[UUID] = []

    async def __call__(self, run_id: UUID) -> bool:
        self.asked.append(run_id)
        return self.launched


def _projector(
    *,
    launched: bool = True,
    campaigns: list[WorkflowCampaign] | None = None,
    **kwargs,
):
    repo = AsyncMock()
    repo.save_campaign = AsyncMock(side_effect=lambda campaign: campaign)
    repo.list_active_campaigns = AsyncMock(return_value=campaigns or [])
    repo.list_active_owner_ids = AsyncMock(return_value=["user-1"])
    event_bus = AsyncMock()
    runs = AsyncMock()
    projector = WorkflowCampaignProjector(
        repo=repo,
        event_bus=event_bus,
        run_launched=_Launched(launched),
        **kwargs,
    )
    projector.bind_runs(runs)
    return projector, repo, event_bus, runs


def _saved(repo: AsyncMock) -> WorkflowCampaign:
    return repo.save_campaign.await_args.args[0]


def _events(event_bus: AsyncMock) -> list[str]:
    return [call.args[0].event for call in event_bus.emit.await_args_list]


@pytest.mark.asyncio
async def test_lifecycle_and_active_owner_projection() -> None:
    coordinators = AsyncMock()
    coordinators.list_active_owner_ids = AsyncMock(return_value=["user-2", "user-1"])
    projector, _, _, _ = _projector(coordinator_repo=coordinators)

    assert projector.running is False
    await projector.start()
    assert projector.running is True
    assert await projector.list_active_owner_ids() == ["user-1", "user-2"]
    await projector.stop()
    assert projector.running is False


@pytest.mark.asyncio
async def test_a_changed_projection_is_saved_and_announced() -> None:
    campaign = _campaign()
    projector, repo, event_bus, _ = _projector(push_dispatcher=AsyncMock())
    stage = CampaignStageState(stage_id="review", label="Review", status="blocked")

    await projector.apply_run_projection(
        campaign,
        _projection(
            campaign,
            status=WorkflowCampaignStatus.BLOCKED,
            active_stage_id="review",
            stage_state=[stage],
            metadata={"pending_workflow_gates": [{"id": "review"}]},
        ),
    )

    saved = _saved(repo)
    assert saved.status is WorkflowCampaignStatus.BLOCKED
    assert saved.active_stage_id == "review"
    assert saved.stage_state == [stage]
    assert saved.metadata["pending_workflow_gates"] == [{"id": "review"}]
    assert saved.session_id == ""
    assert _events(event_bus) == ["workflow.campaign.updated"]
    projector._push_dispatcher.queue_campaign.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_unchanged_projection_writes_nothing() -> None:
    campaign = _campaign()
    projector, repo, event_bus, _ = _projector()

    await projector.apply_run_projection(campaign, _projection(campaign))

    repo.save_campaign.assert_not_awaited()
    event_bus.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_projection_with_no_active_stage_clears_it() -> None:
    campaign = _campaign()
    campaign = WorkflowCampaign(**{**campaign.__dict__, "active_stage_id": "implement"})
    projector, repo, _, _ = _projector()

    await projector.apply_run_projection(campaign, _projection(campaign, active_stage_id=None))

    assert _saved(repo).active_stage_id is None


@pytest.mark.asyncio
async def test_a_completed_run_marks_the_campaign_completed_and_tells_whoever_listens() -> None:
    campaign = _campaign()
    advancer = AsyncMock()
    finished = AsyncMock()
    projector, repo, event_bus, _ = _projector(
        ticket_advancer=advancer, on_run_finished=(finished,)
    )

    await projector.apply_run_projection(
        campaign, _projection(campaign, status=WorkflowCampaignStatus.COMPLETED)
    )

    saved = _saved(repo)
    assert saved.status is WorkflowCampaignStatus.COMPLETED
    assert saved.completed_at is not None
    assert _events(event_bus) == ["workflow.campaign.completed"]
    advancer.advance.assert_awaited_once_with(saved)
    finished.assert_awaited_once_with(saved)


@pytest.mark.asyncio
async def test_a_failed_run_carries_its_reason_and_stage_in_the_event() -> None:
    campaign = _campaign()
    finished = AsyncMock()
    advancer = AsyncMock()
    projector, repo, event_bus, _ = _projector(
        ticket_advancer=advancer, on_run_finished=(finished,)
    )

    await projector.apply_run_projection(
        campaign,
        _projection(
            campaign,
            status=WorkflowCampaignStatus.FAILED,
            metadata={"failure_error": "step blew up", "failure_stage_id": "implement"},
        ),
    )

    event = event_bus.emit.await_args.args[0]
    assert event.event == "workflow.campaign.failed"
    assert event.data["error"] == "step blew up"
    assert event.data["failed_stage_id"] == "implement"
    advancer.advance.assert_not_awaited()
    finished.assert_awaited_once_with(_saved(repo))


@pytest.mark.asyncio
async def test_a_stale_read_does_not_reopen_a_finished_campaign() -> None:
    campaign = _campaign()
    projector, repo, event_bus, _ = _projector()
    repo.save_campaign = AsyncMock(side_effect=CampaignAlreadyTerminalError("done"))

    await projector.apply_run_projection(
        campaign, _projection(campaign, status=WorkflowCampaignStatus.BLOCKED)
    )

    event_bus.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnecting_an_owner_advances_tickets_whose_run_finished_while_away() -> None:
    advancer = AsyncMock()
    projector, _, _, _ = _projector(ticket_advancer=advancer)

    await projector.reconcile_owner("user-1")

    advancer.advance_finished.assert_awaited_once_with("user-1")


@pytest.mark.asyncio
async def test_a_wait_on_a_person_past_its_bound_cancels_the_run_and_counts_it() -> None:
    campaign = _campaign(WorkflowCampaignStatus.BLOCKED, age_seconds=7 * _HOUR)
    projector, repo, _, runs = _projector(
        campaigns=[campaign], input_wait_timeout_seconds=6 * _HOUR
    )

    expired = await projector.expire_stale_campaigns(now=datetime.now(UTC))

    assert expired == 1
    runs.cancel.assert_awaited_once()
    assert runs.cancel.await_args.args[0] == campaign
    assert "No answer to the input request within 6 hours" in runs.cancel.await_args.args[1]
    repo.save_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_wait_within_its_bound_is_left_alone() -> None:
    campaign = _campaign(WorkflowCampaignStatus.BLOCKED, age_seconds=1 * _HOUR)
    projector, _, _, runs = _projector(campaigns=[campaign], input_wait_timeout_seconds=6 * _HOUR)

    assert await projector.expire_stale_campaigns(now=datetime.now(UTC)) == 0
    runs.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_ticket_waiting_at_a_gate_never_expires() -> None:
    campaign = _campaign(
        WorkflowCampaignStatus.BLOCKED,
        age_seconds=72 * _HOUR,
        metadata={TICKET_METADATA_KEY: {"sagaId": str(uuid4()), "issueId": "NIU-1"}},
    )
    projector, _, _, runs = _projector(campaigns=[campaign], input_wait_timeout_seconds=6 * _HOUR)

    assert await projector.expire_stale_campaigns(now=datetime.now(UTC)) == 0
    runs.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_launch_that_died_before_the_ledger_recorded_it_fails_loudly(caplog) -> None:
    campaign = _campaign(WorkflowCampaignStatus.PENDING, age_seconds=120)
    projector, repo, event_bus, runs = _projector(
        launched=False, campaigns=[campaign], launch_lease_seconds=60.0
    )

    with caplog.at_level("ERROR", logger="ting.domain.services.workflow_campaign_projector"):
        expired = await projector.expire_stale_campaigns(now=datetime.now(UTC))

    assert expired == 1
    saved = _saved(repo)
    assert saved.status is WorkflowCampaignStatus.FAILED
    assert saved.metadata["failure_error"] == (
        "The launch ended before the run started. Start a new task to retry."
    )
    assert _events(event_bus) == ["workflow.campaign.failed"]
    assert any("a2a.launch_lease_seconds=60s" in record.getMessage() for record in caplog.records)
    runs.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_pending_run_the_ledger_has_launched_is_not_abandoned() -> None:
    campaign = _campaign(WorkflowCampaignStatus.PENDING, age_seconds=120)
    projector, repo, _, _ = _projector(
        launched=True, campaigns=[campaign], launch_lease_seconds=60.0
    )

    assert await projector.expire_stale_campaigns(now=datetime.now(UTC)) == 0
    repo.save_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_launch_still_inside_its_lease_is_left_alone() -> None:
    campaign = _campaign(WorkflowCampaignStatus.PENDING, age_seconds=10)
    launched = _Launched(False)
    projector, repo, _, _ = _projector(campaigns=[campaign], launch_lease_seconds=60.0)
    projector._run_launched = launched

    assert await projector.expire_stale_campaigns(now=datetime.now(UTC)) == 0
    assert launched.asked == []
    repo.save_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_bounds_disable_expiry_and_the_sweep() -> None:
    campaign = _campaign(WorkflowCampaignStatus.BLOCKED, age_seconds=100 * _HOUR)
    projector, _, _, runs = _projector(
        campaigns=[campaign],
        sweep_interval_seconds=0.01,
        input_wait_timeout_seconds=0.0,
        launch_lease_seconds=0.0,
    )

    assert await projector.expire_stale_campaigns(now=datetime.now(UTC)) == 0
    await projector.start()
    assert projector._sweep_task is None
    await projector.stop()
    runs.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_sweep_runs_on_its_interval_and_stops_cleanly() -> None:
    campaign = _campaign(WorkflowCampaignStatus.BLOCKED, age_seconds=7 * _HOUR)
    projector, _, _, runs = _projector(
        campaigns=[campaign],
        sweep_interval_seconds=0.01,
        input_wait_timeout_seconds=6 * _HOUR,
    )

    await projector.start()
    for _ in range(100):
        if runs.cancel.await_count:
            break
        await asyncio.sleep(0.01)
    await projector.stop()

    assert runs.cancel.await_count >= 1
    assert projector._sweep_task is None


@pytest.mark.asyncio
async def test_one_campaign_failing_its_check_does_not_stop_the_sweep(caplog) -> None:
    broken = _campaign(WorkflowCampaignStatus.PENDING, age_seconds=500)
    stale = _campaign(WorkflowCampaignStatus.BLOCKED, age_seconds=7 * _HOUR)
    projector, _, _, runs = _projector(
        campaigns=[broken, stale],
        input_wait_timeout_seconds=6 * _HOUR,
        launch_lease_seconds=60.0,
    )
    calls = 0

    async def launched(run_id: UUID) -> bool:
        nonlocal calls
        calls += 1
        raise ConnectionError("ledger unavailable")

    projector._run_launched = launched

    with caplog.at_level("ERROR", logger="ting.domain.services.workflow_campaign_projector"):
        expired = await projector.expire_stale_campaigns(now=datetime.now(UTC))

    assert calls == 1
    assert expired == 1
    runs.cancel.assert_awaited_once()
    assert any("Could not check workflow campaign" in r.getMessage() for r in caplog.records)
