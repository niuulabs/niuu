"""Replay-as-live WebSocket adapter.

Re-emits recorded ``session_event_log`` frames (from the DB by session id, or
from a checked-in fixture for offline/CI) over a WebSocket, paced by the
recorded ``ts`` deltas, speaking the exact frame protocol the live-session
client (``@lexi/forge`` ``SessionSocket``, ``?qa=stream``) expects — so the
client renders the replay as if it were a live session.

Two routes (both under ``prefix``):

* ``WS  {prefix}/sessions/{session_id}/replay``  — DB-backed, auth-gated.
* ``WS  {prefix}/replay/fixtures/{name}``         — fixture-backed, gated by a
  separate config flag (serves synthetic data unauthenticated; off in prod).

Replay is read-only: ``send_message``/``steer`` capabilities are advertised
``False`` and inbound control frames other than ``set_internal_visibility`` are
accepted and ignored (never proxied to skuld).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from niuu.domain.transcript_reducer import NON_BROADCAST_KINDS
from niuu.ports.cli.transport import TransportCapabilities
from skuld.channels import filter_internal_blocks
from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository
from volundr.domain.services.session import SessionAccessDeniedError, SessionService
from volundr.domain.services.transcript_rebuild import rebuild_turns
from volundr.replay.fixtures import resolve_fixture
from volundr.replay.pacing import PacingConfig, drive_replay, encode_frame
from volundr.replay.source import (
    FixtureReplaySource,
    RepositoryReplaySource,
    load_fixture_entries,
)

logger = logging.getLogger(__name__)


async def _collect_prefix(src, *, upto_seq: int) -> list[SessionLogEntry]:
    """Collect the recorded frames with ``0 < seq <= upto_seq`` from a source.

    Used to reconstruct the conversation that happened BEFORE a mid-cursor
    attach (``after>0``). Reads from ``after_seq=0`` and stops as soon as the
    stream passes the cursor (sources yield in ascending ``seq``).
    """
    prefix: list[SessionLogEntry] = []
    async for entry in src.entries(after_seq=0):
        if entry.seq > upto_seq:
            break
        prefix.append(entry)
    return prefix


def _mid_cursor_history_frame(prefix: list[SessionLogEntry]) -> dict:
    """Build the ``conversation_history`` frame for a mid-cursor attach.

    Reuses the SHARED reducer via :func:`rebuild_turns` (which folds the prefix
    through ``niuu.domain.transcript_reducer.reduce_frames`` — the one fold the
    live broker and the crash-rebuild also use), so the reconstructed turns are
    identical to what a live reconnect or a full ``after=0`` replay would yield.
    The frame shape matches the live reconnect frame exactly:
    ``{"type": "conversation_history", "turns": [...]}`` (a SYNTHETIC frame, not a
    raw log envelope — documented as the one shape that differs from the verbatim
    seq/kind/role/request_id/payload/ts frames the tail streams).
    """
    rebuilt = rebuild_turns(prefix)
    return {"type": "conversation_history", "turns": rebuilt.turns}


class _VisibilityGate:
    """Per-connection tool-visibility filter, reusing skuld's exact predicate.

    Mirrors ``WebSocketChannel``: tracks the open streaming block type so a
    ``content_block_delta``/``content_block_stop`` belonging to an internal
    block is dropped, and resets that tracking when visibility is turned on
    (matching ``set_show_internal``).

    By design this reuses the SHARED ``filter_internal_blocks`` predicate and
    keeps only the tiny per-connection state (``show`` + ``_open_block_type``),
    rather than depending on the broker's ``WebSocketChannel`` (which is bound to
    live broadcast/room machinery the replay path has no use for). The filtering
    LOGIC lives in one place; only this small state wrapper is local.
    """

    def __init__(self, show: bool) -> None:
        self.show = show
        self._open_block_type: str | None = None

    def set_show(self, visible: bool) -> None:
        self.show = visible
        if visible:
            self._open_block_type = None

    def gate(self, payload: dict) -> dict | None:
        """Return the (possibly stripped) payload to send, or ``None`` to drop."""
        if self.show:
            return payload
        filtered, self._open_block_type = filter_internal_blocks(
            payload, open_block_type=self._open_block_type
        )
        return filtered


async def _authorize_replay(
    session_service: SessionService | None,
    websocket: WebSocket,
    session_id: UUID,
) -> bool:
    """Authorize a DB-replay connection; close 1008 on denial. Return True to proceed.

    Mirrors the REST ``GET .../log`` access contract
    (``rest_session_log._check_access``) with early returns:

    * auth unconfigured (``session_service is None``) -> open;
    * unauthenticated principal (``extract_principal`` raises ``HTTPException``)
      or forbidden principal (``_check_access`` raises ``SessionAccessDeniedError``)
      -> close 1008;
    * session row absent -> open. PARITY with REST /log: a deleted session whose
      durable log survives (no FK) has no per-session ACL to check. If that
      deleted-session exposure is ever tightened, change it HERE and in
      ``rest_session_log._check_access`` together.
    """
    if session_service is None:
        return True

    from fastapi import HTTPException

    from volundr.adapters.inbound.auth import extract_principal

    try:
        principal = await extract_principal(websocket)
    except HTTPException:
        await websocket.close(code=1008)
        return False

    session = await session_service.get_session(session_id)
    if session is None:
        return True

    try:
        await session_service._check_access(session, principal, "read")
    except SessionAccessDeniedError:
        await websocket.close(code=1008)
        return False
    return True


def create_session_replay_router(
    log_repository: SessionEventLogRepository,
    session_service: SessionService | None = None,
    *,
    prefix: str = "/api/v1/forge",
    config,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> APIRouter:
    """Create the FastAPI router exposing the replay-as-live WebSocket routes.

    ``config`` is a ``volundr.config.ReplayConfig`` (passed in to avoid a
    config <-> replay import cycle). ``sleep`` is the pacing clock, injectable so
    tests can pace deterministically without real wall-clock waits.
    """
    # The fixture route silently closes 1008 when its corpus dir is missing,
    # which is baffling to debug — warn loudly at wire-up time instead.
    if config.fixtures_enabled:
        fixtures_dir = config.fixtures_dir_path()
        if not fixtures_dir.is_dir():
            logger.warning(
                "replay: fixtures_enabled but fixtures dir %s does not exist — "
                "every /replay/fixtures request will close 1008",
                fixtures_dir,
            )

    router = APIRouter(prefix=prefix)

    @router.websocket("/sessions/{session_id}/replay")
    async def replay_db(
        websocket: WebSocket,
        session_id: UUID,
        speed: float = Query(default=config.default_speed, gt=0),
        max_gap: float = Query(default=config.max_gap_seconds, ge=0),
        show_internal: bool = Query(default=config.default_show_internal),
        after: int = Query(default=0, ge=0),
        preamble: bool = Query(default=True),
    ) -> None:
        # Auth runs BEFORE accept() (see _authorize_replay). Denial closes the
        # socket with policy-violation 1008 rather than dropping the handshake.
        if not await _authorize_replay(session_service, websocket, session_id):
            return

        src = RepositoryReplaySource(log_repository, session_id, page=config.page_size)
        await _run(
            websocket,
            src,
            str(session_id),
            PacingConfig(speed=speed, max_gap_seconds=max_gap),
            after=after,
            show_internal=show_internal,
            preamble=preamble,
            sleep=sleep,
        )

    @router.websocket("/replay/fixtures/{name}")
    async def replay_fixture(
        websocket: WebSocket,
        name: str,
        speed: float = Query(default=config.default_speed, gt=0),
        max_gap: float = Query(default=config.max_gap_seconds, ge=0),
        show_internal: bool = Query(default=config.default_show_internal),
        after: int = Query(default=0, ge=0),
        preamble: bool = Query(default=True),
    ) -> None:
        if not config.fixtures_enabled:
            await websocket.close(code=1008)
            return
        sid = uuid5(NAMESPACE_URL, f"replay-fixture/{name}")
        try:
            path = resolve_fixture(name, config.fixtures_dir_path())
            entries = load_fixture_entries(path, session_id=sid)
        except (ValueError, FileNotFoundError, KeyError):
            await websocket.close(code=1008)
            return
        src = FixtureReplaySource(entries)
        await _run(
            websocket,
            src,
            str(sid),
            PacingConfig(speed=speed, max_gap_seconds=max_gap),
            after=after,
            show_internal=show_internal,
            preamble=preamble,
            sleep=sleep,
        )

    return router


async def _run(
    ws: WebSocket,
    src,
    session_id_str: str,
    cfg: PacingConfig,
    *,
    after: int,
    show_internal: bool,
    preamble: bool,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Accept the socket, send the preamble, then pace-emit the recorded frames.

    The driver and a receiver run CONCURRENTLY; whichever finishes first cancels
    the other (``asyncio.wait`` + ``FIRST_COMPLETED``). So a client disconnect —
    observed by the receiver as a ``WebSocketDisconnect`` — cancels the driver
    PROMPTLY even if it is parked in a long pacing sleep, rather than waiting for
    the next ``send`` to fail. The receiver honors ``set_internal_visibility``
    toggles; all other inbound frames are ignored (read-only replay). ``finally``
    cancels any straggler and closes.
    """
    await ws.accept()
    gate = _VisibilityGate(show_internal)
    receiver: asyncio.Task | None = None
    driver: asyncio.Task | None = None
    try:
        if preamble:
            await ws.send_text(
                json.dumps({"type": "system", "content": f"Replaying session {session_id_str}"})
            )
            caps = {
                "type": "capabilities",
                **asdict(
                    TransportCapabilities(
                        send_message=False,
                        steer=False,
                        # Read-only replay cannot run slash commands or skills,
                        # and the live ``available_commands`` catalog is not
                        # persisted to the durable log — so advertise these as
                        # False rather than rendering a dead /-menu on the client.
                        slash_commands=False,
                        skills=False,
                    )
                ),
            }
            await ws.send_text(json.dumps(caps))
            # after>0 mid-session attach (SRD FR-6 / INV-6): FIRST reconstruct the
            # conversation that already happened (turns folded from the durable
            # log 1..after via the SHARED reducer) and emit it as a single
            # conversation_history frame — the SAME shape the live broker sends on
            # reconnect — so the client loads full state, THEN streams the tail
            # (seq>after) below. reconstructed history ∪ tail == replay from
            # after=0. For after==0 we send NO conversation_history (the streamed
            # frames ARE the full history).
            if after > 0:
                prefix = await _collect_prefix(src, upto_seq=after)
                if prefix:
                    await ws.send_text(json.dumps(_mid_cursor_history_frame(prefix)))

        async def _emit(entry) -> bool:
            # Synthetic reducer-seed rows (e.g. conversation.turn) are NEVER on the
            # wire — the live broadcast never emits them. Drop them from the RAW
            # streamed TAIL ALWAYS (independent of show_internal) so literal
            # frame-for-frame live==replay==cold equality holds (SRD INV-5). The
            # mid-cursor conversation_history (built above via the SHARED reducer
            # over the full prefix) STILL consumes them as authoritative seeds.
            if entry.kind in NON_BROADCAST_KINDS:
                return False
            payload = gate.gate(entry.payload)
            if payload is None:
                return False
            await ws.send_text(encode_frame(payload))
            return True

        async def _recv() -> None:
            try:
                while True:
                    msg = await ws.receive_json()
                    if isinstance(msg, dict) and msg.get("type") == "set_internal_visibility":
                        # Strict: only a JSON boolean `true` shows; default true when
                        # the key is absent. `is True` avoids the string "false"
                        # (truthy) accidentally unhiding internal frames.
                        gate.set_show(msg.get("visible", True) is True)
                    # Other control frames (steer, slash_command, ...) are
                    # accepted and ignored — replay is read-only.
            except (WebSocketDisconnect, RuntimeError, ValueError):
                return

        receiver = asyncio.create_task(_recv())
        driver = asyncio.create_task(
            drive_replay(src.entries(after_seq=after), cfg=cfg, emit=_emit, sleep=sleep)
        )
        # Whichever finishes first wins: stream exhausted (driver) OR client gone
        # (receiver returns on WebSocketDisconnect). Cancel the loser so a
        # disconnect mid-sleep tears the driver down at once.
        done, pending = await asyncio.wait({driver, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if driver in done:
            driver.result()  # re-raise a driver error (e.g. WebSocketDisconnect)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        for task in (receiver, driver):
            if task is not None and not task.done():
                task.cancel()
        try:
            await ws.close()
        except RuntimeError:
            pass
