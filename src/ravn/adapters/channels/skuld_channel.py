"""SkuldChannel — WebSocket channel for browser delivery via the Skuld broker.

Connects to the Skuld broker WebSocket endpoint and forwards
:class:`~ravn.domain.events.RavnEvent` objects as NDJSON frames,
matching the existing Skuld event protocol.

The channel reconnects automatically on disconnect so ephemeral network
blips do not terminate an in-flight agent turn.

Protocol (NDJSON, one JSON object per line):

    {"type": "thought", "data": "...", "metadata": {}}
    {"type": "tool_start", "data": "BashTool", "metadata": {"input": {...}}}
    {"type": "tool_result", "data": "...", "metadata": {"tool_name": "BashTool"}}
    {"type": "response", "data": "...", "metadata": {}}
    {"type": "error", "data": "...", "metadata": {}}
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets
import websockets.exceptions
from websockets.protocol import State as WsState

from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.channel import ChannelPort

logger = logging.getLogger(__name__)

_DEFAULT_RECONNECT_DELAY_SECONDS = 2.0
_DEFAULT_MAX_RECONNECT_ATTEMPTS = 5


DirectedMessageHandler = Callable[[str], Awaitable[None]]


class SkuldChannel(ChannelPort):
    """Delivers Ravn events to the browser via the Skuld WebSocket broker.

    Args:
        broker_url:    WebSocket URL of the Skuld broker endpoint
                       (e.g. ``ws://localhost:9000/ws/ravn/{peer_id}``).
        session_id:    Agent session identifier forwarded in each event frame.
        peer_id:       Stable participant identifier for this Ravn daemon.
                       Included as ``source`` in each NDJSON frame so the
                       RoomBridge can route events to the correct participant.
        persona:       Display name for this Ravn in the room UI.  Included
                       in each frame for late-joining RoomBridge registration.
        reconnect_delay: Seconds to wait between reconnection attempts.
        max_reconnect_attempts: Maximum number of reconnection attempts before
                               giving up and buffering events locally.
    """

    def __init__(
        self,
        broker_url: str,
        session_id: str,
        *,
        peer_id: str | None = None,
        persona: str | None = None,
        display_name: str | None = None,
        subscribes_to: list[str] | None = None,
        emits: list[str] | None = None,
        tools: list[str] | None = None,
        reconnect_delay: float = _DEFAULT_RECONNECT_DELAY_SECONDS,
        max_reconnect_attempts: int = _DEFAULT_MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self._broker_url = broker_url
        self._session_id = session_id
        self._peer_id = peer_id
        self._persona = persona
        self._display_name = display_name
        self._subscribes_to = subscribes_to or []
        self._emits = emits or []
        self._tools = tools or []
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_attempts = max_reconnect_attempts
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connect_lock = asyncio.Lock()
        self._buffer: list[RavnEvent] = []
        self._on_directed_message: DirectedMessageHandler | None = None
        self._recv_task: asyncio.Task | None = None

    async def emit(self, event: RavnEvent) -> None:
        """Emit *event* to the Skuld broker as an NDJSON frame."""
        payload = self._serialise(event)
        try:
            await self._send(payload)
        except Exception as exc:
            logger.warning("SkuldChannel: emit failed, buffering event: %r", exc)
            self._buffer.append(event)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish the WebSocket connection to the Skuld broker.

        Safe to call multiple times — subsequent calls are no-ops when
        the connection is already open.
        """
        async with self._connect_lock:
            if self._ws is not None and not self._ws.state == WsState.CLOSED:
                return
            await self._do_connect()

    async def disconnect(self) -> None:
        """Close the WebSocket connection gracefully."""
        if self._recv_task is not None:
            self._recv_task.cancel()
            self._recv_task = None
        if self._ws is None:
            return
        try:
            await self._ws.close()
        except Exception:
            pass
        finally:
            self._ws = None

    async def flush_buffer(self) -> None:
        """Re-send any buffered events that could not be delivered earlier."""
        if not self._buffer:
            return
        pending = list(self._buffer)
        self._buffer.clear()
        for event in pending:
            payload = self._serialise(event)
            try:
                await self._send(payload)
            except Exception as exc:
                logger.warning("SkuldChannel: flush failed for event %r: %r", event.type, exc)
                self._buffer.append(event)

    def on_directed_message(self, handler: DirectedMessageHandler) -> None:
        """Register a callback for incoming directed messages from the browser."""
        self._on_directed_message = handler

    async def _recv_loop(self) -> None:
        """Background loop that reads incoming messages from the broker WebSocket.

        The broker sends ``{"type": "directed_message", "content": "..."}``
        when a user @-mentions this Ravn in the chat.  The content is forwarded
        to the registered handler (typically DriveLoop.enqueue).
        """
        while True:
            ws = self._ws
            if ws is None or ws.state == WsState.CLOSED:
                await asyncio.sleep(self._reconnect_delay)
                continue
            try:
                raw = await ws.recv()
                frame = json.loads(raw)
                if frame.get("type") == "directed_message" and self._on_directed_message:
                    content = frame.get("content", "")
                    if content:
                        await self._on_directed_message(content)
            except websockets.exceptions.ConnectionClosed:
                logger.info("SkuldChannel: recv loop — connection closed, waiting for reconnect.")
                await asyncio.sleep(self._reconnect_delay)
            except json.JSONDecodeError:
                logger.warning("SkuldChannel: recv loop — invalid JSON, skipping.")
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("SkuldChannel: recv loop — unexpected error.")
                await asyncio.sleep(self._reconnect_delay)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _do_connect(self) -> None:
        attempts = 0
        while attempts < self._max_reconnect_attempts:
            try:
                self._ws = await websockets.connect(self._broker_url)
                logger.info(
                    "SkuldChannel connected to %s (session=%s).",
                    self._broker_url,
                    self._session_id,
                )
                # Send registration frame with persona metadata
                reg_frame: dict = {
                    "type": "register",
                    "session_id": self._session_id,
                }
                if self._peer_id:
                    reg_frame["source"] = self._peer_id
                if self._persona:
                    reg_frame["persona"] = self._persona
                if self._display_name:
                    reg_frame["display_name"] = self._display_name
                if self._subscribes_to:
                    reg_frame["subscribes_to"] = self._subscribes_to
                if self._emits:
                    reg_frame["emits"] = self._emits
                if self._tools:
                    reg_frame["tools"] = self._tools
                await self._ws.send(json.dumps(reg_frame) + "\n")
                # Start receive loop for incoming directed messages
                if self._recv_task is None or self._recv_task.done():
                    self._recv_task = asyncio.create_task(self._recv_loop())
                return
            except Exception as exc:
                attempts += 1
                logger.warning(
                    "SkuldChannel: connection attempt %d/%d failed: %r",
                    attempts,
                    self._max_reconnect_attempts,
                    exc,
                )
                if attempts < self._max_reconnect_attempts:
                    await asyncio.sleep(self._reconnect_delay)

        logger.error(
            "SkuldChannel: gave up connecting to %s after %d attempts.",
            self._broker_url,
            self._max_reconnect_attempts,
        )

    async def _send(self, payload: str) -> None:
        """Send *payload* over the WebSocket, reconnecting if necessary."""
        if self._ws is None or self._ws.state == WsState.CLOSED:
            await self.connect()

        if self._ws is None:
            raise RuntimeError("SkuldChannel: no WebSocket connection available.")

        try:
            await self._ws.send(payload)
        except websockets.exceptions.ConnectionClosed:
            logger.info("SkuldChannel: connection closed — reconnecting.")
            self._ws = None
            await self.connect()
            if self._ws is not None:
                await self._ws.send(payload)

    def _serialise(self, event: RavnEvent) -> str:
        """Serialise *event* as an NDJSON line (no trailing newline from caller).

        Reconstructs the legacy ``data``/``metadata`` envelope from the
        unified ``payload`` dict so downstream Skuld consumers remain
        compatible.
        """
        payload = event.payload
        match event.type:
            case RavnEventType.THOUGHT:
                data = payload["text"]
                metadata = {"thinking": True} if payload.get("thinking") else {}
            case RavnEventType.RESPONSE:
                data = payload["text"]
                metadata = {}
            case RavnEventType.TOOL_START:
                data = payload["tool_name"]
                metadata = {"input": payload.get("input", {})}
                if "diff" in payload:
                    metadata["diff"] = payload["diff"]
            case RavnEventType.TOOL_RESULT:
                data = payload["result"]
                metadata = {
                    "tool_name": payload.get("tool_name", ""),
                    "is_error": payload.get("is_error", False),
                }
            case RavnEventType.ERROR:
                data = payload["message"]
                metadata = {}
            case RavnEventType.TASK_STARTED:
                data = payload.get("title", "")
                metadata = {
                    "task_id": payload.get("task_id", ""),
                    "title": payload.get("title", ""),
                }
            case RavnEventType.TASK_COMPLETE:
                data = payload
                metadata = {
                    "task_id": event.task_id or "",
                    "success": payload.get("success", False),
                }
            case RavnEventType.OUTCOME:
                # Mesh outcome event — RoomBridge translates to room_outcome
                data = payload
                metadata = {"event_type": payload.get("event_type", "")}
            case RavnEventType.HELP_NEEDED:
                # Help needed event — RoomBridge translates to room_notification
                data = payload
                metadata = {"urgency": event.urgency}
            case _:
                data = str(payload)
                metadata = {}

        frame: dict = {
            "session_id": self._session_id,
            "type": str(event.type),
            "data": data,
            "metadata": metadata,
        }
        # Include source/persona for RoomBridge participant identification.
        # Existing Skuld deployments that do not understand these fields will
        # ignore them (backward-compatible addition).
        if self._peer_id is not None:
            frame["source"] = self._peer_id
        if self._persona is not None:
            frame["persona"] = self._persona
        if event.root_correlation_id:
            frame["root_correlation_id"] = event.root_correlation_id
        return json.dumps(frame) + "\n"
