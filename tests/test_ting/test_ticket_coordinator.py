"""Tests for the persistent coordinator session a ticket works through."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from niuu.config_models import default_session_definitions
from niuu.domain.models import Principal
from ting.domain.models import Saga, SagaStatus, TrackerIssue
from ting.domain.services.ticket_coordinator import (
    TicketCoordinatorService,
    TicketWorkflow,
    coordinator_brief,
    coordinator_ravn_config,
    ticket_branch,
)
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.ticket_coordinator import (
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_metadata,
    ticket_of,
)

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
WORKFLOW = TicketWorkflow(id="workflow-1", name="Reviewed Delivery Flow")


def _saga() -> Saga:
    return Saga(
        id=uuid4(),
        tracker_id="project-1",
        tracker_type="linear",
        slug="platform",
        name="Platform",
        repos=["jve/repo"],
        feature_branch="feat/platform",
        status=SagaStatus.ACTIVE,
        created_at=NOW,
        base_branch="main",
    )


def _issue() -> TrackerIssue:
    return TrackerIssue(
        id="issue-1",
        identifier="NIU-1",
        title="Ship it",
        description="The details.",
        status="Todo",
        url="https://linear.example/NIU-1",
    )


def _coordinator(saga: Saga, *, status=TicketCoordinatorStatus.ACTIVE) -> TicketCoordinator:
    return TicketCoordinator(
        saga_id=saga.id,
        issue_id="issue-1",
        owner_id="owner-1",
        tenant_id="tenant-1",
        session_id="session-1",
        connection_id=None,
        status=status,
        created_at=NOW,
    )


def _service(*, current: TicketCoordinator | None, session_status: str = "running"):
    repo = MagicMock()
    repo.get = AsyncMock(return_value=current)
    repo.save = AsyncMock(side_effect=lambda item: item)
    repo.close = AsyncMock()
    volundr = MagicMock()
    volundr.get_session = AsyncMock(
        return_value=SimpleNamespace(status=session_status, activity_state=None)
    )
    volundr.spawn_session = AsyncMock(return_value=SimpleNamespace(id="session-2"))
    volundr.send_directed_room_message = AsyncMock()
    volundr.start_session = AsyncMock()
    volundr.stop_session = AsyncMock()
    volundr.api_base_url = "https://forge.test/"
    volundr.target_id = "forge-target"
    service = TicketCoordinatorService(
        repo=repo,
        volundr=volundr,
        principal=Principal(user_id="owner-1", email="", tenant_id="tenant-1", roles=[]),
        auth_token="token",
        persona="ticket-coordinator",
        model="gpt-5.6-terra",
        session_definitions=default_session_definitions(),
        configured_models=[],
        cli_turn_timeout_seconds=1800.0,
        ravn_config={"gateway": {"platform": {"a2a_default_metadata": {"model": "m"}}}},
        platform_origin="http://platform",
        anonymous_platform=True,
    )
    return service, repo, volundr


@pytest.mark.asyncio
async def test_a_coordinator_runs_on_its_model_like_a_workflow_stage() -> None:
    service, _, volundr = _service(current=None)

    await service.start_workflow(_saga(), _issue(), WORKFLOW)

    request = volundr.spawn_session.await_args.args[0]
    persona = request.workload_config["personas"][0]
    assert request.model == "gpt-5.6-terra"
    assert request.definition == "skuldCodex"
    assert persona["llm"] == {"model": "gpt-5.6-terra"}
    assert persona["executor"]["kwargs"]["transport_adapter"] == (
        "skuld.transports.codex_ws.CodexWebSocketTransport"
    )
    assert persona["system_prompt_extra"].startswith("The ticket you coordinate is NIU-1")


@pytest.mark.asyncio
async def test_a_coordinator_model_no_runtime_serves_refuses_to_start() -> None:
    service, _, volundr = _service(current=None)
    service._session_definitions = {}

    with pytest.raises(ValueError, match="model 'gpt-5.6-terra'"):
        await service.start_workflow(_saga(), _issue(), WORKFLOW)

    volundr.spawn_session.assert_not_awaited()


async def test_a_coordinator_reaches_the_forge_that_launched_it() -> None:
    service, _, volundr = _service(current=None)

    await service.start_workflow(_saga(), _issue(), WORKFLOW)

    config = volundr.spawn_session.await_args.args[0].workload_config["ravn_config"]
    platform = config["gateway"]["platform"]
    assert platform["enabled"] is True
    assert platform["base_url"] == "https://forge.test"
    assert platform["workload_exchange_url"] == (
        "https://forge.test/api/v1/tokens/workload/exchange"
    )
    assert platform["a2a_default_connection_id"] == "forge-target"


def test_a_coordinator_inherits_the_deployments_platform_settings() -> None:
    flock = {"gateway": {"platform": {"enabled": True, "base_url": "https://forge"}}, "a": 1}
    own = {"gateway": {"platform": {"timeout": 5}}, "memory": {"reflection_model": "same"}}

    assert coordinator_ravn_config(flock, own) == {
        "gateway": {"platform": {"enabled": True, "base_url": "https://forge", "timeout": 5}},
        "a": 1,
        "memory": {"reflection_model": "same"},
    }
    assert "timeout" not in flock["gateway"]["platform"]


def test_the_brief_names_the_ticket_and_the_workflow() -> None:
    brief = coordinator_brief(_issue(), repo="jve/repo", workflow=WORKFLOW)

    assert "NIU-1" in brief
    assert "The details." in brief
    assert "Reviewed Delivery Flow" in brief
    assert "workflow-1" in brief


def test_ticket_metadata_round_trips() -> None:
    saga_id = uuid4()
    metadata = {
        "ticket": ticket_metadata(saga_id=saga_id, issue_id="i", identifier="N-1", title="T")
    }

    assert ticket_of(metadata) == (str(saga_id), "i")


@pytest.mark.parametrize("metadata", [{}, {"ticket": "x"}, {"ticket": {"sagaId": "s"}}])
def test_campaigns_without_a_full_ticket_link_have_none(metadata: dict) -> None:
    assert ticket_of(metadata) is None


async def test_the_first_start_creates_the_session_with_launch_defaults() -> None:
    saga = _saga()
    service, repo, volundr = _service(current=None)

    coordinator = await service.start_workflow(saga, _issue(), WORKFLOW)

    assert coordinator.session_id == "session-2"
    repo.save.assert_awaited_once()
    volundr.send_directed_room_message.assert_not_awaited()
    request = volundr.spawn_session.await_args.args[0]
    platform = request.workload_config["ravn_config"]["gateway"]["platform"]
    assert platform["a2a_agent_card_urls"] == ["http://platform/.well-known/agent-card.json"]
    assert platform["a2a_trusted_origins"] == ["http://platform"]
    assert platform["anonymous_dev_mode"] is True
    assert platform["a2a_gate_decisions"] is False
    assert platform["a2a_default_metadata"]["model"] == "m"
    assert platform["a2a_default_metadata"]["branch"] == "feat/platform-niu-1"
    assert platform["a2a_default_metadata"]["baseBranch"] == saga.base_branch
    assert platform["a2a_default_metadata"]["ticket"]["identifier"] == "NIU-1"


def test_two_tickets_of_a_project_get_different_branches() -> None:
    saga = _saga()
    first = ticket_branch(saga, _issue())
    second = ticket_branch(saga, replace(_issue(), identifier="NIU-2"))

    assert first == "feat/platform-niu-1"
    assert second == "feat/platform-niu-2"
    assert first != second


def test_a_ticket_branch_can_coexist_with_the_project_branch() -> None:
    saga = _saga()

    assert not ticket_branch(saga, _issue()).startswith(f"{saga.feature_branch}/")


def test_a_ticket_identifier_becomes_a_valid_ref() -> None:
    issue = replace(_issue(), identifier="Team Alpha/42")

    assert ticket_branch(_saga(), issue) == "feat/platform-team-alpha-42"


async def test_a_coordinator_knows_its_ticket_on_every_turn() -> None:
    saga = _saga()
    service, _, volundr = _service(current=None)

    await service.start_workflow(saga, _issue(), WORKFLOW)

    persona = volundr.spawn_session.await_args.args[0].workload_config["personas"][0]
    assert "NIU-1: Ship it" in persona["system_prompt_extra"]
    assert "The details." in persona["system_prompt_extra"]


async def test_a_start_brief_goes_ahead_of_waiting_notices() -> None:
    saga = _saga()
    service, _, volundr = _service(current=_coordinator(saga))

    await service.start_workflow(saga, _issue(), WORKFLOW)

    assert volundr.send_directed_room_message.await_args.kwargs["metadata"] == {"queue_first": True}


async def test_a_later_start_reuses_the_running_session() -> None:
    saga = _saga()
    service, repo, volundr = _service(current=_coordinator(saga))

    coordinator = await service.start_workflow(saga, _issue(), WORKFLOW)

    assert coordinator.session_id == "session-1"
    volundr.spawn_session.assert_not_awaited()
    volundr.start_session.assert_not_awaited()
    args = volundr.send_directed_room_message.await_args.args
    assert args[1] == "flock-ticket-coordinator"
    assert "Reviewed Delivery Flow" in args[2]
    saved = repo.save.await_args.args[0]
    assert saved.requested_workflow_id == "workflow-1"
    assert saved.requested_at is not None


async def test_a_stopped_session_is_started_again_keeping_its_context() -> None:
    saga = _saga()
    service, _, volundr = _service(current=_coordinator(saga), session_status="stopped")

    coordinator = await service.start_workflow(saga, _issue(), WORKFLOW)

    volundr.start_session.assert_awaited_once()
    volundr.spawn_session.assert_not_awaited()
    volundr.send_directed_room_message.assert_not_awaited()
    assert "Start the workflow" in coordinator.pending_brief
    assert coordinator.requested_workflow_id == WORKFLOW.id


@pytest.mark.parametrize("status", ["created", "starting", "provisioning"])
async def test_a_booting_session_is_left_to_come_up_with_the_brief_waiting(status: str) -> None:
    saga = _saga()
    service, _, volundr = _service(current=_coordinator(saga), session_status=status)

    coordinator = await service.start_workflow(saga, _issue(), WORKFLOW)

    volundr.start_session.assert_not_awaited()
    volundr.send_directed_room_message.assert_not_awaited()
    assert coordinator.pending_brief


async def test_starting_again_clears_a_failed_start() -> None:
    saga = _saga()
    failed = replace(_coordinator(saga), start_error="The session would not start.")
    service, _, volundr = _service(current=failed)

    coordinator = await service.start_workflow(saga, _issue(), WORKFLOW)

    volundr.send_directed_room_message.assert_awaited_once()
    assert coordinator.start_error is None
    assert coordinator.pending_brief is None


async def test_a_deleted_session_is_replaced_keeping_its_start_time() -> None:
    saga = _saga()
    earlier = replace(_coordinator(saga), created_at=NOW)
    service, _, volundr = _service(current=earlier)
    volundr.get_session.return_value = None

    coordinator = await service.start_workflow(saga, _issue(), WORKFLOW)

    assert coordinator.session_id == "session-2"
    assert coordinator.created_at == NOW


async def test_a_coordinator_whose_agent_died_is_replaced() -> None:
    saga = _saga()
    earlier = replace(_coordinator(saga), created_at=NOW)
    service, _, volundr = _service(current=earlier)
    volundr.get_session.return_value = SimpleNamespace(status="running", activity_state="error")

    coordinator = await service.start_workflow(saga, _issue(), WORKFLOW)

    volundr.stop_session.assert_awaited_once()
    assert volundr.stop_session.await_args.args[0] == earlier.session_id
    volundr.spawn_session.assert_awaited_once()
    volundr.send_directed_room_message.assert_not_awaited()
    assert coordinator.session_id == "session-2"
    assert coordinator.created_at == NOW


async def test_a_closed_coordinator_gets_a_new_session() -> None:
    saga = _saga()
    service, _, volundr = _service(
        current=_coordinator(saga, status=TicketCoordinatorStatus.CLOSED)
    )

    coordinator = await service.start_workflow(saga, _issue(), WORKFLOW)

    volundr.spawn_session.assert_awaited_once()
    assert coordinator.status == TicketCoordinatorStatus.ACTIVE


async def test_closing_stops_the_session_and_records_it() -> None:
    saga = _saga()
    current = _coordinator(saga)
    service, repo, volundr = _service(current=current)

    await service.close(current)

    volundr.stop_session.assert_awaited_once()
    repo.close.assert_awaited_once_with(saga.id, "issue-1")


async def test_a_ticket_waiting_at_a_gate_never_expires() -> None:
    projector = WorkflowCampaignProjector.__new__(WorkflowCampaignProjector)
    projector._input_wait_timeout = 1.0
    campaign = SimpleNamespace(
        status="blocked",
        metadata={"ticket": {"sagaId": "s", "issueId": "i"}},
        last_activity_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert await projector._stale_expiry(campaign, now=NOW) is None
