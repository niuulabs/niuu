"""SessionJoinManager — dynamic membership in other sessions' Skuld rooms.

A resident ravn lives in its own room, but the work it launches (research
campaigns, spec stacks, plan runs) executes in TEMPORARY Volundr sessions,
each with its own Skuld room. This manager lets the resident walk into
those rooms as a real participant — observe, answer ``help_needed``
questions directed at it, post updates — and walk out when the work ends.

The daemon's primary :class:`SkuldChannel` (its own room) stays untouched;
joins open *additional* channels at runtime. Frames directed at the
resident inside a joined room are tagged with their source session and
handed to the registered observer — they must never be confused with the
operator talking in the resident's own room.

Ownership: the broker enforces session ownership on ``/ws/ravn`` (see
skuld.config.WsAuthConfig), so joins present the resident's platform
identity token. A resident can only join sessions its owner owns.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from ravn.adapters.channels.skuld_channel import AuthTokenProvider, SkuldChannel
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.channel import ChannelPort

logger = logging.getLogger(__name__)

#: async (source_session_id, content, metadata) -> None
JoinObserver = Callable[[str, str, dict[str, Any] | None], Awaitable[None]]

_CHAT_ENDPOINT_SUFFIX = "/session"
_RAVN_WS_SUFFIX = "/ws/ravn"
_SESSION_RUNNING_STATUS = "running"
_SESSION_TERMINAL_STATUSES = {"failed", "stopped", "stopping", "archived"}


def derive_ravn_ws_url(chat_endpoint: str, peer_id: str) -> str:
    """Derive the ravn participant WebSocket URL from a session chat endpoint.

    Session chat endpoints end in ``/session`` (the browser endpoint); the
    ravn participant endpoint is the sibling ``/ws/ravn/{peer_id}`` path on
    the same broker.
    """
    endpoint = (chat_endpoint or "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("chat_endpoint is required to join a session")
    if not peer_id.strip():
        raise ValueError("peer_id is required to join a session")
    if endpoint.startswith("http://"):
        endpoint = "ws://" + endpoint.removeprefix("http://")
    elif endpoint.startswith("https://"):
        endpoint = "wss://" + endpoint.removeprefix("https://")
    if endpoint.endswith(_CHAT_ENDPOINT_SUFFIX):
        endpoint = endpoint.removesuffix(_CHAT_ENDPOINT_SUFFIX)
    return f"{endpoint}{_RAVN_WS_SUFFIX}/{peer_id}"


@dataclass
class JoinedSession:
    """One live membership in another session's room."""

    session_id: str
    ws_url: str
    channel: SkuldChannel
    joined_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def describe(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "ws_url": self.ws_url,
            "connected": self.channel.connected,
            "joined_at": self.joined_at.isoformat(),
        }


class SessionJoinManager:
    """Opens and closes secondary SkuldChannels into other sessions' rooms."""

    def __init__(
        self,
        *,
        peer_id: str,
        persona: str,
        display_name: str = "",
        subscribes_to: list[str] | None = None,
        auth_token_provider: AuthTokenProvider | None = None,
        reconnect_delay: float = 2.0,
        max_reconnect_attempts: int = 5,
        platform_base_url: str = "",
        session_ready_timeout: float = 600.0,
    ) -> None:
        self._peer_id = peer_id
        self._persona = persona
        self._display_name = display_name or persona
        self._subscribes_to = list(subscribes_to or [])
        self._auth_token_provider = auth_token_provider
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_attempts = max_reconnect_attempts
        self._platform_base_url = platform_base_url.strip().rstrip("/")
        self._session_ready_timeout = session_ready_timeout
        self._joined: dict[str, JoinedSession] = {}
        self._observer: JoinObserver | None = None
        self._status_channel: ChannelPort | None = None

    def set_observer(self, observer: JoinObserver) -> None:
        """Register the handler for frames directed at us in joined rooms."""
        self._observer = observer

    def set_status_channel(self, channel: ChannelPort | None) -> None:
        """Register the resident's own room channel for join status updates."""
        self._status_channel = channel

    def set_subscribes_to(self, event_types: list[str]) -> None:
        """Set event subscriptions used when joining workflow rooms."""
        self._subscribes_to = list(event_types or [])
        for entry in self._joined.values():
            entry.channel._subscribes_to = list(self._subscribes_to)

    @property
    def peer_id(self) -> str:
        return self._peer_id

    def joined(self) -> list[dict[str, Any]]:
        """Describe current memberships."""
        return [entry.describe() for entry in self._joined.values()]

    async def join(self, session_id: str, chat_endpoint: str) -> dict[str, Any]:
        """Join *session_id*'s room as a ravn participant.

        Raises on failure — a join the agent believes happened must have
        actually happened.
        """
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session_id is required")
        if session_id in self._joined:
            return self._joined[session_id].describe()

        try:
            chat_endpoint = await self._wait_for_session_ready(session_id, chat_endpoint)
            ws_url = derive_ravn_ws_url(chat_endpoint, self._peer_id)
            channel = SkuldChannel(
                broker_url=ws_url,
                session_id=session_id,
                peer_id=self._peer_id,
                persona=self._persona,
                display_name=self._display_name,
                subscribes_to=self._subscribes_to,
                reconnect_delay=self._reconnect_delay,
                max_reconnect_attempts=self._max_reconnect_attempts,
                auth_token_provider=self._auth_token_provider,
            )

            async def _on_directed(content: str, metadata: dict[str, Any] | None) -> None:
                if self._observer is None:
                    logger.warning(
                        "SessionJoinManager: directed message from session %s "
                        "dropped — no observer registered",
                        session_id,
                    )
                    return
                await self._observer(session_id, content, metadata)

            channel.on_directed_message(_on_directed)
            await channel.connect()
            if not channel.connected:
                raise RuntimeError(
                    f"failed to join session {session_id}: broker at {ws_url} unreachable "
                    "or connection rejected (ownership?)"
                )

            entry = JoinedSession(session_id=session_id, ws_url=ws_url, channel=channel)
            self._joined[session_id] = entry
            logger.info("SessionJoinManager: joined session %s at %s", session_id, ws_url)
            await self._notify_status(
                session_id,
                f"Joined session room {session_id}. "
                "I will receive matching subscribed outcomes here.",
            )
            return entry.describe()
        except Exception as exc:
            await self._notify_status(
                session_id,
                f"Failed to join session room {session_id}: {exc}",
            )
            raise

    async def _notify_status(self, session_id: str, text: str) -> None:
        if self._status_channel is None:
            return
        try:
            await self._status_channel.emit(
                RavnEvent.response(
                    self._peer_id,
                    text,
                    correlation_id=session_id,
                    session_id=session_id,
                )
            )
        except Exception:
            logger.warning("SessionJoinManager: failed to report join status", exc_info=True)

    async def _wait_for_session_ready(self, session_id: str, chat_endpoint: str) -> str:
        """Wait for Volundr's existing readiness state before opening the room socket."""
        if not self._platform_base_url:
            return chat_endpoint

        url = f"{self._platform_base_url}/api/v1/forge/sessions/{session_id}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._session_ready_timeout
        last_status = ""
        last_error = ""
        timeout = httpx.Timeout(min(10.0, max(1.0, self._reconnect_delay + 5.0)))

        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                try:
                    headers: dict[str, str] = {}
                    if self._auth_token_provider is not None:
                        headers["Authorization"] = f"Bearer {await self._auth_token_provider()}"
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                    payload = response.json()
                    status = str(payload.get("status") or "").strip().lower()
                    if status != last_status:
                        logger.info(
                            "SessionJoinManager: session %s status=%s before join",
                            session_id,
                            status or "unknown",
                        )
                        last_status = status
                    if status == _SESSION_RUNNING_STATUS:
                        endpoint = str(
                            payload.get("chat_endpoint")
                            or payload.get("chatEndpoint")
                            or chat_endpoint
                        ).strip()
                        return endpoint or chat_endpoint
                    if status in _SESSION_TERMINAL_STATUSES:
                        reason = str(payload.get("error") or "").strip()
                        suffix = f": {reason}" if reason else ""
                        raise RuntimeError(
                            f"session {session_id} reached {status} before observer join{suffix}"
                        )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {401, 403}:
                        raise RuntimeError(
                            f"cannot read session {session_id} readiness: "
                            f"HTTP {exc.response.status_code}"
                        ) from exc
                    last_error = f"HTTP {exc.response.status_code}"
                except httpx.HTTPError as exc:
                    last_error = str(exc)

                remaining = deadline - loop.time()
                if remaining <= 0:
                    suffix = f" (last error: {last_error})" if last_error else ""
                    raise RuntimeError(
                        f"session {session_id} did not become running within "
                        f"{self._session_ready_timeout:.0f}s before observer join{suffix}"
                    )
                await asyncio.sleep(min(self._reconnect_delay, remaining))

    async def leave(self, session_id: str) -> bool:
        """Leave a joined session's room. Returns False when not joined."""
        entry = self._joined.pop(session_id.strip(), None)
        if entry is None:
            return False
        await entry.channel.disconnect()
        logger.info("SessionJoinManager: left session %s", session_id)
        return True

    async def post(self, session_id: str, text: str) -> bool:
        """Post a response frame into a joined session's room.

        Returns False when not joined to *session_id*.
        """
        entry = self._joined.get(session_id.strip())
        if entry is None:
            return False
        event = RavnEvent(
            type=RavnEventType.RESPONSE,
            source=self._peer_id,
            payload={"text": text},
            timestamp=datetime.now(UTC),
            urgency=0.5,
            correlation_id=session_id,
            session_id=session_id,
        )
        await entry.channel.emit(event)
        return True

    async def close(self) -> None:
        """Leave every joined session (daemon shutdown)."""
        for session_id in list(self._joined):
            await self.leave(session_id)


# ---------------------------------------------------------------------------
# Composition-root builder (drive loop owns the instance; the tool is injected)
# ---------------------------------------------------------------------------

# Re-exchange a workload token once it is within this many seconds of expiry.
# Without a cache, a reconnect storm (N joins × M attempts each) re-reads the
# token file and re-POSTs the exchange endpoint on a tight loop — exactly when
# the platform is already degraded.
_TOKEN_REFRESH_MARGIN_SECONDS = 60.0


def _build_token_provider(settings: Any) -> AuthTokenProvider | None:
    """Platform identity for cross-session joins (PAT or cached workload token)."""
    platform = settings.gateway.platform
    if not platform.enabled:
        return None

    if platform.pat_token:

        async def pat_provider() -> str:
            return str(platform.pat_token)

        return pat_provider

    cached: dict[str, float | str] = {"token": "", "exp": 0.0}

    async def provider() -> str:
        import time  # noqa: PLC0415

        now = time.time()
        if cached["token"] and float(cached["exp"]) - now > _TOKEN_REFRESH_MARGIN_SECONDS:
            return str(cached["token"])

        from niuu.ws_identity import decode_jwt_claims  # noqa: PLC0415
        from ravn.adapters.tools.platform_tools import (  # noqa: PLC0415
            _exchange_workload_token,
        )

        token = await _exchange_workload_token(
            platform.base_url,
            platform.timeout,
            workload_token_file=platform.workload_token_file,
            exchange_url=platform.workload_exchange_url,
            audiences=list(platform.workload_audiences or []),
        )
        # Cache until the token's own exp (best effort — an un-decodable token
        # just isn't cached and is re-exchanged next time).
        exp = decode_jwt_claims(token).get("exp")
        cached["token"] = token
        cached["exp"] = float(exp) if isinstance(exp, (int, float)) else 0.0
        return token

    return provider


def build_session_join_manager(settings: Any) -> SessionJoinManager:
    """Construct a manager from daemon settings (identity + auth).

    Built once at the resident daemon's composition root (the drive loop) and
    injected into both the loop's observer and the ``session_join`` tool's
    runtime context, so the two share one membership set without a module
    global.
    """
    peer_id = (
        settings.mesh.own_peer_id
        if settings.mesh.enabled and settings.mesh.own_peer_id
        else "ravn-daemon"
    )
    persona = getattr(settings, "persona", "") or "ravn"
    display_name = (
        settings.skuld.display_name or getattr(settings.environment, "resident_name", "") or persona
    )
    return SessionJoinManager(
        peer_id=peer_id,
        persona=persona,
        display_name=display_name,
        auth_token_provider=_build_token_provider(settings),
        reconnect_delay=settings.skuld.reconnect_delay_seconds,
        max_reconnect_attempts=settings.skuld.max_reconnect_attempts,
        platform_base_url=(
            settings.gateway.platform.base_url if settings.gateway.platform.enabled else ""
        ),
        session_ready_timeout=settings.skuld.session_ready_timeout_seconds,
    )
