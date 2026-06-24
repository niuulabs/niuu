"""Real-PG regression tests for the NUL-byte JSONB persistence bug.

PostgreSQL JSONB cannot store the Unicode NUL code point (U+0000). Before the
fix, agent output containing NUL (crash dumps, hang-detector listings) caused
asyncpg to raise ``UntranslatableCharacterError`` on the JSONB INSERT. Because
both write paths use ``executemany``, one poisoned frame failed the WHOLE batch
and every frame in it was dropped — sessions looked frozen while the agent was
still working.

These tests assert the entry/event now PERSISTS and round-trips with the NUL
stripped, and that valid content is intact. If the sanitizer in
``volundr.adapters.outbound._jsonb`` is reverted, the round-trip assertions
fail because asyncpg raises ``UntranslatableCharacterError`` at append/emit
time (verified by removing the helper and re-running).

Build the NUL byte at runtime via ``chr(0)`` — never paste a literal NUL.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from tests.integration.pool_wrapper import TransactionalPool
from volundr.adapters.outbound.pg_event_sink import PostgresEventSink
from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.domain.models import (
    SessionEvent,
    SessionEventType,
    SessionLogEntry,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

_NUL = chr(0)


@pytest_asyncio.fixture(loop_scope="session")
async def txn_pool() -> TransactionalPool:
    """Per-test transactional wrapper against the already-migrated platform DB.

    This bug only touches two JSONB write paths whose tables (session_event_log,
    session_events) already exist in the live DB, so we connect directly rather
    than depending on the shared ``db_pool`` fixture (which re-applies the full
    migration chain). The transaction is ROLLED BACK after each test, so nothing
    is left behind in the platform database.
    """
    conn = await asyncpg.connect(
        host=os.environ.get("TEST_DATABASE_HOST", "localhost"),
        port=int(os.environ.get("TEST_DATABASE_PORT", "5432")),
        user=os.environ.get("TEST_DATABASE_USER", "volundr"),
        password=os.environ.get("TEST_DATABASE_PASSWORD", "volundr"),
        database=os.environ.get("TEST_DATABASE_NAME", "volundr"),
    )
    txn = conn.transaction()
    await txn.start()
    try:
        yield TransactionalPool(conn)
    finally:
        await txn.rollback()
        await conn.close()


async def test_session_event_log_persists_nul_bearing_payload(txn_pool):
    """append() of a NUL-bearing payload persists and round-trips, NUL stripped."""
    log = PostgresSessionEventLog(txn_pool)
    session_id = uuid4()
    payload = {
        "type": "assistant",
        "text": f"crash{_NUL}dump",
        "nested": {f"ke{_NUL}y": [f"x{_NUL}y", "café 日本語"]},
    }
    entry = SessionLogEntry(
        session_id=session_id,
        seq=1,
        kind="assistant",
        payload=payload,
        ts=datetime.now(UTC),
        role="assistant",
        request_id="forge-web-1",
    )

    submitted = await log.append([entry])
    assert submitted == 1

    rows = await log.read_after(session_id, after_seq=0, limit=10)
    assert len(rows) == 1
    stored = rows[0].payload
    # NUL stripped everywhere, valid content (incl. unicode) preserved.
    assert stored["text"] == "crashdump"
    assert stored["nested"] == {"key": ["xy", "café 日本語"]}


async def test_event_sink_persists_nul_bearing_data(txn_pool):
    """emit_batch() of a NUL-bearing event persists and round-trips, NUL stripped."""
    # session_events has a FK to sessions(id); insert a minimal session row.
    session_id = uuid4()
    await txn_pool.execute(
        """INSERT INTO sessions (id, name, model, repo, branch, status)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        session_id,
        "nul-test",
        "claude-sonnet-4-20250514",
        "repo",
        "main",
        "running",
    )

    sink = PostgresEventSink(txn_pool)
    data = {
        "content_preview": f"hang{_NUL}listing",
        "items": [f"a{_NUL}b", {f"de{_NUL}ep": "café 日本語"}],
    }
    event = SessionEvent(
        id=uuid4(),
        session_id=session_id,
        event_type=SessionEventType.MESSAGE_ASSISTANT,
        timestamp=datetime.now(UTC),
        data=data,
        sequence=0,
        tokens_in=10,
        tokens_out=20,
        cost=Decimal("0.001"),
        model="claude-sonnet-4-20250514",
    )

    await sink.emit_batch([event])

    events = await sink.get_events(session_id)
    assert len(events) == 1
    stored = events[0].data
    assert stored["content_preview"] == "hanglisting"
    assert stored["items"] == ["ab", {"deep": "café 日本語"}]
