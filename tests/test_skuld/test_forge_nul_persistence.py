"""End-to-end NUL-byte tolerance proof: forge producer → durable log → Volundr PG.

WHY THIS FILE EXISTS
--------------------
Volundr's /log persistence path used to 500 on NUL bytes. Agent output (crash
dumps, hang-detector listings, raw terminal bytes) regularly contains the NUL
code point (U+0000). ``json.dumps`` escapes it to the valid JSON sequence
``\\u0000``, but a PostgreSQL JSONB column CANNOT store that escape — asyncpg
raises ``UntranslatableCharacterError``. Because the write paths use
``executemany``, a single poisoned frame failed the WHOLE batch, so every frame
in it was dropped and sessions looked frozen while the agent was still working.

The authoritative fix + its unit/integration proof live with the outbound
adapters (``src/volundr/adapters/outbound/_jsonb.py`` and the
``tests/test_adapters`` / ``tests/integration/volundr`` suites owned by the
adapter agent). THIS file is COMPLEMENTARY producer-side coverage: it proves the
forge tmux producer can EMIT binary (NUL-bearing) content, that the binary frame
survives into the broker's durable event log without crashing the producer, that
``rebuild_turns`` tolerates it, and — the payoff — that the harness-shaped
``SessionLogEntry`` list PERSISTS through a REAL Volundr ``PostgresSessionEventLog``
against real PostgreSQL and round-trips back out.

Honest scoping: the tmux/broker tests below are producer-side (no Volundr backend
in the loop). The real-PG persistence proof (``test_*_persists_to_real_pg*``) is
the genuine end-to-end seam and is the part that would have caught the 500.

All tests are ``@pytest.mark.integration`` (the default pytest addopts deselect
them) and the tmux ones skip when tmux is unavailable.

NUL CONSTRUCTION
----------------
Every NUL byte in this file is built at runtime via ``chr(0)`` — NEVER a literal
NUL in the source (a literal NUL is exactly the bug; it would corrupt the file).
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from tests.support.forge import BrokerHarness

_NUL = chr(0)

# Real-PG connection for the payoff tests. Defaults match the platform DB the
# Forge stack uses locally (volundr/volundr/volundr); every field is env-
# overridable so CI can point elsewhere. The schema is assumed already migrated
# (the running platform owns session_event_log); we skip if it is not reachable.
_DB_HOST = os.environ.get("TEST_DATABASE_HOST", "localhost")
_DB_PORT = int(os.environ.get("TEST_DATABASE_PORT", "5432"))
_DB_USER = os.environ.get("TEST_DATABASE_USER", "volundr")
_DB_PASSWORD = os.environ.get("TEST_DATABASE_PASSWORD", "volundr")
_DB_NAME = os.environ.get("TEST_DATABASE_NAME", "volundr")


def _require_tmux() -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")


@pytest_asyncio.fixture
async def db_pool():
    """Real asyncpg pool against the platform DB; skip the test if unreachable.

    Self-contained on purpose: the shared integration fixtures live under
    ``tests/integration/`` and are not visible from ``tests/test_skuld/``. We do
    NOT run migrations here — the live platform already owns the
    ``session_event_log`` table; a missing table skips rather than masks a real
    schema problem.
    """
    import asyncpg

    try:
        pool = await asyncpg.create_pool(
            host=_DB_HOST,
            port=_DB_PORT,
            user=_DB_USER,
            password=_DB_PASSWORD,
            database=_DB_NAME,
            min_size=1,
            max_size=2,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001 - any connect failure -> skip, not error
        pytest.skip(f"real PostgreSQL not reachable: {type(exc).__name__}: {exc}")

    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.session_event_log') IS NOT NULL")
    if not exists:
        await pool.close()
        pytest.skip("session_event_log table is not migrated in the target DB")

    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def txn_pool(db_pool):
    """Per-test transactional wrapper that ROLLS BACK — no DB pollution.

    Mirrors ``tests/integration/volundr/conftest.py``: acquire one connection,
    open a transaction, hand back a ``TransactionalPool`` whose ``acquire()``
    re-yields that same connection, then roll back afterward.
    """
    from tests.integration.pool_wrapper import TransactionalPool

    conn = await db_pool.acquire()
    txn = conn.transaction()
    await txn.start()
    try:
        yield TransactionalPool(conn)
    finally:
        await txn.rollback()
        await db_pool.release(conn)


def _payload_contains_nul(value: object) -> bool:
    """Recursively check whether any string leaf / dict key carries a NUL."""
    if isinstance(value, str):
        return _NUL in value
    if isinstance(value, dict):
        return any(_NUL in k for k in value if isinstance(k, str)) or any(
            _payload_contains_nul(v) for v in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_payload_contains_nul(v) for v in value)
    return False


# --------------------------------------------------------------------------- producer + rebuild


@pytest.mark.integration
@pytest.mark.asyncio
async def test_nul_turn_lands_in_durable_log_and_rebuilds() -> None:
    """Producer side: a NUL-bearing turn reaches the durable log and rebuilds.

    Drives a real tmux session whose fakeagent emits an assistant line embedding
    a NUL byte. Asserts (i) the producer did NOT crash and the durable event-log
    buffer carries a frame whose payload still contains the NUL, and (ii)
    ``rebuild_turns`` handles those entries without error.
    """
    _require_tmux()

    from volundr.domain.services.transcript_rebuild import rebuild_turns

    async with BrokerHarness(hooks=True, idle_timeout_s=0.3) as h:
        client = await h.connect()

        # The fakeagent splices a real NUL into the assistant content.
        client.send({"type": "message", "content": "nul:crashdump"})

        # The turn closes with a result — proves the producer survived the NUL.
        await client.wait_for(
            lambda frames: any(f.get("type") == "result" for f in frames),
            timeout=8.0,
        )

        log = h.event_log
        # The durable buffer must carry the binary frame, NUL intact (the broker
        # does not sanitize — sanitization is Volundr's job at the JSONB seam).
        assert any(_payload_contains_nul(entry.get("payload")) for entry in log), (
            "durable log dropped or sanitized the NUL-bearing frame; "
            f"kinds={[e.get('kind') for e in log]}"
        )

        # rebuild_turns must tolerate the binary entries without raising.
        entries = h.event_log_entries()
        rebuilt = rebuild_turns(entries)

    assert rebuilt.turns, "rebuild_turns produced no turns from the NUL-bearing log"


# --------------------------------------------------------------------------- payoff: real PG


def _harness_entry_payload_with_nul():
    """A SessionLogEntry payload shaped exactly like a broker assistant frame.

    Mirrors what ``_enqueue_event_log`` buffers: the raw CLI frame is the
    ``payload``. The NUL is spliced into the assistant text (a string leaf) AND
    into a dict key, so the persistence path is exercised against both — matching
    the recursive guarantee the ``_jsonb`` helper makes.
    """
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": f"crashdump{_NUL}[nul] tail"}],
        },
        # NUL in a dict KEY, plus a nested NUL value and preserved non-ASCII.
        f"meta{_NUL}key": {"detail": f"hang{_NUL}detector", "unicode": "café ✓ 日本語"},
    }


def _make_entries(session_id: uuid.UUID, payload: dict, count: int = 3) -> list:
    """A small batch of SessionLogEntry rows (executemany is the failure mode)."""
    from volundr.domain.models import SessionLogEntry

    now = datetime.now(UTC)
    entries = []
    for seq in range(1, count + 1):
        # Only the middle frame carries the NUL — proves ONE poisoned frame in an
        # executemany batch no longer fails the whole batch (the original bug).
        frame = payload if seq == 2 else {"type": "assistant", "seq_marker": seq}
        entries.append(
            SessionLogEntry(
                session_id=session_id,
                seq=seq,
                kind=str(frame.get("type", "unknown")),
                payload=frame,
                ts=now,
                role="assistant",
                request_id=f"req-{seq}",
            )
        )
    return entries


@pytest.mark.integration
@pytest.mark.asyncio
async def test_nul_entries_persist_to_real_pg_and_round_trip(txn_pool) -> None:
    """THE PAYOFF: harness-shaped NUL entries PERSIST through real Volundr PG.

    Feeds a batch of ``SessionLogEntry`` rows (one carrying a NUL in a string
    leaf, a nested value, AND a dict key) to a REAL ``PostgresSessionEventLog``
    against real PostgreSQL via the rolled-back ``txn_pool`` fixture. After the
    ``_jsonb`` fix this ``append`` succeeds and the rows round-trip via
    ``read_after`` — the NUL stripped from the persisted JSONB, all other
    content (including non-ASCII Unicode) preserved.

    BEFORE the fix this exact call raised
    ``asyncpg.exceptions.UntranslatableCharacterError`` on the executemany,
    dropping the whole batch — which is precisely the platform-wide data loss.
    """
    from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog

    session_id = uuid.uuid4()
    payload = _harness_entry_payload_with_nul()
    entries = _make_entries(session_id, payload)

    log = PostgresSessionEventLog(txn_pool)

    written = await log.append(entries)
    assert written == len(entries)

    round_tripped = await log.read_after(session_id, after_seq=0, limit=100)
    assert len(round_tripped) == len(entries), "not all NUL-bearing entries persisted/round-tripped"

    # The NUL must be gone from the persisted JSONB (Postgres cannot hold it),
    # but every other byte — including non-ASCII Unicode — must survive.
    poisoned = next(e for e in round_tripped if e.seq == 2)
    flat = repr(poisoned.payload)
    assert _NUL not in flat, "NUL survived into the persisted JSONB payload"
    assert "crashdump[nul] tail" in poisoned.payload["message"]["content"][0]["text"]
    assert "café ✓ 日本語" in flat, "non-ASCII Unicode was corrupted by sanitization"
    # The NUL-bearing dict key persisted with the NUL stripped (not dropped).
    assert any("metakey" == k for k in poisoned.payload), (
        f"NUL-stripped dict key missing; keys={list(poisoned.payload)}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_pg_rejects_raw_nul_without_sanitization(txn_pool) -> None:
    """Reproduces the ROOT rejection: a raw NUL JSONB write still 500s.

    Pins the mechanism the fix defends against. Writing ``json.dumps`` output
    that contains a ``\\u0000`` escape directly into a JSONB column raises
    ``UntranslatableCharacterError``. This is a guard rail: if Postgres ever
    silently started accepting NUL, the sanitization layer would be dead code and
    this test would fail loudly, prompting a re-think.
    """
    import asyncpg

    bad = '{"text": "crash' + "\\u0000" + '"}'  # literal backslash-u-0-0-0-0 escape
    with pytest.raises(asyncpg.exceptions.UntranslatableCharacterError):
        await txn_pool.execute(
            "INSERT INTO session_event_log "
            "(session_id, seq, kind, role, request_id, payload, ts) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)",
            uuid.uuid4(),
            1,
            "assistant",
            "assistant",
            "req-raw",
            bad,
            datetime.now(UTC),
        )
