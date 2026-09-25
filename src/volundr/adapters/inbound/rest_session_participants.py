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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from niuu.adapters.inbound.auth_context import extract_bearer_token
from niuu.domain.services.token_scope import require_scope, scoped_credential_claims
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import Principal
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

# The scope a Kubernetes session pod's RemoteAuthorizationAdapter requests
# when exchanging its projected workload-identity token — see
# niuu.domain.services.token_scope.KNOWN_WORKLOAD_SCOPES and
# skuld.room_role_remote.RemoteAuthorizationAdapter.
_ROOM_ROLE_SCOPE = "forge:session:room-role"

# The K8s ServiceAccount naming convention session pods use (see
# volundr.adapters.outbound.openbao_secret_injection
# .OpenBaoAgentInjectionAdapter._service_account_name), baked into the
# exchanged workload JWT's workload_sub claim by
# niuu.domain.services.workload_identity.WorkloadIdentityService.issue_token.
# A session id's own string form is always a substring of that service
# account name, so this is what binds a scoped credential to the ONE
# session it may ask about.
_SESSION_SERVICE_ACCOUNT_PREFIX = "openbao-session-"


def _require_session_scoped_workload_credential(request: Request, session_id: UUID) -> None:
    """Refuse anything but a workload credential scoped to THIS session.

    Unlike every other route on this router (gated by extract_principal's
    ordinary Cedar-backed access checks), this endpoint discloses "does user
    X have role Y on session Z" to whoever calls it — read-only, but still a
    real information disclosure if left open to any authenticated caller.
    ``require_scope`` alone is not enough here: it admits UNSCOPED
    credentials unchanged by design (see token_scope.token_has_scope), so it
    would let any ordinary human JWT or PAT through. This requires the
    stronger, positive claim that the token IS a scoped workload credential
    for this exact session.
    """
    token = extract_bearer_token(request) or ""
    claims = scoped_credential_claims(token)
    if claims is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This route requires a scoped workload credential "
            f"(token_use=valkyrie_build, scope={_ROOM_ROLE_SCOPE!r}).",
        )
    granted_scopes = claims.get("scopes", [])
    if not isinstance(granted_scopes, list) or _ROOM_ROLE_SCOPE not in granted_scopes:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Token is missing the required scope: {_ROOM_ROLE_SCOPE}",
        )
    workload_sub = str(claims.get("workload_sub", ""))
    session_marker = f"{_SESSION_SERVICE_ACCOUNT_PREFIX}{session_id}"
    if session_marker not in workload_sub and str(session_id) not in workload_sub:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This workload credential is not scoped to this session — a session's "
            "pod may only ask about its own participants.",
        )


async def _resolve_target_principal(
    request: Request, *, user_id: str, tenant_id: str, roles: list[str]
) -> Principal | None:
    """Re-derive the TARGET caller's platform-role-mapped Principal.

    Mirrors volundr.main's _resolve_ws_principal (the session proxy's own
    ownership/room-role guards) so a Kubernetes session pod's raw,
    Envoy-forwarded x-auth-roles ("developer") maps to Cedar's expected
    "volundr:developer" the SAME way every other entry point does — an
    unmapped Principal here would silently demote every owner whose IdP role
    is not already namespaced, the exact class of incident
    project history already hit once (see identity_adapter role mapping in
    volundr/main.py). Returns None (never a bare, unmapped Principal) when
    the configured identity adapter rejects the target outright.
    """
    principal = Principal(user_id=user_id, email="", tenant_id=tenant_id, roles=list(roles))
    identity = getattr(request.app.state, "identity", None)
    from identity.adapters.jwks import JwksIdentityAdapter

    if isinstance(identity, JwksIdentityAdapter):
        try:
            return await identity.revalidate_verified_principal(principal)
        except InvalidTokenError:
            return None
    if not isinstance(identity, HeaderAuthenticationPort):
        return principal
    headers = {
        "x-auth-user-id": principal.user_id,
        "x-auth-tenant": principal.tenant_id,
        "x-auth-roles": ",".join(principal.roles),
    }
    try:
        return await identity.validate_headers(headers)
    except InvalidTokenError:
        return None


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


class EffectiveRoomRoleResponse(BaseModel):
    """A target principal's Cedar-derived room role, or None (no grant)."""

    role: str | None = None


# Runtime backends whose pod-level attach authorization ALWAYS consults
# session_participants grants. Mini-mode's session proxy
# (niuu.session_proxy.SkuldPortRegistry) resolves the room role from these
# grants itself (see niuu.room_access), so "process" is safe unconditionally.
GRANT_HONORING_RUNTIME_BACKENDS = frozenset({"process"})

# Runtime backends whose pods route attach through the Kubernetes Gateway's
# Envoy ext_authz check (charts/skuld/templates/securitypolicy.yaml +
# httproute.yaml) instead of the session proxy. That gate authorizes only
# "start" — Cedar's owner/admin-only action — so a grant is usable there ONLY
# once the pod itself is ALSO deployed with ws_auth.room_role_source: remote
# (skuld.room_role_remote.RemoteAuthorizationAdapter asking Forge for the
# grant on every request that reaches it — see PodManagerConfig.room_role_source
# in volundr/config.py and charts/skuld/values.yaml's wsAuth.room_role_remote).
# Without that, an invite would show up in listings and accept successfully,
# but every attach attempt would 403 against Envoy before reaching the
# session pod at all — a grant nobody can ever use. Refusing the invite up
# front (409, with the remedy) is more honest than shipping that.
#
# Limited to "kubernetes" only: it is the sole backend independently verified
# end-to-end, and the only one WorkloadIdentityContributor projects a
# niuu-workload service-account token onto — RemoteAuthorizationAdapter has
# no credential to exchange on "openshell" (owns its own workload identity)
# or "vm" (no Kubernetes token issuer or projected-volume implementation at
# all), so including them here would let an invite succeed with no way for
# the pod to ever honour it. "docker" is excluded for the same underlying
# reason (not routed through the session proxy, no remote-adapter path).
REMOTE_CAPABLE_RUNTIME_BACKENDS = frozenset({"kubernetes"})


def create_session_participants_router(
    service: SessionParticipantService,
    session_service: SessionService,
    *,
    prefix: str = "/api/v1/forge",
    runtime_backend: str = "process",
    room_role_source: str = "deployment",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Sessions"])
    grants_are_attachable = runtime_backend in GRANT_HONORING_RUNTIME_BACKENDS or (
        runtime_backend in REMOTE_CAPABLE_RUNTIME_BACKENDS and room_role_source == "remote"
    )

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
            remedy = (
                f"session backend whose pod authorization consults "
                f"session_participants grants (currently: "
                f"{', '.join(sorted(GRANT_HONORING_RUNTIME_BACKENDS))})"
            )
            if runtime_backend in REMOTE_CAPABLE_RUNTIME_BACKENDS:
                remedy += (
                    ", or set pod_manager.room_role_source: remote (volundr/config.py's "
                    "PodManagerConfig) to deploy this backend's session pods with "
                    "ws_auth.room_role_source: remote"
                )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This deployment's session pods authorize attach by ownership "
                "only (Kubernetes Gateway ext_authz), so a participant grant "
                f"could never be used to attach. Participant invites require a {remedy}.",
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

    if room_role_source == "remote":

        @router.get(
            "/sessions/{session_id}/participants/role",
            response_model=EffectiveRoomRoleResponse,
            # Defense-in-depth: this rejects a scoped credential that lacks
            # the scope entirely (and is also what the KNOWN_WORKLOAD_SCOPES
            # <-> enforcement-coverage guard in test_token_scope.py finds).
            # It does NOT reject an ordinary unscoped human JWT/PAT —
            # require_scope admits those unchanged by design — so
            # _require_session_scoped_workload_credential below still does
            # the real, positive "this MUST be a scoped workload credential
            # naming THIS session" check.
            dependencies=[Depends(require_scope("forge:session:room-role"))],
        )
        async def get_effective_room_role(
            request: Request,
            session_id: UUID,
            user_id: str = Query(min_length=1, max_length=200),
            tenant_id: str = Query(default=""),
            roles: str = Query(default=""),
        ) -> EffectiveRoomRoleResponse:
            """Resolve a target caller's Cedar-derived room role for this session.

            Called remotely by a Kubernetes-backed session pod's
            ``skuld.room_role_remote.RemoteAuthorizationAdapter`` — never by a
            browser, and only registered at all when this deployment is
            configured for remote room-role resolution (``room_role_source
            == "remote"``; see ``PodManagerConfig.room_role_source``).

            Authorization is intentionally stricter than every other route on
            this router:

            1. The bearer token must be a scoped workload credential
               (``scoped_credential_claims`` — ``token_use ==
               "valkyrie_build"``) carrying the ``forge:session:room-role``
               scope. An ordinary human JWT or unscoped PAT is refused
               outright: this endpoint discloses "does user X have role Y on
               session Z", which nothing but this session's own pod should
               ever be able to ask.
            2. The credential's ``workload_sub`` claim (the K8s
               ServiceAccount subject baked into the exchanged token by
               ``WorkloadIdentityService.issue_token`` — see
               ``niuu.domain.services.workload_identity``) must name THIS
               session: session pods use the ``openbao-session-{id}``
               service account (``OpenBaoAgentInjectionAdapter
               ._service_account_name``), so a token minted for session A's
               pod can never be used to probe session B's participants.

            ``user_id``/``tenant_id``/``roles`` name the TARGET principal
            whose role is being asked about — already verified upstream by
            that pod's Envoy JWT filter into the x-auth-* headers it
            forwards, but re-derived through Forge's own identity adapter
            (``_resolve_target_principal``) so IdP-raw roles (e.g.
            "developer") map to Cedar's expected "volundr:developer" the
            SAME way every other entry point maps them — an unmapped
            Principal here would demote every owner to 403, the exact class
            of incident this project has already hit once.

            Read-only: no state changes. Returns ``{"role": None}`` rather
            than 403/404 when the target has no active grant, matching
            ``effective_room_role``'s "no role" answer — this is what lets
            the remote adapter fail closed on transport/auth errors while
            still treating "no grant" as its own, distinct, expected outcome.
            """
            _require_session_scoped_workload_credential(request, session_id)
            session = await _get_session(session_id)
            target = await _resolve_target_principal(
                request,
                user_id=user_id,
                tenant_id=tenant_id,
                roles=[r for r in (role.strip() for role in roles.split(",")) if r],
            )
            if target is None:
                return EffectiveRoomRoleResponse(role=None)
            role = await service.effective_room_role(session, target)
            return EffectiveRoomRoleResponse(role=role)

    return router
