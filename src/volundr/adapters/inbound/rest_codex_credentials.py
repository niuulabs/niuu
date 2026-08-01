"""Authenticated access-only Codex token broker endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.outbound.codex_credential_broker import CodexCredentialBrokerError
from volundr.domain.models import Principal
from volundr.domain.ports import CodexCredentialBrokerPort


class CodexTokenRequest(BaseModel):
    credential_name: str = Field(default="codex-credentials", min_length=1, max_length=253)
    credential_field: str = Field(default="auth.json", min_length=1, max_length=253)
    force_refresh: bool = False
    previous_access_token_sha256: str = Field(default="", max_length=64)


class CodexTokenResponse(BaseModel):
    access_token: str
    chatgpt_account_id: str
    expires_in: int
    chatgpt_plan_type: str = ""


def create_codex_credentials_router(
    broker: CodexCredentialBrokerPort,
    *,
    prefix: str = "/api/v1/internal/credentials/codex",
) -> APIRouter:
    """Create the normal-authenticated broker route used by Skuld sessions."""
    router = APIRouter(prefix=prefix, tags=["credentials-internal"])

    @router.post("/tokens", response_model=CodexTokenResponse, include_in_schema=False)
    async def get_tokens(
        body: CodexTokenRequest,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> CodexTokenResponse:
        response.headers["Cache-Control"] = "no-store"
        try:
            tokens = await broker.get_tokens(
                owner_id=principal.user_id,
                credential_name=body.credential_name,
                credential_field=body.credential_field,
                force_refresh=body.force_refresh,
                previous_access_token_sha256=body.previous_access_token_sha256,
            )
        except CodexCredentialBrokerError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
                headers={"Cache-Control": "no-store"},
            ) from exc
        return CodexTokenResponse(
            access_token=tokens.access_token,
            chatgpt_account_id=tokens.account_id,
            expires_in=tokens.expires_in,
            chatgpt_plan_type=tokens.plan_type,
        )

    return router
