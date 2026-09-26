"""Forward an authorized Forge replay socket to its registered owner."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit

from fastapi import WebSocket, WebSocketDisconnect
from starlette.types import ASGIApp
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from niuu.adapters.inbound.remote_urls import build_remote_url
from niuu.adapters.outbound.guild_transport import (
    GuildTransportError,
    default_connect_timeout_ceiling_seconds,
    resolve_guild_ws_ssl,
)
from niuu.domain.models import RegisteredInstance

logger = logging.getLogger(__name__)

# Same maximum frame size used by the live-session proxy: histories can exceed
# the library's 1 MiB default. Replay frames remain bounded independently of pages.
REPLAY_MAX_FRAME_BYTES = 16 * 1024 * 1024


def _safe_log_value(value: str) -> str:
    """Keep request identifiers from forging additional log records."""
    return value.replace("\r", "").replace("\n", "")


async def forward_replay(
    websocket: WebSocket,
    instance: RegisteredInstance,
    session_id: str,
    *,
    headers: dict[str, str],
    embedded_app: ASGIApp | None,
) -> None:
    path = f"/api/v1/forge/sessions/{session_id}/replay"
    if str(instance.config.get("transport", "")).lower() == "embedded":
        if embedded_app is None:
            await websocket.close(code=1011)
            return
        scope = dict(websocket.scope)
        scope.update(path=path, raw_path=path.encode(), root_path="")
        # Preserve headers and query params. The backing replay adapter performs
        # its normal session authorization before accepting this same socket.
        await embedded_app(scope, websocket.receive, websocket.send)
        return

    try:
        http_url = build_remote_url(
            instance.base_url, "/api/v1/forge", f"/sessions/{session_id}/replay"
        )
        parsed = urlsplit(http_url)
        url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                parsed.path,
                str(websocket.query_params),
                "",
            )
        )
        ssl_context = await resolve_guild_ws_ssl(
            instance,
            dial_url=instance.base_url,
            timeout=default_connect_timeout_ceiling_seconds(),
        )
        connect_kwargs: dict[str, object] = {"ssl": ssl_context} if ssl_context is not None else {}
        async with connect(
            url, additional_headers=headers, max_size=REPLAY_MAX_FRAME_BYTES, **connect_kwargs
        ) as remote:
            await websocket.accept()
            await _relay(websocket, remote)
    except InvalidStatus:
        await websocket.close(code=1008)
    except (ConnectionClosed, WebSocketDisconnect):
        return
    except GuildTransportError as exc:
        logger.warning(
            "Guild transport refused replay bridging to instance %s (session %s): %s",
            _safe_log_value(instance.id),
            _safe_log_value(session_id),
            _safe_log_value(str(exc)),
        )
        await websocket.close(code=1011)
    except (OSError, ValueError):
        await websocket.close(code=1011)


async def _relay(websocket: WebSocket, remote) -> None:
    async def upstream():
        try:
            while True:
                await remote.send(await websocket.receive_text())
        except WebSocketDisconnect:
            return

    async def downstream():
        async for frame in remote:
            if isinstance(frame, bytes):
                await websocket.send_bytes(frame)
            else:
                await websocket.send_text(frame)

    tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=1000)
