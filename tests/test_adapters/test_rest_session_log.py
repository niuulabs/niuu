"""Tests for the durable session event log REST endpoints."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository


class InMemoryLog(SessionEventLogRepository):
    """In-memory, idempotent log for endpoint tests."""

    def __init__(self):
        self._rows: dict[tuple, SessionLogEntry] = {}

    async def append(self, entries: list[SessionLogEntry]) -> int:
        for e in entries:
            self._rows.setdefault((e.session_id, e.seq), e)
        return len(entries)

    async def read_after(self, session_id, after_seq=0, limit=1000) -> list[SessionLogEntry]:
        rows = [e for (sid, seq), e in self._rows.items() if sid == session_id and seq > after_seq]
        rows.sort(key=lambda e: e.seq)
        return rows[:limit]

    async def latest_seq(self, session_id) -> int:
        seqs = [seq for (sid, seq) in self._rows if sid == session_id]
        return max(seqs) if seqs else 0


def _client() -> tuple[TestClient, InMemoryLog]:
    repo = InMemoryLog()
    app = FastAPI()
    app.include_router(create_session_log_router(repo, session_service=None))
    return TestClient(app), repo


def _frame(seq: int, kind: str = "assistant", **extra) -> dict:
    return {"seq": seq, "kind": kind, "payload": {"n": seq}, **extra}


class TestAppend:
    def test_append_returns_submitted_and_latest_seq(self):
        client, _ = _client()
        sid = str(uuid4())

        resp = client.post(
            f"/api/v1/forge/sessions/{sid}/log",
            json={"entries": [_frame(1), _frame(2, "result")]},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body == {"submitted": 2, "latest_seq": 2}

    def test_append_is_idempotent_on_seq(self):
        client, _ = _client()
        sid = str(uuid4())
        payload = {"entries": [_frame(1)]}

        client.post(f"/api/v1/forge/sessions/{sid}/log", json=payload)
        client.post(f"/api/v1/forge/sessions/{sid}/log", json=payload)

        resp = client.get(f"/api/v1/forge/sessions/{sid}/log")
        assert len(resp.json()) == 1

    def test_append_rejects_empty_batch(self):
        client, _ = _client()
        sid = str(uuid4())

        resp = client.post(f"/api/v1/forge/sessions/{sid}/log", json={"entries": []})

        assert resp.status_code == 422


class TestReplay:
    def test_replay_returns_frames_after_cursor_in_order(self):
        client, _ = _client()
        sid = str(uuid4())
        client.post(
            f"/api/v1/forge/sessions/{sid}/log",
            json={"entries": [_frame(1), _frame(2), _frame(3)]},
        )

        resp = client.get(f"/api/v1/forge/sessions/{sid}/log", params={"after": 1})

        seqs = [e["seq"] for e in resp.json()]
        assert seqs == [2, 3]

    def test_replay_full_transcript_from_zero(self):
        client, _ = _client()
        sid = str(uuid4())
        client.post(
            f"/api/v1/forge/sessions/{sid}/log",
            json={
                "entries": [
                    _frame(1, "assistant", role="assistant", request_id="r1"),
                    _frame(2, "tool_use", request_id="r1"),
                    _frame(3, "tool_result", role="user"),
                ]
            },
        )

        resp = client.get(f"/api/v1/forge/sessions/{sid}/log")

        body = resp.json()
        assert [e["kind"] for e in body] == ["assistant", "tool_use", "tool_result"]
        assert body[0]["request_id"] == "r1"
        assert body[0]["session_id"] == sid

    def test_replay_empty_session_is_empty_list(self):
        client, _ = _client()
        resp = client.get(f"/api/v1/forge/sessions/{uuid4()}/log")
        assert resp.status_code == 200
        assert resp.json() == []


# Rows mirroring the streaming shape: assistant/user content lists carrying
# tool_use / tool_result blocks plus standalone content_block_* deltas.
_INTERNAL_ROWS = [
    {
        "seq": 1,
        "kind": "user",
        "payload": {"type": "user", "message": {"role": "user", "content": "go"}},
    },
    {
        "seq": 2,
        "kind": "assistant",
        "payload": {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "reading"},
                    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"p": "a.ts"}},
                ],
            },
        },
    },
    {
        "seq": 3,
        "kind": "user",
        "role": "user",
        "payload": {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "body"}],
            },
        },
    },
    {
        "seq": 4,
        "kind": "content_block_start",
        "payload": {"type": "content_block_start", "content_block": {"type": "tool_use"}},
    },
    {
        "seq": 5,
        "kind": "content_block_delta",
        "payload": {"type": "content_block_delta", "delta": {"partial_json": "{}"}},
    },
    {
        "seq": 6,
        "kind": "content_block_stop",
        "payload": {"type": "content_block_stop"},
    },
    {"seq": 7, "kind": "result", "payload": {"type": "result", "ok": True}},
]


def _seed_internal(client, sid: str) -> None:
    client.post(
        f"/api/v1/forge/sessions/{sid}/log",
        json={"entries": _INTERNAL_ROWS},
    )


def _gate_via_filter(rows: list[dict]) -> list[dict]:
    """Reference gate: the SAME shared predicate the streaming paths use."""
    from skuld.channels import filter_internal_blocks

    kept: list[dict] = []
    open_block: str | None = None
    for r in rows:
        filtered, open_block = filter_internal_blocks(r["payload"], open_block_type=open_block)
        if filtered is None:
            continue
        kept.append(r)
    return kept


class TestColdReadVisibility:
    def test_hides_internal_by_default(self):
        # SRD FR-7 / INV-10: cold-read defaults to internal HIDDEN, applying the
        # SAME filter_internal_blocks the live/replay paths use.
        client, _ = _client()
        sid = str(uuid4())
        _seed_internal(client, sid)

        body = client.get(f"/api/v1/forge/sessions/{sid}/log").json()

        kinds = [e["kind"] for e in body]
        # seq3 (tool_result-only), seq4/5/6 (internal content_block span) dropped.
        assert kinds == ["user", "assistant", "result"]
        # The assistant frame keeps text but the tool_use block is stripped.
        assistant = next(e for e in body if e["kind"] == "assistant")
        block_types = [b["type"] for b in assistant["payload"]["message"]["content"]]
        assert block_types == ["text"]

    def test_unhides_with_show_internal_true(self):
        client, _ = _client()
        sid = str(uuid4())
        _seed_internal(client, sid)

        body = client.get(
            f"/api/v1/forge/sessions/{sid}/log", params={"show_internal": "true"}
        ).json()

        # All 7 frames verbatim, tool_use/tool_result intact.
        assert [e["seq"] for e in body] == [1, 2, 3, 4, 5, 6, 7]
        assistant = next(e for e in body if e["kind"] == "assistant")
        block_types = [b["type"] for b in assistant["payload"]["message"]["content"]]
        assert "tool_use" in block_types

    def test_dropped_set_equals_shared_filter(self):
        # INV-10: the cold-read dropped set == the shared predicate's dropped set.
        client, _ = _client()
        sid = str(uuid4())
        _seed_internal(client, sid)

        gated = client.get(f"/api/v1/forge/sessions/{sid}/log").json()
        expected_seqs = [r["seq"] for r in _gate_via_filter(_INTERNAL_ROWS)]
        assert [e["seq"] for e in gated] == expected_seqs


def _entry(sid, seq: int, **overrides) -> SessionLogEntry:
    defaults = {
        "session_id": sid,
        "seq": seq,
        "kind": "assistant",
        "payload": {"n": seq},
        "ts": datetime.now(UTC),
        "role": "assistant",
        "request_id": "req-1",
    }
    defaults.update(overrides)
    return SessionLogEntry(**defaults)


class TestDetectConflictsDefaultImpl:
    """INV-3c: the concrete port default (inherited by every in-memory fake with
    ZERO edits) surfaces a distinct-payload re-append while leaving an identical
    re-append a silent no-op. Built on read_after — no extra fake methods."""

    async def test_inherited_without_any_fake_edits(self):
        # InMemoryLog never defines detect_conflicts — it must come from the port.
        assert "detect_conflicts" not in InMemoryLog.__dict__
        assert callable(InMemoryLog().detect_conflicts)

    async def test_empty_entries_returns_empty(self):
        assert await InMemoryLog().detect_conflicts([]) == []

    async def test_identical_reappend_is_no_conflict(self):
        repo = InMemoryLog()
        sid = uuid4()
        stored = _entry(sid, 5)
        await repo.append([stored])

        assert await repo.detect_conflicts([_entry(sid, 5)]) == []

    async def test_distinct_payload_same_seq_detected(self):
        repo = InMemoryLog()
        sid = uuid4()
        await repo.append([_entry(sid, 5, payload={"n": 5})])

        conflicts = await repo.detect_conflicts([_entry(sid, 5, payload={"n": 999})])

        assert conflicts == [5]

    async def test_brand_new_seq_is_not_a_conflict(self):
        repo = InMemoryLog()
        sid = uuid4()
        await repo.append([_entry(sid, 1)])

        assert await repo.detect_conflicts([_entry(sid, 2)]) == []

    async def test_per_session_grouping(self):
        repo = InMemoryLog()
        sid_a, sid_b = uuid4(), uuid4()
        await repo.append([_entry(sid_a, 1, payload={"n": 1})])
        await repo.append([_entry(sid_b, 1, payload={"n": 1})])

        conflicts = await repo.detect_conflicts(
            [
                _entry(sid_a, 1, payload={"n": 1}),  # identical -> no conflict
                _entry(sid_b, 1, payload={"n": 2}),  # distinct -> conflict
            ]
        )

        assert conflicts == [1]
