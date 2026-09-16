"""Owner-authorized durable dispatch claims and delivery-status lookup."""

from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.message_delivery import MessageDeliveryConflictError, MessageDeliveryRepository
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

RequestId = Annotated[str, Path(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")]


class ClaimRequest(BaseModel):
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim_token: UUID


class SettleRequest(BaseModel):
    claim_token: UUID
    status: Literal["delivered", "failed"]
    error: str | None = Field(default=None, max_length=4000)


def create_message_delivery_router(
    repository: MessageDeliveryRepository,
    session_service: SessionService,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Sessions"])

    async def check_access(request: Request, session_id: UUID, action: str) -> None:
        principal = await extract_principal(request)
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(404, "Session not found")
        try:
            await session_service._check_access(session, principal, action)
        except SessionAccessDeniedError:
            raise HTTPException(403, "Not authorized to access message delivery") from None

    @router.post("/sessions/{session_id}/message-deliveries/{request_id}/claim")
    async def claim(
        request: Request, session_id: UUID, request_id: RequestId, body: ClaimRequest
    ) -> dict:
        await check_access(request, session_id, "update")
        try:
            result = await repository.claim(
                session_id, request_id, body.payload_hash, body.claim_token
            )
        except MessageDeliveryConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return asdict(result)

    @router.post("/sessions/{session_id}/message-deliveries/{request_id}/settle")
    async def settle(
        request: Request, session_id: UUID, request_id: RequestId, body: SettleRequest
    ) -> dict:
        await check_access(request, session_id, "update")
        try:
            result = await repository.settle(
                session_id, request_id, body.claim_token, body.status, body.error
            )
        except MessageDeliveryConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return asdict(result)

    @router.get("/sessions/{session_id}/message-deliveries/{request_id}")
    async def get(request: Request, session_id: UUID, request_id: RequestId) -> dict:
        await check_access(request, session_id, "read")
        result = await repository.get(session_id, request_id)
        if result is None:
            raise HTTPException(404, "No dispatch has been recorded for this request")
        return asdict(result)

    return router
