"""INV-9 / D6 (un-xfail) at the volundr REST tier: a RUNNING session whose live
pod answers ``/conversation/history`` with a renderable-but-empty / seed-only body
falls THROUGH ``_live_transcript_is_renderable`` to the durable-log rebuild.

This is the volundr-tier replacement the old broker-harness xfail asked for
(``tests/test_skuld/test_forge_crash_reconnect.py::test_d6``). The existing
``tests/test_adapters/test_rest_conversation_fallback.py`` proves the STOPPED-session
(no live pod / no ``chat_endpoint``) branch. This file proves the harder, distinct
branch: a session that is STILL ``RUNNING`` with a reachable pod whose live transcript
is *empty*, so the endpoint must NOT short-circuit on the live 200 — it must reconcile
to the DB transcript rebuilt from raw crash frames (assistant / content_block_delta
with NO terminating ``result``), surfacing an ``interrupted`` assistant turn.

Real REST router (``create_router``) + real ``SessionService`` + real
``SessionArchiveService`` over in-memory repos. NO Postgres, NO Docker, NO tmux. The
live pod is a mocked ``httpx.AsyncClient`` returning the seed-only body, exactly like
the existing ``test_rest.py`` conversation tests.

The DB-rebuilt expectation is derived INDEPENDENTLY from the shared reducer
(``transcript_rebuild.rebuild_turns`` over ``read_after(0)``) — the same reducer the
live broker fold uses (INV-4) — so the HTTP assertion is non-tautological: it compares
the endpoint's output against a reducer run we drive ourselves over the same durable
frames, not against a second copy of the endpoint's own fold.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.test_domain.test_session_archive_service import InMemorySessionEventLog
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.archive_store import FileSystemArchiveStore
from volundr.config import LocalMountsConfig
from volundr.domain.models import Session, SessionLogEntry, SessionStatus
from volundr.domain.services import SessionArchiveService, SessionService
from volundr.domain.services.transcript_rebuild import rebuild_turns

_CONV_PATH = "/api/v1/forge/sessions/{sid}/conversation"
# The endpoint's deterministic 'no transcript anywhere' close — see rest.py
# get_conversation final raise (HTTP 503). Named to avoid a magic number.
_NO_TRANSCRIPT_STATUS = 503

# Pod liveness: a RUNNING session must look reachable (RUNNING => reachable, INV-9).
_RUNNING_POD_MANAGER = MockPodManager(wait_for_ready_result=SessionStatus.RUNNING)


@pytest.fixture(autouse=True)
def _strip_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """tests/test_volundr/* is NOT covered by the test_skuld autouse strip and a dev
    box leaks SKULD__* / VOLUNDR__* / BIFROST__* into the process; pydantic settings
    would ingest them. Strip them here so this file is hermetic on its own."""
    leaked = [
        key
        for key in os.environ
        if key.startswith(("SKULD__", "SKULD_", "VOLUNDR__", "VOLUNDR_", "BIFROST__", "BIFROST_"))
    ]
    for key in leaked:
        monkeypatch.delenv(key, raising=False)


class _NoWorkspaceStorage:
    """Storage with NO workspace on disk — forces the durable-log rebuild branch
    inside ``SessionArchiveService.get_transcript`` (workspace candidates all miss,
    archive store empty, so ``_load_event_log_transcript`` runs)."""

    def resolve_session_workspace_path(self, _session_id: str) -> str | None:
        return None

    async def get_workspace_by_session(self, _session_id: str):
        return None


def _crash_frames(session_id) -> list[SessionLogEntry]:
    """Raw frames from a session that crashed mid-turn: a human turn, then streamed
    assistant deltas with NO terminating ``result``. The reducer must flush the open
    assistant span as an ``interrupted`` turn — exactly the dead-session shape the live
    pod can no longer render."""
    now = datetime(2026, 6, 27, 9, 0, 0, tzinfo=UTC)
    return [
        SessionLogEntry(
            session_id=session_id,
            seq=1,
            kind="user",
            payload={"uuid": "U1", "message": {"content": "summarize the repo"}},
            ts=now,
            role="user",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=2,
            kind="assistant",
            payload={"message": {"content": [{"type": "text", "text": "Reading "}]}},
            ts=now,
            role="assistant",
            request_id="req-crash",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=3,
            kind="content_block_delta",
            payload={"delta": {"type": "text_delta", "text": "the source tree"}},
            ts=now,
            request_id="req-crash",
        ),
        # NO result frame — the turn never closed (crash mid-stream).
    ]


def _build_client(
    session: Session,
    event_log: InMemorySessionEventLog,
) -> tuple[TestClient, InMemorySessionRepository]:
    repository = InMemorySessionRepository()
    session_service = SessionService(
        repository=repository,
        pod_manager=_RUNNING_POD_MANAGER,
        validate_repos=False,
    )
    archive_service = SessionArchiveService(
        session_service,
        _NoWorkspaceStorage(),
        FileSystemArchiveStore(),
        event_log_repository=event_log,
    )

    app = FastAPI()
    app.include_router(create_router(session_service, archive_service=archive_service))

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return TestClient(app), repository


def _seed_only_live_body() -> dict:
    """What a still-alive pod whose WS crashed mid-turn returns: HTTP 200, ONE seed
    user turn, no assistant, not active, no last_activity hint. This is precisely the
    payload ``_live_transcript_is_renderable`` must reject so the endpoint reconciles
    to the durable log instead of rendering a 'dead' session."""
    return {
        "turns": [{"id": "seed", "role": "user", "content": "summarize the repo"}],
        "is_active": False,
        "last_activity": "",
    }


def _patch_live_pod(seed_body: dict):
    """Patch ``rest.httpx.AsyncClient`` so the live-pod GET returns ``seed_body`` with
    HTTP 200 — the reachable-but-empty pod of INV-9."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = seed_body
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    return mock_response, mock_client


async def _running_session(repository: InMemorySessionRepository) -> Session:
    session = Session(
        name="crash-mid-turn",
        model="claude-opus-4-8",
        status=SessionStatus.STOPPED,
    )
    running = session.with_endpoints(
        f"ws://localhost:8080/s/{session.id}/session",
        f"https://localhost:8080/s/{session.id}/session",
    ).with_status(SessionStatus.RUNNING)
    await repository.create(running)
    return running


@pytest.mark.asyncio
async def test_running_session_empty_live_pod_falls_back_to_rebuilt_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INV-9 headline: a RUNNING session whose reachable pod returns a seed-only live
    transcript falls THROUGH to ``forge.get_transcript`` ->
    ``SessionArchiveService._load_event_log_transcript`` and returns the DB-rebuilt
    turns (user + interrupted assistant), NOT the empty live body."""
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    await event_log.append(_crash_frames(session.id))

    # Independently derive the expectation from the SHARED reducer over the durable
    # frames the endpoint will read (read_after(0)). This is the live-fold reducer.
    rows = await event_log.read_after(session.id, after_seq=0)
    expected_turns = rebuild_turns(rows).turns
    # Sanity on the independent expectation: a crash mid-turn => interrupted assistant.
    assert [t["role"] for t in expected_turns] == ["user", "assistant"]
    assert expected_turns[1]["metadata"]["status"] == "interrupted"

    mock_response, mock_client = _patch_live_pod(_seed_only_live_body())
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    body = resp.json()

    # The live pod WAS consulted (proving we drove the RUNNING branch, not the
    # stopped-session shortcut), then we fell through.
    mock_client.get.assert_awaited_once()
    assert "conversation/history" in mock_client.get.await_args.args[0]

    # The endpoint returned the DURABLE-LOG rebuild, not the empty live body.
    assert body["is_active"] is False
    assert [t["role"] for t in body["turns"]] == ["user", "assistant"]
    assert body["turns"][1]["metadata"]["status"] == "interrupted"
    assert "the source tree" in body["turns"][1]["content"]

    # Frame-for-frame: the HTTP turns equal the independently-driven reducer output.
    assert body["turns"] == expected_turns
    # And it is NOT the seed-only live body (which had no assistant turn).
    assert body["turns"] != _seed_only_live_body()["turns"]


@pytest.mark.asyncio
async def test_running_session_fallback_uses_shared_reducer_frame_for_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INV-4 at the RUNNING read path: the HTTP turns == rebuild_turns(read_after(0)),
    deterministic uuid5 ids included — the same reducer the broker live fold runs, so
    live==persisted==replay==cold-read share one fold. Re-derived independently here."""
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    await event_log.append(_crash_frames(session.id))

    rows = await event_log.read_after(session.id, after_seq=0)
    first = rebuild_turns(rows).turns
    second = rebuild_turns(rows).turns
    # Deterministic ids: two rebuilds of the same log are byte-identical.
    assert first == second
    assert [t["id"] for t in first] == [t["id"] for t in second]

    _, mock_client = _patch_live_pod(_seed_only_live_body())
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        body = client.get(_CONV_PATH.format(sid=session.id)).json()

    assert body["turns"] == first


@pytest.mark.asyncio
async def test_dead_session_conversation_close_is_deterministic_not_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INV-9 deterministic close: a RUNNING session with NO durable frames at all (the
    pod is reachable-but-empty AND the log is empty) does not silently return the empty
    live body NOR a 200 with phantom turns — it surfaces a deterministic 503, the
    'no transcript anywhere' close, rather than masquerading a dead session as alive."""
    event_log = InMemorySessionEventLog()  # intentionally empty
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)

    _, mock_client = _patch_live_pod(_seed_only_live_body())
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_CONV_PATH.format(sid=session.id))

    # Reachable pod was consulted, fell through (empty log => no rebuild), and the
    # endpoint closed deterministically instead of echoing the seed-only live body.
    mock_client.get.assert_awaited_once()
    assert resp.status_code == _NO_TRANSCRIPT_STATUS
    # A deterministic close carries a reason, NOT a phantom turns body.
    assert "turns" not in resp.json()
    assert resp.json()["detail"]


@pytest.mark.asyncio
async def test_unknown_session_conversation_404_not_empty_body() -> None:
    """INV-9 deterministic close on a truly unknown session id — a hard 404, never a
    silent empty conversation body."""
    event_log = InMemorySessionEventLog()
    client, _ = _build_client(Session(name="x", model="m", status=SessionStatus.STOPPED), event_log)

    resp = client.get(_CONV_PATH.format(sid=uuid4()))

    assert resp.status_code == 404
