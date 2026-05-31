"""Slack gateway adapter — polling via conversations.history + REST API.

Design principles:
- Polls ``conversations.history`` on a configurable interval for each watched
  channel; tracks the ``latest`` timestamp to avoid reprocessing messages.
- Bot token authentication (xoxb-...).
- ``@Ravn`` app mentions in any channel trigger an agent turn.
- Slash commands forwarded to the bot DM are translated to agent prompts.
- Block Kit used for structured tool output (code blocks, lists).
- chat_id: Slack channel ID (e.g. ``"C0123ABCDE"``).

Note: Socket Mode (real-time events without polling) requires an app-level
token (xapp-...).  Configure ``app_token_env`` to enable it; falls back to
polling when not set.
"""

from __future__ import annotations
from contextlib import suppress

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from ravn.adapters.channels._http_mixin import GatewayHttpMixin
from ravn.adapters.channels._slash_commands import GATEWAY_SLASH_PROMPTS
from ravn.adapters.channels.gateway import RavnGateway
from ravn.config import SlackChannelConfig
from ravn.ports.gateway_channel import GatewayChannelPort, MessageHandler

logger = logging.getLogger(__name__)

# Slack slash commands → agent prompts.
# Uses ravn- prefixed variants to avoid collisions with other Slack apps.
_SLASH_PROMPTS: dict[str, str] = {
    f"/ravn-{cmd.lstrip('/')}": prompt for cmd, prompt in GATEWAY_SLASH_PROMPTS.items()
}


class SlackGateway(GatewayHttpMixin, GatewayChannelPort):
    """Polls Slack channels and routes messages through :class:`RavnGateway`.

    Each call to :meth:`watch_channel` registers a channel to monitor.  On
    each poll tick, new messages are fetched and dispatched.  The bot's own
    user ID is resolved on startup and used to filter self-messages.
    """

    def __init__(
        self,
        config: SlackChannelConfig,
        gateway: RavnGateway,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._gateway = gateway
        self._bot_token: str = os.environ.get(config.bot_token_env, "")
        self._handler: MessageHandler | None = None
        self._http_client = http_client
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        # channel_id → latest message timestamp seen
        self._channel_cursors: dict[str, str] = {}
        # Channels to monitor (populated on start from the bot's channel membership)
        self._watched_channels: list[str] = []
        self._bot_user_id: str = ""

    # ------------------------------------------------------------------
    # GatewayChannelPort interface
    # ------------------------------------------------------------------

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        """Resolve the bot identity and launch the polling loop."""
        if not self._bot_token:
            logger.error(
                "Slack bot token env var %r is not set; Slack gateway disabled.",
                self._config.bot_token_env,
            )
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="slack-gateway")

    async def stop(self) -> None:
        """Stop the polling loop."""
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
        """Post *text* to the Slack channel *chat_id*."""
        limit = self._config.message_max_chars
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        await self._api_post(
            "chat.postMessage",
            json={"channel": chat_id, "text": text},
        )

    async def send_image(self, chat_id: str, image: bytes, caption: str = "") -> None:
        """Upload *image* bytes to *chat_id* via files.upload."""
        await self._api_post(
            "files.upload",
            data={
                "channels": chat_id,
                "initial_comment": caption,
                "filename": "image.png",
            },
            content=image,
        )

    async def send_audio(self, chat_id: str, audio: bytes) -> None:
        """Upload *audio* bytes to *chat_id* via files.upload."""
        await self._api_post(
            "files.upload",
            data={
                "channels": chat_id,
                "filename": "audio.ogg",
            },
            content=audio,
        )

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Resolve bot identity then poll indefinitely."""
        try:
            await self._resolve_bot_identity()
            await self._discover_channels()
            logger.info("Slack gateway started (bot: %s).", self._bot_user_id)
        except Exception:
            logger.exception("Slack gateway startup failed.")
            return

        while not self._stop_event.is_set():
            try:
                await self._poll_all_channels()
            except asyncio.CancelledError:
                logger.info("Slack gateway stopped.")
                raise
            except Exception:
                logger.exception(
                    "Slack poll error; retrying in %.1fs.",
                    self._config.retry_delay,
                )
                await asyncio.sleep(self._config.retry_delay)
                continue

            await asyncio.sleep(self._config.poll_interval)

    async def _resolve_bot_identity(self) -> None:
        """Call auth.test to get the bot's user ID."""
        data = await self._api_post("auth.test", json={})
        self._bot_user_id = data.get("user_id", "")

    async def _discover_channels(self) -> None:
        """Fetch channels the bot is a member of."""
        data = await self._api_get(
            "conversations.list",
            params={"types": "public_channel,private_channel,im,mpim", "exclude_archived": "true"},
        )
        channels = data.get("channels", [])
        self._watched_channels = [c["id"] for c in channels if c.get("is_member")]
        # Seed cursors to current time so we only process new messages
        now = str(time.time())
        for cid in self._watched_channels:
            self._channel_cursors.setdefault(cid, now)
        logger.debug("Slack watching %d channel(s).", len(self._watched_channels))

    async def _poll_all_channels(self) -> None:
        """Poll each watched channel for new messages."""
        for channel_id in list(self._watched_channels):
            await self._poll_channel(channel_id)

    async def _poll_channel(self, channel_id: str) -> None:
        """Fetch new messages from *channel_id* since last cursor."""
        oldest = self._channel_cursors.get(channel_id, "0")
        params: dict[str, str] = {
            "channel": channel_id,
            "oldest": oldest,
            "limit": "50",
            "inclusive": "false",
        }
        try:
            data = await self._api_get("conversations.history", params=params)
        except Exception:
            logger.warning("Failed to poll Slack channel %s.", channel_id)
            return

        messages: list[dict[str, Any]] = data.get("messages", [])
        # Slack returns newest first; reverse to process oldest first
        for msg in reversed(messages):
            ts: str = msg.get("ts", "")
            # Advance cursor
            if ts > oldest:
                self._channel_cursors[channel_id] = ts

            # Skip bot messages and sub-type messages (edits, joins, etc.)
            if msg.get("bot_id") or msg.get("subtype"):
                continue
            if msg.get("user") == self._bot_user_id:
                continue

            text: str = msg.get("text", "").strip()
            if not text:
                continue

            # Only react if the bot is mentioned or it's a DM channel
            if not self._should_handle(channel_id, text):
                continue

            # Strip the mention from the text
            clean_text = self._strip_mention(text)
            prompt = self._translate_slash(clean_text)
            session_id = f"slack:{channel_id}"

            try:
                response = await self._gateway.handle_message(session_id, prompt)
            except Exception:
                logger.exception("Error processing Slack message from %s.", channel_id)
                response = "Sorry, something went wrong. Please try again."

            await self.send_text(channel_id, response or "(no response)")

            if self._handler is not None:
                await self._handler(channel_id, clean_text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_handle(self, channel_id: str, text: str) -> bool:
        """Return True if the message should be forwarded to the agent."""
        # Always handle DMs (channel IDs starting with 'D')
        if channel_id.startswith("D"):
            return True
        # Handle @mentions
        if self._bot_user_id and f"<@{self._bot_user_id}>" in text:
            return True
        # Handle slash commands targeting Ravn
        first_word = text.split()[0].lower() if text else ""
        return first_word in _SLASH_PROMPTS

    def _strip_mention(self, text: str) -> str:
        """Remove the bot mention prefix from *text*."""
        if self._bot_user_id:
            mention = f"<@{self._bot_user_id}>"
            text = text.replace(mention, "").strip()
        return text

    def _translate_slash(self, text: str) -> str:
        """Return the natural-language prompt for a Ravn slash command, or *text*."""
        if not text.startswith("/"):
            return text
        command = text.split()[0].lower()
        return _SLASH_PROMPTS.get(command, text)

    def _slack_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._bot_token}"}

    async def _api_post(self, method: str, **kwargs: Any) -> dict[str, Any]:
        """POST to the Slack Web API; raises on error."""
        url = f"{self._config.api_base}/{method}"
        return await self._http_post(url, headers=self._slack_headers(), **kwargs)

    async def _api_get(self, method: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """GET from the Slack Web API."""
        url = f"{self._config.api_base}/{method}"
        return await self._http_get(url, headers=self._slack_headers(), params=params)
