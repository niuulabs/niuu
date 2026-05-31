"""Telegram gateway — long-polls the Bot API and routes messages to RavnGateway.

Design principles:
- Pure outbound polling (getUpdates with long-poll timeout).
- No webhook, no open inbound port — works behind NAT and on a $35 Pi.
- Per-chat session isolation via RavnGateway.
- Only responds to pre-approved chat IDs (when allowed_chat_ids is set).
- Slash commands (/stop, /status, /todo, /budget) are translated to natural-
  language prompts and forwarded to the agent.
"""

from __future__ import annotations
from contextlib import suppress

import asyncio
import logging
import os
from typing import Any

import httpx

from ravn.adapters.channels._slash_commands import GATEWAY_SLASH_PROMPTS
from ravn.adapters.channels.gateway import RavnGateway
from ravn.config import TelegramChannelConfig

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Slash commands recognised by the gateway; translated to agent prompts.
_SLASH_PROMPTS: dict[str, str] = GATEWAY_SLASH_PROMPTS

# Bot command definitions registered with Telegram via setMyCommands.
_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "stop", "description": "Stop and summarise current work"},
    {"command": "status", "description": "Show current task status"},
    {"command": "todo", "description": "List todo items"},
    {"command": "budget", "description": "Show iteration budget usage"},
]


class TelegramGateway:
    """Polls Telegram and dispatches incoming messages to :class:`RavnGateway`.

    Uses long-polling (``getUpdates?timeout=…``) so no webhook or open port is
    required.  On error the loop waits :attr:`~TelegramChannelConfig.retry_delay`
    seconds before retrying.
    """

    def __init__(
        self,
        config: TelegramChannelConfig,
        gateway: RavnGateway,
    ) -> None:
        self._config = config
        self._gateway = gateway
        self._token: str = os.environ.get(config.token_env, "")
        self._offset: int = 0

    def _api_url(self, method: str) -> str:
        return _TELEGRAM_API_BASE.format(token=self._token, method=method)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Poll Telegram indefinitely until the task is cancelled."""
        if not self._token:
            logger.error(
                "Telegram token env var %r is not set; Telegram gateway disabled.",
                self._config.token_env,
            )
            return

        timeout = httpx.Timeout(self._config.poll_timeout + 10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            await self._register_commands(client)
            logger.info("Telegram gateway started (polling).")
            while True:
                try:
                    await self._poll_once(client)
                except asyncio.CancelledError:
                    logger.info("Telegram gateway stopped.")
                    raise
                except Exception:
                    logger.exception(
                        "Telegram poll error; retrying in %.1fs.",
                        self._config.retry_delay,
                    )
                    await asyncio.sleep(self._config.retry_delay)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_once(self, client: httpx.AsyncClient) -> None:
        params: dict[str, Any] = {
            "offset": self._offset,
            "timeout": self._config.poll_timeout,
            "allowed_updates": ["message"],
        }
        resp = await client.get(self._api_url("getUpdates"), params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            logger.warning("getUpdates returned ok=false: %s", data)
            return
        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            await self._handle_update(client, update)

    # ------------------------------------------------------------------
    # Update handling
    # ------------------------------------------------------------------

    async def _handle_update(self, client: httpx.AsyncClient, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not message:
            return

        chat_id: int = message["chat"]["id"]
        if not self._is_allowed(chat_id):
            logger.debug("Ignoring message from non-allowed chat_id %s.", chat_id)
            return

        text: str = message.get("text", "").strip()
        if not text:
            return

        prompt = self._translate_command(text)
        session_id = f"telegram:{chat_id}"

        try:
            await self._send_typing(client, chat_id)
            response = await self._gateway.handle_message(session_id, prompt)
        except Exception:
            logger.exception("Error processing Telegram message from chat %s.", chat_id)
            response = "Sorry, something went wrong. Please try again."

        await self._send_message(client, chat_id, response or "(no response)")

    def _is_allowed(self, chat_id: int) -> bool:
        """Return True if *chat_id* is in the allow list (or the list is empty)."""
        if not self._config.allowed_chat_ids:
            return True
        return chat_id in self._config.allowed_chat_ids

    def _translate_command(self, text: str) -> str:
        """Return the natural-language prompt for a slash command, or *text* as-is."""
        if not text.startswith("/"):
            return text
        command = text.split()[0].lower()
        return _SLASH_PROMPTS.get(command, text)

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    async def _send_typing(self, client: httpx.AsyncClient, chat_id: int) -> None:
        """Send a typing indicator — best effort, errors are silenced."""
        with suppress(Exception):
            await client.post(
                self._api_url("sendChatAction"),
                json={"chat_id": chat_id, "action": "typing"},
            )

    async def _send_message(self, client: httpx.AsyncClient, chat_id: int, text: str) -> None:
        """Send *text* to *chat_id*, truncating if it exceeds the API limit."""
        limit = self._config.message_max_chars
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        try:
            await client.post(
                self._api_url("sendMessage"),
                json={"chat_id": chat_id, "text": text},
            )
        except Exception:
            logger.exception("Failed to send Telegram message to chat %s.", chat_id)

    async def _register_commands(self, client: httpx.AsyncClient) -> None:
        """Register slash commands with the Bot API (best effort)."""
        try:
            await client.post(
                self._api_url("setMyCommands"),
                json={"commands": _BOT_COMMANDS},
            )
            logger.debug("Telegram bot commands registered.")
        except Exception:
            logger.warning("Failed to register Telegram bot commands.")
