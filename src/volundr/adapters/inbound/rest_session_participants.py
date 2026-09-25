"""Durable per-session collaboration grants: invite, accept, revoke, list.

Several participants can share one agent room in the browser through these
DURABLE grants. Authorization is delegated entirely to
``SessionParticipantService`` (Cedar's ``admit``/room-scoped actions and the
owner/admin ``_check_access`` ladder); this module only maps its outcomes and
domain errors onto HTTP.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.services.session import SessionAccessDeniedError, SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import (
    CrossTenantInviteError,
    InviteeNotFoundError,
    OwnerInviteError,
    ParticipantAlreadyActiveError,
    ParticipantNotFoundError,
    ParticipantRole,
    ParticipantStateError,
    SelfInviteError,
    SessionParticipant,
)

# Mapped to one generic response: distinguishing "no such user" from "wrong
# tenant" (or from inviting yourself/the owner) over HTTP would let a caller
# enumerate which user_ids exist or which tenant a user_id belongs to.
_INVITE_NOT_ALLOWED = (
    InviteeNotFoundError,
    CrossTenantInviteError,
    SelfInviteError,
    OwnerInviteError,
)


class ParticipantInviteRequest(BaseModel):
    """Request body for inviting a participant."""

    user_id: str = Field(min_length=1, max_length=200)
    role: ParticipantRole
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _expires_at_is_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class ParticipantResponse(BaseModel):
    """A durable collaboration grant, as returned to API callers."""

    session_id: UUID
    user_id: str
    tenant_id: str
    role: str
    status: str
    invited_by: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None

    @classmethod
    def from_participant(cls, participant: SessionParticipant) -> ParticipantResponse:
        return cls(
            session_id=participant.session_id,
            user_id=participant.user_id,
            tenant_id=participant.tenant_id,
            role=participant.role.value,
            status=participant.status.value,
            invited_by=participant.invited_by,
            created_at=participant.created_at,
            updated_at=participant.updated_at,
            expires_at=participant.expires_at,
        )


# Runtime backends whose pod-level attach authorization actually consults
# session_participants grants. Mini-mode's session proxy
# (niuu.session_proxy.SkuldPortRegistry) resolves the room role from these
# grants itself (see niuu.room_access), so "process" is safe. Every other
# backend (kubernetes, openshell, vm) routes attach through the Kubernetes
# Gateway's Envoy ext_authz check instead (charts/skuld/templates/
# securitypolicy.yaml + httproute.yaml), which authorizes only "start" —
# Cedar's owner/admin-only action; no session_participants grant is ever
# authorized for it (see architecture.md's workload-identity section). An
# invite issued there would show up in listings and accept successfully, but
# every attach attempt would 403 against Envoy before reaching the session
# pod at all — a grant nobody can ever use. Refusing the invite up front
# (409, with the remedy) is more honest than shipping that.
#
# This has only been verified for "kubernetes"; "openshell" and "vm" are
# included defensively (they are also pod-based, Gateway-routed backends by
# construction) but their attach path has not been independently traced in
# this change.
GRANT_HONORING_RUNTIME_BACKENDS = frozenset({"process"})


def create_session_participants_router(
    service: SessionParticipantService,
    session_service: SessionService,
    *,
    prefix: str = "/api/v1/forge",
    runtime_backend: str = "process",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Sessions"])
    grants_are_attachable = runtime_backend in GRANT_HONORING_RUNTIME_BACKENDS

    async def _get_session(session_id: UUID):
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
        return session

    @router.post(
        "/sessions/{session_id}/participants",
        status_code=status.HTTP_201_CREATED,
        response_model=ParticipantResponse,
    )
    async def invite_participant(
        request: Request, session_id: UUID, body: ParticipantInviteRequest
    ) -> ParticipantResponse:
        """Invite a same-tenant user to the session's room (owner/admin only).

        Room-role gating restricts which browser-originated messages a
        participant may send; it does not sandbox the agent itself. If this
        session's permission mode lets the agent act without per-tool
        confirmation, a viewer or approver can still ask the agent to do
        anything the agent's own tools reach, and it executes with the
        session owner's credentials. Invite only participants you would
        trust to drive the agent directly, or switch the session to a
        confirming permission mode first.
        """
        principal = await extract_principal(request)
        session = await _get_session(session_id)
        if not grants_are_attachable:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This deployment's session pods authorize attach by ownership "
                "only (Kubernetes Gateway ext_authz), so a participant grant "
                "could never be used to attach. Participant invites require a "
                f"session backend whose pod authorization consults "
                f"session_participants grants (currently: "
                f"{', '.join(sorted(GRANT_HONORING_RUNTIME_BACKENDS))}).",
            )
        try:
            participant = await service.invite(
                session,
                principal,
                user_id=body.user_id,
                role=body.role,
                expires_at=body.expires_at,
            )
        except SessionAccessDeniedError:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Not authorized to invite participants"
            ) from None
        except _INVITE_NOT_ALLOWED as exc:
            # One generic response for every reason (user not found, wrong
            # tenant, self-invite, owner-invite) — see _INVITE_NOT_ALLOWED.
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Cannot invite this user"
            ) from exc
        except ParticipantAlreadyActiveError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return ParticipantResponse.from_participant(participant)

    @router.post(
        "/sessions/{session_id}/participants/accept",
        response_model=ParticipantResponse,
    )
    async def accept_participant(request: Request, session_id: UUID) -> ParticipantResponse:
        """The invited user accepts their own invitation."""
        principal = await extract_principal(request)
        session = await _get_session(session_id)
        try:
            participant = await service.accept(session, principal)
        except ParticipantNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ParticipantStateError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return ParticipantResponse.from_participant(participant)

    @router.delete(
        "/sessions/{session_id}/participants/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def revoke_participant(request: Request, session_id: UUID, user_id: str) -> None:
        """Revoke a grant: owner/admin revokes anyone, a participant may leave."""
        principal = await extract_principal(request)
        session = await _get_session(session_id)
        try:
            await service.revoke(session, principal, user_id)
        except SessionAccessDeniedError:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Not authorized to revoke this participant"
            ) from None
        except ParticipantNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.get(
        "/sessions/{session_id}/participants",
        response_model=list[ParticipantResponse],
    )
    async def list_session_participants(
        request: Request, session_id: UUID
    ) -> list[ParticipantResponse]:
        """List every grant for the session's room."""
        principal = await extract_principal(request)
        session = await _get_session(session_id)
        try:
            participants = await service.list_participants(session, principal)
        except SessionAccessDeniedError:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Not authorized to list participants"
            ) from None
        return [ParticipantResponse.from_participant(p) for p in participants]

    return router
