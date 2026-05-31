"""Matrix gateway adapter — Matrix client-server API via /sync long-polling.

Design principles:
- Uses the Matrix CS API ``/_matrix/client/v3/sync`` with a 30-second
  long-poll timeout; no external library required (httpx only).
- Supports sovereign self-hosted homeservers — no dependency on matrix.org.
- E2E encryption flag is surfaced in config but not yet implemented;
  a warning is logged if ``e2e: true`` is set.
- chat_id: full Matrix room ID (e.g. ``"!abc:matrix.niuu.world"``).
- Ignores its own messages (compares ``sender`` with the configured user_id).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import suppress
from typing import Any
from urllib.parse import quote

import httpx

from ravn.adapters.channels._http_mixin import GatewayHttpMixin
from ravn.adapters.channels.gateway import RavnGateway
from ravn.config import MatrixChannelConfig
from ravn.ports.gateway_channel import GatewayChannelPort, MessageHandler

logger = logging.getLogger(__name__)

# Matrix message event type for plain text
_MSGTYPE_TEXT = "m.text"
_EVENT_TYPE_MESSAGE = "m.room.message"


class MatrixGateway(GatewayHttpMixin, GatewayChannelPort):
    """Polls a Matrix homeserver via /sync and routes messages to :class:`RavnGateway`.

    Uses long-poll sync (``timeout`` query parameter) so events arrive quickly
    without hammering the server.  The ``next_batch`` token is carried across
    calls to avoid reprocessing events.
    """

    def __init__(
        self,
        config: MatrixChannelConfig,
        gateway: RavnGateway,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._gateway = gateway
        self._user_id: str = os.environ.get(config.user_id_env, "")
        self._access_token: str = os.environ.get(config.access_token_env, "")
        self._handler: MessageHandler | None = None
        self._http_client = http_client
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._next_batch: str | None = None

    # ------------------------------------------------------------------
    # GatewayChannelPort interface
    # ------------------------------------------------------------------

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        """Validate credentials and launch the sync loop."""
        if not self._access_token:
            logger.error(
                "Matrix access token env var %r is not set; Matrix gateway disabled.",
                self._config.access_token_env,
            )
            return
        if self._config.e2e:
            logger.warning(
                "Matrix E2E encryption requested but not yet implemented; "
                "messages will be sent and received in plaintext."
            )
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="matrix-gateway")

    async def stop(self) -> None:
        """Stop the sync loop."""
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
        """Send a plain-text message to Matrix room *chat_id*."""
        limit = self._config.message_max_chars
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        txn_id = uuid.uuid4().hex
        await self._cs_put(
            f"/rooms/{_quote(chat_id)}/send/{_EVENT_TYPE_MESSAGE}/{txn_id}",
            json={
                "msgtype": _MSGTYPE_TEXT,
                "body": text,
            },
        )

    async def send_image(self, chat_id: str, image: bytes, caption: str = "") -> None:
        """Upload *image* to the homeserver and send an m.image event."""
        mxc_url = await self._upload_media(image, content_type="image/png", filename="image.png")
        txn_id = uuid.uuid4().hex
        await self._cs_put(
            f"/rooms/{_quote(chat_id)}/send/m.room.message/{txn_id}",
            json={
                "msgtype": "m.image",
                "body": caption or "image.png",
                "url": mxc_url,
            },
        )

    async def send_audio(self, chat_id: str, audio: bytes) -> None:
        """Upload *audio* to the homeserver and send an m.audio event."""
        mxc_url = await self._upload_media(audio, content_type="audio/ogg", filename="audio.ogg")
        txn_id = uuid.uuid4().hex
        await self._cs_put(
            f"/rooms/{_quote(chat_id)}/send/m.room.message/{txn_id}",
            json={
                "msgtype": "m.audio",
                "body": "audio.ogg",
                "url": mxc_url,
            },
        )

    # ------------------------------------------------------------------
    # Sync loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Run the /sync long-poll loop indefinitely."""
        logger.info("Matrix gateway started (user: %s).", self._user_id)
        while not self._stop_event.is_set():
            try:
                await self._sync_once()
            except asyncio.CancelledError:
                logger.info("Matrix gateway stopped.")
                raise
            except Exception:
                logger.exception(
                    "Matrix sync error; retrying in %.1fs.",
                    self._config.retry_delay,
                )
                await asyncio.sleep(self._config.retry_delay)

    async def _sync_once(self) -> None:
        """Perform a single /sync call and dispatch any new messages."""
        params: dict[str, str | int] = {
            "timeout": self._config.sync_timeout_ms,
            "filter": '{"room":{"timeline":{"limit":50}}}',
        }
        if self._next_batch is not None:
            params["since"] = self._next_batch

        data = await self._cs_get("/_matrix/client/v3/sync", params=params)  # type: ignore[arg-type]
        self._next_batch = data.get("next_batch")

        rooms = data.get("rooms", {})
        joined = rooms.get("join", {})
        for room_id, room_data in joined.items():
            timeline = room_data.get("timeline", {})
            for event in timeline.get("events", []):
                await self._handle_event(room_id, event)

    async def _handle_event(self, room_id: str, event: dict[str, Any]) -> None:
        """Process a single Matrix room event."""
        if event.get("type") != _EVENT_TYPE_MESSAGE:
            return

        sender: str = event.get("sender", "")
        if sender == self._user_id:
            return  # ignore own messages

        content = event.get("content", {})
        if content.get("msgtype") != _MSGTYPE_TEXT:
            return

        text: str = content.get("body", "").strip()
        if not text:
            return

        chat_id = room_id
        session_id = f"matrix:{room_id}"

        try:
            response = await self._gateway.handle_message(session_id, text)
        except Exception:
            logger.exception("Error processing Matrix message from room %s.", room_id)
            response = "Sorry, something went wrong. Please try again."

        await self.send_text(chat_id, response or "(no response)")

        if self._handler is not None:
            await self._handler(chat_id, text)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _cs_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def _cs_get(
        self, path: str, params: dict[str, str | int] | None = None
    ) -> dict[str, Any]:
        url = f"{self._config.homeserver}{path}"
        timeout = self._config.sync_timeout_ms / 1000.0 + 10.0
        return await self._http_get(url, headers=self._cs_headers(), params=params, timeout=timeout)

    async def _cs_put(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._config.homeserver}{path}"
        return await self._http_put(url, headers=self._cs_headers(), json=json)

    async def _upload_media(
        self,
        data: bytes,
        *,
        content_type: str,
        filename: str,
    ) -> str:
        """Upload *data* to the homeserver media repo; returns the mxc:// URL."""
        url = (
            f"{self._config.homeserver}/_matrix/media/v3/upload?filename={quote(filename, safe='')}"
        )
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": content_type,
        }
        result = await self._http_post(url, headers=headers, content=data)
        return result["content_uri"]


def _quote(room_id: str) -> str:
    """URL-encode a Matrix room ID for use in path segments."""
    return quote(room_id, safe="")
