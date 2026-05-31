"""Telegram long-polling shim — receives updates and re-posts them locally.

In production, Telegram calls Ting's public webhook
(``POST /api/v1/ting/telegram/webhook``). In dev, the platform usually isn't
reachable from the internet, so this shim runs python-telegram-bot's
``Application.updater.start_polling()`` and forwards every received update
as a raw JSON POST to the same webhook endpoint at ``127.0.0.1``.

This keeps **all** command-handler code (parsing, auth, dispatch, replies)
in the existing FastAPI router so the polling and webhook paths can never
drift in behaviour. Cost is one extra in-process HTTP roundtrip per
Telegram message — negligible for chat traffic.

Gated behind ``telegram.polling`` config flag; off by default.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_WEBHOOK_PATH = "/api/v1/ting/telegram/webhook"


class TelegramPollingService:
    """Background long-polling adapter that bridges into the webhook router.

    Lifecycle mirrors python-telegram-bot's ``Application``:
      ``start()`` builds the Application, registers a single catch-all
      handler that forwards updates locally, and starts polling.
      ``stop()`` tears it all down cleanly.

    The ``self_url`` should be the loopback origin where Ting's REST API is
    reachable (e.g. ``http://127.0.0.1:8080``). Forwarding goes via
    httpx, with the configured webhook secret in the
    ``X-Telegram-Bot-Api-Secret-Token`` header so the existing router
    auth check passes.
    """

    def __init__(
        self,
        *,
        bot_token: str,
        webhook_secret: str,
        self_url: str,
        timeout: float = 10.0,
    ) -> None:
        self._bot_token = bot_token
        self._webhook_secret = webhook_secret
        self._self_url = self_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self._application: Any | None = None
        self._started = False

    async def start(self) -> None:
        """Build the Application, register the catch-all, and start polling."""
        if self._started:
            return
        if not self._bot_token:
            logger.info("Telegram polling skipped — bot_token not configured")
            return

        # Imported lazily so import failure doesn't block ting startup when
        # polling is disabled.
        try:
            from telegram import Update
            from telegram.ext import Application, TypeHandler
        except ImportError:
            logger.warning(
                "Telegram polling enabled but python-telegram-bot is not installed; "
                "skipping. Install it with: pip install 'python-telegram-bot>=21.0'"
            )
            return

        self._application = Application.builder().token(self._bot_token).build()
        self._application.add_handler(TypeHandler(Update, self._forward_update))
        await self._application.initialize()
        await self._application.start()
        await self._application.updater.start_polling()
        self._started = True
        logger.info(
            "Telegram polling started — forwarding updates to %s%s", self._self_url, _WEBHOOK_PATH
        )

    async def _forward_update(self, update: Any, context: Any) -> None:
        """Re-POST the raw Telegram update to our own webhook endpoint."""
        del context
        try:
            payload = update.to_dict()
        except Exception:
            logger.warning("Failed to serialise Telegram update; dropping", exc_info=True)
            return

        headers = {"Content-Type": "application/json"}
        if self._webhook_secret:
            headers["X-Telegram-Bot-Api-Secret-Token"] = self._webhook_secret

        url = f"{self._self_url}{_WEBHOOK_PATH}"
        try:
            resp = await self._client.post(url, content=json.dumps(payload), headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "Webhook router returned %d for forwarded update (body: %s)",
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception:
            logger.warning("Failed to forward Telegram update to %s", url, exc_info=True)

    async def stop(self) -> None:
        """Stop polling and tear down the Application."""
        if self._application is not None and self._started:
            try:
                if hasattr(self._application, "updater") and self._application.updater:
                    await self._application.updater.stop()
                await self._application.stop()
                await self._application.shutdown()
            except Exception:
                logger.warning("Error stopping Telegram polling Application", exc_info=True)
            self._application = None
        self._started = False
        await self._client.aclose()
