"""The one start a finished run gets is claimed in a single conditional update."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from ting.adapters.postgres_ticket_coordinators import PostgresTicketCoordinatorRepository

NOW = datetime(2026, 10, 30, tzinfo=UTC)


def _row(**overrides) -> dict:
    return {
        "saga_id": uuid4(),
        "issue_id": "issue-1",
        "owner_id": "owner-1",
        "tenant_id": "tenant-1",
        "session_id": "coordinator-session",
        "connection_id": None,
        "status": "active",
        "created_at": NOW,
        "closed_at": None,
        "requested_workflow_id": "workflow-2",
        "requested_at": NOW,
        "pending_brief": None,
        "start_error": None,
        "advanced_from": "campaign-1",
        **overrides,
    }


async def test_claiming_is_one_conditional_update() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=_row())
    repo = PostgresTicketCoordinatorRepository(pool)

    claimed = await repo.claim_advance(
        uuid4(),
        "issue-1",
        finished_campaign_id="campaign-1",
        workflow_id="workflow-2",
        requested_at=NOW,
    )

    assert claimed is not None
    assert claimed.advanced_from == "campaign-1"
    sql = pool.fetchrow.await_args.args[0]
    assert "UPDATE ticket_coordinators" in sql
    assert "advanced_from IS DISTINCT FROM $3" in sql
    assert "status = 'active'" in sql


async def test_a_claim_already_taken_returns_nothing() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    repo = PostgresTicketCoordinatorRepository(pool)

    claimed = await repo.claim_advance(
        uuid4(),
        "issue-1",
        finished_campaign_id="campaign-1",
        workflow_id="workflow-2",
        requested_at=NOW,
    )

    assert claimed is None


async def test_a_coordinator_is_found_by_its_session_only_while_active() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=_row())
    repo = PostgresTicketCoordinatorRepository(pool)

    assert (await repo.get_by_session("coordinator-session")).session_id == "coordinator-session"
    assert "status = 'active'" in pool.fetchrow.await_args.args[0]


async def test_active_owners_are_listed_once_each() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"owner_id": "owner-1"}])
    repo = PostgresTicketCoordinatorRepository(pool)

    assert await repo.list_active_owner_ids() == ["owner-1"]
    assert "DISTINCT" in pool.fetch.await_args.args[0]
