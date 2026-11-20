"""PostgresQuestionRepository against a fake asyncpg pool (LEX-1932).

The atomic claim and the commit order are proven against real PostgreSQL in
``test_question_state_postgres_integration.py``; this checks the SQL each call issues
and the row mapping, in every run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from volundr.adapters.outbound.postgres_questions import (
    EVENT_ORDER_LOCK,
    PostgresQuestionRepository,
)
from volundr.domain.notifications import NotificationScope
from volundr.domain.questions import AnsweredBy, QuestionAnswer, QuestionState

NOW = datetime(2026, 11, 5, 14, 5, tzinfo=UTC)


def _state_row(notification_id, *, state="answered", answers='[{"item_id": "a"}]', **extra):
    row = {
        "notification_id": notification_id,
        "owner_id": "owner-a",
        "tenant_id": "tenant-a",
        "session_id": None,
        "state": state,
        "answer_request_id": "r1",
        "answered_device_id": "install-phone",
        "answered_device_label": "iPhone",
        "answer_content": "Release",
        "answers": answers,
        "answered_at": NOW,
        "closed_at": None,
        "closed_by_notification_id": None,
    }
    row.update(extra)
    return row


class _Conn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed: list[tuple] = []
        self.fetched: list[str] = []

    async def fetchrow(self, sql, *args):
        self.fetched.append(sql)
        return self.rows.pop(0) if self.rows else None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    def transaction(self):
        class _Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return _Tx()


class _Pool:
    def __init__(self, conn=None, *, rows=None, value=0):
        self.conn = conn
        self.rows = rows or []
        self.value = value
        self.queries: list[tuple] = []

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        return self.rows[0] if self.rows else None

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        return self.rows

    async def fetchval(self, sql, *args):
        self.queries.append((sql, args))
        return self.value


def _answer():
    return QuestionAnswer(
        request_id="r1",
        device=AnsweredBy(device_id="install-phone", device_label="iPhone"),
        content="Release",
        answers=[{"item_id": "a"}],
        at=NOW,
    )


async def _claim(repo, question):
    return await repo.claim_answer(
        notification_id=question,
        owner_id="owner-a",
        tenant_id="tenant-a",
        session_id=None,
        answer=_answer(),
    )


async def test_a_won_claim_takes_the_order_lock_then_appends_its_event():
    question = uuid4()
    conn = _Conn([_state_row(question)])

    record, recorded = await _claim(PostgresQuestionRepository(_Pool(conn)), question)

    assert recorded is True
    assert record.answer.device == AnsweredBy("install-phone", "iPhone")
    assert record.answer.answers == [{"item_id": "a"}]
    assert "ON CONFLICT (notification_id) DO NOTHING" in conn.fetched[0]
    (lock, lock_args), (insert, insert_args) = conn.executed
    assert "pg_advisory_xact_lock" in lock and lock_args == (EVENT_ORDER_LOCK,)
    assert "INSERT INTO forge_question_events" in insert
    assert insert_args[3:6] == ("answered", "install-phone", "iPhone")


async def test_a_lost_claim_returns_the_first_answer_and_appends_nothing():
    question = uuid4()
    conn = _Conn([None, _state_row(question, answers=[{"item_id": "first"}])])

    record, recorded = await _claim(PostgresQuestionRepository(_Pool(conn)), question)

    assert recorded is False
    assert record.answer.answers == [{"item_id": "first"}]
    assert conn.executed == []


async def test_a_close_updates_only_an_answered_question_and_appends_its_event():
    question, closer = uuid4(), uuid4()
    conn = _Conn(
        [_state_row(question, state="withdrawn", closed_at=NOW, closed_by_notification_id=closer)]
    )

    record = await PostgresQuestionRepository(_Pool(conn)).close(
        notification_id=question,
        owner_id="owner-a",
        tenant_id="tenant-a",
        session_id=None,
        state=QuestionState.WITHDRAWN,
        at=NOW,
        closed_by_notification_id=closer,
    )

    assert record.state is QuestionState.WITHDRAWN and record.closed_by_notification_id == closer
    assert "WHERE forge_question_states.state = 'answered'" in conn.fetched[0]
    assert [sql for sql, _ in conn.executed][0].startswith("SELECT pg_advisory_xact_lock")
    assert conn.executed[1][1][3:6] == ("withdrawn", None, None)


async def test_closing_a_closed_question_changes_nothing():
    conn = _Conn([None])
    record = await PostgresQuestionRepository(_Pool(conn)).close(
        notification_id=uuid4(),
        owner_id="owner-a",
        tenant_id=None,
        session_id=None,
        state=QuestionState.RESOLVED,
        at=NOW,
        closed_by_notification_id=None,
    )
    assert record is None
    assert conn.executed == []


async def test_close_refuses_answered():
    with pytest.raises(ValueError, match="resolved or withdrawn"):
        await PostgresQuestionRepository(_Pool(_Conn([]))).close(
            notification_id=uuid4(),
            owner_id="o",
            tenant_id=None,
            session_id=None,
            state=QuestionState.ANSWERED,
            at=NOW,
            closed_by_notification_id=None,
        )


async def test_get_maps_an_open_closed_question_without_an_answer():
    question = uuid4()
    pool = _Pool(rows=[_state_row(question, state="resolved", answered_at=None, closed_at=NOW)])
    record = await PostgresQuestionRepository(pool).get(question)
    assert record.state is QuestionState.RESOLVED and record.answer is None
    assert await PostgresQuestionRepository(_Pool(rows=[])).get(question) is None


async def test_events_are_scoped_like_the_feed():
    question = uuid4()
    pool = _Pool(
        rows=[
            {
                "seq": 7,
                "notification_id": question,
                "owner_id": "owner-a",
                "tenant_id": "tenant-a",
                "state": "answered",
                "answered_device_id": None,
                "answered_device_label": None,
                "at": NOW,
            }
        ],
        value=7,
    )
    repo = PostgresQuestionRepository(pool)
    scope = NotificationScope(user_id="owner-a", tenant_id="tenant-a", is_admin=False)

    events = await repo.events_after(scope, after=3, limit=10)
    head = await repo.head_seq(scope)

    assert [(e.seq, e.state, e.answered_by) for e in events] == [(7, QuestionState.ANSWERED, None)]
    assert head == 7
    sql, args = pool.queries[0]
    assert "owner_id = $1" in sql and "seq > $2" in sql and "LIMIT $3" in sql
    assert args == ("owner-a", 3, 10)
    assert json.dumps(events[0].wire("question:thor:x"))
