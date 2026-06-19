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

from niuu.ports.cli.transport import TransportCapabilities
from skuld.channels import filter_internal_blocks
from volundr.domain.ports import SessionEventLogRepository
from volundr.domain.services.session import SessionAccessDeniedError, SessionService
from volundr.replay.fixtures import resolve_fixture
from volundr.replay.pacing import PacingConfig, drive_replay, encode_frame
from volundr.replay.source import (
    FixtureReplaySource,
    RepositoryReplaySource,
    load_fixture_entries,
)

logger = logging.getLogger(__name__)


class _VisibilityGate:
    """Per-connection tool-visibility filter, reusing skuld's exact predicate.

    Mirrors ``WebSocketChannel``: tracks the open streaming block type so a
    ``content_block_delta``/``content_block_stop`` belonging to an internal
    block is dropped, and resets that tracking when visibility is turned on
    (matching ``set_show_internal``).
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
        # Auth runs BEFORE accept(). extract_principal only reads .app.state /
        # .headers / .query_params, all present on a WebSocket connection. We
        # cannot raise HTTPException after the WS scope, so both an
        # unauthenticated principal (extract_principal -> HTTPException) and a
        # denied principal (_check_access -> SessionAccessDeniedError) close with
        # policy-violation 1008 instead of dropping the handshake.
        if session_service is not None:
            from fastapi import HTTPException

            from volundr.adapters.inbound.auth import extract_principal

            try:
                principal = await extract_principal(websocket)
            except HTTPException:
                await websocket.close(code=1008)
                return
            session = await session_service.get_session(session_id)
            if session is not None:
                try:
                    await session_service._check_access(session, principal, "read")
                except SessionAccessDeniedError:
                    await websocket.close(code=1008)
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
            # after>0 mid-session attach: conversation_history reconstruction is
            # deferred — v1 replays the post-cursor tail only (same contract as
            # the REST GET .../log?after= cold read). For after==0 we send NO
            # conversation_history (the streamed frames are the full history).

        async def _emit(entry) -> bool:
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
                        gate.set_show(bool(msg.get("visible", True)))
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
