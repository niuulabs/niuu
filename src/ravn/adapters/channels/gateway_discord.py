"""Discord gateway adapter — Discord Gateway WebSocket + REST API.

Design principles:
- Connects to the Discord Gateway via WebSocket (OP 2 IDENTIFY).
- Handles heartbeat, reconnect, and invalid-session flows.
- Sends messages via the Discord REST API (no discord.py dependency).
- chat_id format: ``"guild_id/channel_id"`` or bare ``"channel_id"``
  (guild_id is optional when the bot only serves one guild).
- Slash commands (/stop, /status, /todo, /budget) are translated to
  natural-language prompts before being forwarded to RavnGateway.
- Reaction-based approval: 👍 (approve) / 👎 (reject) on messages
  that include an [APPROVAL_REQUESTED] marker call the on_message
  handler with a synthetic approve/reject text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from typing import Any

import httpx
import websockets

from ravn.adapters.channels._http_mixin import GatewayHttpMixin
from ravn.adapters.channels._slash_commands import GATEWAY_SLASH_PROMPTS
from ravn.adapters.channels.gateway import RavnGateway
from ravn.config import DiscordChannelConfig
from ravn.ports.gateway_channel import GatewayChannelPort, MessageHandler

logger = logging.getLogger(__name__)

# Discord Gateway opcodes
_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11

__all__ = ["_OP_HEARTBEAT_ACK"]

# Slash commands recognised by the gateway; translated to agent prompts.
_SLASH_PROMPTS: dict[str, str] = GATEWAY_SLASH_PROMPTS

# Approval marker embedded in messages that need human confirmation.
_APPROVAL_MARKER = "[APPROVAL_REQUESTED]"
_APPROVE_EMOJI = "👍"
_REJECT_EMOJI = "👎"

# Discord intent flags — GUILD_MESSAGES (1<<9) + MESSAGE_CONTENT (1<<15)
_INTENTS = (1 << 9) | (1 << 15)


class DiscordGateway(GatewayHttpMixin, GatewayChannelPort):
    """Connects to Discord Gateway and routes messages through :class:`RavnGateway`.

    WebSocket lifecycle:

    1. Connect → receive ``OP 10 HELLO`` → start heartbeat task.
    2. Send ``OP 2 IDENTIFY`` with bot token and intents.
    3. Receive ``OP 0 READY`` → store session_id.
    4. Listen for ``OP 0 MESSAGE_CREATE`` events → route to gateway.
    5. On ``OP 7 RECONNECT`` or connection drop → reconnect.
    """

    def __init__(
        self,
        config: DiscordChannelConfig,
        gateway: RavnGateway,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._gateway = gateway
        self._token: str = os.environ.get(config.token_env, "")
        self._handler: MessageHandler | None = None
        self._http_client = http_client
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        # Per-message approval tracking: message_id → (chat_id, session_id)
        # Capped at max_pending_approvals to prevent unbounded growth.
        self._pending_approvals: dict[str, tuple[str, str]] = {}

    # ------------------------------------------------------------------
    # GatewayChannelPort interface
    # ------------------------------------------------------------------

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        """Launch the WebSocket receive loop as a background asyncio task."""
        if not self._token:
            logger.error(
                "Discord token env var %r is not set; Discord gateway disabled.",
                self._config.token_env,
            )
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="discord-gateway")

    async def stop(self) -> None:
        """Signal the receive loop to stop and await its completion."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def run(self) -> None:
        """Start and run until cancelled (convenience for :func:`asyncio.create_task`)."""
        await self.start()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task

    async def send_text(self, chat_id: str, text: str) -> None:
        """Send *text* to the Discord channel identified by *chat_id*."""
        channel_id = self._channel_id(chat_id)
        limit = self._config.message_max_chars
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        await self._rest_post(
            f"/channels/{channel_id}/messages",
            json={"content": text},
        )

    async def send_image(self, chat_id: str, image: bytes, caption: str = "") -> None:
        """Send *image* bytes to *chat_id* as a multipart attachment."""
        channel_id = self._channel_id(chat_id)
        limit = self._config.message_max_chars
        if caption and len(caption) > limit:
            caption = caption[: limit - 3] + "..."
        await self._rest_post_multipart(
            f"/channels/{channel_id}/messages",
            payload_json={"content": caption},
            files={"files[0]": ("image.png", image, "image/png")},
        )

    async def send_audio(self, chat_id: str, audio: bytes) -> None:
        """Send *audio* bytes to *chat_id* as a multipart attachment."""
        channel_id = self._channel_id(chat_id)
        await self._rest_post_multipart(
            f"/channels/{channel_id}/messages",
            payload_json={},
            files={"files[0]": ("audio.ogg", audio, "audio/ogg")},
        )

    # ------------------------------------------------------------------
    # WebSocket receive loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Connect to Discord Gateway and run the event loop indefinitely."""
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Discord gateway error; reconnecting in %.1fs.",
                    self._config.retry_delay,
                )
                await asyncio.sleep(self._config.retry_delay)

    async def _connect_and_listen(self) -> None:
        """Open a single WebSocket session and process events until disconnected."""
        logger.info("Discord gateway connecting to %s", self._config.gateway_url)
        async with websockets.connect(self._config.gateway_url) as ws:
            heartbeat_task: asyncio.Task[None] | None = None
            try:
                async for raw in ws:
                    payload: dict[str, Any] = json.loads(raw)
                    op: int = payload.get("op", -1)
                    data: Any = payload.get("d")

                    if op == _OP_HELLO:
                        interval_ms: int = data["heartbeat_interval"]
                        heartbeat_task = asyncio.create_task(
                            self._heartbeat_loop(ws, interval_ms / 1000.0)
                        )
                        await self._identify(ws)

                    elif op == _OP_DISPATCH:
                        event_name: str = payload.get("t", "")
                        await self._handle_dispatch(event_name, data)

                    elif op == _OP_RECONNECT:
                        logger.info("Discord requested reconnect.")
                        break

                    elif op == _OP_INVALID_SESSION:
                        logger.warning("Discord invalid session; re-identifying.")
                        await asyncio.sleep(1)
                        await self._identify(ws)

                    elif op == _OP_HEARTBEAT:
                        # Discord asked us to heartbeat immediately
                        await ws.send(json.dumps({"op": _OP_HEARTBEAT, "d": None}))

            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _heartbeat_loop(self, ws: Any, interval_seconds: float) -> None:
        """Send OP 1 HEARTBEAT every *interval_seconds*."""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await ws.send(json.dumps({"op": _OP_HEARTBEAT, "d": None}))
            except Exception:
                logger.debug("Discord heartbeat failed — WS closed.")
                return

    async def _identify(self, ws: Any) -> None:
        """Send OP 2 IDENTIFY with bot token and intents."""
        payload = {
            "op": _OP_IDENTIFY,
            "d": {
                "token": self._token,
                "intents": _INTENTS,
                "properties": {
                    "os": "linux",
                    "browser": "ravn",
                    "device": "ravn",
                },
            },
        }
        await ws.send(json.dumps(payload))

    # ------------------------------------------------------------------
    # Dispatch handling
    # ------------------------------------------------------------------

    async def _handle_dispatch(self, event_name: str, data: Any) -> None:
        if event_name == "MESSAGE_CREATE":
            await self._on_message_create(data)
        elif event_name == "MESSAGE_REACTION_ADD":
            await self._on_reaction_add(data)
        elif event_name == "READY":
            logger.info("Discord gateway ready (user: %s).", data.get("user", {}).get("username"))

    async def _on_message_create(self, data: dict[str, Any]) -> None:
        # Ignore messages from bots (including ourselves)
        author = data.get("author", {})
        if author.get("bot"):
            return

        channel_id: str = data.get("channel_id", "")
        guild_id: str = data.get("guild_id", self._config.guild_id)
        chat_id = f"{guild_id}/{channel_id}" if guild_id else channel_id
        text: str = data.get("content", "").strip()

        if not text:
            return

        # Track messages with approval markers so we can handle reactions.
        # Evict the oldest entry when the cap is reached to keep memory bounded.
        message_id: str = data.get("id", "")
        if _APPROVAL_MARKER in text and message_id:
            session_id = f"discord:{chat_id}"
            if len(self._pending_approvals) >= self._config.max_pending_approvals:
                oldest_key = next(iter(self._pending_approvals))
                del self._pending_approvals[oldest_key]
            self._pending_approvals[message_id] = (chat_id, session_id)

        prompt = self._translate_slash(text)
        session_id = f"discord:{chat_id}"

        try:
            response = await self._gateway.handle_message(session_id, prompt)
        except Exception:
            logger.exception("Error processing Discord message from %s.", chat_id)
            response = "Sorry, something went wrong. Please try again."

        await self.send_text(chat_id, response or "(no response)")

        if self._handler is not None:
            await self._handler(chat_id, prompt)

    async def _on_reaction_add(self, data: dict[str, Any]) -> None:
        """Handle 👍/👎 reactions on approval-requested messages."""
        message_id: str = data.get("message_id", "")
        if message_id not in self._pending_approvals:
            return

        emoji_name: str = data.get("emoji", {}).get("name", "")
        if emoji_name == _APPROVE_EMOJI:
            prompt = "approved"
        elif emoji_name == _REJECT_EMOJI:
            prompt = "rejected"
        else:
            return  # Leave the approval pending for the correct reaction

        chat_id, session_id = self._pending_approvals.pop(message_id)

        try:
            response = await self._gateway.handle_message(session_id, prompt)
        except Exception:
            logger.exception("Error processing Discord reaction from %s.", chat_id)
            response = "Sorry, something went wrong."

        await self.send_text(chat_id, response or "(no response)")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _channel_id(self, chat_id: str) -> str:
        """Extract the channel ID from a ``guild_id/channel_id`` or bare channel ID."""
        if "/" in chat_id:
            return chat_id.split("/", 1)[1]
        return chat_id

    def _translate_slash(self, text: str) -> str:
        """Return the natural-language prompt for a Ravn slash command, or *text* as-is."""
        if not text.startswith("/"):
            return text
        command = text.split()[0].lower()
        return _SLASH_PROMPTS.get(command, text)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bot {self._token}"}

    async def _rest_post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        """POST JSON to the Discord REST API; returns parsed JSON."""
        url = f"{self._config.api_base}{path}"
        headers = {**self._auth_headers(), "Content-Type": "application/json"}
        return await self._http_post(url, headers=headers, **kwargs)

    async def _rest_post_multipart(
        self,
        path: str,
        *,
        payload_json: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        """POST multipart/form-data to the Discord REST API (for file uploads)."""
        url = f"{self._config.api_base}{path}"
        return await self._http_post(
            url,
            headers=self._auth_headers(),
            data={"payload_json": json.dumps(payload_json)},
            files=files,
        )
