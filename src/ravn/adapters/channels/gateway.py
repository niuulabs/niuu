"""Gateway orchestrator — manages Telegram and HTTP channels for Ravn.

Each channel+user combination gets its own isolated :class:`RavnAgent` session.
The gateway runs as asyncio tasks alongside the main agent loop — no separate
process, no broker, no open inbound ports required.

Usage::

    gateway = RavnGateway(config, agent_factory)
    response = await gateway.handle_message("telegram:123456", "hello")

    # or for SSE streaming:
    async for event in gateway.handle_message_stream("http:default", "hello"):
        ...
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ravn.config import GatewayConfig
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.profile import RavnProfile
from ravn.ports.channel import ChannelPort
from ravn.ports.executor import ExecutionAgentPort

logger = logging.getLogger(__name__)

# Factory that creates a new execution agent bound to the supplied channel.
AgentFactory = Callable[[ChannelPort], ExecutionAgentPort]

# Optional broadcast callback invoked on every emitted event.
BroadcastCallback = Callable[[RavnEvent], Awaitable[None]]


class GatewayChannel(ChannelPort):
    """Buffers :class:`RavnEvent` objects in an asyncio queue for gateway consumption.

    Each :class:`GatewaySession` owns one channel instance.  When ``run_turn``
    emits events they land in the queue; the gateway then drains them to
    deliver to Telegram / HTTP.

    An optional *broadcast_cb* is called for every event so the HTTP
    ``GET /events`` broadcast stream can distribute events to all subscribers.
    """

    def __init__(
        self,
        *,
        broadcast_cb: BroadcastCallback | None = None,
    ) -> None:
        self._queue: asyncio.Queue[RavnEvent | None] = asyncio.Queue()
        self._broadcast_cb = broadcast_cb

    async def emit(self, event: RavnEvent) -> None:
        await self._queue.put(event)
        if self._broadcast_cb is not None:
            await self._broadcast_cb(event)

    async def signal_done(self) -> None:
        """Push a sentinel ``None`` to unblock any pending :meth:`stream` call."""
        await self._queue.put(None)

    async def collect_response(self) -> str:
        """Drain the queue and return the final response text.

        Reads events until a :attr:`~RavnEventType.RESPONSE` or
        :attr:`~RavnEventType.ERROR` is received (or a sentinel ``None``).
        Returns the response text, or ``"[error] …"`` on error.
        """
        while True:
            event = await self._queue.get()
            if event is None:
                return ""
            if event.type == RavnEventType.RESPONSE:
                return event.payload["text"]
            if event.type == RavnEventType.ERROR:
                return f"[error] {event.payload['message']}"

    def drain(self) -> None:
        """Discard any leftover items (e.g. sentinel) from the queue.

        Call between turns to prevent stale sentinels from causing the
        next :meth:`stream` call to return immediately.
        """
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def stream(self) -> AsyncIterator[RavnEvent]:
        """Yield events until :attr:`~RavnEventType.RESPONSE`, ERROR, or sentinel.

        Used by the HTTP SSE endpoint to stream events in real time.
        """
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
            if event.type in (RavnEventType.RESPONSE, RavnEventType.ERROR):
                return


@dataclass
class GatewaySession:
    """A single isolated session bound to one user+channel pair."""

    agent: ExecutionAgentPort
    channel: GatewayChannel
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RavnGateway:
    """Orchestrates per-session :class:`RavnAgent` instances across gateway channels.

    Sessions are identified by a string key such as ``telegram:123456`` or
    ``http:192.168.1.1``.  Each session gets its own :class:`RavnAgent` and
    :class:`GatewayChannel` so conversation history and state are fully isolated.

    All events emitted by any session's channel are also broadcast to
    subscribers registered via :meth:`subscribe`.
    """

    def __init__(
        self,
        config: GatewayConfig,
        agent_factory: AgentFactory,
        *,
        profile: RavnProfile | None = None,
        interaction_tracker: Any | None = None,
    ) -> None:
        self._config = config
        self._agent_factory = agent_factory
        self._profile = profile
        self._interaction_tracker = interaction_tracker
        self._sessions: dict[str, GatewaySession] = {}
        self._broadcast_queues: list[asyncio.Queue[RavnEvent | None]] = []

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def session_ids(self) -> list[str]:
        """Return all active session IDs."""
        return list(self._sessions.keys())

    def get_status(self) -> dict:
        """Return a status dict for the /status endpoint.

        Includes profile identity fields when a :class:`~ravn.domain.profile.RavnProfile`
        was supplied at construction time.
        """
        ids = self.session_ids()
        status: dict = {"session_count": len(ids), "active_sessions": ids}
        if self._profile is not None:
            status["profile"] = self._profile.to_dict()
        return status

    def get_or_create_session(self, session_id: str) -> GatewaySession:
        """Return an existing session or create a fresh one."""
        if session_id in self._sessions:
            return self._sessions[session_id]
        channel = GatewayChannel(broadcast_cb=self._broadcast)
        agent = self._agent_factory(channel)
        session = GatewaySession(agent=agent, channel=channel)
        self._sessions[session_id] = session
        logger.debug("Created gateway session %r.", session_id)
        return session

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def handle_message(self, session_id: str, text: str) -> str:
        """Process *text* and return the agent's complete response.

        Serialises concurrent calls to the same session via a per-session lock.
        Suitable for Telegram where we wait for the full response before sending.
        """
        if self._interaction_tracker is not None:
            self._interaction_tracker.touch()
        session = self.get_or_create_session(session_id)
        async with session.lock:
            await session.agent.run_turn(text)
            return await session.channel.collect_response()

    async def handle_message_stream(self, session_id: str, text: str) -> AsyncIterator[RavnEvent]:
        """Run *text* against *session_id* and yield events as they arrive.

        The agent turn runs concurrently with the event drain so callers
        receive events in real time.  Suitable for HTTP SSE.
        """
        if self._interaction_tracker is not None:
            self._interaction_tracker.touch()
        session = self.get_or_create_session(session_id)
        async with session.lock:
            run_task: asyncio.Task[object] = asyncio.create_task(
                self._run_and_signal(session, text)
            )
            try:
                async for event in session.channel.stream():
                    yield event
            finally:
                await run_task
                session.channel.drain()

    async def _run_and_signal(self, session: GatewaySession, text: str) -> None:
        """Run agent turn then push a sentinel so :meth:`stream` unblocks."""
        try:
            await session.agent.run_turn(text)
        finally:
            await session.channel.signal_done()

    # ------------------------------------------------------------------
    # Broadcast (GET /events)
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[RavnEvent | None]:
        """Register a new broadcast subscriber queue and return it."""
        q: asyncio.Queue[RavnEvent | None] = asyncio.Queue()
        self._broadcast_queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[RavnEvent | None]) -> None:
        """Remove *q* from the broadcast subscriber list."""
        try:
            self._broadcast_queues.remove(q)
        except ValueError:
            pass

    async def _broadcast(self, event: RavnEvent) -> None:
        """Fan out *event* to all registered broadcast subscribers."""
        for q in self._broadcast_queues:
            await q.put(event)
