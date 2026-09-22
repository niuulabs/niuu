"""Real PostgreSQL contract for CI's migrated test DB. No local database is started."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.domain.models import GitSource, Session, SessionLogEntry, SessionStatus
from volundr.domain.session_read_state import SessionReadStateChange, SessionReadStateConflictError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def final(session_id, seq):
    return SessionLogEntry(
        session_id=session_id,
        seq=seq,
        kind="conversation.turn",
        role="assistant",
        ts=datetime.now(UTC),
        payload={
            "type": "conversation.turn",
            "turn": {
                "id": f"final-{seq}",
                "role": "assistant",
                "content": "Finished",
                "visibility": "public",
                "metadata": {"final_output": True},
            },
        },
    )


async def test_persisted_final_read_and_duplicate_do_not_resurrect_unread(txn_pool):
    repo = PostgresSessionRepository(txn_pool)
    log = PostgresSessionEventLog(txn_pool)
    session = Session(
        id=uuid4(),
        name="read-contract",
        model="test",
        source=GitSource(repo="https://example.test/repo", branch="main"),
        status=SessionStatus.STOPPED,
    )
    await repo.create(session)
    await log.append([final(session.id, 10)])
    state = (await repo.get_read_states([session.id], "reader-a"))[session.id]
    assert state.is_unread and state.latest_output_turn_id == "final-10"
    state = await repo.change_read_state(
        session.id,
        "reader-a",
        SessionReadStateChange(state="read", through_seq=10, expected_revision=0),
    )
    assert not state.is_unread
    await log.append([final(session.id, 10)])
    assert not (await repo.get_read_states([session.id], "reader-a"))[session.id].is_unread
    assert (await repo.get_read_states([session.id], "reader-b"))[session.id].is_unread
    await log.append([final(session.id, 20)])
    state = await repo.change_read_state(
        session.id,
        "reader-a",
        SessionReadStateChange(state="read", through_seq=10, expected_revision=1),
    )
    assert state.is_unread and state.latest_output_seq == 20
    state = await repo.change_read_state(
        session.id,
        "reader-a",
        SessionReadStateChange(state="unread", through_seq=20, expected_revision=2),
    )
    with pytest.raises(SessionReadStateConflictError):
        await repo.change_read_state(
            session.id,
            "reader-a",
            SessionReadStateChange(state="read", through_seq=20, expected_revision=2),
        )
    assert (await repo.get_read_states([session.id], "reader-a"))[session.id].manually_unread
    assert (await repo.get(session.id)).status == SessionStatus.STOPPED
    await repo.delete(session.id)
    assert (
        await txn_pool.fetchval(
            "SELECT count(*) FROM session_read_states WHERE session_id=$1", session.id
        )
        == 0
    )
