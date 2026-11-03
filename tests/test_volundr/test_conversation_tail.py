"""LEX-1345: ``GET /sessions/{id}/conversation?tail=N`` reads only the newest turns.

Voice asks "what is the session doing" and needs the last few messages. Without ``tail``
the endpoint serves the whole transcript (up to 564 MB on Thor) and the caller slices it.
With ``tail``:

* a running session's broker is asked for its protocol-2 recent page, so it builds only
  that window; an older broker that ignores the page parameters is sliced here;
* a stopped session rebuilt from the durable event log reads only the log tail, in
  widening windows, instead of the last 50k frames.

Without ``tail`` the response is unchanged (covered by the existing conversation tests).
Real router + real ``SessionService`` + real ``SessionArchiveService`` over in-memory
repos, like ``test_conversation_db_fallback.py``.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.transcript_rebuild import rebuild_turns
from tests.conftest import (
    InMemorySessionRepository,
    MockPodManager,
    make_session_participant_service,
)
from tests.test_domain.test_session_archive_service import InMemorySessionEventLog
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.archive_store import FileSystemArchiveStore
from volundr.config import LocalMountsConfig
from volundr.domain.models import Session, SessionLogEntry, SessionStatus
from volundr.domain.services import SessionArchiveService, SessionService

_CONV_PATH = "/api/v1/forge/sessions/{sid}/conversation"
_NOW = datetime(2026, 10, 19, 9, 0, 0, tzinfo=UTC)
# Frames per exchange in `_exchanges`: user, assistant, 3 deltas, result.
_FRAMES_PER_EXCHANGE = 6


@pytest.fixture(autouse=True)
def _strip_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [k for k in os.environ if k.startswith(("SKULD_", "VOLUNDR_", "BIFROST_"))]:
        monkeypatch.delenv(key, raising=False)


class _NoWorkspaceStorage:
    """No workspace and no archive: the transcript comes from the durable event log."""

    def resolve_session_workspace_path(self, _session_id: str) -> str | None:
        return None

    async def get_workspace_by_session(self, _session_id: str):
        return None


class _CountingEventLog(InMemorySessionEventLog):
    """Counts the frames handed out, to prove the tail read decodes less of the log."""

    def __init__(self, entries: list[SessionLogEntry] | None = None):
        super().__init__(entries)
        self.frames_read = 0

    async def read_after(self, session_id, after_seq: int = 0, limit: int = 1000):
        page = await super().read_after(session_id, after_seq=after_seq, limit=limit)
        self.frames_read += len(page)
        return page


def _exchanges(session_id, count: int) -> list[SessionLogEntry]:
    """``count`` complete user/assistant exchanges, each streamed over several frames."""
    rows: list[SessionLogEntry] = []
    seq = 0

    def add(kind: str, payload: dict, **extra) -> None:
        nonlocal seq
        seq += 1
        rows.append(
            SessionLogEntry(
                session_id=session_id, seq=seq, kind=kind, payload=payload, ts=_NOW, **extra
            )
        )

    for n in range(1, count + 1):
        req = f"req-{n}"
        add("user", {"uuid": f"U{n}", "message": {"content": f"question {n}"}}, role="user")
        add(
            "assistant",
            {"message": {"content": [{"type": "text", "text": f"answer {n}"}]}},
            role="assistant",
            request_id=req,
        )
        for part in range(3):
            add(
                "content_block_delta",
                {"delta": {"type": "text_delta", "text": f" part{part}"}},
                request_id=req,
            )
        add("result", {"subtype": "success"}, request_id=req)
    assert len(rows) == count * _FRAMES_PER_EXCHANGE
    return rows


def _archive_service(
    event_log, repository: InMemorySessionRepository, *, initial_frames: int = 2000
) -> SessionArchiveService:
    session_service = SessionService(
        repository=repository, pod_manager=MockPodManager(), validate_repos=False
    )
    return SessionArchiveService(
        session_service,
        _NoWorkspaceStorage(),
        FileSystemArchiveStore(),
        event_log_repository=event_log,
        tail_initial_frames=initial_frames,
    )


async def _stopped_session(repository: InMemorySessionRepository) -> Session:
    session = Session(name="tail", model="m", status=SessionStatus.STOPPED)
    await repository.create(session)
    return session


# ── archive service: tail rebuild ─────────────────────────────────────


@pytest.mark.asyncio
async def test_tail_rebuild_matches_full_rebuild_tail_and_reads_less() -> None:
    """The tail rebuild returns exactly the newest turns of the full rebuild (ids
    included), while decoding only a fraction of the log."""
    initial = 8
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 40)
    full_turns = rebuild_turns(frames).turns
    assert len(full_turns) == 80

    event_log = _CountingEventLog(frames)
    service = _archive_service(event_log, repository, initial_frames=initial)
    transcript = await service.get_transcript(session.id, tail_turns=3)

    assert transcript["turns"] == full_turns[-3:]
    assert transcript["history_tail"] is True
    assert transcript["is_active"] is False
    # 8 frames hold one and a bit exchanges: too few, so the window doubles once to 16.
    assert event_log.frames_read == 16
    assert event_log.frames_read < len(frames)


@pytest.mark.asyncio
async def test_tail_rebuild_drops_the_fragment_cut_at_the_window_edge() -> None:
    """A window that starts mid-exchange begins with a fragment; it is never returned.
    7 frames = the tail of one exchange's deltas + one full exchange: the full exchange
    (2 turns) is not MORE than 2 turns, so the window widens before answering."""
    initial = 7
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 10)
    full_turns = rebuild_turns(frames).turns

    service = _archive_service(_CountingEventLog(frames), repository, initial_frames=initial)
    transcript = await service.get_transcript(session.id, tail_turns=2)

    assert transcript["turns"] == full_turns[-2:]
    assert [t["role"] for t in transcript["turns"]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_tail_rebuild_of_a_short_log_returns_every_turn() -> None:
    """A log shorter than the window is read whole, and a tail larger than the
    transcript returns all of it."""
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 2)
    event_log = _CountingEventLog(frames)

    transcript = await _archive_service(event_log, repository).get_transcript(
        session.id, tail_turns=10
    )

    assert transcript["turns"] == rebuild_turns(frames).turns
    assert event_log.frames_read == len(frames)


@pytest.mark.asyncio
async def test_tail_rebuild_respects_the_frame_ceiling() -> None:
    """The window never grows past ``max_frames``, the full rebuild's ceiling."""
    initial = 4
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 20)
    event_log = _CountingEventLog(frames)
    service = _archive_service(event_log, repository, initial_frames=initial)

    transcript = await service._load_event_log_tail(session.id, tail_turns=30, max_frames=24)

    assert transcript is not None
    assert event_log.frames_read == 24
    assert transcript["turns"][-1] == rebuild_turns(frames).turns[-1]


@pytest.mark.asyncio
async def test_tail_rebuild_tolerates_gaps_in_seq() -> None:
    """Seqs are not guaranteed dense; a window read never returns a frame twice."""
    initial = 5
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = [replace(f, seq=f.seq * 3) for f in _exchanges(session.id, 6)]
    full_turns = rebuild_turns(frames).turns

    service = _archive_service(_CountingEventLog(frames), repository, initial_frames=initial)
    transcript = await service.get_transcript(session.id, tail_turns=4)

    assert transcript["turns"] == full_turns[-4:]


@pytest.mark.asyncio
async def test_get_transcript_without_tail_is_the_full_rebuild() -> None:
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 5)

    transcript = await _archive_service(_CountingEventLog(frames), repository).get_transcript(
        session.id
    )

    assert transcript["turns"] == rebuild_turns(frames).turns
    assert "history_tail" not in transcript


# ── REST: ?tail=N ─────────────────────────────────────────────────────


def _client(
    event_log, repository: InMemorySessionRepository, *, initial_frames: int = 2000
) -> TestClient:
    session_service = SessionService(
        repository=repository,
        pod_manager=MockPodManager(wait_for_ready_result=SessionStatus.RUNNING),
        validate_repos=False,
    )
    app = FastAPI()
    app.include_router(
        create_router(
            session_service,
            archive_service=_archive_service(event_log, repository, initial_frames=initial_frames),
            session_participant_service=make_session_participant_service(session_service),
        )
    )

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return TestClient(app)


async def _running_session(repository: InMemorySessionRepository) -> Session:
    session = Session(name="tail-live", model="m", status=SessionStatus.STOPPED)
    running = session.with_endpoints(
        f"ws://localhost:8080/s/{session.id}/session",
        f"https://localhost:8080/s/{session.id}/session",
    ).with_status(SessionStatus.RUNNING)
    await repository.create(running)
    return running


def _patch_live(monkeypatch: pytest.MonkeyPatch, body: dict) -> AsyncMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = body
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = response
    client_cls = MagicMock()
    client_cls.return_value.__aenter__.return_value = client
    monkeypatch.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
    return client


@pytest.mark.asyncio
async def test_rest_tail_on_a_stopped_session_reads_the_log_tail() -> None:
    initial = 8
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 40)
    event_log = _CountingEventLog(frames)
    client = _client(event_log, repository, initial_frames=initial)

    resp = client.get(_CONV_PATH.format(sid=session.id), params={"tail": 3})

    assert resp.status_code == 200
    body = resp.json()
    assert body["turns"] == rebuild_turns(frames).turns[-3:]
    assert body["tail"] == 3
    # The tail rebuild never counted the older turns: no total, no paging window.
    for key in ("total_turns", "window_offset", "history_tail", "projection_revision"):
        assert key not in body
    assert event_log.frames_read < len(frames)


@pytest.mark.asyncio
async def test_rest_without_tail_still_serves_the_whole_transcript() -> None:
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 6)
    event_log = _CountingEventLog(frames)

    body = _client(event_log, repository).get(_CONV_PATH.format(sid=session.id)).json()

    assert body["turns"] == rebuild_turns(frames).turns
    assert "tail" not in body
    assert event_log.frames_read == len(frames)


@pytest.mark.asyncio
async def test_rest_tail_asks_a_running_broker_for_its_recent_page(monkeypatch) -> None:
    """The broker is asked for a protocol-2 page of at most ``tail`` turns; its paging
    metadata is dropped and its whole-transcript ``total_turns`` kept."""
    repository = InMemorySessionRepository()
    session = await _running_session(repository)
    page = {
        "turns": [
            {"id": "t9", "role": "user", "content": "ping"},
            {"id": "t10", "role": "assistant", "content": "pong"},
        ],
        "history_protocol": 2,
        "total_turns": 10,
        "window_offset": 8,
        "window_end": 10,
        "older_cursor": "abc",
        "refresh_cursor": "def",
        "has_more_before": True,
        "page_kind": "recent",
        "projection_revision": "rev",
        "is_active": True,
        "last_activity": "typing",
        "activity_state": "active",
    }
    live = _patch_live(monkeypatch, page)

    resp = _client(_CountingEventLog(), repository).get(
        _CONV_PATH.format(sid=session.id), params={"tail": 2, "detail": "shallow"}
    )

    assert resp.status_code == 200
    params = live.get.await_args.kwargs["params"]
    assert params["history_protocol"] == 2
    assert params["limit"] == 2
    assert params["max_bytes"] > 0
    assert params["detail"] == "shallow"
    body = resp.json()
    assert [t["id"] for t in body["turns"]] == ["t9", "t10"]
    assert body["tail"] == 2
    assert body["total_turns"] == 10
    assert body["is_active"] is True
    assert body["activity_state"] == "active"
    for key in ("history_protocol", "window_offset", "older_cursor", "refresh_cursor"):
        assert key not in body


@pytest.mark.asyncio
async def test_rest_tail_slices_an_old_broker_that_returns_everything(monkeypatch) -> None:
    repository = InMemorySessionRepository()
    session = await _running_session(repository)
    full = {
        "turns": [{"id": f"t{n}", "role": "assistant", "content": f"m{n}"} for n in range(20)],
        "is_active": True,
        "last_activity": "x",
    }
    _patch_live(monkeypatch, full)

    body = (
        _client(_CountingEventLog(), repository)
        .get(_CONV_PATH.format(sid=session.id), params={"tail": 3})
        .json()
    )

    assert [t["id"] for t in body["turns"]] == ["t17", "t18", "t19"]
    assert body["total_turns"] == 20
    assert body["tail"] == 3


@pytest.mark.parametrize(
    "extra",
    [
        {"before": 1},
        {"after": 2},
        {"after_id": "x"},
        {"cursor": "abc"},
        {"turn_id": "x"},
    ],
)
@pytest.mark.asyncio
async def test_rest_tail_rejects_paging_parameters(extra) -> None:
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)

    resp = _client(_CountingEventLog(), repository).get(
        _CONV_PATH.format(sid=session.id), params={"tail": 3, **extra}
    )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rest_tail_overrides_a_protocol2_page_request_sent_alongside(monkeypatch) -> None:
    """Clients send ``history_protocol=2&limit=N`` with ``tail`` so a server without
    ``tail`` answers with its recent page; a server with it serves the tail."""
    repository = InMemorySessionRepository()
    session = await _stopped_session(repository)
    frames = _exchanges(session.id, 12)
    event_log = _CountingEventLog(frames)
    client = _client(event_log, repository, initial_frames=8)

    resp = client.get(
        _CONV_PATH.format(sid=session.id),
        params={"tail": 2, "history_protocol": 2, "limit": 2, "detail": "shallow"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["turns"] == rebuild_turns(frames).turns[-2:]
    assert body["tail"] == 2
    assert "history_protocol" not in body
    assert event_log.frames_read < len(frames)
