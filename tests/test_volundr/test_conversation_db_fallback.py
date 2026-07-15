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
from volundr.adapters.inbound import rest as rest_mod
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.outbound.archive_store import FileSystemArchiveStore
from volundr.config import LocalMountsConfig
from volundr.domain.models import (
    Session,
    SessionActivityState,
    SessionLogEntry,
    SessionStatus,
)
from volundr.domain.services import SessionArchiveService, SessionService
from volundr.domain.services.transcript_rebuild import rebuild_turns

_CONV_PATH = "/api/v1/forge/sessions/{sid}/conversation"
_ACTIVITY_PATH = "/api/v1/forge/sessions/{sid}/activity"
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


def _renderable_short_live_body() -> dict:
    """FAULT C: a RUNNING session whose resumed/restarted broker is DESYNCED from the
    durable log. The live pod returns a renderable body (it has an assistant turn, so
    ``_live_transcript_is_renderable`` accepts it) that is NOT streaming
    (``is_active``=False, no ``last_activity``) but carries FEWER turns than the durable
    log. Without the FAULT C reconciliation the endpoint would echo this short body and
    the client would see a partial transcript for a live session."""
    return {
        "turns": [
            {"id": "live-u", "role": "user", "content": "summarize the repo"},
            {"id": "live-a", "role": "assistant", "content": "Reading the source tree"},
        ],
        "is_active": False,
        "last_activity": "",
    }


def _renderable_streaming_live_body() -> dict:
    """A HEALTHY still-streaming live body: renderable, ``is_active``=True, and it
    legitimately carries the in-progress turn. FAULT C reconciliation must NOT touch
    this — even if the durable log momentarily has more settled turns, we keep the live
    body verbatim so the streaming in_progress turn is never dropped."""
    return {
        "turns": [
            {"id": "live-u", "role": "user", "content": "summarize the repo"},
        ],
        "is_active": True,
        "last_activity": "2026-06-28T12:00:00Z",
    }


def _longer_crash_frames(session_id) -> list[SessionLogEntry]:
    """Durable frames with strictly MORE settled content than the short live body:
    two complete user/assistant exchanges (each closed with a ``result``), so the
    durable rebuild yields 4 turns vs the live body's 2."""
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
            payload={"message": {"content": [{"type": "text", "text": "Reading the tree"}]}},
            ts=now,
            role="assistant",
            request_id="req-1",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=3,
            kind="result",
            payload={"subtype": "success"},
            ts=now,
            request_id="req-1",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=4,
            kind="user",
            payload={"uuid": "U2", "message": {"content": "now run the tests"}},
            ts=now,
            role="user",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=5,
            kind="assistant",
            payload={"message": {"content": [{"type": "text", "text": "Running tests"}]}},
            ts=now,
            role="assistant",
            request_id="req-2",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=6,
            kind="result",
            payload={"subtype": "success"},
            ts=now,
            request_id="req-2",
        ),
    ]


@pytest.mark.asyncio
async def test_running_short_live_body_falls_back_to_longer_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAULT C: a renderable but NON-streaming live body with FEWER turns than the
    durable rebuild (broker desynced after resume/restart) must be REPLACED by the
    durable rebuild. The live pod is consulted (RUNNING branch), passes the renderable
    gate, but because it is not streaming and the durable log is strictly longer, the
    endpoint prefers the durable transcript."""
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    await event_log.append(_longer_crash_frames(session.id))

    rows = await event_log.read_after(session.id, after_seq=0)
    expected_turns = rebuild_turns(rows).turns
    # Sanity: the durable rebuild is strictly LONGER than the short live body (2 turns).
    assert len(expected_turns) == 4
    assert len(_renderable_short_live_body()["turns"]) == 2

    # The cold open serves the live body optimistically (no synchronous rebuild) and warms the
    # durable-count cache off the request path; the desync is corrected once that cache is warm
    # (steady state / next poll). Seed it warm so this asserts the CORRECTION path, not the open.
    rest_mod._DURABLE_COUNT_CACHE[str(session.id)] = (
        await event_log.latest_seq(session.id),
        len(expected_turns),
    )

    _, mock_client = _patch_live_pod(_renderable_short_live_body())
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    body = resp.json()
    # The live pod WAS consulted (RUNNING branch) ...
    mock_client.get.assert_awaited_once()
    # ... but the DURABLE rebuild (4 turns) was returned, NOT the short live body.
    assert len(body["turns"]) == 4
    assert body["turns"] == expected_turns
    assert body["turns"] != _renderable_short_live_body()["turns"]


@pytest.mark.asyncio
async def test_running_streaming_live_body_is_kept_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAULT C guard: a HEALTHY still-streaming live body (is_active=True) is kept
    VERBATIM even when the durable log momentarily has more settled turns — the
    reconciliation must never run for a streaming body or it would drop the live
    in_progress turn. This proves the fix does not regress the healthy case."""
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    # Durable log has MORE turns than the streaming live body would — yet because the
    # live body is_active=True we must keep it verbatim regardless.
    await event_log.append(_longer_crash_frames(session.id))

    streaming_body = _renderable_streaming_live_body()
    _, mock_client = _patch_live_pod(streaming_body)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    body = resp.json()
    mock_client.get.assert_awaited_once()
    # Streaming body kept verbatim: the single live turn, is_active True, NOT the
    # 4-turn durable rebuild.
    assert body["is_active"] is True
    assert body["turns"] == streaming_body["turns"]


@pytest.mark.asyncio
async def test_running_equal_length_live_body_is_kept_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAULT C boundary: a renderable, non-streaming live body whose turn count is
    EQUAL to the durable rebuild is kept verbatim (we only prefer durable when it is
    STRICTLY longer). Guards against needlessly replacing an in-sync live body."""
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    # Durable rebuild from the original crash frames = 2 turns (user + interrupted).
    await event_log.append(_crash_frames(session.id))
    rows = await event_log.read_after(session.id, after_seq=0)
    assert len(rebuild_turns(rows).turns) == 2

    # Live body also has 2 turns and is renderable + non-streaming.
    equal_live = {
        "turns": [
            {"id": "live-u", "role": "user", "content": "summarize the repo"},
            {"id": "live-a", "role": "assistant", "content": "from the live pod"},
        ],
        "is_active": False,
        "last_activity": "",
    }
    _, mock_client = _patch_live_pod(equal_live)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    body = resp.json()
    mock_client.get.assert_awaited_once()
    # Equal length => keep the LIVE body verbatim (durable not strictly longer).
    assert body["turns"] == equal_live["turns"]
    assert body["turns"][1]["content"] == "from the live pod"


def _renderable_in_progress_live_body() -> dict:
    """Q2 REGRESSION: a HEALTHY mid-turn broker during the tool-execution window. The
    agent emitted a tool_use and is waiting on the result, so the broker's streaming
    buffer flags are EMPTY (``is_active``=False, ``last_activity``=''), yet the live body
    DOES carry the running ``in_progress`` turn (role=assistant, parts=[tool_use]). The
    old ``is_streaming = is_active or last_activity`` proxy reads this as 'not streaming'
    and runs the FAULT C reconciliation, which can drop the live in_progress turn for a
    durable rebuild that merely counts more turns. The fix gates on the turn payload
    (last turn ``in_progress``) instead, so reconciliation is skipped here."""
    return {
        "turns": [
            {"id": "live-u", "role": "user", "content": "summarize the repo"},
            {
                "id": "in-progress",
                "role": "assistant",
                "content": "",
                "parts": [
                    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}
                ],
                "metadata": {"status": "in_progress"},
                "in_progress": True,
            },
        ],
        "is_active": False,
        "last_activity": "",
    }


@pytest.mark.asyncio
async def test_running_in_progress_live_body_not_dropped_for_longer_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q2 REGRESSION: a renderable live body with ``is_active``=False / ``last_activity``=''
    whose FINAL turn is an ``in_progress`` assistant turn (tool_use-only — the streaming
    buffer flags are legitimately empty) must be kept VERBATIM even though the durable
    rebuild has STRICTLY MORE turns. The FAULT C count-vs-durable reconciliation must NOT
    run while the live body carries an in_progress turn, or the live streaming turn is
    silently dropped — the exact empty/short-transcript streaming regression this effort
    set out to fix. Before the fix (gating on the buffer-flag proxy) this returned the
    4-turn durable rebuild and dropped the in_progress turn; this test would have FAILED."""
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    # Durable rebuild = 4 turns, strictly MORE than the live body's 2 — the exact
    # condition under which the old proxy gate would have replaced the live body.
    await event_log.append(_longer_crash_frames(session.id))
    rows = await event_log.read_after(session.id, after_seq=0)
    assert len(rebuild_turns(rows).turns) == 4

    in_progress_body = _renderable_in_progress_live_body()
    assert len(in_progress_body["turns"]) == 2  # strictly fewer than durable's 4

    _, mock_client = _patch_live_pod(in_progress_body)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_CONV_PATH.format(sid=session.id))

    assert resp.status_code == 200
    body = resp.json()
    mock_client.get.assert_awaited_once()
    # The live in_progress turn is PRESERVED — NOT replaced by the longer durable rebuild.
    assert body["turns"] == in_progress_body["turns"]
    assert body["turns"][-1]["in_progress"] is True
    assert len(body["turns"]) == 2


# --- detail=shallow elision on the volundr durable / fallback path ---------------

_TOOL_RESULT_PATH = "/api/v1/forge/sessions/{sid}/tool-result/{tuid}"


def _big_tool_result_frames(session_id, content: str) -> list[SessionLogEntry]:
    """A closed user/assistant turn whose assistant block carries a LARGE tool_result —
    the durable rebuild yields a turn whose parts include a heavy tool_result that
    ``detail=shallow`` must elide on the volundr durable path."""
    now = datetime(2026, 6, 27, 9, 0, 0, tzinfo=UTC)
    return [
        SessionLogEntry(
            session_id=session_id,
            seq=1,
            kind="user",
            payload={"uuid": "U1", "message": {"content": "run ls"}},
            ts=now,
            role="user",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=2,
            kind="assistant",
            payload={
                "message": {
                    "content": [{"type": "tool_use", "id": "tu-big", "name": "Bash", "input": {}}]
                }
            },
            ts=now,
            role="assistant",
            request_id="req-1",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=3,
            kind="user",
            payload={
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu-big", "content": content}
                    ]
                }
            },
            ts=now,
            role="user",
            request_id="req-1",
        ),
        SessionLogEntry(
            session_id=session_id,
            seq=4,
            kind="result",
            payload={"subtype": "success"},
            ts=now,
            request_id="req-1",
        ),
    ]


def _find_tool_result_block(turns: list, tool_use_id: str) -> dict | None:
    for turn in turns:
        for block in turn.get("parts") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") == tool_use_id
            ):
                return block
    return None


@pytest.mark.asyncio
async def test_shallow_elides_big_tool_result_on_durable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detail=shallow on a STOPPED/seed-only session (durable-rebuild path, no live pod)
    must elide a heavy ``tool_result`` to a placeholder via ``_maybe_elide`` on the
    volundr main durable-rebuild branch. Guards against an elision regression on the
    durable path that the broker-side tests can't catch."""
    from skuld.conversation_shallow import INLINE_BYTE_LIMIT

    big = "Z" * (INLINE_BYTE_LIMIT + 2000)
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    # STOPPED session => no chat_endpoint => the live-pod branch is skipped entirely and
    # we hit the main `_maybe_elide(await forge.get_transcript(...))` durable branch.
    session = Session(name="stopped", model="claude-opus-4-8", status=SessionStatus.STOPPED)
    await repository.create(session)
    await event_log.append(_big_tool_result_frames(session.id, big))

    # Sanity: FULL mode keeps the big content inline.
    full = client.get(_CONV_PATH.format(sid=session.id)).json()
    full_block = _find_tool_result_block(full["turns"], "tu-big")
    assert full_block is not None
    assert full_block.get("content") == big
    assert "truncated" not in full_block

    # SHALLOW mode elides the big content on the durable path.
    shallow = client.get(_CONV_PATH.format(sid=session.id), params={"detail": "shallow"}).json()
    shallow_block = _find_tool_result_block(shallow["turns"], "tu-big")
    assert shallow_block is not None
    assert shallow_block["truncated"] is True
    assert "content" not in shallow_block
    assert shallow_block["byte_size"] == len(big.encode("utf-8"))


@pytest.mark.asyncio
async def test_shallow_elides_on_fault_c_durable_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detail=shallow on the FAULT C durable-return branch: a RUNNING session whose live
    pod is short/non-streaming, so the endpoint prefers the (strictly longer) durable
    rebuild AND must elide it (``_maybe_elide(durable)`` at the FAULT C return). Proves
    the FAULT C path honours shallow, not just the main durable branch."""
    from skuld.conversation_shallow import INLINE_BYTE_LIMIT

    big = "Q" * (INLINE_BYTE_LIMIT + 2000)
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    # Durable rebuild has a big tool_result and (2 turns) is strictly longer than the
    # short live body (we reuse the renderable-short live body = ... actually it must be
    # SHORTER than durable). Give durable the big-tool-result frames (2 turns); make the
    # live body 1 turn so durable is strictly longer and the FAULT C branch is taken.
    await event_log.append(_big_tool_result_frames(session.id, big))
    rows = await event_log.read_after(session.id, after_seq=0)
    durable_turns = rebuild_turns(rows).turns
    assert len(durable_turns) == 2

    # Renderable gate needs an assistant turn OR >1 turn; a single SETTLED assistant
    # turn is renderable (has_assistant=True) and non-streaming, and is strictly shorter
    # than the 2-turn durable rebuild — so the FAULT C durable-return branch is taken.
    short_live = {
        "turns": [{"id": "live-a", "role": "assistant", "content": "partial"}],
        "is_active": False,
        "last_activity": "",
    }
    assert len(short_live["turns"]) < len(durable_turns)

    # Warm the durable-count cache so the FAULT C desync correction fires on this call (the cold
    # open otherwise serves the live body optimistically and warms the count in the background).
    rest_mod._DURABLE_COUNT_CACHE[str(session.id)] = (
        await event_log.latest_seq(session.id),
        len(durable_turns),
    )

    _, mock_client = _patch_live_pod(short_live)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_CONV_PATH.format(sid=session.id), params={"detail": "shallow"})

    assert resp.status_code == 200
    body = resp.json()
    mock_client.get.assert_awaited_once()
    # FAULT C durable rebuild was returned (longer than live) AND elided for shallow.
    shallow_block = _find_tool_result_block(body["turns"], "tu-big")
    assert shallow_block is not None
    assert shallow_block["truncated"] is True
    assert "content" not in shallow_block


# --- volundr-tier tool-result endpoint: proxy + durable-scan fallback -------------


def _patch_tool_result_pod(*, status_code: int, body: dict | None):
    """Mock ``rest.httpx.AsyncClient`` for the tool-result endpoint: the live pod GET
    returns ``status_code`` with ``body``. raise_for_status mirrors httpx (raises only
    for >= 400)."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.json.return_value = body

    def _raise_for_status():
        if status_code >= 400:
            raise httpx.HTTPStatusError("err", request=MagicMock(), response=mock_response)

    mock_response.raise_for_status.side_effect = _raise_for_status

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    return mock_response, mock_client


@pytest.mark.asyncio
async def test_tool_result_live_pod_proxied(monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) The volundr tool-result endpoint proxies a RUNNING session's live pod: the
    pod returns the full block and the endpoint returns it verbatim, without touching
    the durable log."""
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)

    proxied = {"tool_use_id": "tu-big", "content": "FULL CONTENT", "is_error": False}
    _, mock_client = _patch_tool_result_pod(status_code=200, body=proxied)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_TOOL_RESULT_PATH.format(sid=session.id, tuid="tu-big"))

    assert resp.status_code == 200
    assert resp.json() == proxied
    mock_client.get.assert_awaited_once()
    assert "tool-result" in mock_client.get.await_args.args[0]


@pytest.mark.asyncio
async def test_tool_result_live_pod_404_falls_back_to_durable_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) Live pod returns 404 (rebooted with a seed-only transcript) -> the endpoint
    falls THROUGH to the durable scan and finds the block in the rebuilt turns."""
    big = "Z" * 6000
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    await event_log.append(_big_tool_result_frames(session.id, big))

    _, mock_client = _patch_tool_result_pod(status_code=404, body=None)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_TOOL_RESULT_PATH.format(sid=session.id, tuid="tu-big"))

    assert resp.status_code == 200
    body = resp.json()
    mock_client.get.assert_awaited_once()  # pod WAS consulted, then fell through
    assert body["tool_use_id"] == "tu-big"
    assert body["content"] == big
    assert body["is_error"] is False


@pytest.mark.asyncio
async def test_tool_result_stopped_session_durable_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) A STOPPED session has no chat_endpoint -> the live-pod branch is skipped
    entirely and the durable scan finds the block. The httpx client must NOT be called."""
    big = "Z" * 6000
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = Session(name="stopped", model="claude-opus-4-8", status=SessionStatus.STOPPED)
    await repository.create(session)
    await event_log.append(_big_tool_result_frames(session.id, big))

    # No live-pod patch: a STOPPED session must never reach httpx. If it does, the real
    # AsyncClient would try a network call (or fail) — proving the branch was skipped.
    resp = client.get(_TOOL_RESULT_PATH.format(sid=session.id, tuid="tu-big"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["tool_use_id"] == "tu-big"
    assert body["content"] == big


@pytest.mark.asyncio
async def test_tool_result_absent_everywhere_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) Absent from both the live pod (404) and the durable log -> the endpoint
    surfaces a deterministic 404."""
    event_log = InMemorySessionEventLog()
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    await event_log.append(_big_tool_result_frames(session.id, "Z" * 6000))

    _, mock_client = _patch_tool_result_pod(status_code=404, body=None)
    with monkeypatch.context() as m:
        client_cls = MagicMock()
        client_cls.return_value.__aenter__.return_value = mock_client
        m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
        resp = client.get(_TOOL_RESULT_PATH.format(sid=session.id, tuid="does-not-exist"))

    assert resp.status_code == 404
    mock_client.get.assert_awaited_once()


def _build_activity_client(
    pod_manager: MockPodManager = _RUNNING_POD_MANAGER,
) -> tuple[TestClient, InMemorySessionRepository, SessionService]:
    """Like ``_build_client`` but exposes the live ``SessionService`` so an activity
    test can drive (and, for the 500 path, monkeypatch) the real update_activity."""
    repository = InMemorySessionRepository()
    session_service = SessionService(
        repository=repository,
        pod_manager=pod_manager,
        validate_repos=False,
    )
    app = FastAPI()
    app.include_router(create_router(session_service))

    class _SettingsStub:
        local_mounts = LocalMountsConfig()

    app.state.settings = _SettingsStub()
    app.state.admin_settings = {}
    return TestClient(app), repository, session_service


@pytest.mark.asyncio
async def test_activity_report_with_state_since_persists_state() -> None:
    """FAULT A end-to-end: POSTing an activity report carrying ``state_since`` returns
    204 AND actually persists ``activity_state`` (not None) on the session. Before the
    facade fix the endpoint raised TypeError on the ``state_since=`` kwarg, swallowed it
    into a false 204, and the state never changed — this test would have caught that by
    asserting the persisted state is ACTIVE, not the seed/None value."""
    client, repository, _ = _build_activity_client()
    session = await _running_session(repository)
    # Precondition: a fresh running session has no ACTIVE activity_state yet.
    before = await repository.get(session.id)
    assert before.activity_state != SessionActivityState.ACTIVE

    resp = client.post(
        _ACTIVITY_PATH.format(sid=session.id),
        json={
            "state": "active",
            "state_since": "2026-06-28T12:00:00+00:00",
            "metadata": {"source": "test"},
        },
    )

    assert resp.status_code == 204
    persisted = await repository.get(session.id)
    # The whole point of FAULT A: the state is now persisted, not None / unchanged.
    assert persisted.activity_state == SessionActivityState.ACTIVE
    assert persisted.activity_state_since is not None


@pytest.mark.asyncio
async def test_activity_report_surfaces_500_when_update_fails() -> None:
    """FAULT A defence: if ``update_activity`` raises a genuine (non-NotFound) error the
    endpoint must surface a 500, NOT masquerade the failure as a 204. The old code
    swallowed any Exception and returned 204, hiding persistence failures from the
    broker. Here we force update_activity to raise and assert the HTTP status is 500."""
    client, repository, session_service = _build_activity_client()
    session = await _running_session(repository)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated persistence failure")

    session_service.update_activity = _boom  # type: ignore[method-assign]

    resp = client.post(
        _ACTIVITY_PATH.format(sid=session.id),
        json={"state": "active", "metadata": {"source": "test"}},
    )

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_activity_report_unknown_session_is_404_not_500() -> None:
    """The SessionNotFoundError branch stays a 404 (not folded into the new 500 path)."""
    client, _, _ = _build_activity_client()

    resp = client.post(
        _ACTIVITY_PATH.format(sid=uuid4()),
        json={"state": "active", "metadata": {}},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unknown_session_conversation_404_not_empty_body() -> None:
    """INV-9 deterministic close on a truly unknown session id — a hard 404, never a
    silent empty conversation body."""
    event_log = InMemorySessionEventLog()
    client, _ = _build_client(Session(name="x", model="m", status=SessionStatus.STOPPED), event_log)

    resp = client.get(_CONV_PATH.format(sid=uuid4()))

    assert resp.status_code == 404
