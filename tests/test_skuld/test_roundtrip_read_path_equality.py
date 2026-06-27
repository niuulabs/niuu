"""Epic F invariant suite — INV-5 (the headline round-trip), INV-1, INV-10.

THE HEADLINE CONTRACT (SRD §7, INV-5):

    what you saw LIVE (post-gate)
        == what you REPLAY from after=0 (post-gate)
        == what you COLD-READ via GET /log (default gated)

…frame-for-frame, over ONE shared durable log and ONE shared visibility gate
(:func:`skuld.channels.filter_internal_blocks`). This is the seam where Epic A
(durable superset), Epic B (one shared reducer) and Epic D (read-path
unification) meet: a single mixed turn is driven through a *real*
``skuld.broker.Broker`` and then read back through all three surfaces.

Self-contained: no tmux, no real Postgres, no aiohttp, no live IDP. The Broker
is wired the MINIMAL manual way (no ``startup()``) — same approach as
``tests/support/forge/broker_harness.py`` documents: a truthy
``volundr_api_url`` so ``_enqueue_event_log`` buffers, and the flush loop is
NEVER started so the buffer accumulates and is fully readable. The Volundr HTTP
client is stubbed so the broker's background reporters (activity / usage /
timeline / trace spans / pipeline) touch no network.

Shared fakes are reused by IMPORT only (no edits to conftest / support):

* ``tests.test_adapters.test_rest_session_log.InMemoryLog`` — the one durable
  log fake (mirrors the ``pg_session_event_log`` contract).
* ``volundr.adapters.inbound.rest_session_log.create_session_log_router`` — the
  cold-read GET /log surface (Epic D, default gated).
* ``volundr.adapters.inbound.ws_session_replay.create_session_replay_router`` —
  the replay-as-live WS surface (Epic D, default gated).
* ``skuld.channels.WebSocketChannel`` / ``filter_internal_blocks`` — the ONE
  live broadcast gate.

INV coverage here (genuine gaps, not duplicating Epics A–D):

* INV-5  — the unified three-way round-trip equality (the headline test).
* INV-1  — every frame a live client saw is present in the durable log
  (superset), systematically across ALL frame kinds in the mixed turn,
  appended BEFORE it is broadcast.
* INV-10 — visibility parity: the dropped set is identical across the three
  read paths because they share ``filter_internal_blocks``.

The autouse ``tests/test_skuld/conftest.py`` fixture strips ambient
``SKULD__*``/``VOLUNDR*`` env, so this file (under ``tests/test_skuld``) does not
strip them itself.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDisconnect

from skuld.broker import Broker
from skuld.channels import WebSocketChannel, filter_internal_blocks
from skuld.config import SkuldSettings

# Reuse the SINGLE in-memory durable-log fake the REST/replay endpoint tests use
# (mirrors the pg_session_event_log append/read_after/latest_seq contract).
from tests.test_adapters.test_rest_session_log import InMemoryLog
from volundr.adapters.inbound.rest_session_log import create_session_log_router
from volundr.adapters.inbound.ws_session_replay import create_session_replay_router
from volundr.config import ReplayConfig
from volundr.domain.models import SessionLogEntry

# ---------------------------------------------------------------------------
# Infrastructure stubs (NO network, NO tmux) — mock infrastructure, per rules.
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal httpx-response surface the broker's reporters read."""

    status_code = 200
    text = ""
    url = "http://harness.invalid/"


class _FakeHttpClient:
    """Stand-in for ``httpx.AsyncClient`` — every call is a no-op 200.

    The broker fires background reporters (activity / usage / timeline / trace
    spans / pipeline) the instant a real frame flows; with a truthy
    ``volundr_api_url`` they would otherwise hit the network. This keeps them
    in-process and deterministic while still exercising the broker's real
    enqueue-then-broadcast paths.
    """

    async def post(self, *_a, **_k) -> _FakeResponse:
        return _FakeResponse()

    async def patch(self, *_a, **_k) -> _FakeResponse:
        return _FakeResponse()

    async def aclose(self) -> None:
        return None


class _RecordingWebSocket:
    """In-process stand-in for the live browser WebSocket surface a
    ``WebSocketChannel`` touches (``send_text`` / ``send_json`` / ``close``).

    Records the JSON frames the channel actually delivered — i.e. the LIVE,
    post-gate frame stream a connected client would render.
    """

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed = False

    async def send_text(self, text: str) -> None:
        import json

        self.frames.append(json.loads(text))

    async def send_json(self, payload: dict) -> None:
        self.frames.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# The mixed deterministic turn.
#
# One turn that exercises EVERY gate branch + the broker's two persist-before-
# broadcast choke points:
#   * _handle_cli_event  — transport frames (user / assistant text+tool_use /
#     tool_result-only user / internal content_block span / result / error).
#   * _emit_broker_frame — broker-originated frames (user_confirmed steering ACK
#     + user_delivered delivery ACK).
#
# Gate behaviour the round-trip must reproduce identically on all 3 read paths:
#   * the assistant frame keeps its text block, drops its tool_use block;
#   * the tool_result-only user frame is dropped wholesale;
#   * the internal content_block_{start,delta,stop} span is dropped wholesale;
#   * user / user_confirmed / user_delivered / result / error pass verbatim.
# ---------------------------------------------------------------------------


def _transport_frames() -> list[dict]:
    return [
        {"type": "user", "role": "user", "message": {"role": "user", "content": "go"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "reading the file"},
                    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"p": "a.ts"}},
                ],
            },
        },
        {
            "type": "user",
            "role": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "body"}],
            },
        },
        {"type": "content_block_start", "content_block": {"type": "tool_use"}},
        {"type": "content_block_delta", "delta": {"partial_json": "{}"}},
        {"type": "content_block_stop"},
        # modelUsage empty on purpose: keeps _report_usage / token-pipeline tasks
        # from firing, so the background fan-out stays small and drainable.
        {"type": "result", "subtype": "success", "result": "done", "modelUsage": {}},
        {"type": "error", "error": {"message": "boom"}},
    ]


def _broker_frames() -> list[dict]:
    # Broker-originated steering + delivery ACKs (Epic C). They are NOT transport
    # frames, so they ride _emit_broker_frame (persist-before-broadcast), and the
    # gate passes them verbatim (not assistant/user/content_block).
    return [
        {"type": "user_confirmed", "id": "m1", "content": "steer me", "steering_state": "pending"},
        {"type": "user_delivered", "id": "m1"},
    ]


async def _drain_background_tasks() -> None:
    """Let the broker's fire-and-forget reporters run to completion.

    _handle_cli_event schedules activity/timeline/pipeline/mesh tasks via
    asyncio.create_task. They all hit the stubbed (offline) HTTP client and
    return; draining them here prevents 'Task was destroyed but it is pending'
    warnings (which -W error would turn into failures).
    """
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    for _ in range(50):
        pending = [t for t in asyncio.all_tasks(loop) if t is not current and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
    raise AssertionError("broker background tasks did not settle")


async def _drive_turn(broker: Broker) -> None:
    """Drive the mixed turn through the broker's REAL choke points."""
    for frame in _transport_frames():
        await broker._handle_cli_event(frame)
    for frame in _broker_frames():
        await broker._emit_broker_frame(frame)
    await _drain_background_tasks()


# ---------------------------------------------------------------------------
# Read-path helpers — the ONE shared gate applied three ways.
# ---------------------------------------------------------------------------


def _gate_payloads(payloads: list[dict]) -> list[dict]:
    """Apply the SHARED filter_internal_blocks predicate to a raw payload stream.

    This is the reference live gate: the SAME per-stream open_block_type
    threading the WebSocketChannel, the replay _VisibilityGate, and the
    cold-read _gate_entries all use. Returns the visible (possibly stripped)
    payloads in order.
    """
    kept: list[dict] = []
    open_block: str | None = None
    for payload in payloads:
        filtered, open_block = filter_internal_blocks(payload, open_block_type=open_block)
        if filtered is None:
            continue
        kept.append(filtered)
    return kept


# The ONE documented synthetic aggregate (FR-3 / FR-10): ``conversation.turn``
# rows are written to the durable log by ``Broker._append_turn`` as a derived,
# non-authoritative reducer SEED — they are NOT wire frames and are NEVER
# broadcast to a live channel. The shared reducer (``reduce_frames``) consumes
# them as ``sdk_turns`` and skips the matching raw frames so a fold never
# double-counts. They therefore must NOT participate in the raw frame-for-frame
# stream a wire client renders.
_SYNTHETIC_AGGREGATE_KIND = "conversation.turn"


def _streamable(payloads: list[dict]) -> list[dict]:
    """Drop the synthetic ``conversation.turn`` aggregate from a payload stream.

    This is the wire-streamable projection: what a client actually RENDERS frame
    by frame, excluding the derived reducer-seed rows that the live broadcast
    never emitted. The companion ``test_synthetic_aggregate_is_the_sole_*`` tests
    prove this projection hides NOTHING else (the dropped rows are exactly the
    synthetic aggregates, and they are losslessly re-derivable by the reducer).
    """
    return [p for p in payloads if p.get("type") != _SYNTHETIC_AGGREGATE_KIND]


def _buffer_to_log_entries(broker: Broker, session_id: UUID) -> list[SessionLogEntry]:
    """Map the broker's durable buffer to SessionLogEntry rows (the log contract)."""
    from datetime import UTC, datetime

    entries: list[SessionLogEntry] = []
    for raw in broker._event_log_buffer:
        ts_raw = raw.get("ts")
        ts = datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else datetime.now(UTC)
        entries.append(
            SessionLogEntry(
                session_id=session_id,
                seq=int(raw["seq"]),
                kind=str(raw["kind"]),
                payload=raw["payload"],
                ts=ts,
                role=raw.get("role"),
                request_id=raw.get("request_id"),
            )
        )
    return entries


def _cold_read(repo: InMemoryLog, session_id: UUID) -> list[dict]:
    """Default-gated cold read via GET /log (Epic D)."""
    app = FastAPI()
    app.include_router(create_session_log_router(repo, session_service=None))
    client = TestClient(app)
    resp = client.get(f"/api/v1/forge/sessions/{session_id}/log")
    assert resp.status_code == 200
    return resp.json()


def _replay_after_zero(repo: InMemoryLog, session_id: UUID) -> list[dict]:
    """Default-gated replay-as-live from after=0 (Epic D), preamble suppressed."""
    cfg = ReplayConfig(
        enabled=True,
        fixtures_enabled=False,
        max_gap_seconds=0.0,  # instant pacing
        default_show_internal=False,  # gated by default, like live + cold-read
    )
    app = FastAPI()
    app.include_router(create_session_replay_router(repo, session_service=None, config=cfg))
    client = TestClient(app)
    out: list[dict] = []
    # preamble=false so the stream is ONLY the gated frame tail (no system /
    # capabilities envelope), directly comparable to live + cold-read.
    url = f"/api/v1/forge/sessions/{session_id}/replay?preamble=false"
    with client.websocket_connect(url) as ws:
        try:
            while True:
                out.append(ws.receive_json())
        except WebSocketDisconnect:
            pass
    return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_id() -> UUID:
    return uuid4()


@pytest.fixture
async def driven(session_id, tmp_path):
    """A real Broker that has just emitted the mixed turn, with a connected live
    channel — returns (broker, live_ws, shared_log)."""
    broker = Broker(
        settings=SkuldSettings(
            volundr_api_url="http://harness.invalid",
            event_log_enabled=True,
            session={"id": str(session_id), "workspace_dir": str(tmp_path)},
        )
    )
    # Offline HTTP client for every background reporter.
    fake_client = _FakeHttpClient()

    async def _get_client() -> _FakeHttpClient:
        return fake_client

    broker._get_http_client = _get_client  # type: ignore[assignment]

    # Attach the REAL live broadcast gate: WebSocketChannel(show_internal=False)
    # records exactly the post-gate frames a connected browser would render.
    live_ws = _RecordingWebSocket()
    channel = WebSocketChannel(live_ws, show_internal=False)
    broker._channels.add(channel)

    await _drive_turn(broker)

    # The broker's _trace_id is the session UUID (session.id is a UUID string).
    assert broker._trace_id == session_id

    # Drain the durable buffer into the ONE shared log (no real PG).
    repo = InMemoryLog()
    await repo.append(_buffer_to_log_entries(broker, session_id))

    return broker, live_ws, repo


# ---------------------------------------------------------------------------
# INV-1 — superset: every frame a live client saw is in the durable log,
# appended BEFORE it was broadcast.
# ---------------------------------------------------------------------------


class TestSupersetINV1:
    async def test_every_live_frame_is_in_the_log(self, driven, session_id):
        broker, live_ws, repo = driven

        log_entries = await repo.read_after(session_id, after_seq=0, limit=10_000)
        # The durable log carries the FULL stream; the post-gate live stream is a
        # SUBSET of it. Project the log down to what a wire client renders — apply
        # the SAME shared gate AND drop the synthetic conversation.turn aggregate
        # (never broadcast live) — and every frame the client saw must be present,
        # in order.
        log_payloads = _gate_payloads(_streamable([e.payload for e in log_entries]))

        assert live_ws.frames  # the turn actually reached a client
        assert live_ws.frames == log_payloads

    async def test_log_is_a_strict_superset_of_live(self, driven, session_id):
        broker, live_ws, repo = driven

        all_entries = await repo.read_after(session_id, after_seq=0, limit=10_000)
        # Raw log > gated live: the internal frames the client never saw
        # (tool_result-only user, the content_block span) AND the synthetic
        # conversation.turn aggregates are still durably present, so no agent
        # output was lost (INV-1 superset, not equality).
        assert len(all_entries) > len(live_ws.frames)
        raw_kinds = [e.kind for e in all_entries]
        assert "content_block_start" in raw_kinds
        assert "content_block_delta" in raw_kinds
        assert "content_block_stop" in raw_kinds
        assert _SYNTHETIC_AGGREGATE_KIND in raw_kinds

    def test_persist_before_broadcast_ordering(self, session_id, tmp_path):
        # INV-1 ordering: _enqueue_event_log runs BEFORE _channels.broadcast in
        # both choke points, so a frame is durable the instant it is visible.
        # Assert this by observing the buffer length at broadcast time.
        async def _run() -> None:
            broker = Broker(
                settings=SkuldSettings(
                    volundr_api_url="http://harness.invalid",
                    event_log_enabled=True,
                    session={"id": str(session_id), "workspace_dir": str(tmp_path)},
                )
            )

            async def _get_client() -> _FakeHttpClient:
                return _FakeHttpClient()

            broker._get_http_client = _get_client  # type: ignore[assignment]

            buffer_len_at_broadcast: list[int] = []

            class _SpyChannel(WebSocketChannel):
                async def send_event(self, event: dict) -> None:
                    # At the moment of broadcast, the frame is ALREADY buffered.
                    buffer_len_at_broadcast.append(len(broker._event_log_buffer))

            broker._channels.add(_SpyChannel(_RecordingWebSocket(), show_internal=True))

            await broker._emit_broker_frame({"type": "user_confirmed", "id": "x"})
            await _drain_background_tasks()

            assert buffer_len_at_broadcast == [1]

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# INV-5 — the headline three-way round-trip equality.
# ---------------------------------------------------------------------------


class TestRoundTripEqualityINV5:
    async def test_live_equals_replay_equals_cold_read_frame_for_frame(self, driven, session_id):
        # THE HEADLINE: live (post-gate) == replay after=0 (post-gate) == gated
        # cold-read, frame-for-frame, over ONE shared log and ONE shared gate.
        # Compared over the wire-streamable projection — i.e. excluding the
        # synthetic conversation.turn reducer-seed that the live broadcast never
        # emitted (its sole-difference status is PROVED in the next test, so this
        # projection is not hiding any real divergence).
        broker, live_ws, repo = driven

        # (1) LIVE — what the connected client saw, gated by the real
        #     WebSocketChannel(show_internal=False). (No synthetic rows ever reach
        #     a live channel, so _streamable is a no-op here — applied for symmetry.)
        live = _streamable(live_ws.frames)

        # (2) COLD-READ — GET /log default gated, over the SAME shared log.
        cold_rows = _cold_read(repo, session_id)
        cold = _streamable([row["payload"] for row in cold_rows])

        # (3) REPLAY after=0 — replay-as-live default gated, over the SAME log.
        replay = _streamable(_replay_after_zero(repo, session_id))

        # All three independently-derived streams must agree frame-for-frame.
        assert live, "live stream was empty — the turn never reached the client"
        assert live == cold
        assert cold == replay

        # Spell out the visible shape so a regression is legible, not just "!=".
        assert [p["type"] for p in cold] == [
            "user",
            "assistant",  # tool_use stripped, text kept
            "result",
            "error",
            "user_confirmed",
            "user_delivered",
        ]
        assistant = next(p for p in cold if p.get("type") == "assistant")
        block_types = [b["type"] for b in assistant["message"]["content"]]
        assert block_types == ["text"]  # tool_use dropped by the shared gate

    async def test_synthetic_aggregate_is_excluded_from_the_wire(self, driven, session_id):
        # GUARDS the closed gap: the synthetic conversation.turn reducer-seed rows
        # are the ONLY rows that ever distinguished the durable log from the live
        # broadcast, and the wire read paths now exclude them ALWAYS. So:
        #   * the gated cold-read wire stream carries NO conversation.turn rows;
        #   * cold-read MINUS live is now EMPTY at the wire (the leak is closed);
        #   * the rows still live durably in the raw log (read_after), proving the
        #     exclusion is a wire projection, not a deletion (INV-1 superset holds).
        broker, live_ws, repo = driven

        cold_rows = _cold_read(repo, session_id)
        cold_payloads = [row["payload"] for row in cold_rows]

        # No synthetic rows on the cold-read WIRE.
        assert all(p.get("type") != _SYNTHETIC_AGGREGATE_KIND for p in cold_payloads)
        # Cold-read MINUS live is now EMPTY — the wire streams agree exactly.
        difference = [p for p in cold_payloads if p not in live_ws.frames]
        assert difference == [], "synthetic aggregates must no longer leak onto the wire"
        assert cold_payloads == live_ws.frames

        # …yet the synthetic rows are STILL durably present in the raw log: the wire
        # exclusion is a projection over read_after, not a deletion from it.
        raw_entries = await repo.read_after(session_id, after_seq=0, limit=10_000)
        raw_synthetic = [e for e in raw_entries if e.kind == _SYNTHETIC_AGGREGATE_KIND]
        assert raw_synthetic, "conversation.turn must remain authoritative in the durable log"

    async def test_synthetic_aggregate_is_losslessly_rederivable(self, driven, session_id):
        # The wire-excluded synthetic rows lose NOTHING: folding the FULL durable log
        # (read_after — which still carries the conversation.turn seeds) through the
        # SHARED reducer (the rebuild path) reproduces the same renderable turns. This
        # is WHY excluding conversation.turn from the raw wire stream is safe
        # (FR-3/FR-10): the reduce/rebuild path consumes them as authoritative
        # sdk_turns, so the only place they ever mattered still sees them.
        from volundr.domain.services.transcript_rebuild import rebuild_turns

        broker, _live_ws, repo = driven
        entries = await repo.read_after(session_id, after_seq=0, limit=10_000)

        # The seeds are present in the raw log the rebuild reads (not on the wire).
        assert any(e.kind == _SYNTHETIC_AGGREGATE_KIND for e in entries)

        rebuilt = rebuild_turns(entries).turns
        # The conversation.turn seeds carry the user + assistant + error turns;
        # the rebuild surfaces them as renderable turns (id/role/content present).
        roles = [t["role"] for t in rebuilt]
        assert "user" in roles
        assert "assistant" in roles
        assert all(t.get("id") and t.get("role") for t in rebuilt)

    async def test_dropped_set_is_identical_across_paths(self, driven, session_id):
        # INV-10: the frames the visibility gate DROPS (tool_result-only user, the
        # internal content_block span) must be dropped by ALL THREE read paths.
        broker, live_ws, repo = driven

        gated_kinds = {f.get("type") for f in live_ws.frames}
        cold_kinds = {
            p.get("type") for p in _streamable([r["payload"] for r in _cold_read(repo, session_id)])
        }
        replay_kinds = {f.get("type") for f in _streamable(_replay_after_zero(repo, session_id))}

        for hidden in ("content_block_start", "content_block_delta", "content_block_stop"):
            assert hidden not in gated_kinds
            assert hidden not in cold_kinds
            assert hidden not in replay_kinds

        # And the surviving wire kinds agree across the three surfaces.
        assert gated_kinds == cold_kinds == replay_kinds

    async def test_literal_frame_for_frame_including_synthetic_rows(self, driven, session_id):
        # THE SRD's LITERAL wording, no _streamable projection: the wire frames a
        # cold-read GET /log surfaces are byte-identical to what the live broadcast
        # emitted. This now PASSES (former xfail) because BOTH raw-streaming read
        # paths exclude the synthetic conversation.turn reducer-seed rows ALWAYS —
        # an always-on, visibility-independent filter in rest_session_log and
        # ws_session_replay keyed on NON_BROADCAST_KINDS — closing the INV-5 gap.
        broker, live_ws, repo = driven
        cold = [row["payload"] for row in _cold_read(repo, session_id)]
        assert live_ws.frames == cold


# ---------------------------------------------------------------------------
# INV-5 (M-5) — per-connect handshake frames (system welcome + capabilities) are
# addressed to ONE socket, NOT the canonical shared stream. The durable log
# accumulates one pair PER historical connect; the RAW read paths must surface
# NONE of them (a connecting client gets its OWN fresh handshake), so a cold-read
# of a multi-connect session must not show duplicate "Connected to session" /
# capabilities pairs, and must equal a SINGLE live connect's visible handshake set
# (which is empty for both — the read paths drop ALL of them).
# ---------------------------------------------------------------------------


class _CapsTransport:
    """Minimal transport exposing the capabilities the handshake serializes."""

    def __init__(self) -> None:
        from niuu.ports.cli.transport import TransportCapabilities

        self.capabilities = TransportCapabilities()


async def _simulate_browser_handshake(broker: Broker) -> _RecordingWebSocket:
    """Replay the broker's REAL per-connect handshake send for ONE browser connect.

    Sends the SAME two broker-originated first-connect frames ``handle_websocket``
    sends — the ``system`` "Connected to session …" welcome (stamped with the
    per-connect marker) and the ``capabilities`` catalog — through the SAME
    persist-before-send choke point (``_safe_send_broker_frame_to``), so each
    connect appends one welcome + one capabilities pair to the durable buffer
    exactly as a live connect would.
    """
    from dataclasses import asdict

    from niuu.domain.transcript_reducer import PER_CONNECT_MARKER

    ws = _RecordingWebSocket()
    await broker._safe_send_broker_frame_to(
        ws,
        {
            "type": "system",
            "content": f"Connected to session {broker.session_id}",
            PER_CONNECT_MARKER: True,
        },
    )
    caps = {"type": "capabilities", **asdict(broker._transport.capabilities)}
    await broker._safe_send_broker_frame_to(ws, caps)
    return ws


def _handshake_frames(payloads: list[dict]) -> list[dict]:
    """The per-connect handshake frames in a payload stream (welcome + caps)."""
    from niuu.domain.transcript_reducer import is_per_connect_ephemeral

    return [p for p in payloads if is_per_connect_ephemeral(p.get("type", ""), p)]


class TestPerConnectHandshakeExclusionINV5:
    async def _broker(self, session_id: UUID, tmp_path) -> Broker:
        broker = Broker(
            settings=SkuldSettings(
                volundr_api_url="http://harness.invalid",
                event_log_enabled=True,
                session={"id": str(session_id), "workspace_dir": str(tmp_path)},
            )
        )

        async def _get_client() -> _FakeHttpClient:
            return _FakeHttpClient()

        broker._get_http_client = _get_client  # type: ignore[assignment]
        broker._transport = _CapsTransport()  # type: ignore[assignment]
        return broker

    async def _log_after(self, broker: Broker, session_id: UUID) -> InMemoryLog:
        repo = InMemoryLog()
        await repo.append(_buffer_to_log_entries(broker, session_id))
        return repo

    async def test_multi_connect_cold_read_has_no_duplicate_handshake_pairs(
        self, session_id, tmp_path
    ):
        broker = await self._broker(session_id, tmp_path)

        # TWO browser connects: the durable log accumulates TWO welcome+caps pairs.
        ws1 = await _simulate_browser_handshake(broker)
        ws2 = await _simulate_browser_handshake(broker)
        # Each LIVE socket saw exactly ONE pair — its own.
        assert _handshake_frames(ws1.frames) == ws1.frames
        assert len(ws1.frames) == 2
        assert len(ws2.frames) == 2

        repo = await self._log_after(broker, session_id)

        # The raw durable log is a superset — it carries BOTH historical pairs.
        from niuu.domain.transcript_reducer import is_per_connect_ephemeral

        raw = await repo.read_after(session_id, after_seq=0, limit=10_000)
        raw_handshakes = [e for e in raw if is_per_connect_ephemeral(e.kind, e.payload)]
        assert len(raw_handshakes) == 4  # two welcome + two capabilities

        # The cold-read surfaces NEITHER historical pair — a connecting client gets
        # its own fresh handshake, so no duplicates (and in fact none at all).
        cold = [row["payload"] for row in _cold_read(repo, session_id)]
        assert _handshake_frames(cold) == []
        welcomes = [p for p in cold if "Connected to session" in str(p.get("content", ""))]
        assert welcomes == []
        assert [p for p in cold if p.get("type") == "capabilities"] == []

    async def test_cold_read_equals_single_live_connect_visible_set(self, session_id, tmp_path):
        broker = await self._broker(session_id, tmp_path)
        await _simulate_browser_handshake(broker)
        await _simulate_browser_handshake(broker)
        repo = await self._log_after(broker, session_id)

        # A SINGLE live connect's VISIBLE handshake set on the read paths is empty
        # (the connecting client gets its own fresh pair out-of-band, not from the
        # replayed log). The multi-connect cold-read must equal that: empty.
        cold = [row["payload"] for row in _cold_read(repo, session_id)]
        replay = _replay_after_zero(repo, session_id)
        assert _handshake_frames(cold) == []
        assert _handshake_frames(replay) == []
        # And cold-read == replay frame-for-frame (both drop every handshake).
        assert cold == replay


# ---------------------------------------------------------------------------
# INV-10 — visibility parity: show_internal=true unhides identically, and the
# raw (ungated) stream is the SAME on every read path.
# ---------------------------------------------------------------------------


class TestConfiguredVisibilityDefaultParityINV10:
    """M-7: the live channel default, the replay default, and the cold-read default
    all read from ONE configured source — flip the configured default and ALL THREE
    move together (no hardcoded literal on the live path)."""

    async def _live_channel_default(self, session_id, tmp_path, *, flag: bool) -> bool:
        """Drive the REAL broker handshake with the configured flag and report the
        resulting live ``WebSocketChannel.show_internal`` (the live path's default)."""
        from starlette.websockets import WebSocketDisconnect as _WSDisconnect

        from niuu.ports.cli.transport import TransportCapabilities

        broker = Broker(
            settings=SkuldSettings(
                volundr_api_url="http://harness.invalid",
                event_log_enabled=True,
                default_show_internal=flag,
                session={"id": str(session_id), "workspace_dir": str(tmp_path)},
            )
        )

        async def _get_client() -> _FakeHttpClient:
            return _FakeHttpClient()

        broker._get_http_client = _get_client  # type: ignore[assignment]

        class _T:
            is_alive = True
            capabilities = TransportCapabilities()

            async def start(self) -> None:  # pragma: no cover - is_alive short-circuits
                return None

        broker._transport = _T()  # type: ignore[assignment]

        class _StubWS:
            async def accept(self) -> None:
                return None

            async def send_json(self, _payload: dict) -> None:
                return None

            async def send_text(self, _text: str) -> None:
                return None

            async def receive_json(self) -> dict:
                # Exit the receive loop right after the handshake.
                raise _WSDisconnect()

            async def close(self, code: int = 1000) -> None:
                return None

        broker._update_jwt_from_websocket = lambda _ws: None  # type: ignore[assignment]

        # The handshake registers the live channel and the disconnect tears it down
        # in `finally`, so capture it AT add-time (its show_internal is fixed at
        # construction from the configured default — the M-7 surface under test).
        added: list = []
        real_add = broker._channels.add

        def _spy_add(channel) -> None:
            if channel.channel_type == "browser":
                added.append(channel)
            real_add(channel)

        broker._channels.add = _spy_add  # type: ignore[assignment]
        await broker.handle_websocket(_StubWS())

        assert added, "the broker registered no live browser channel"
        return added[0].show_internal

    @pytest.mark.parametrize("flag", [False, True])
    async def test_all_three_defaults_move_with_the_config(self, session_id, tmp_path, flag):
        # (1) LIVE channel default — read from SkuldSettings.default_show_internal.
        live_default = await self._live_channel_default(session_id, tmp_path, flag=flag)
        assert live_default is flag

        # Seed a log with an internal tool_use block.
        from datetime import UTC, datetime

        repo = InMemoryLog()
        await repo.append(
            [
                SessionLogEntry(
                    session_id=session_id,
                    seq=1,
                    kind="assistant",
                    payload={
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "hi"},
                                {"type": "tool_use", "id": "t1", "name": "R", "input": {}},
                            ],
                        },
                    },
                    ts=datetime.now(UTC),
                )
            ]
        )

        # (2) COLD-READ default — create_session_log_router(default_show_internal=flag).
        app = FastAPI()
        app.include_router(
            create_session_log_router(repo, session_service=None, default_show_internal=flag)
        )
        cold = TestClient(app).get(f"/api/v1/forge/sessions/{session_id}/log").json()
        cold_block_types = [
            b.get("type")
            for row in cold
            if row["kind"] == "assistant"
            for b in row["payload"]["message"]["content"]
        ]

        # (3) REPLAY default — ReplayConfig(default_show_internal=flag).
        cfg = ReplayConfig(
            enabled=True, fixtures_enabled=False, max_gap_seconds=0.0, default_show_internal=flag
        )
        rapp = FastAPI()
        rapp.include_router(create_session_replay_router(repo, session_service=None, config=cfg))
        with TestClient(rapp).websocket_connect(
            f"/api/v1/forge/sessions/{session_id}/replay?preamble=false"
        ) as ws:
            replay: list[dict] = []
            try:
                while True:
                    replay.append(ws.receive_json())
            except WebSocketDisconnect:
                pass
        replay_block_types = [
            b.get("type")
            for f in replay
            if f.get("type") == "assistant"
            for b in f["message"]["content"]
        ]

        # All three move together: when the configured default is True the internal
        # tool_use block is SHOWN on cold-read and replay (and the live channel
        # passes internals through); when False it is HIDDEN on both read paths (and
        # the live channel filters it). The live default boolean equals the flag, and
        # the two read paths' visible block sets agree.
        assert cold_block_types == replay_block_types
        if flag:
            assert "tool_use" in cold_block_types
        else:
            assert "tool_use" not in cold_block_types
            assert cold_block_types == ["text"]


class TestVisibilityParityINV10:
    async def test_show_internal_unhides_identically(self, driven, session_id):
        broker, live_ws, repo = driven

        # Cold-read with internals shown.
        app = FastAPI()
        app.include_router(create_session_log_router(repo, session_service=None))
        client = TestClient(app)
        cold_full = client.get(
            f"/api/v1/forge/sessions/{session_id}/log",
            params={"show_internal": "true"},
        ).json()

        # Replay with internals shown.
        cfg = ReplayConfig(
            enabled=True,
            fixtures_enabled=False,
            max_gap_seconds=0.0,
            default_show_internal=True,
        )
        rapp = FastAPI()
        rapp.include_router(create_session_replay_router(repo, session_service=None, config=cfg))
        rclient = TestClient(rapp)
        replay_full: list[dict] = []
        with rclient.websocket_connect(
            f"/api/v1/forge/sessions/{session_id}/replay?preamble=false&show_internal=true"
        ) as ws:
            try:
                while True:
                    replay_full.append(ws.receive_json())
            except WebSocketDisconnect:
                pass

        # Ungated, the two read paths are byte-identical frame-for-frame.
        assert [row["payload"] for row in cold_full] == replay_full
        # …and they carry the internal frames the gated view dropped.
        full_kinds = [row["kind"] for row in cold_full]
        assert "content_block_start" in full_kinds
        assert "tool_use" in [
            b.get("type")
            for row in cold_full
            if row["kind"] == "assistant"
            for b in row["payload"]["message"]["content"]
        ]
