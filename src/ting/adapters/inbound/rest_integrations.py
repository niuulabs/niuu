"""FastAPI REST adapter for Ting Telegram setup.

Ting no longer owns a separate integrations CRUD API. Shared integration
management lives under ``/api/v1/integrations`` via niuu-shared.
"""

from __future__ import annotations

import hashlib
import hmac
import time

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
) -> APIRouter:
    """Create router for the Telegram deeplink setup endpoint."""
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

        TODO: Move this under the shared integrations surface once Telegram
        setup is represented natively by niuu-shared.
        """
        if not telegram_hmac_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram HMAC key not configured",
            )
        ts = str(int(time.time()))
        payload = f"{principal.user_id}:{ts}"
        key = telegram_hmac_key.encode()
        sig = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:telegram_hmac_sig_length]
        token = f"{payload}:{sig}"
        deeplink = f"https://t.me/{telegram_bot_username}?start={token}"
        return TelegramSetupResponse(deeplink=deeplink, token=token)

    return router
