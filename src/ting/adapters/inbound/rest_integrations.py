"""FastAPI REST adapter for Ting Telegram setup.

Ting no longer owns a separate integrations CRUD API. Shared integration
management lives under ``/api/v1/integrations`` via niuu-shared.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ting.adapters.inbound.auth import extract_principal


class TelegramSetupResponse(BaseModel):
    """Response model for Telegram deeplink setup."""

    deeplink: str = Field(description="Telegram deeplink URL for bot setup")
    token: str = Field(description="Signed setup token")


def create_telegram_setup_router(
    telegram_bot_username: str = "TingBot",
    telegram_hmac_key: str = "",
    telegram_hmac_sig_length: int = 32,
    clock: Callable[[], float] = time.time,
) -> APIRouter:
    """Create router for the Telegram deeplink setup endpoint.

    ``clock`` supplies the token timestamp; tests inject a fixed one.
    """
    router = APIRouter(
        prefix="/api/v1/ting",
        tags=["Ting Telegram"],
    )

    @router.get("/telegram/setup", response_model=TelegramSetupResponse)
    @router.get("/integrations/telegram/setup", response_model=TelegramSetupResponse)
    async def telegram_setup(
        principal=Depends(extract_principal),
    ) -> TelegramSetupResponse:
        """Generate a signed Telegram deeplink for bot setup.

        This remains Ting-owned because the signed token is consumed by Ting's
        Telegram webhook. Shared integration CRUD and OAuth live in niuu-shared.
        """
        if not telegram_hmac_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram HMAC key not configured",
            )
        ts = str(int(clock()))
        payload = f"{principal.user_id}:{ts}"
        key = telegram_hmac_key.encode()
        sig = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:telegram_hmac_sig_length]
        token = f"{payload}:{sig}"
        deeplink = f"https://t.me/{telegram_bot_username}?start={token}"
        return TelegramSetupResponse(deeplink=deeplink, token=token)

    return router
