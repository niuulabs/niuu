"""Real PostgreSQL: Forge keeps G10's work facts at log ingest (F12d).

Each derived-state golden's rows are appended through the production insert SQL; the
session read back carries the reference's ``last_work_at``/``tool_open``, and the
fields-only derivation over it gives the golden's expected state.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.adapters.outbound.postgres import PostgresSessionRepository
from volundr.domain.models import GitSource, Session, SessionLogEntry, SessionStatus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "control_plane"
CASES = json.loads((FIXTURES / "derived-state.json").read_text())["cases"]
_spec = importlib.util.spec_from_file_location("dsr", FIXTURES / "derived_state_reference.py")
REF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REF)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _entries(session_id, rows) -> list[SessionLogEntry]:
    out = []
    for seq, row in enumerate(rows, start=1):
        payload: dict = {"type": row["kind"]}
        if row.get("hook"):
            payload["hook_event_name"] = row["hook"]
            payload["payload"] = {"hook_event_name": row["hook"]}
        out.append(
            SessionLogEntry(
                session_id=session_id, seq=seq, kind=row["kind"], payload=payload, ts=_ts(row["ts"])
            )
        )
    return out


async def _session(repo) -> Session:
    session = Session(
        id=uuid4(),
        name="work-facts",
        model="test",
        source=GitSource(repo="https://example.test/repo", branch="main"),
        status=SessionStatus.RUNNING,
    )
    await repo.create(session)
    return session


@pytest.mark.parametrize("case", [c for c in CASES if c["rows"]], ids=lambda c: c["name"][:50])
async def test_ingest_keeps_reference_work_facts(txn_pool, case):
    repo, log = PostgresSessionRepository(txn_pool), PostgresSessionEventLog(txn_pool)
    session = await _session(repo)
    entries = _entries(session.id, case["rows"])
    # One frame per batch, then the whole batch again: a producer retry folds nothing twice.
    for entry in entries:
        await log.append([entry])
    await log.append(entries)
    stored = await repo.get(session.id)
    ref_last, ref_open = REF.work_facts(case["rows"])
    assert stored.last_work_at == ref_last
    assert stored.tool_open is ref_open

    fields = dict(case["session"])
    fields["last_work_at"] = stored.last_work_at.isoformat() if stored.last_work_at else None
    fields["tool_open"] = stored.tool_open
    got = REF.derive(fields, None, case["open_native_asks"], _ts(case["now"]))
    assert got["state"] == case["expect"]["state"]
    if "basis" in case["expect"]:
        assert got["basis"] == case["expect"]["basis"]


async def test_untouched_session_has_no_work_time(txn_pool):
    repo = PostgresSessionRepository(txn_pool)
    session = await _session(repo)
    stored = await repo.get(session.id)
    assert stored.last_work_at is None
    assert stored.tool_open is False


async def test_generic_session_update_does_not_clobber_work_facts(txn_pool):
    repo, log = PostgresSessionRepository(txn_pool), PostgresSessionEventLog(txn_pool)
    session = await _session(repo)
    at = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    await log.append(
        [
            SessionLogEntry(
                session_id=session.id,
                seq=1,
                kind="claude_hook",
                payload={"type": "claude_hook", "hook_event_name": "PreToolUse"},
                ts=at,
            )
        ]
    )
    stale = await repo.get(session.id)
    stale.last_work_at, stale.open_tool_count = None, 0
    stale.name = "renamed"
    await repo.update(stale)
    stored = await repo.get(session.id)
    assert stored.name == "renamed"
    assert stored.last_work_at == at and stored.tool_open is True


async def test_update_returns_the_stored_facts_not_the_callers_copy(txn_pool):
    repo, log = PostgresSessionRepository(txn_pool), PostgresSessionEventLog(txn_pool)
    session = await _session(repo)
    stale = await repo.get(session.id)
    at = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    await log.append(
        [
            SessionLogEntry(
                session_id=session.id,
                seq=1,
                kind="claude_hook",
                payload={"type": "claude_hook", "hook_event_name": "PreToolUse"},
                ts=at,
            )
        ]
    )
    returned = await repo.update(stale)
    assert returned.last_work_at == at and returned.tool_open is True


async def test_frames_that_change_nothing_do_not_write_the_session(txn_pool):
    repo, log = PostgresSessionRepository(txn_pool), PostgresSessionEventLog(txn_pool)
    session = await _session(repo)
    new = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)
    old = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)

    def entry(seq, kind, ts, hook=None):
        payload = {"type": kind}
        if hook:
            payload["hook_event_name"] = hook
        return SessionLogEntry(session_id=session.id, seq=seq, kind=kind, payload=payload, ts=ts)

    await log.append([entry(1, "assistant", new)])
    ctid = await txn_pool.fetchval("SELECT ctid::text FROM sessions WHERE id = $1", session.id)
    # An older work row and a close with nothing open change nothing: no new row version.
    await log.append([entry(2, "assistant", old), entry(3, "claude_hook", old, "PostToolUse")])
    assert (
        await txn_pool.fetchval("SELECT ctid::text FROM sessions WHERE id = $1", session.id) == ctid
    )
    stored = await repo.get(session.id)
    assert stored.last_work_at == new and stored.tool_open is False
