"""Tests for the durable session event log REST endpoints."""

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
