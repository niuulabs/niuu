"""Atomic, append-only native history adoption into an idle Forge session."""

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from niuu.domain.text_projection import repair_legacy_turn, repair_marker
from tests.test_domain.test_text_projection_repair import captured
from volundr.adapters.outbound.pg_session_event_log import _INSERT_SQL, PostgresSessionEventLog
from volundr.domain.history_import import (
    HistoryImportConflictError,
    HistoryImportSessionNotFoundError,
    HistoryImportValidationError,
)
from volundr.domain.models import SessionLogEntry
from volundr.domain.services.transcript_rebuild import rebuild_turns

_NATIVE_TS = datetime(2026, 9, 8, 7, 43, 4, tzinfo=UTC)
_SOURCE = "codex-native:original-thread"


def _entry(session_id, **overrides):
    return SessionLogEntry(
        **{
            "session_id": session_id,
            "seq": 1,
            "kind": "assistant",
            "payload": {"type": "assistant", "content": [{"type": "text", "text": "Recovered"}]},
            "ts": _NATIVE_TS,
            "role": "assistant",
            "request_id": "native-turn-1",
            **overrides,
        }
    )


def _repository(status="stopped", *, captured=None, markers=None, head=6):
    pool = MagicMock()
    conn = AsyncMock()
    conn.transaction = MagicMock()
    conn.fetchrow.side_effect = [None if status is None else {"status": status}, captured]
    conn.fetch.return_value = markers or []
    conn.fetchval.return_value = head
    pool.acquire.return_value.__aenter__.return_value = conn
    return PostgresSessionEventLog(pool), conn


async def test_import_preserves_head_order_timestamps_and_appends_completion_marker():
    sid = uuid4()
    repo, conn = _repository()
    entries = [_entry(sid, seq=10), _entry(sid, seq=20, kind="result", payload={"type": "result"})]

    assert await repo.import_history(sid, _SOURCE, entries) == 2

    assert "FOR UPDATE" in conn.fetchrow.call_args_list[0].args[0]
    assert conn.fetchrow.call_args_list[0].args[1] == sid
    sql, args = conn.executemany.call_args.args
    assert "ON CONFLICT" not in sql  # a collision must abort the entire import
    assert [arg[1] for arg in args] == [7, 8, 9]
    assert all(arg[0] == sid for arg in args)
    assert [arg[6] for arg in args[:2]] == [_NATIVE_TS, _NATIVE_TS]
    assert args[0][3:5] == ("assistant", "native-turn-1")
    assert json.loads(args[0][5]) == entries[0].payload
    assert args[-1][2] == "history_import"
    marker = json.loads(args[-1][5])
    assert marker == {
        "type": "history_import",
        "source_id": _SOURCE,
        **repo._history_snapshot(entries),
    }
    assert marker["count"] == 2
    assert marker["partial"] is False
    assert len(marker["snapshot_sha256"]) == 64
    assert [entry.seq for entry in entries] == [10, 20]  # immutable input
    assert conn.transaction.call_count == 2  # transaction + scrub retry savepoint


@pytest.mark.parametrize("status", ["created", "stopped", "failed"])
async def test_import_accepts_idle_session_statuses(status):
    sid = uuid4()
    repo, conn = _repository(status, head=None)
    assert await repo.import_history(sid, _SOURCE, [_entry(sid)]) == 1
    assert conn.executemany.call_args.args[1][0][1] == 1


@pytest.mark.parametrize("status", ["running", "starting", "provisioning", "stopping", "archived"])
async def test_import_refuses_nonidle_session_statuses(status):
    sid = uuid4()
    repo, conn = _repository(status)
    with pytest.raises(HistoryImportConflictError, match="must be created, stopped, or failed"):
        await repo.import_history(sid, _SOURCE, [_entry(sid)])
    conn.executemany.assert_not_awaited()


async def test_import_refuses_missing_session():
    sid = uuid4()
    repo, conn = _repository(None)
    with pytest.raises(HistoryImportSessionNotFoundError):
        await repo.import_history(sid, _SOURCE, [_entry(sid)])
    conn.executemany.assert_not_awaited()


@pytest.mark.parametrize(
    "kind", ["user", "assistant", "tool_result", "conversation.turn", "unknown"]
)
async def test_import_refuses_captured_history(kind):
    sid = uuid4()
    repo, conn = _repository(captured={"seq": 7, "kind": kind})
    with pytest.raises(
        HistoryImportConflictError, match="already contains captured history at seq 7"
    ):
        await repo.import_history(sid, _SOURCE, [_entry(sid)])
    conn.executemany.assert_not_awaited()


@pytest.mark.parametrize("serialized", [False, True])
async def test_same_source_retry_is_noop_even_after_restart(serialized):
    sid = uuid4()
    entries = [_entry(sid)]
    marker = {"source_id": _SOURCE, **PostgresSessionEventLog._history_snapshot(entries)}
    repo, conn = _repository(
        "running", markers=[{"payload": json.dumps(marker) if serialized else marker}]
    )

    assert await repo.import_history(sid, _SOURCE, entries) == 0

    conn.executemany.assert_not_awaited()
    conn.fetchval.assert_not_awaited()


async def test_different_import_source_is_a_conflict():
    sid = uuid4()
    repo, conn = _repository(markers=[{"payload": {"source_id": "a-different-thread"}}])
    with pytest.raises(HistoryImportConflictError, match="different history source"):
        await repo.import_history(sid, _SOURCE, [_entry(sid)])
    conn.executemany.assert_not_awaited()


@pytest.mark.parametrize("change", ["text", "timestamp", "count", "source_hash", "partial"])
async def test_same_native_id_changed_snapshot_cannot_report_idempotent_success(change):
    sid = uuid4()
    original = _entry(
        sid,
        payload={
            "type": "assistant",
            "content": "first",
            "metadata": {"native_import": {"source_sha256": "before", "partial": True}},
        },
    )
    marker = {"source_id": _SOURCE, **PostgresSessionEventLog._history_snapshot([original])}
    repo, conn = _repository(markers=[{"payload": marker}])
    changed = json.loads(json.dumps(original.payload))
    timestamp = original.ts
    if change == "text":
        changed["content"] = "new content"
    elif change == "source_hash":
        changed["metadata"]["native_import"]["source_sha256"] = "after"
    elif change == "partial":
        changed["metadata"]["native_import"]["partial"] = False
    elif change == "timestamp":
        timestamp = datetime(2026, 9, 8, 8, 0, tzinfo=UTC)
    entries = [replace(original, payload=changed, ts=timestamp)]
    if change == "count":
        entries.append(replace(original, seq=2))

    with pytest.raises(HistoryImportConflictError, match="snapshot changed"):
        await repo.import_history(sid, _SOURCE, entries)

    conn.executemany.assert_not_awaited()


async def test_legacy_marker_without_snapshot_is_not_treated_as_verified_success():
    sid = uuid4()
    repo, conn = _repository(markers=[{"payload": {"source_id": _SOURCE, "count": 1}}])
    with pytest.raises(HistoryImportConflictError, match="snapshot changed"):
        await repo.import_history(sid, _SOURCE, [_entry(sid)])
    conn.executemany.assert_not_awaited()


def test_snapshot_excludes_destination_uuid_and_cursor_but_preserves_native_identity():
    first_sid, second_sid = uuid4(), uuid4()
    first = _entry(first_sid, seq=1, payload={"session_id": str(first_sid), "text": "same"})
    second = _entry(second_sid, seq=912, payload={"text": "same", "session_id": str(second_sid)})
    snapshot = PostgresSessionEventLog._history_snapshot
    assert snapshot([first]) == snapshot([second])
    native_one = replace(first, payload={"session_id": "native-one", "text": "same"})
    native_two = replace(first, payload={"session_id": "native-two", "text": "same"})
    assert snapshot([native_one]) != snapshot([native_two])


def test_snapshot_is_order_sensitive():
    sid = uuid4()
    first = _entry(sid, payload={"text": "first"})
    second = _entry(sid, payload={"text": "second"})
    snapshot = PostgresSessionEventLog._history_snapshot
    assert snapshot([first, second]) != snapshot([second, first])


@pytest.mark.parametrize("source", ["", " ", None, "bad\x00identity"])
async def test_invalid_source_is_rejected_before_database_access(source):
    sid = uuid4()
    repo, conn = _repository()
    with pytest.raises(HistoryImportValidationError):
        await repo.import_history(sid, source, [_entry(sid)])
    conn.fetchrow.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["empty", "foreign", "marker"])
async def test_invalid_import_frames_are_rejected(invalid):
    sid = uuid4()
    repo, conn = _repository()
    entries = {
        "empty": [],
        "foreign": [_entry(uuid4())],
        "marker": [_entry(sid, kind="history_import")],
    }[invalid]
    with pytest.raises(HistoryImportValidationError):
        await repo.import_history(sid, _SOURCE, entries)
    conn.fetchrow.assert_not_awaited()


async def test_import_scrubs_nul_and_keeps_atomic_retry_inside_locked_transaction():
    sid = uuid4()
    repo, conn = _repository()
    conn.executemany.side_effect = [asyncpg.UntranslatableCharacterError("bad character"), None]

    assert await repo.import_history(sid, _SOURCE, [_entry(sid, payload={"text": "a\x00b"})]) == 1

    assert conn.executemany.await_count == 2
    assert json.loads(conn.executemany.call_args.args[1][0][5]) == {"text": "a�b"}
    assert conn.executemany.call_args_list[0].args[1] == conn.executemany.call_args_list[1].args[1]


async def test_append_uses_same_session_lock_in_stable_session_order():
    pool = AsyncMock()
    repo = PostgresSessionEventLog(pool)
    lower, higher = sorted([uuid4(), uuid4()])

    await repo.append([_entry(higher), _entry(lower, seq=2), _entry(lower, seq=1)])

    sql, args = pool.executemany.call_args.args
    assert "SELECT id FROM sessions WHERE id = $1 FOR UPDATE" in sql
    assert [(arg[0], arg[1]) for arg in args] == [(lower, 2), (lower, 1), (higher, 1)]


@pytest_asyncio.fixture
async def real_history_pool():
    """Opt-in real PostgreSQL, isolated schema; never migrate or touch user sessions."""
    dsn = os.environ.get("FORGE_HISTORY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set FORGE_HISTORY_TEST_DATABASE_URL for isolated PostgreSQL import checks")
    schema = "forge_history_test_" + uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = None
    try:
        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=4, server_settings={"search_path": schema}
        )
        await pool.execute("CREATE TABLE sessions (id uuid PRIMARY KEY, status text NOT NULL)")
        await pool.execute(
            """CREATE TABLE session_event_log (
                session_id uuid NOT NULL, seq bigint NOT NULL, kind text NOT NULL,
                role text, request_id text, payload jsonb NOT NULL, ts timestamptz NOT NULL,
                PRIMARY KEY (session_id, seq)
            )"""
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@pytest.mark.parametrize("status", ["running", "starting", "stopping", "failed", "created"])
async def test_projection_repair_refuses_every_nonstopped_status(status):
    _, original, candidate = captured()
    marker = repair_marker([repair_legacy_turn(original, candidate, source_head=9)])
    repo, conn = _repository(status)
    with pytest.raises(ValueError, match="stopped session"):
        await repo.append_projection_repair(uuid4(), marker, expected_head=10)
    conn.execute.assert_not_awaited()


async def test_projection_repair_refuses_changed_head_before_reading_or_writing_seeds():
    _, original, candidate = captured()
    marker = repair_marker([repair_legacy_turn(original, candidate, source_head=9)])
    repo, conn = _repository("stopped", head=11)
    with pytest.raises(ValueError, match="head changed"):
        await repo.append_projection_repair(uuid4(), marker, expected_head=10)
    conn.execute.assert_not_awaited()
    assert conn.fetch.await_count == 1


async def test_projection_repair_rejects_scrubbing_that_would_invalidate_its_proof():
    _, original, candidate = captured()
    candidate["parts"][0]["id"] = "native\x00message"
    marker = repair_marker([repair_legacy_turn(original, candidate, source_head=9)])
    repo, conn = _repository("stopped", head=10)
    with pytest.raises(ValueError, match="exact JSONB-safe content"):
        await repo.append_projection_repair(uuid4(), marker, expected_head=10)
    conn.execute.assert_not_awaited()


@pytest.mark.integration
async def test_real_projection_repair_preserves_raw_prefix_and_cold_rebuild(real_history_pool):
    pool = real_history_pool
    sid = uuid4()
    await pool.execute("INSERT INTO sessions VALUES ($1, 'stopped')", sid)
    repo = PostgresSessionEventLog(pool)
    frames, original, candidate = captured()
    raw = [_entry(sid, seq=frame.seq, kind=frame.kind, payload=frame.payload) for frame in frames]
    raw.append(_entry(sid, seq=10, kind="conversation.turn", payload={"turn": original}))
    await repo.append(raw)
    before = await repo.read_after(sid)
    replacement = repair_legacy_turn(original, candidate, source_head=9)
    marker = repair_marker([replacement])
    assert await repo.append_projection_repair(sid, marker, expected_head=10) == 11
    after = await repo.read_after(sid)
    assert after[:-1] == before
    assert after[-1].payload == marker
    assert rebuild_turns(after).turns == [replacement]
    # An exact retry is harmless even after normal runtime resumes/appends.
    await pool.execute("UPDATE sessions SET status='running' WHERE id=$1", sid)
    await repo.append([_entry(sid, seq=12, kind="system", payload={"type": "system"})])
    assert await repo.append_projection_repair(sid, marker, expected_head=10) == 11
    assert await repo.latest_seq(sid) == 12


@pytest.mark.integration
async def test_real_projection_repair_rejects_invalid_proof_atomically(real_history_pool):
    pool = real_history_pool
    sid = uuid4()
    await pool.execute("INSERT INTO sessions VALUES ($1, 'stopped')", sid)
    repo = PostgresSessionEventLog(pool)
    _, original, candidate = captured()
    await repo.append([_entry(sid, seq=1, kind="conversation.turn", payload={"turn": original})])
    before = await repo.read_after(sid)
    marker = repair_marker([repair_legacy_turn(original, candidate, source_head=1)])
    marker["repairs"][0]["parts"][0]["text"] += "corruption"
    with pytest.raises(ValueError, match="content verification"):
        await repo.append_projection_repair(sid, marker, expected_head=1)
    assert await repo.read_after(sid) == before


@pytest.mark.integration
async def test_real_import_round_trip_preserves_chrome_and_native_time(real_history_pool):
    pool = real_history_pool
    sid = uuid4()
    await pool.execute("INSERT INTO sessions VALUES ($1, 'stopped')", sid)
    repo = PostgresSessionEventLog(pool)
    chrome = [
        ("system", {"subtype": "init"}),
        ("available_commands", {"type": "available_commands", "commands": []}),
        ("system", {"_per_connect_handshake": True}),
        ("capabilities", {"type": "capabilities"}),
        ("system", {"_per_connect_handshake": True}),
        ("capabilities", {"type": "capabilities"}),
    ]
    await repo.append(
        [
            _entry(sid, seq=seq, kind=kind, payload=payload)
            for seq, (kind, payload) in enumerate(chrome, 1)
        ]
    )
    before = await repo.read_after(sid)

    entries = [_entry(sid, payload={"text": "a\x00b"})]
    assert await repo.import_history(sid, _SOURCE, entries) == 1
    assert await repo.import_history(sid, _SOURCE, entries) == 0
    after = await repo.read_after(sid)

    assert after[:6] == before
    assert [row.seq for row in after] == list(range(1, 9))
    assert after[6].ts == _NATIVE_TS
    assert after[6].payload == {"text": "a�b"}
    assert after[7].payload["source_id"] == _SOURCE
    assert after[7].payload["snapshot_sha256"] == repo._history_snapshot(entries)["snapshot_sha256"]
    with pytest.raises(HistoryImportConflictError, match="snapshot changed"):
        await repo.import_history(sid, _SOURCE, [_entry(sid)])
    assert await repo.read_after(sid) == after
    with pytest.raises(HistoryImportConflictError):
        await repo.import_history(sid, "another-source", [_entry(sid)])


@pytest.mark.integration
@pytest.mark.parametrize(
    "kind", ["user", "assistant", "tool_result", "conversation.turn", "unknown", "system"]
)
async def test_real_import_rejects_captured_or_unrecognized_history(real_history_pool, kind):
    pool = real_history_pool
    sid = uuid4()
    await pool.execute("INSERT INTO sessions VALUES ($1, 'stopped')", sid)
    repo = PostgresSessionEventLog(pool)
    await repo.append([_entry(sid, kind=kind, payload={})])

    with pytest.raises(HistoryImportConflictError):
        await repo.import_history(sid, _SOURCE, [_entry(sid)])

    assert await repo.latest_seq(sid) == 1


@pytest.mark.integration
async def test_real_import_rolls_back_all_frames_when_marker_fails(real_history_pool, monkeypatch):
    pool = real_history_pool
    sid = uuid4()
    await pool.execute("INSERT INTO sessions VALUES ($1, 'stopped')", sid)
    repo = PostgresSessionEventLog(pool)
    original = repo._entry_to_args

    def poison_marker(entry):
        args = original(entry)
        if entry.kind == "history_import":
            return (*args[:5], "invalid JSON", args[6])
        return args

    monkeypatch.setattr(repo, "_entry_to_args", poison_marker)
    with pytest.raises(asyncpg.InvalidTextRepresentationError):
        await repo.import_history(sid, _SOURCE, [_entry(sid), _entry(sid)])
    assert await repo.latest_seq(sid) == 0

    monkeypatch.setattr(repo, "_entry_to_args", original)
    assert await repo.import_history(sid, _SOURCE, [_entry(sid)]) == 1


@pytest.mark.integration
async def test_real_import_waits_for_inflight_append_then_refuses_merge(real_history_pool):
    pool = real_history_pool
    sid, unrelated = uuid4(), uuid4()
    await pool.executemany("INSERT INTO sessions VALUES ($1, 'stopped')", [(sid,), (unrelated,)])
    repo = PostgresSessionEventLog(pool)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(_INSERT_SQL, [repo._entry_to_args(_entry(sid))])
            importing = asyncio.create_task(repo.import_history(sid, _SOURCE, [_entry(sid)]))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(importing), timeout=0.1)
            # Another session is independent of this session's row lock.
            assert await asyncio.wait_for(repo.append([_entry(unrelated)]), timeout=2) == 1
        with pytest.raises(HistoryImportConflictError):
            await asyncio.wait_for(importing, timeout=2)
    assert await repo.latest_seq(sid) == 1


@pytest.mark.integration
async def test_real_append_waits_for_import_session_lock(real_history_pool):
    pool = real_history_pool
    sid = uuid4()
    await pool.execute("INSERT INTO sessions VALUES ($1, 'stopped')", sid)
    repo = PostgresSessionEventLog(pool)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.fetchrow("SELECT status FROM sessions WHERE id = $1 FOR UPDATE", sid)
            appending = asyncio.create_task(repo.append([_entry(sid)]))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(appending), timeout=0.1)
        assert await asyncio.wait_for(appending, timeout=2) == 1
    assert await repo.latest_seq(sid) == 1
