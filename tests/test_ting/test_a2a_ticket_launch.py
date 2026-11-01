"""Ting decides which workflow a ticket runs. A coordinator's judgment never does."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ting.adapters.postgres_workflow_campaigns import PostgresWorkflowCampaignRepository
from ting.domain.models import SagaBoardWorkflow, WorkflowCampaignStatus
from ting.system_workflows import load_system_workflows

from .test_a2a_tasks import (
    InMemoryA2ALaunchRepository,
    InMemoryCampaignRepository,
    InMemoryWorkflowRepository,
    _make_campaign,
    _make_client,
    _rpc,
    _send_params,
)

PLANNING = "Ticket Planning Flow"
DELIVERY = "Reviewed Delivery Flow"
OFF_BOARD = "Tracker Delivery Flow"
NOW = datetime.now(UTC)


def _workflow(name: str) -> Any:
    return next(item for item in load_system_workflows() if item.name == name)


def _pin(name: str) -> SagaBoardWorkflow:
    workflow = _workflow(name)
    return SagaBoardWorkflow(
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_snapshot={"name": name, "version": workflow.version, "graph": workflow.graph},
    )


class _Board:
    def __init__(self, *, pins: tuple[SagaBoardWorkflow, ...] | None = None) -> None:
        self.saga_id = uuid4()
        self.campaigns = InMemoryCampaignRepository()
        self.launches = InMemoryA2ALaunchRepository()
        workflows = InMemoryWorkflowRepository(
            [_workflow(PLANNING), _workflow(DELIVERY), _workflow(OFF_BOARD)]
        )
        self.client, _, self.forge = _make_client(
            workflow_repo=workflows, campaign_repo=self.campaigns, launch_repo=self.launches
        )
        self.saga = SimpleNamespace(
            id=self.saga_id,
            forge_project_id=self.saga_id,
            name="Platform",
            board_workflows=(_pin(PLANNING), _pin(DELIVERY)) if pins is None else pins,
            workflow_id=None,
            workflow_snapshot=None,
        )
        self.client.app.state.saga_repo.get_saga.return_value = self.saga
        self.ticket = {
            "sagaId": str(self.saga_id),
            "issueId": "issue-1",
            "identifier": "NIU-1166",
        }

    def request(self, workflow_id: Any = None, *, requested_at: datetime = NOW) -> None:
        self.client.app.state.ticket_coordinator_repo.get.return_value = SimpleNamespace(
            session_id="coordinator-session",
            requested_workflow_id=str(workflow_id) if workflow_id else None,
            requested_at=requested_at,
        )

    def run(self, name: str, status: WorkflowCampaignStatus, **overrides: Any) -> None:
        campaign = _make_campaign(
            slug=f"a2a-{name.split()[0].lower()}",
            status=status,
            metadata={"ticket": self.ticket},
        )
        campaign = replace(campaign, owner_id=self.owner_id(), **overrides)
        self.campaigns._campaigns[campaign.id] = campaign

    def owner_id(self) -> str:
        return "user-1"

    def launch(self, name: str, *, message_id: str = "msg-1") -> dict[str, Any]:
        params = _send_params(str(_workflow(name).id))
        params["message"]["messageId"] = message_id
        params["message"]["metadata"]["ticket"] = self.ticket
        params["message"]["metadata"]["repo"] = "jve/kit-extension-test"
        response = _rpc(self.client, "SendMessage", params)
        assert response.status_code == 200, response.text
        return response.json()


def _refused(body: dict[str, Any], text: str) -> None:
    assert "error" in body, body
    assert text in body["error"]["message"]


def test_a_workflow_on_the_board_starts() -> None:
    board = _Board()
    board.request(_workflow(PLANNING).id)

    body = board.launch(PLANNING)

    assert "error" not in body
    assert board.campaigns._campaigns


def test_a_workflow_not_on_the_board_is_refused_and_says_what_to_do(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NIU-1166: the coordinator picked Tracker Delivery Flow, which has no person gate."""
    board = _Board()

    body = board.launch(OFF_BOARD)

    _refused(body, "not on this project's board")
    _refused(body, "Ticket Planning Flow, Reviewed Delivery Flow")
    _refused(body, "Start the workflow your brief names")
    assert not board.campaigns._campaigns
    assert not board.forge.spawned
    assert "refused a ticket launch (not_on_board)" in caplog.text


def test_a_board_workflow_that_was_not_the_one_asked_for_is_refused() -> None:
    board = _Board()
    board.request(_workflow(DELIVERY).id)

    body = board.launch(PLANNING)

    _refused(body, f"Ting asked for {DELIVERY}")
    assert not board.campaigns._campaigns


def test_a_request_a_run_has_already_answered_no_longer_binds() -> None:
    board = _Board()
    board.request(_workflow(DELIVERY).id, requested_at=NOW - timedelta(hours=1))
    board.run(DELIVERY, WorkflowCampaignStatus.COMPLETED, created_at=NOW)

    body = board.launch(PLANNING)

    assert "error" not in body


def test_a_failed_launch_does_not_answer_the_request() -> None:
    board = _Board()
    board.request(_workflow(DELIVERY).id, requested_at=NOW - timedelta(hours=1))
    board.run(DELIVERY, WorkflowCampaignStatus.FAILED, created_at=NOW)

    _refused(board.launch(PLANNING), f"Ting asked for {DELIVERY}")
    assert "error" not in board.launch(DELIVERY, message_id="msg-2")


@pytest.mark.parametrize(
    "status",
    [
        WorkflowCampaignStatus.PENDING,
        WorkflowCampaignStatus.RUNNING,
        WorkflowCampaignStatus.BLOCKED,
    ],
)
def test_a_second_run_alongside_an_active_one_is_refused(
    status: WorkflowCampaignStatus,
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = _Board()
    board.run(PLANNING, status)

    body = board.launch(DELIVERY)

    _refused(body, "already has run a2a-ticket in progress")
    assert not board.forge.spawned
    assert "refused a ticket launch (active_run)" in caplog.text


def test_a_refused_launch_leaves_its_message_free_to_be_judged_again() -> None:
    board = _Board()
    board.run(PLANNING, WorkflowCampaignStatus.RUNNING)

    _refused(board.launch(DELIVERY), "already has run")

    reservation = next(iter(board.launches.by_message.values()))
    assert reservation.state == "reserved"
    assert reservation.lease_token is None


def test_a_project_with_no_board_keeps_todays_behaviour() -> None:
    board = _Board(pins=())
    board.run(PLANNING, WorkflowCampaignStatus.RUNNING)

    assert "error" not in board.launch(OFF_BOARD)


def test_a_launch_for_no_ticket_is_never_held_to_a_board() -> None:
    board = _Board()
    params = _send_params(str(_workflow(OFF_BOARD).id))
    params["message"]["metadata"]["repo"] = "jve/kit-extension-test"

    assert "error" not in _rpc(board.client, "SendMessage", params).json()


def test_a_ticket_in_a_project_this_user_lacks_is_refused() -> None:
    board = _Board()
    board.client.app.state.saga_repo.get_saga.return_value = None

    _refused(board.launch(PLANNING), "names a project this user does not have")


def test_a_ticket_with_a_malformed_project_is_refused() -> None:
    board = _Board()
    board.ticket = {**board.ticket, "sagaId": "not-a-uuid"}

    _refused(board.launch(PLANNING), "names no valid project")


class _Connection:
    """Behaves as Postgres does: the advisory lock serialises the two launches."""

    def __init__(self, lock: asyncio.Lock, rows: dict[Any, str]) -> None:
        self._lock = lock
        self._rows = rows
        self._locked = False

    def transaction(self) -> _Connection:
        return self

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._locked:
            self._lock.release()
            self._locked = False

    async def execute(self, sql: str, *args: Any) -> str:
        if "pg_advisory_xact_lock" in sql:
            await self._lock.acquire()
            self._locked = True
            return "SELECT 1"
        await asyncio.sleep(0)
        self._rows[args[0]] = args[1]
        return "INSERT 0 1"

    async def fetchval(self, sql: str, *args: Any) -> str | None:
        await asyncio.sleep(0)
        return next(iter(self._rows.values()), None)


class _Pool:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.rows: dict[Any, str] = {}

    def acquire(self) -> _Connection:
        return _Connection(self.lock, self.rows)


async def test_two_launches_racing_for_a_ticket_cannot_both_pass() -> None:
    pool = _Pool()
    repo = PostgresWorkflowCampaignRepository(pool)
    saga_id = uuid4()
    first = _make_campaign(slug="a2a-first", status=WorkflowCampaignStatus.PENDING)
    second = _make_campaign(slug="a2a-second", status=WorkflowCampaignStatus.PENDING)

    results = await asyncio.gather(
        repo.save_ticket_campaign(first, saga_id=saga_id, issue_id="issue-1"),
        repo.save_ticket_campaign(second, saga_id=saga_id, issue_id="issue-1"),
    )

    assert results == [None, "a2a-first"]
    assert list(pool.rows.values()) == ["a2a-first"]


async def test_the_race_is_held_by_a_lock_not_a_unique_index() -> None:
    connection = SimpleNamespace(
        execute=AsyncMock(return_value="INSERT 0 1"),
        fetchval=AsyncMock(return_value="a2a-active"),
        transaction=lambda: _Connection(asyncio.Lock(), {}),
    )
    pool = SimpleNamespace(
        acquire=lambda: SimpleNamespace(
            __aenter__=None,
        )
    )

    class _Acquire:
        async def __aenter__(self) -> Any:
            return connection

        async def __aexit__(self, *_exc: object) -> None:
            return None

    pool.acquire = _Acquire
    repo = PostgresWorkflowCampaignRepository(pool)

    active = await repo.save_ticket_campaign(
        _make_campaign(slug="a2a-new"), saga_id=uuid4(), issue_id="issue-1"
    )

    assert active == "a2a-active"
    assert "pg_advisory_xact_lock" in connection.execute.await_args_list[0].args[0]
    assert connection.execute.await_count == 1
