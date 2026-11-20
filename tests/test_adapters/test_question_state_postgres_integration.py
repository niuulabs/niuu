"""Real PostgreSQL checks for question state (LEX-1932): migration 000123 through the
production startup runner, and what only separate connections can show (two devices
answering at once).

Opt-in: set ``FORGE_HISTORY_TEST_DATABASE_URL`` to a disposable database. Each test
gets its own schema, migrated through the production startup runner, and drops it.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from cli.resources import ordered_migration_files
from volundr.adapters.outbound.postgres_questions import PostgresQuestionRepository
from volundr.adapters.outbound.startup_schema import apply_startup_migrations
from volundr.domain.notifications import NotificationScope
from volundr.domain.questions import AnsweredBy, QuestionAnswer, QuestionState

pytestmark = pytest.mark.integration

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
OWNER = NotificationScope(user_id="owner-a", tenant_id="tenant-a", is_admin=False)
ADMIN = NotificationScope(user_id="root", tenant_id="tenant-a", is_admin=True)
OTHER = NotificationScope(user_id="owner-b", tenant_id="tenant-b", is_admin=False)


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get("FORGE_HISTORY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set FORGE_HISTORY_TEST_DATABASE_URL for isolated PostgreSQL checks")
    schema = "forge_question_test_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    created = None
    try:
        created = await asyncpg.create_pool(
            dsn, min_size=1, max_size=8, server_settings={"search_path": schema}
        )
        async with created.acquire() as conn:
            await apply_startup_migrations(conn, ordered_migration_files(MIGRATIONS))
        yield created
    finally:
        if created is not None:
            await created.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


def _answer(device_id: str, request_id: str) -> QuestionAnswer:
    return QuestionAnswer(
        request_id=request_id,
        device=AnsweredBy(device_id=device_id, device_label=device_id.title()),
        content=f"from {device_id}",
        answers=[{"item_id": "slots", "selected": ["Release"]}],
        at=datetime.now(UTC),
    )


async def _claim(repo, question, device_id, request_id):
    return await repo.claim_answer(
        notification_id=question,
        owner_id="owner-a",
        tenant_id="tenant-a",
        session_id=uuid4(),
        answer=_answer(device_id, request_id),
    )


async def test_the_startup_runner_applies_the_migration_once(pool):
    files = ordered_migration_files(MIGRATIONS)
    assert "000123_forge_question_states.up.sql" in [path.name for path in files]
    async with pool.acquire() as conn:
        await apply_startup_migrations(conn, files)
        tables = {
            row["tablename"]
            for row in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            )
        }
    assert {"forge_question_states", "forge_question_events"} <= tables


async def test_two_devices_answering_at_once_record_one_answer(pool):
    repo = PostgresQuestionRepository(pool)
    question = uuid4()

    results = await asyncio.gather(
        *(_claim(repo, question, f"device-{n}", f"answer-{n}") for n in range(8))
    )

    winners = [record for record, recorded in results if recorded]
    assert len(winners) == 1
    first = winners[0]
    assert {record.answer.request_id for record, _ in results} == {first.answer.request_id}
    stored = await repo.get(question)
    assert stored.answer.device == first.answer.device
    assert stored.answer.answers == [{"item_id": "slots", "selected": ["Release"]}]
    events = await repo.events_after(OWNER, after=0, limit=50)
    assert [(e.state, e.answered_by) for e in events] == [
        (QuestionState.ANSWERED, first.answer.device)
    ]


async def test_close_after_an_answer_keeps_it_and_a_second_close_changes_nothing(pool):
    repo = PostgresQuestionRepository(pool)
    question, closer = uuid4(), uuid4()
    await _claim(repo, question, "phone", "a1")
    now = datetime.now(UTC)

    closed = await repo.close(
        notification_id=question,
        owner_id="owner-a",
        tenant_id="tenant-a",
        session_id=None,
        state=QuestionState.RESOLVED,
        at=now,
        closed_by_notification_id=closer,
    )
    again = await repo.close(
        notification_id=question,
        owner_id="owner-a",
        tenant_id="tenant-a",
        session_id=None,
        state=QuestionState.WITHDRAWN,
        at=now,
        closed_by_notification_id=uuid4(),
    )
    record, recorded = await _claim(repo, question, "mac", "a2")

    assert closed.state is QuestionState.RESOLVED
    assert closed.closed_by_notification_id == closer
    assert closed.answer.device.device_id == "phone"
    assert again is None
    assert recorded is False and record.state is QuestionState.RESOLVED
    states = [e.state for e in await repo.events_after(OWNER, after=0, limit=50)]
    assert states == [QuestionState.ANSWERED, QuestionState.RESOLVED]


async def test_an_open_question_can_be_withdrawn(pool):
    repo = PostgresQuestionRepository(pool)
    question = uuid4()
    record = await repo.close(
        notification_id=question,
        owner_id="owner-a",
        tenant_id="tenant-a",
        session_id=None,
        state=QuestionState.WITHDRAWN,
        at=datetime.now(UTC),
        closed_by_notification_id=None,
    )
    assert record.state is QuestionState.WITHDRAWN
    assert record.answer is None
    with pytest.raises(ValueError):
        await repo.close(
            notification_id=uuid4(),
            owner_id="owner-a",
            tenant_id="tenant-a",
            session_id=None,
            state=QuestionState.ANSWERED,
            at=datetime.now(UTC),
            closed_by_notification_id=None,
        )


async def test_events_are_scoped_paged_and_headed(pool):
    repo = PostgresQuestionRepository(pool)
    for n in range(3):
        await _claim(repo, uuid4(), "phone", f"a{n}")
    await repo.claim_answer(
        notification_id=uuid4(),
        owner_id="owner-b",
        tenant_id="tenant-b",
        session_id=None,
        answer=_answer("ipad", "b1"),
    )

    page = await repo.events_after(OWNER, after=0, limit=2)
    rest = await repo.events_after(OWNER, after=page[-1].seq, limit=10)

    assert len(page) == 2 and len(rest) == 1
    assert [e.seq for e in page + rest] == sorted(e.seq for e in page + rest)
    assert await repo.head_seq(OWNER) == rest[-1].seq
    assert len(await repo.events_after(ADMIN, after=0, limit=10)) == 3
    assert [e.owner_id for e in await repo.events_after(OTHER, after=0, limit=10)] == ["owner-b"]
    assert await repo.head_seq(NotificationScope("nobody", "t", False)) == 0


async def test_an_event_waits_for_the_one_before_it_to_commit(pool):
    """Seqs commit in order, so a reader can never skip a later-committing lower seq."""
    from volundr.adapters.outbound.postgres_questions import EVENT_ORDER_LOCK

    repo = PostgresQuestionRepository(pool)
    async with pool.acquire() as holder:
        transaction = holder.transaction()
        await transaction.start()
        await holder.execute("SELECT pg_advisory_xact_lock($1)", EVENT_ORDER_LOCK)
        claim = asyncio.create_task(_claim(repo, uuid4(), "phone", "a1"))
        await asyncio.sleep(0.5)
        assert not claim.done()
        assert await repo.events_after(OWNER, after=0, limit=10) == []
        await transaction.rollback()
    _, recorded = await asyncio.wait_for(claim, timeout=5)
    assert recorded is True
    assert len(await repo.events_after(OWNER, after=0, limit=10)) == 1
