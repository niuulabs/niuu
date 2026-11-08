"""What Ting learns from a ticket's run: its status on the board and its next step."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ting.domain.campaign_run_projection import RunProjection
from ting.domain.models import (
    PR_STATE_MERGED,
    PRStatus,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.ticket_coordinator import PULL_REQUEST_KEY, ticket_metadata

from .test_work_board_api import (
    DELIVERY,
    HEADERS,
    NOW,
    PLANNING,
    _adapter,
    _board_task,
    _bundled,
    _campaign,
    _client,
    _coordinator,
    _Coordinators,
    _engine,
    _issue,
    _pending_gate,
    _planned_saga,
    _run_of,
    _saga,
    _snapshot,
    _start,
)

OWNER = "owner-1"
PR = PRStatus(
    pr_id="4", url="https://github.com/o/r/pull/4", state="open", mergeable=True, ci_passed=None
)
MERGED = replace(PR, state=PR_STATE_MERGED)


def _ticket_campaign(
    saga, *, status=WorkflowCampaignStatus.RUNNING, **metadata
) -> WorkflowCampaign:
    return replace(
        _campaign(saga, status=status),
        metadata={
            "ticket": ticket_metadata(
                saga_id=saga.id, issue_id="issue-1", identifier="NIU-1", title="T"
            ),
            **metadata,
        },
    )


def _projector(
    campaign: WorkflowCampaign | None,
    *,
    coordinators: _Coordinators | None = None,
    advancer: Any = None,
) -> tuple[WorkflowCampaignProjector, SimpleNamespace]:
    repo = SimpleNamespace(
        save_campaign=AsyncMock(side_effect=lambda item: item),
        list_active_campaigns=AsyncMock(return_value=[campaign] if campaign else []),
    )
    projector = WorkflowCampaignProjector(
        repo=repo,
        event_bus=SimpleNamespace(emit=AsyncMock()),
        run_launched=AsyncMock(return_value=True),
        coordinator_repo=coordinators,
        ticket_advancer=advancer,
    )
    return projector, repo


def _projection(campaign: WorkflowCampaign, status: WorkflowCampaignStatus) -> RunProjection:
    return RunProjection(
        status=status,
        active_stage_id=None,
        stage_state=campaign.stage_state,
        metadata=dict(campaign.metadata),
    )


async def test_coordinators_keep_their_owners_followed() -> None:
    saga = _saga(_snapshot())
    projector, repo = _projector(None, coordinators=_Coordinators(_coordinator(saga)))
    repo.list_active_owner_ids = AsyncMock(return_value=["someone-else"])

    assert await projector.list_active_owner_ids() == ["owner-1", "someone-else"]


async def test_a_run_reaching_completed_advances_the_ticket_once() -> None:
    saga = _saga(_snapshot())
    advancer = SimpleNamespace(advance=AsyncMock())
    campaign = _ticket_campaign(saga)
    projector, _ = _projector(campaign, advancer=advancer)

    await projector.apply_run_projection(
        campaign, _projection(campaign, WorkflowCampaignStatus.COMPLETED)
    )

    advancer.advance.assert_awaited_once()
    assert advancer.advance.await_args.args[0].status == WorkflowCampaignStatus.COMPLETED

    # The same projection applied again finds nothing left to change.
    finished = replace(campaign, status=WorkflowCampaignStatus.COMPLETED)
    await projector.apply_run_projection(
        finished, _projection(finished, WorkflowCampaignStatus.COMPLETED)
    )

    advancer.advance.assert_awaited_once()


@pytest.mark.parametrize("status", [WorkflowCampaignStatus.FAILED, WorkflowCampaignStatus.BLOCKED])
async def test_a_run_that_did_not_succeed_never_advances(status: WorkflowCampaignStatus) -> None:
    saga = _saga(_snapshot())
    advancer = SimpleNamespace(advance=AsyncMock())
    campaign = _ticket_campaign(saga)
    projector, _ = _projector(campaign, advancer=advancer)

    await projector.apply_run_projection(campaign, _projection(campaign, status))

    advancer.advance.assert_not_awaited()


async def test_a_reconnect_catches_up_on_finished_runs() -> None:
    advancer = SimpleNamespace(advance_finished=AsyncMock())
    projector, _ = _projector(None, advancer=advancer)

    await projector.reconcile_owner(OWNER)

    advancer.advance_finished.assert_awaited_once_with(OWNER)


def test_a_failed_automatic_start_shows_blocked_and_a_retry_launches_again() -> None:
    saga = _planned_saga()
    finished = _run_of(saga, PLANNING, WorkflowCampaignStatus.COMPLETED)
    coordinators = _Coordinators(
        replace(
            _coordinator(saga),
            requested_workflow_id=str(_bundled(DELIVERY).id),
            requested_at=NOW + timedelta(hours=1),
            start_error="Reviewed Delivery Flow could not start: no session",
        )
    )
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([]),
        campaigns=[finished],
        coordinators=coordinators,
    )

    task = _board_task(client, saga)

    assert (task["board"]["columnId"], task["board"]["state"]) == ("building", "blocked")
    assert task["board"]["reason"] == "Reviewed Delivery Flow could not start: no session"
    assert task["coordinator"]["startError"] == task["board"]["reason"]

    retried = _start(client, saga)

    assert retried.status_code == 200
    _engine(client).launch.assert_awaited_once()
    assert coordinators.rows[(saga.id, "issue-1")].start_error is None


def test_a_pending_gate_is_what_the_ticket_waits_on() -> None:
    saga = _planned_saga()
    gate = {**_pending_gate(), "node_id": "plan-review-gate"}
    client = _client(
        saga,
        issues=[_issue()],
        runs=[],
        adapter=_adapter([gate]),
        campaigns=[_run_of(saga, PLANNING, WorkflowCampaignStatus.BLOCKED)],
    )

    assert _board_task(client, saga)["board"]["state"] == "needsDecision"


def test_a_failed_run_blocks_its_ticket_with_the_reason_it_failed() -> None:
    saga = _planned_saga()
    run = _run_of(saga, PLANNING, WorkflowCampaignStatus.FAILED)
    campaign = replace(run, metadata={**run.metadata, "failure_error": "the coder step failed"})
    client = _client(saga, issues=[_issue()], runs=[], adapter=_adapter([]), campaigns=[campaign])

    board = _board_task(client, saga)["board"]

    assert board["state"] == "blocked"
    assert board["reason"] == "the coder step failed"


def test_the_board_shows_who_decided_last_and_the_pull_request() -> None:
    saga = _planned_saga()
    decided = {
        **_pending_gate(),
        "status": "changes_requested",
        "decided_by": "jozef@niuu.world",
        "notes": "Rename it.",
        "updated_at": "2026-10-30T10:00:00+00:00",
    }
    running = _run_of(saga, DELIVERY, WorkflowCampaignStatus.RUNNING)
    campaign = replace(
        running,
        metadata={
            **running.metadata,
            PULL_REQUEST_KEY: {"id": "4", "url": PR.url, "state": "open"},
        },
    )
    client = _client(
        saga, issues=[_issue()], runs=[], adapter=_adapter([decided]), campaigns=[campaign]
    )

    response = client.get(f"/api/v1/ting/work/project/{saga.id}", headers=HEADERS).json()
    task = response["tasks"][0]

    assert task["board"]["lastDecision"]["decidedBy"] == "jozef@niuu.world"
    assert task["board"]["lastDecision"]["status"] == "changes_requested"
    assert task["board"]["lastDecision"]["notes"] == "Rename it."
    assert task["board"]["lastDecision"]["label"]
    assert task["workflowRun"]["prUrl"] == PR.url
