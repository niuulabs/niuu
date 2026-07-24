"""SkuldChannel — WebSocket channel for browser delivery via the Skuld broker.

Connects to the Skuld broker WebSocket endpoint and projects
:class:`~ravn.domain.events.RavnEvent` objects into transport-neutral
collaboration frames before delivery.

The channel reconnects automatically on disconnect so ephemeral network
blips do not terminate an in-flight agent turn.

Protocol (NDJSON, one JSON object per line):

    {"type": "collaboration.events", "events": [{"kind": "activity", ...}]}
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import websockets
import websockets.exceptions
from websockets.protocol import State as WsState

from niuu.collaboration.room import matches_subscription
from niuu.observability import get_observability
from ravn.adapters.collaboration import project_ravn_event
from ravn.domain.events import RavnEvent
from ravn.ports.channel import ChannelPort

logger = logging.getLogger(__name__)

_DEFAULT_RECONNECT_DELAY_SECONDS = 2.0
_DEFAULT_MAX_RECONNECT_ATTEMPTS = 5


DirectedMessageHandler = Callable[[str, dict[str, Any] | None], Awaitable[None]]
AuthTokenProvider = Callable[[], Awaitable[str]]


def _render_collaboration_observation(frame: dict[str, Any], event_type: str) -> str:
    """Render neutral evidence without prescribing how Ravn should react."""
    observation = {
        "event_type": event_type,
        "participant_id": frame.get("participantId") or "",
        "fields": frame.get("fields") or {},
        "summary": frame.get("summary") or "",
        "verdict": frame.get("verdict") or "",
    }
    return "Collaboration outcome observed:\n" + json.dumps(
        observation, sort_keys=True, default=str
    )


class SkuldChannel(ChannelPort):
    """Delivers Ravn events to the browser via the Skuld WebSocket broker.

    Args:
        broker_url:    WebSocket URL of the Skuld broker endpoint
                       (e.g. ``ws://localhost:9000/ws/ravn/{peer_id}``).
        session_id:    Agent session identifier forwarded in each event frame.
        peer_id:       Stable participant identifier for this Ravn daemon.
                       Included as ``source`` in each NDJSON frame so the
                       the collaboration adapter can route events correctly.
        persona:       Display name for this Ravn in the room UI.  Included
                       in each frame for late participant registration.
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
        auth_token_provider: AuthTokenProvider | None = None,
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
        self._auth_token_provider = auth_token_provider
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connect_lock = asyncio.Lock()
        self._buffer: list[RavnEvent] = []
        self._on_directed_message: DirectedMessageHandler | None = None
        self._recv_task: asyncio.Task | None = None
        self._connect_gave_up = False

    async def emit(self, event: RavnEvent) -> None:
        """Emit *event* to the Skuld broker as an NDJSON frame."""
        telemetry = get_observability()
        with telemetry.span(
            "ravn.port.skuld.emit",
            attributes={
                "ravn.event.type": str(event.type),
                "ravn.peer.id": self._peer_id or "",
                "ravn.session.id": event.session_id or self._session_id,
            },
            carrier=event.trace_context,
        ):
            payload = self._serialise(event, trace_context=telemetry.inject())
            try:
                await self._send(payload)
                telemetry.count(
                    "ravn.skuld.events",
                    attributes={"event_type": str(event.type), "outcome": "sent"},
                )
            except Exception as exc:
                logger.warning("SkuldChannel: emit failed, buffering event: %r", exc)
                self._buffer.append(event)
                telemetry.count(
                    "ravn.skuld.events",
                    attributes={"event_type": str(event.type), "outcome": "buffered"},
                )

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """True when the WebSocket connection is currently open."""
        return self._ws is not None and self._ws.state != WsState.CLOSED

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
        with suppress(Exception):
            await self._ws.close()
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
                # Receive-only channels (a resident's session_join memberships)
                # never call _send, so without an active re-dial here a dropped
                # connection would silently stop delivering directed messages
                # forever. Re-establish it (connect re-sends the register frame).
                await asyncio.sleep(self._reconnect_delay)
                with suppress(Exception):
                    await self.connect()
                if self._connect_gave_up:
                    return
                continue
            try:
                raw = await ws.recv()
                frame = json.loads(raw)
                if frame.get("type") == "directed_message" and self._on_directed_message:
                    content = frame.get("content", "")
                    metadata = frame.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = None
                    if content:
                        carrier = (
                            metadata.get("trace_context", {}) if isinstance(metadata, dict) else {}
                        )
                        if not isinstance(carrier, dict):
                            carrier = {}
                        with get_observability().span(
                            "ravn.port.skuld.directed_message.receive",
                            attributes={"ravn.peer.id": self._peer_id or ""},
                            carrier=carrier,
                        ):
                            await self._on_directed_message(content, metadata)
                elif frame.get("type") == "collaboration.outcome" and self._on_directed_message:
                    event_type = str(frame.get("eventType") or "").strip()
                    if event_type and matches_subscription(event_type, self._subscribes_to):
                        metadata = {
                            "room_outcome": True,
                            "event_type": event_type,
                            "participant_id": frame.get("participantId") or "",
                            "fields": frame.get("fields") or {},
                            "summary": frame.get("summary") or "",
                            "verdict": frame.get("verdict") or "",
                        }
                        await self._on_directed_message(
                            _render_collaboration_observation(frame, event_type),
                            metadata,
                        )
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
        self._connect_gave_up = False
        while attempts < self._max_reconnect_attempts:
            try:
                connect_kwargs: dict = {}
                if self._auth_token_provider is not None:
                    # Cross-session joins hit the broker's ownership check —
                    # present the platform identity token on the handshake.
                    token = await self._auth_token_provider()
                    if token:
                        connect_kwargs["additional_headers"] = {"Authorization": f"Bearer {token}"}
                self._ws = await websockets.connect(self._broker_url, **connect_kwargs)
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
        self._connect_gave_up = True

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

    def _serialise(
        self,
        event: RavnEvent,
        *,
        trace_context: dict[str, str] | None = None,
    ) -> str:
        """Serialise *event* as an NDJSON line (no trailing newline from caller).

        Ravn owns interpretation of its events. Skuld receives only the
        resulting transport-neutral collaboration event kinds.
        """
        events = project_ravn_event(event, persona=self._persona or "")
        propagated_context = trace_context or event.trace_context
        if propagated_context:
            for projected in events:
                projected["traceContext"] = dict(propagated_context)
        frame: dict = {
            "session_id": self._session_id,
            "type": "collaboration.events",
            "events": events,
        }
        # Include source/persona for collaboration participant identification.
        # Existing Skuld deployments that do not understand these fields will
        # ignore them (backward-compatible addition).
        if self._peer_id is not None:
            frame["source"] = self._peer_id
        if self._persona is not None:
            frame["persona"] = self._persona
        if event.root_correlation_id:
            frame["root_correlation_id"] = event.root_correlation_id
        if propagated_context:
            frame["trace_context"] = dict(propagated_context)
        return json.dumps(frame) + "\n"
