"""End-to-end tests for the replay-as-live WebSocket router.

Driven through ``TestClient.websocket_connect`` against a bare ``FastAPI`` app
with ONLY the replay router mounted — no live DB, no broker. The DB-backed route
uses the in-memory ``SessionEventLogRepository`` from the REST log tests; the
fixture route reads a checked-in ``*.frames.json`` (proving one corpus serves
both repos offline).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

# Reuse the in-memory log used by the REST endpoint tests.
from tests.test_adapters.test_rest_session_log import InMemoryLog
from volundr.adapters.inbound.ws_session_replay import _run, create_session_replay_router
from volundr.config import ReplayConfig
from volundr.domain.models import SessionLogEntry
from volundr.replay.fixtures import default_fixtures_dir
from volundr.replay.pacing import PacingConfig

# The SINGLE packaged fixture corpus (what prod serves) — no duplicate test copy.
_FIXTURES_DIR = str(default_fixtures_dir())
_BASE = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)


def _app(repo: InMemoryLog | None = None, **cfg_kwargs) -> tuple[FastAPI, InMemoryLog]:
    repo = repo if repo is not None else InMemoryLog()
    cfg = ReplayConfig(
        enabled=True,
        fixtures_enabled=cfg_kwargs.pop("fixtures_enabled", True),
        fixtures_dir=cfg_kwargs.pop("fixtures_dir", _FIXTURES_DIR),
        max_gap_seconds=cfg_kwargs.pop("max_gap_seconds", 0.0),  # instant by default
        default_show_internal=cfg_kwargs.pop("default_show_internal", True),
        **cfg_kwargs,
    )
    app = FastAPI()
    app.include_router(create_session_replay_router(repo, session_service=None, config=cfg))
    return app, repo


async def _seed(repo: InMemoryLog, session_id, rows: list[dict]) -> None:
    entries = [
        SessionLogEntry(
            session_id=session_id,
            seq=r["seq"],
            kind=r["kind"],
            payload=r["payload"],
            ts=_BASE + timedelta(seconds=r.get("offset", 0)),
            role=r.get("role"),
            request_id="req-1",
        )
        for r in rows
    ]
    await repo.append(entries)


def _drain(ws) -> list[dict]:
    """Read frames until the server closes the socket."""
    from starlette.testclient import WebSocketDisconnect

    out: list[dict] = []
    try:
        while True:
            out.append(ws.receive_json())
    except WebSocketDisconnect:
        pass
    return out


# Rows mirroring the Phase-1 claude-incremental-refactor shape: assistant/user
# messages whose content lists carry tool_use / tool_result blocks.
_MIXED_ROWS = [
    {
        "seq": 1,
        "kind": "user",
        "offset": 0,
        "payload": {"type": "user", "message": {"role": "user", "content": "do the thing"}},
    },
    {
        "seq": 2,
        "kind": "assistant",
        "offset": 3,
        "payload": {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "reading"},
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "Read",
                        "input": {"file_path": "a.ts"},
                    },
                ],
            },
        },
    },
    {
        "seq": 3,
        "kind": "user",
        "offset": 4,
        "payload": {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "file body"},
                ],
            },
        },
    },
    {
        "seq": 4,
        "kind": "assistant",
        "offset": 9,
        "payload": {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu2", "name": "Edit", "input": {}},
                ],
            },
        },
    },
    {"seq": 5, "kind": "result", "offset": 10, "payload": {"type": "result", "ok": True}},
]


def _block_types(frame: dict) -> list[str]:
    msg = frame.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):
        return [b.get("type") for b in msg["content"] if isinstance(b, dict)]
    return []


# --------------------------------------------------------------------------- #
# Preamble + verbatim stream
# --------------------------------------------------------------------------- #


def test_preamble_order_and_no_conversation_history_at_after_zero():
    sid = uuid4()
    app, repo = _app()
    with TestClient(app) as client:
        # seed synchronously via the running app's portal
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        with client.websocket_connect(f"/api/v1/forge/sessions/{sid}/replay?speed=1000") as ws:
            frames = _drain(ws)

    assert frames[0]["type"] == "system"
    assert f"Replaying session {sid}" in frames[0]["content"]
    assert frames[1]["type"] == "capabilities"
    # Read-only capabilities advertised.
    assert frames[1]["send_message"] is False
    assert frames[1]["steer"] is False
    # Read-only replay cannot honor slash commands / skills (the live
    # available_commands catalog is not in the durable log).
    assert frames[1]["slash_commands"] is False
    assert frames[1]["skills"] is False
    # No conversation_history when attaching from zero (the stream IS history).
    assert not any(f.get("type") == "conversation_history" for f in frames)


def test_payloads_stream_verbatim_in_seq_order():
    sid = uuid4()
    app, repo = _app()
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        with client.websocket_connect(f"/api/v1/forge/sessions/{sid}/replay?speed=1000") as ws:
            frames = _drain(ws)

    body = [f for f in frames if f.get("type") not in ("system", "capabilities")]
    assert [f["type"] for f in body] == ["user", "assistant", "user", "assistant", "result"]
    # Verbatim: the tool_use input survives unchanged when internals are shown.
    assert body[1]["message"]["content"][1]["name"] == "Read"


def test_preamble_can_be_suppressed():
    sid = uuid4()
    app, repo = _app()
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        with client.websocket_connect(
            f"/api/v1/forge/sessions/{sid}/replay?speed=1000&preamble=false"
        ) as ws:
            frames = _drain(ws)

    assert not any(f.get("type") in ("system", "capabilities") for f in frames)
    assert frames[0]["type"] == "user"


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #


def test_show_internal_default_on_streams_tool_blocks():
    sid = uuid4()
    app, repo = _app(default_show_internal=True)
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        with client.websocket_connect(f"/api/v1/forge/sessions/{sid}/replay?speed=1000") as ws:
            frames = _drain(ws)

    body = [f for f in frames if f.get("type") not in ("system", "capabilities")]
    # All five frames present; tool_use / tool_result blocks intact.
    assert len(body) == 5
    assert "tool_use" in _block_types(body[1])
    assert "tool_result" in _block_types(body[2])
    assert "tool_use" in _block_types(body[3])


def test_show_internal_false_strips_tool_blocks():
    sid = uuid4()
    app, repo = _app()
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        with client.websocket_connect(
            f"/api/v1/forge/sessions/{sid}/replay?speed=1000&show_internal=false"
        ) as ws:
            frames = _drain(ws)

    body = [f for f in frames if f.get("type") not in ("system", "capabilities")]
    # seq1 (user text) kept; seq2 assistant keeps text, drops tool_use; seq3
    # (tool_result-only) dropped entirely; seq4 (tool_use-only) dropped; seq5 kept.
    assert [f["type"] for f in body] == ["user", "assistant", "result"]
    assert _block_types(body[1]) == ["text"]  # tool_use stripped, text retained
    # No tool blocks anywhere in the visible stream.
    for f in body:
        assert "tool_use" not in _block_types(f)
        assert "tool_result" not in _block_types(f)


def test_set_internal_visibility_toggle_unhides_subsequent_tool_frames():
    # Start hidden; send the toggle BEFORE reading; use generous pacing so the
    # concurrent receiver processes the toggle well before the later tool-only
    # frame (seq4 @ +9s) is emitted.
    sid = uuid4()
    app, repo = _app(max_gap_seconds=2.0)
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        with client.websocket_connect(
            f"/api/v1/forge/sessions/{sid}/replay?speed=20&show_internal=false"
        ) as ws:
            ws.send_json({"type": "set_internal_visibility", "visible": True})
            frames = _drain(ws)

    body = [f for f in frames if f.get("type") not in ("system", "capabilities")]
    # After the toggle lands, the tool-only frame (seq4) is no longer dropped.
    tool_use_frames = [f for f in body if "tool_use" in _block_types(f)]
    assert any(
        f.get("message", {}).get("content", [{}])[0].get("name") == "Edit" for f in tool_use_frames
    )
    # And the final result frame still arrives (stream completed).
    assert body[-1]["type"] == "result"


# --------------------------------------------------------------------------- #
# Cursor / empty
# --------------------------------------------------------------------------- #


def test_after_cursor_replays_tail_only():
    sid = uuid4()
    app, repo = _app()
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        with client.websocket_connect(
            f"/api/v1/forge/sessions/{sid}/replay?speed=1000&after=3"
        ) as ws:
            frames = _drain(ws)

    body = [f for f in frames if f.get("type") not in ("system", "capabilities")]
    # Only seq 4 (assistant) and seq 5 (result) remain after cursor=3.
    assert [f["type"] for f in body] == ["assistant", "result"]


def test_empty_session_sends_preamble_then_closes():
    sid = uuid4()
    app, _repo = _app()
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/forge/sessions/{sid}/replay?speed=1000") as ws:
            frames = _drain(ws)

    # Preamble only, then a clean close.
    assert [f["type"] for f in frames] == ["system", "capabilities"]


# --------------------------------------------------------------------------- #
# Fixture route (no DB touched)
# --------------------------------------------------------------------------- #


def test_fixture_route_streams_payloads_without_touching_repo():
    # A repo that explodes if read — proves the fixture route never hits the DB.
    class ExplodingRepo(InMemoryLog):
        async def read_after(self, *a, **k):  # type: ignore[override]
            raise AssertionError("fixture route must not touch the repository")

    app, _repo = _app(repo=ExplodingRepo())
    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/forge/replay/fixtures/two-turn?speed=1000&preamble=false"
        ) as ws:
            frames = _drain(ws)

    # Compare against the on-disk fixture's payloads in seq order.
    raw = json.loads((Path(_FIXTURES_DIR) / "two-turn.frames.json").read_text())
    expected = [r["payload"] for r in sorted(raw, key=lambda r: r["seq"])]
    assert frames == expected


def test_fixture_route_full_name_also_works():
    app, _repo = _app()
    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/forge/replay/fixtures/two-turn.frames.json?speed=1000&preamble=false"
        ) as ws:
            frames = _drain(ws)
    assert len(frames) >= 2


# --------------------------------------------------------------------------- #
# Auth path (session_service wired)
# --------------------------------------------------------------------------- #


class _FakeSessionService:
    """Minimal SessionService stub exercising the auth branch DB-free.

    ``get_session`` returns a sentinel session (non-None so ``_check_access``
    runs); ``_check_access`` either raises ``SessionAccessDeniedError`` or
    returns, per ``deny``.
    """

    def __init__(self, *, deny: bool) -> None:
        self._deny = deny

    async def get_session(self, session_id):  # noqa: ANN001
        return object()  # sentinel: non-None so _check_access is invoked

    async def _check_access(self, session, principal, action="read"):  # noqa: ANN001
        if self._deny:
            from volundr.domain.services.session import SessionAccessDeniedError

            raise SessionAccessDeniedError(uuid4(), "user-x")


def _app_with_session_service(service, **cfg_kwargs) -> tuple[FastAPI, InMemoryLog]:
    repo = InMemoryLog()
    cfg = ReplayConfig(
        enabled=True,
        fixtures_enabled=cfg_kwargs.pop("fixtures_enabled", True),
        fixtures_dir=cfg_kwargs.pop("fixtures_dir", _FIXTURES_DIR),
        max_gap_seconds=cfg_kwargs.pop("max_gap_seconds", 0.0),
        default_show_internal=cfg_kwargs.pop("default_show_internal", True),
        **cfg_kwargs,
    )
    app = FastAPI()
    app.include_router(create_session_replay_router(repo, session_service=service, config=cfg))
    # Allow-all identity so extract_principal succeeds without an Authorization
    # header (the deny/success behavior is driven by _check_access, not auth).
    # Subclass to satisfy the isinstance(AllowAllIdentityAdapter) branch without
    # standing up a real UserRepository.
    from volundr.adapters.outbound.identity import AllowAllIdentityAdapter

    class _AllowAll(AllowAllIdentityAdapter):
        def __init__(self) -> None:  # noqa: D401
            self._default_tenant_id = "default"

    app.state.identity = _AllowAll()
    return app, repo


def test_access_denied_closes_1008():
    sid = uuid4()
    app, repo = _app_with_session_service(_FakeSessionService(deny=True))
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        _expect_close(client, f"/api/v1/forge/sessions/{sid}/replay?speed=1000", code=1008)


def test_access_granted_streams():
    sid = uuid4()
    app, repo = _app_with_session_service(_FakeSessionService(deny=False))
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        with client.websocket_connect(f"/api/v1/forge/sessions/{sid}/replay?speed=1000") as ws:
            frames = _drain(ws)

    # Preamble + full body streamed once access is granted.
    assert frames[0]["type"] == "system"
    assert frames[1]["type"] == "capabilities"
    body = [f for f in frames if f.get("type") not in ("system", "capabilities")]
    assert [f["type"] for f in body] == ["user", "assistant", "user", "assistant", "result"]


def test_missing_token_closes_1008():
    # Token-mode identity (neither AllowAll nor Envoy) with no Authorization
    # header -> extract_principal raises HTTPException -> graceful close(1008)
    # before accept(), not an uncaught handshake drop.
    class _TokenModeIdentity:
        """Not an AllowAll/Envoy adapter, so extract_principal hits the
        token-validation branch and raises on the missing header."""

    sid = uuid4()
    service = _FakeSessionService(deny=False)
    app, repo = _app_with_session_service(service)
    app.state.identity = _TokenModeIdentity()
    with TestClient(app) as client:
        client.portal.call(_seed, repo, sid, _MIXED_ROWS)
        _expect_close(client, f"/api/v1/forge/sessions/{sid}/replay?speed=1000", code=1008)


# --------------------------------------------------------------------------- #
# Rejections (close 1008)
# --------------------------------------------------------------------------- #


def _expect_close(client, url, *, code: int | None = None) -> int:
    """Connect and assert the socket is closed (no frames). Return the close code.

    If ``code`` is given, assert exactly that close code.
    """
    from starlette.testclient import WebSocketDisconnect

    try:
        with client.websocket_connect(url) as ws:
            ws.receive_json()
        raise AssertionError("expected the socket to be closed")
    except WebSocketDisconnect as exc:
        if code is not None:
            assert exc.code == code, f"expected close {code}, got {exc.code}"
        return exc.code


def test_fixture_route_rejects_traversal_with_slash():
    # A name decoding to a path with "/" (e.g. "..%2fsecret") never matches the
    # single-segment {name} route — Starlette refuses the handshake (close 1000).
    # The traversal is blocked at the routing layer before our handler runs.
    app, _repo = _app()
    with TestClient(app) as client:
        _expect_close(client, "/api/v1/forge/replay/fixtures/..%2fsecret", code=1000)


def test_fixture_route_rejects_dotdot_name_in_handler():
    # An encoded ".." with no slash ("%2e%2e") reaches the handler as a single
    # segment; resolve_fixture / load then reject it -> handler closes 1008.
    app, _repo = _app()
    with TestClient(app) as client:
        _expect_close(client, "/api/v1/forge/replay/fixtures/%2e%2e", code=1008)


def test_fixture_route_rejects_unknown_fixture():
    app, _repo = _app()
    with TestClient(app) as client:
        _expect_close(client, "/api/v1/forge/replay/fixtures/does-not-exist", code=1008)


def test_fixture_route_closed_when_fixtures_disabled():
    app, _repo = _app(fixtures_enabled=False)
    with TestClient(app) as client:
        _expect_close(client, "/api/v1/forge/replay/fixtures/two-turn", code=1008)


# --------------------------------------------------------------------------- #
# _run concurrency: deterministic disconnect-cancellation + toggle (no TestClient,
# no wall-clock). A hand-rolled fake WS controls receive timing exactly.
# --------------------------------------------------------------------------- #


class _FakeWS:
    """Minimal WebSocket double for driving ``_run`` directly.

    ``incoming`` items are returned (dicts) or raised (exceptions) by successive
    ``receive_json`` calls; once exhausted, ``receive_json`` blocks forever.
    """

    def __init__(self, incoming: list) -> None:
        self.sent: list[dict] = []
        self.closed: int | None = None
        self.accepted = False
        self._incoming = list(incoming)

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def receive_json(self):
        if self._incoming:
            item = self._incoming.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        await asyncio.Event().wait()  # park: no more client frames

    async def close(self, code: int = 1000) -> None:
        self.closed = code


class _ListSrc:
    def __init__(self, entries: list[SessionLogEntry]) -> None:
        self._entries = entries

    async def entries(self, *, after_seq: int = 0):
        for e in self._entries:
            if e.seq > after_seq:
                yield e


def _sle(seq: int, offset: float, kind: str, payload: dict) -> SessionLogEntry:
    return SessionLogEntry(
        session_id=uuid4(),
        seq=seq,
        kind=kind,
        payload=payload,
        ts=_BASE + timedelta(seconds=offset),
        role=None,
        request_id="r",
    )


async def test_disconnect_during_pacing_sleep_cancels_driver_promptly():
    # The driver parks in a sleep that never returns; the receiver sees an
    # immediate disconnect. The concurrent wait must cancel the parked driver so
    # _run returns having emitted ONLY the pre-sleep frame — not the whole stream.
    rows = [
        _sle(1, 0, "assistant", {"type": "assistant", "seq": 1}),
        _sle(2, 5, "assistant", {"type": "assistant", "seq": 2}),
        _sle(3, 10, "assistant", {"type": "assistant", "seq": 3}),
    ]
    parked = asyncio.Event()  # never set

    async def never_returns(_d: float) -> None:
        await parked.wait()

    ws = _FakeWS(incoming=[WebSocketDisconnect()])
    await asyncio.wait_for(
        _run(
            ws,
            _ListSrc(rows),
            "s",
            PacingConfig(speed=1.0, max_gap_seconds=2.0),
            after=0,
            show_internal=True,
            preamble=False,
            sleep=never_returns,
        ),
        timeout=2.0,
    )

    body = [f for f in ws.sent if f.get("type") not in ("system", "capabilities")]
    assert [f["seq"] for f in body] == [1]  # parked before seq2; disconnect cancelled it
    assert ws.closed is not None


async def test_visibility_toggle_during_stream_is_honored_deterministically():
    # show_internal starts False; the receiver delivers visible=True then parks.
    # A yield-only sleep guarantees the receiver applies the toggle before the
    # later tool-only frame is emitted — deterministic, no wall-clock.
    rows = [
        _sle(1, 0, "user", {"type": "user", "message": {"role": "user", "content": "go"}}),
        _sle(
            2,
            3,
            "assistant",
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tu", "name": "Edit", "input": {}}],
                },
            },
        ),
    ]

    async def yield_sleep(_d: float) -> None:
        for _ in range(8):
            await asyncio.sleep(0)

    ws = _FakeWS(incoming=[{"type": "set_internal_visibility", "visible": True}])
    await asyncio.wait_for(
        _run(
            ws,
            _ListSrc(rows),
            "s",
            PacingConfig(speed=1.0, max_gap_seconds=2.0),
            after=0,
            show_internal=False,
            preamble=False,
            sleep=yield_sleep,
        ),
        timeout=2.0,
    )

    # The tool-only frame (seq2) was hidden at start; the toggle, applied during
    # the pre-seq2 sleep, unhides it.
    assert any("tool_use" in _block_types(f) for f in ws.sent)
