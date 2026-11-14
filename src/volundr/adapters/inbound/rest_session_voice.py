"""REST adapter for a session's realtime voice credentials (under ``/api/v1/forge``).

``POST /sessions/{session_id}/voice/client-secrets`` gives the session's Skuld broker the
short-lived provider credential its owner's phone runs a realtime voice call with. Forge
mints it from the owner's own provider key (its credential store), so the key never
enters the session pod. Only the session's own ``forge_session`` credential (scope
``forge:session:voice``) may call it, only for its own session, and only while that
session's current launch runs the voice channel.

* 200 the credential: provider, token, expiry, the model, and whether it is session-bound
* 401 the credential is revoked (session gone, restarted, stopped or archived)
* 403 not the session's own Forge session credential
* 404 the session does not exist
* 409 the launch runs no voice channel, the session has no owner, or the owner has stored
  no key for the provider
* 422 the provider is not one this Forge mints for, or the body is malformed
* 502 the provider refused or failed to mint
* 503 Forge runs without session voice (``voice.enabled`` is false)
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.services.token_scope import FORGE_SESSION_VOICE_SCOPE, require_scope
from volundr.adapters.inbound.forge_session_auth import (
    forge_session_claims,
    require_bound_session,
)
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_voice import (
    SessionVoiceCredentialService,
    UnknownVoiceProviderError,
    VoiceProviderKeyMissingError,
)
from volundr.domain.session_launch_facts import voice_channel_launched
from volundr.ports.realtime_voice import RealtimeCredentialError

logger = logging.getLogger(__name__)


class VoiceClientSecretRequest(BaseModel):
    """Body of ``POST /sessions/{id}/voice/client-secrets``."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, description="The realtime provider (openai, xai).")
    model: str = Field(min_length=1, description="The realtime model the call runs.")
    voice: str = Field(default="", description="The voice the call speaks with.")
    instructions: str = Field(default="", description="The call's instructions.")
    reasoning: str = Field(
        default="", description="The reasoning effort, where the provider has one."
    )


class VoiceClientSecretResponse(BaseModel):
    """A provider credential for one realtime call."""

    provider: str
    token: str = Field(description="The short-lived provider credential. Never log it.")
    expires_at_ms: int = Field(description="Epoch milliseconds the credential stops working.")
    model: str
    session_bound: bool = Field(
        description="Whether model, voice and instructions are baked into the credential."
    )


def create_session_voice_router(
    service: SessionVoiceCredentialService | None,
    session_service: SessionService,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """The voice credential route. ``service=None`` mounts it answering 503 with the remedy."""
    router = APIRouter(prefix=prefix, tags=["Session voice"])

    @router.post(
        "/sessions/{session_id}/voice/client-secrets",
        response_model=VoiceClientSecretResponse,
        dependencies=[Depends(require_scope(FORGE_SESSION_VOICE_SCOPE))],
    )
    async def mint_voice_client_secret(
        request: Request,
        body: VoiceClientSecretRequest,
        response: Response,
        session_id: UUID = Path(description="Session id"),
    ) -> VoiceClientSecretResponse:
        """Mint the session owner's realtime provider credential for one voice call."""
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "This Forge mints no session voice credentials: set voice.enabled with "
                "voice.providers, and enable forge_mcp.session_tokens",
            )
        if forge_session_claims(request) is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the session's own Forge session credential may mint its voice credentials",
            )
        require_bound_session(request, session_id)
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        if not voice_channel_launched(session.workload_config):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This session's current launch does not run the voice channel; restart it "
                "with voice.enabled",
            )
        if not session.owner_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This session has no owner, so there is no provider key to mint voice "
                "credentials from",
            )
        try:
            credential = await service.mint(
                owner_id=session.owner_id,
                provider=body.provider,
                model=body.model,
                voice=body.voice,
                instructions=body.instructions,
                reasoning=body.reasoning,
            )
        except UnknownVoiceProviderError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except VoiceProviderKeyMissingError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except RealtimeCredentialError as exc:
            logger.error(
                "Voice credential mint failed for session %s (%s): %s",
                session_id,
                body.provider,
                exc,
            )
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        return VoiceClientSecretResponse(
            provider=body.provider,
            token=credential.token,
            expires_at_ms=credential.expires_at_ms,
            model=credential.model,
            session_bound=credential.session_bound,
        )

    return router
