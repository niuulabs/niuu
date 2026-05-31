"""WhatsApp gateway adapter — Meta Cloud API (Business API mode).

Design principles:
- Sends messages via Meta Graph API: ``POST /v18.0/{phone_number_id}/messages``.
- Receives messages via a Meta webhook (HTTP POST from Meta's servers).
- Runs a lightweight FastAPI app on a configurable port for webhook delivery.
- chat_id: E.164 phone number (e.g. ``"+4412345678"``) or group JID.
- ``mode: local_bridge`` is a future extension (whatsapp-web.js); currently
  raises :class:`NotImplementedError` to avoid silent misconfiguration.
- The webhook GET endpoint handles Meta's challenge verification flow.
- Voice messages are flagged and routed with a note (STT depends on NIU-533).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from contextlib import suppress
from typing import Any

import httpx

from ravn.adapters.channels._http_mixin import GatewayHttpMixin
from ravn.adapters.channels.gateway import RavnGateway
from ravn.config import WhatsAppChannelConfig
from ravn.ports.gateway_channel import GatewayChannelPort, MessageHandler

logger = logging.getLogger(__name__)


class WhatsAppGateway(GatewayHttpMixin, GatewayChannelPort):
    """Routes WhatsApp messages to :class:`RavnGateway` via the Meta Cloud API.

    Start this adapter alongside the HTTP gateway to expose a webhook endpoint
    that Meta's servers will POST incoming messages to.

    Requires:
    * ``WA_API_KEY`` env var — Meta bearer token.
    * ``WA_PHONE_NUMBER_ID`` env var — phone number ID from Meta dashboard.
    * ``WA_WEBHOOK_VERIFY_TOKEN`` env var — verification token registered in
      the Meta developer console.
    * An externally reachable URL configured in the Meta app console pointing
      to ``http://<host>:<port>/webhook``.
    """

    def __init__(
        self,
        config: WhatsAppChannelConfig,
        gateway: RavnGateway,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if config.mode == "local_bridge":
            raise NotImplementedError(
                "WhatsApp local_bridge mode (whatsapp-web.js) is not yet implemented. "
                "Use mode: business_api with the Meta Cloud API."
            )
        self._config = config
        self._gateway = gateway
        self._api_key: str = os.environ.get(config.api_key_env, "")
        self._phone_number_id: str = os.environ.get(config.phone_number_id_env, "")
        self._verify_token: str = os.environ.get(config.webhook_verify_token_env, "")
        self._webhook_secret: str = os.environ.get(config.webhook_secret_env, "")
        self._handler: MessageHandler | None = None
        self._http_client = http_client
        self._server_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # GatewayChannelPort interface
    # ------------------------------------------------------------------

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        """Start the webhook HTTP server."""
        if not self._api_key:
            logger.error("WhatsApp API key is not set; WhatsApp gateway disabled.")
            return
        self._server_task = asyncio.create_task(self._run_webhook_server(), name="whatsapp-webhook")

    async def stop(self) -> None:
        """Stop the webhook HTTP server."""
        if self._server_task is not None:
            self._server_task.cancel()
            await asyncio.gather(self._server_task, return_exceptions=True)
            self._server_task = None

    async def run(self) -> None:
        """Start and run until cancelled (convenience for :func:`asyncio.create_task`)."""
        await self.start()
        if self._server_task is not None:
            with suppress(asyncio.CancelledError):
                await self._server_task

    async def send_text(self, chat_id: str, text: str) -> None:
        """Send a text message to *chat_id* via the Meta Cloud API."""
        limit = self._config.message_max_chars
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        payload = {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "text",
            "text": {"body": text},
        }
        await self._graph_post(f"/{self._phone_number_id}/messages", json=payload)

    async def send_image(self, chat_id: str, image: bytes, caption: str = "") -> None:
        """Upload *image* via Meta and send as an image message."""
        media_id = await self._upload_media(image, mime_type="image/png")
        payload = {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "image",
            "image": {"id": media_id, "caption": caption},
        }
        await self._graph_post(f"/{self._phone_number_id}/messages", json=payload)

    async def send_audio(self, chat_id: str, audio: bytes) -> None:
        """Upload *audio* via Meta and send as an audio message."""
        media_id = await self._upload_media(audio, mime_type="audio/ogg")
        payload = {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "audio",
            "audio": {"id": media_id},
        }
        await self._graph_post(f"/{self._phone_number_id}/messages", json=payload)

    # ------------------------------------------------------------------
    # Webhook server
    # ------------------------------------------------------------------

    async def _run_webhook_server(self) -> None:  # pragma: no cover
        """Run a FastAPI webhook server on the configured host:port."""
        import uvicorn
        from fastapi import FastAPI, Query, Request, Response

        app = FastAPI(title="Ravn WhatsApp Webhook", docs_url=None, redoc_url=None)

        @app.get("/webhook")
        async def verify(
            hub_mode: str = Query(default="", alias="hub.mode"),
            hub_verify_token: str = Query(default="", alias="hub.verify_token"),
            hub_challenge: str = Query(default="", alias="hub.challenge"),
        ) -> Response:
            """Meta webhook verification (GET challenge-response)."""
            if hub_mode == "subscribe" and hub_verify_token == self._verify_token:
                return Response(content=hub_challenge, media_type="text/plain")
            return Response(status_code=403)

        @app.post("/webhook")
        async def receive(request: Request) -> Response | dict[str, str]:
            """Handle inbound message events from Meta."""
            body_bytes = await request.body()
            signature = request.headers.get("X-Hub-Signature-256", "")
            if not self._verify_signature(body_bytes, signature):
                return Response(status_code=403)
            body: dict[str, Any] = json.loads(body_bytes)
            await self._handle_webhook_body(body)
            return {"status": "ok"}

        config = uvicorn.Config(
            app,
            host=self._config.webhook_host,
            port=self._config.webhook_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        logger.info(
            "WhatsApp webhook listening on %s:%s",
            self._config.webhook_host,
            self._config.webhook_port,
        )
        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("WhatsApp webhook server stopped.")
            raise

    async def _handle_webhook_body(self, body: dict[str, Any]) -> None:
        """Parse and dispatch a Meta webhook payload."""
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages: list[dict[str, Any]] = value.get("messages", [])
                for msg in messages:
                    await self._handle_message(msg)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Process a single WhatsApp message object."""
        msg_type: str = msg.get("type", "")
        from_number: str = msg.get("from", "")
        chat_id = from_number

        if msg_type == "text":
            text: str = msg.get("text", {}).get("body", "").strip()
        elif msg_type == "audio":
            # Voice message — STT pipeline depends on NIU-533
            text = "[Voice message received — STT transcription pending NIU-533]"
        elif msg_type == "image":
            caption = msg.get("image", {}).get("caption", "")
            text = caption or "[Image received]"
        else:
            logger.debug("WhatsApp: ignoring message type %r from %s.", msg_type, from_number)
            return

        if not text:
            return

        session_id = f"whatsapp:{chat_id}"

        try:
            response = await self._gateway.handle_message(session_id, text)
        except Exception:
            logger.exception("Error processing WhatsApp message from %s.", chat_id)
            response = "Sorry, something went wrong. Please try again."

        await self.send_text(chat_id, response or "(no response)")

        if self._handler is not None:
            await self._handler(chat_id, text)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _graph_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def _graph_post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        """POST to the Meta Graph API."""
        url = f"{self._config.api_base}{path}"
        return await self._http_post(url, headers=self._graph_headers(), json=json)

    async def _upload_media(self, data: bytes, *, mime_type: str) -> str:
        """Upload *data* to Meta's media endpoint; returns the media ID."""
        url = f"{self._config.api_base}/{self._phone_number_id}/media"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        result = await self._http_post(
            url,
            headers=headers,
            files={"file": ("media", data, mime_type)},
            data={"messaging_product": "whatsapp"},
        )
        return result["id"]

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """Verify the Meta X-Hub-Signature-256 header.

        Returns ``True`` when no secret is configured (verification disabled)
        or when the HMAC digest matches.  Returns ``False`` on mismatch.
        """
        if not self._webhook_secret:
            return True
        expected = (
            "sha256=" + hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(expected, signature)
