"""INV-3 (idempotency + conflict detection) — full port/adapter-tier contract.

The durable session event log is idempotent on ``(session_id, seq)`` via
``ON CONFLICT (session_id, seq) DO NOTHING``. That single clause carries the
whole INV-3 invariant, and it has two faces:

* **INV-3a/b — idempotent.** The at-least-once producer (skuld) re-appends the
  SAME frame on retry. That MUST be a silent no-op: the row set is unchanged and
  ``detect_conflicts`` does NOT flag it (an identical re-append is correct, not a
  bug). ``append`` itself never grows the row.

* **INV-3c — conflict surfaced, not swallowed.** ``ON CONFLICT DO NOTHING`` would
  ALSO silently swallow a genuine bug: a *different* payload reusing a seq another
  frame already owns. The hot path must stay DO-NOTHING (we never want a retry to
  500), so the detection is a separate, cold, queryable signal —
  ``SessionEventLogRepository.detect_conflicts`` — that reads stored rows back and
  reports the seqs whose stored frame DIFFERS from the candidate.

This suite asserts that contract at the port tier (the concrete default the
in-memory fakes inherit) and at the PG-adapter tier (the bounded ``ANY`` SELECT
override), reusing the shared ``InMemoryLog`` fake by import and a fake asyncpg
pool mirroring ``test_pg_session_event_log.py``. No real PG, no Docker.

Independently-derived expectation: each assertion compares ``detect_conflicts``
output against the seqs we KNOW we mutated when seeding, never against a second
run of the same method.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

# ``InMemoryLog`` is reused by IMPORT — never redefined. Its whole point for this
# suite is that it NEVER defines ``detect_conflicts``: the method must come from
# the port's concrete default, proving the contract is inherited free.
from tests.test_adapters.test_rest_session_log import InMemoryLog
from volundr.adapters.outbound.pg_session_event_log import PostgresSessionEventLog
from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository


def _entry(session_id, seq: int, **overrides) -> SessionLogEntry:
    defaults = {
        "session_id": session_id,
        "seq": seq,
        "kind": "assistant",
        "payload": {"type": "assistant", "n": seq},
        "ts": datetime.now(UTC),
        "role": "assistant",
        "request_id": "req-1",
    }
    defaults.update(overrides)
    return SessionLogEntry(**defaults)


class TestPortContractShape:
    """The fake inherits the port's concrete default with zero edits."""

    def test_inmemory_fake_does_not_redefine_detect_conflicts(self):
        # If the fake ever grows its own override, this suite would be asserting
        # the fake's behaviour, not the shared port contract. Guard against that.
        assert "detect_conflicts" not in InMemoryLog.__dict__
        assert "detect_conflicts" in SessionEventLogRepository.__dict__

    async def test_empty_batch_short_circuits_to_empty(self):
        # Both tiers: no candidates => no conflicts, no read issued.
        assert await InMemoryLog().detect_conflicts([]) == []

        pool = AsyncMock()
        assert await PostgresSessionEventLog(pool).detect_conflicts([]) == []
        pool.fetch.assert_not_called()


class TestIdempotentReappendIsSilentNoop:
    """(a) Same (session_id, seq) + IDENTICAL payload re-append is a no-op."""

    async def test_append_does_not_grow_row_set(self):
        repo = InMemoryLog()
        sid = uuid4()
        original = _entry(sid, 7, payload={"type": "assistant", "n": 7})

        await repo.append([original])
        # The at-least-once producer retries the EXACT same frame.
        await repo.append([_entry(sid, 7, payload={"type": "assistant", "n": 7})])

        rows = await repo.read_after(sid, after_seq=0)
        assert len(rows) == 1  # second append swallowed by ON CONFLICT DO NOTHING
        assert rows[0].seq == 7
        # The STORED row is the first writer's, untouched.
        assert rows[0].payload == {"type": "assistant", "n": 7}

    async def test_first_writer_wins_payload_is_preserved(self):
        repo = InMemoryLog()
        sid = uuid4()
        await repo.append([_entry(sid, 3, payload={"v": "first"})])

        # A LATER append at the same seq with a different payload cannot mutate
        # the stored row (DO NOTHING). The conflict is invisible to read_after —
        # which is exactly why detect_conflicts (INV-3c) has to exist.
        await repo.append([_entry(sid, 3, payload={"v": "second"})])

        stored = (await repo.read_after(sid, after_seq=0))[0]
        assert stored.payload == {"v": "first"}


class TestDetectConflictsPortDefault:
    """(b)+(c) The concrete port default, exercised through the in-memory fake."""

    async def test_identical_reappend_is_not_flagged(self):
        # (b) The idempotent path is NOT a conflict.
        repo = InMemoryLog()
        sid = uuid4()
        stored = _entry(sid, 5, payload={"type": "assistant", "n": 5})
        await repo.append([stored])

        candidate_identical = _entry(sid, 5, payload={"type": "assistant", "n": 5})
        assert await repo.detect_conflicts([candidate_identical]) == []

    async def test_distinct_payload_same_seq_is_flagged(self):
        # (c) A different payload reusing an owned seq is surfaced, not swallowed.
        repo = InMemoryLog()
        sid = uuid4()
        await repo.append([_entry(sid, 5, payload={"type": "assistant", "n": 5})])

        conflicting = _entry(sid, 5, payload={"type": "assistant", "n": 999})
        assert await repo.detect_conflicts([conflicting]) == [5]

    async def test_distinct_kind_is_flagged(self):
        repo = InMemoryLog()
        sid = uuid4()
        await repo.append([_entry(sid, 8, kind="assistant")])

        assert await repo.detect_conflicts([_entry(sid, 8, kind="tool_use")]) == [8]

    async def test_distinct_role_is_flagged(self):
        repo = InMemoryLog()
        sid = uuid4()
        await repo.append([_entry(sid, 9, role="assistant")])

        assert await repo.detect_conflicts([_entry(sid, 9, role="user")]) == [9]

    async def test_distinct_request_id_is_flagged(self):
        repo = InMemoryLog()
        sid = uuid4()
        await repo.append([_entry(sid, 4, request_id="req-a")])

        assert await repo.detect_conflicts([_entry(sid, 4, request_id="req-b")]) == [4]

    async def test_unstored_seq_is_not_a_conflict(self):
        repo = InMemoryLog()
        sid = uuid4()
        await repo.append([_entry(sid, 1)])

        # seq 2 was never stored — re-using it is a fresh append, not a clash.
        assert await repo.detect_conflicts([_entry(sid, 2)]) == []

    async def test_mixed_batch_reports_only_the_distinct_seqs(self):
        # The genuine, non-tautological case: seed a known transcript, re-present a
        # batch where we DELIBERATELY mutate two seqs and leave two identical, then
        # assert detect_conflicts == exactly the seqs we mutated (independently
        # derived from the seeding, not from a second fold).
        repo = InMemoryLog()
        sid = uuid4()
        seeded = [
            _entry(sid, 1, payload={"n": 1}),
            _entry(sid, 2, payload={"n": 2}),
            _entry(sid, 3, payload={"n": 3}),
            _entry(sid, 4, payload={"n": 4}),
        ]
        await repo.append(seeded)

        mutated_seqs = {2, 4}
        candidate_batch = [
            _entry(sid, 1, payload={"n": 1}),  # identical
            _entry(sid, 2, payload={"n": 222}),  # distinct
            _entry(sid, 3, payload={"n": 3}),  # identical
            _entry(sid, 4, payload={"n": 444}),  # distinct
        ]

        conflicts = await repo.detect_conflicts(candidate_batch)
        assert conflicts == sorted(mutated_seqs)

    async def test_conflicts_are_grouped_per_session(self):
        repo = InMemoryLog()
        sid_a, sid_b = uuid4(), uuid4()
        # Same seq value (1) in two sessions — grouping must not cross-contaminate.
        await repo.append([_entry(sid_a, 1, payload={"n": 1})])
        await repo.append([_entry(sid_b, 1, payload={"n": 1})])

        conflicts = await repo.detect_conflicts(
            [
                _entry(sid_a, 1, payload={"n": 1}),  # identical -> clean
                _entry(sid_b, 1, payload={"n": 2}),  # distinct -> flagged
            ]
        )
        assert conflicts == [1]


class TestDetectConflictsPostgresAdapter:
    """(d) The PG override: one bounded SELECT detects the clash; append stays
    a pure ON CONFLICT DO NOTHING executemany (hot path untouched)."""

    @staticmethod
    def _stored_row(entry: SessionLogEntry) -> dict:
        # Mirror the asyncpg Record shape read_after / detect_conflicts decode.
        return {
            "session_id": entry.session_id,
            "seq": entry.seq,
            "kind": entry.kind,
            "role": entry.role,
            "request_id": entry.request_id,
            "payload": entry.payload,
            "ts": entry.ts,
        }

    async def test_distinct_payload_is_reported_via_single_any_select(self):
        sid = uuid4()
        stored = _entry(sid, 11, payload={"type": "assistant", "x": 1})
        candidate = _entry(sid, 11, payload={"type": "assistant", "x": 2})
        pool = AsyncMock()
        pool.fetch.return_value = [self._stored_row(stored)]
        log = PostgresSessionEventLog(pool)

        conflicts = await log.detect_conflicts([candidate])

        assert conflicts == [11]
        # Exactly ONE bounded, parameterized SELECT over the candidate seqs.
        pool.fetch.assert_called_once()
        sql, *params = pool.fetch.call_args[0]
        assert "seq = ANY($2::bigint[])" in sql
        assert params[0] == sid
        assert params[1] == [11]

    async def test_identical_stored_row_is_not_reported(self):
        sid = uuid4()
        candidate = _entry(sid, 11, payload={"type": "assistant", "x": 1})
        pool = AsyncMock()
        # Stored row is byte-identical to the candidate (the retry case).
        pool.fetch.return_value = [self._stored_row(candidate)]
        log = PostgresSessionEventLog(pool)

        assert await log.detect_conflicts([candidate]) == []

    async def test_append_hot_path_is_only_conflict_do_nothing_executemany(self):
        # The whole point of INV-3c living on a cold path: appending NEVER reads
        # back. Driving the real append must issue exactly one executemany whose
        # SQL is the ON CONFLICT DO NOTHING insert, and zero fetches.
        sid = uuid4()
        pool = AsyncMock()
        log = PostgresSessionEventLog(pool)

        submitted = await log.append([_entry(sid, i) for i in range(1, 4)])

        assert submitted == 3
        pool.executemany.assert_called_once()
        insert_sql, args = pool.executemany.call_args[0]
        assert "INSERT INTO session_event_log" in insert_sql
        assert "ON CONFLICT (session_id, seq) DO NOTHING" in insert_sql
        assert len(args) == 3
        # No read-back, no detect_conflicts coupling on the hot path.
        pool.fetch.assert_not_called()

    async def test_only_the_differing_seq_in_a_batch_is_reported(self):
        # Independently-derived expectation: one stored row matches, one differs;
        # the reported seq is exactly the differing one.
        sid = uuid4()
        clean = _entry(sid, 20, payload={"n": 20})
        clashing_stored = _entry(sid, 21, payload={"n": 21})
        clashing_candidate = _entry(sid, 21, payload={"n": 9999})
        pool = AsyncMock()
        pool.fetch.return_value = [
            self._stored_row(clean),
            self._stored_row(clashing_stored),
        ]
        log = PostgresSessionEventLog(pool)

        conflicts = await log.detect_conflicts([clean, clashing_candidate])

        assert conflicts == [21]
        pool.fetch.assert_called_once()
        _, *params = pool.fetch.call_args[0]
        assert sorted(params[1]) == [20, 21]

    async def test_unstored_candidate_seq_yields_no_conflict(self):
        sid = uuid4()
        candidate = _entry(sid, 30)
        pool = AsyncMock()
        pool.fetch.return_value = []  # nothing stored at that seq yet
        log = PostgresSessionEventLog(pool)

        assert await log.detect_conflicts([candidate]) == []


class TestPortAndAdapterAgreeOnConflictRule:
    """The two tiers must surface the SAME conflict set for the same input —
    otherwise the in-memory fake would be lying about production behaviour."""

    @staticmethod
    def _row(entry: SessionLogEntry) -> dict:
        return {
            "session_id": entry.session_id,
            "seq": entry.seq,
            "kind": entry.kind,
            "role": entry.role,
            "request_id": entry.request_id,
            "payload": entry.payload,
            "ts": entry.ts,
        }

    async def test_same_input_same_conflict_seqs_both_tiers(self):
        sid = uuid4()
        stored = [
            _entry(sid, 1, payload={"n": 1}),
            _entry(sid, 2, payload={"n": 2}),
            _entry(sid, 3, payload={"n": 3}),
        ]
        candidates = [
            _entry(sid, 1, payload={"n": 1}),  # identical
            _entry(sid, 2, payload={"n": 2222}),  # distinct
            _entry(sid, 3, payload={"n": 3}),  # identical
        ]
        expected = [2]

        mem = InMemoryLog()
        await mem.append(stored)
        mem_conflicts = await mem.detect_conflicts(candidates)

        pool = AsyncMock()
        pool.fetch.return_value = [self._row(e) for e in stored]
        pg_conflicts = await PostgresSessionEventLog(pool).detect_conflicts(candidates)

        assert mem_conflicts == expected
        assert pg_conflicts == expected
