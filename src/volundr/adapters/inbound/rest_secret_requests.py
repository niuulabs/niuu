"""REST adapter for secure secret requests (under ``/api/v1/forge``).

An agent asks its session's owner for one secret; the owner types it into a masked card
that posts straight here; the agent claims it once. The value is returned by exactly one
route, ``claim``, and never appears in a log line, an error body or any other response.

* ``POST /sessions/{id}/secret-requests``                 open a request (201), or return the
  session's open one for the same name (200)
* ``GET  /sessions/{id}/secret-requests``                 the session's requests, newest first
* ``GET  /sessions/{id}/secret-requests/{rid}``           one request
* ``POST /sessions/{id}/secret-requests/{rid}/fulfill``   the owner supplies the value
* ``POST /sessions/{id}/secret-requests/{rid}/decline``   the owner declines
* ``POST /sessions/{id}/secret-requests/{rid}/claim``     the session collects the value once
* ``POST /sessions/{id}/secret-requests/{rid}/cancel``    the session or the owner withdraws it

Create, list, get, claim and cancel are open to the session's own ``forge_session`` token
(scope ``forge:session:secret``) and to the owner. Fulfil and decline are the owner's
alone: every scoped credential is refused there, whatever its scopes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import (
    FORGE_SESSION_SECRET_SCOPE,
    FORGE_SESSION_TOKEN_USE,
    OPENSHELL_SESSION_TOKEN_USE,
    require_scope,
    scoped_credential_claims,
)
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.forge_session_auth import require_bound_session
from volundr.domain.models import Session, SessionStatus
from volundr.domain.secret_requests import (
    SECRET_NAME_MAX_CHARS,
    SECRET_NAME_PATTERN,
    SecretKeep,
    SecretRequest,
    SecretRequestError,
    SecretRequestNotFoundError,
    SecretRequestStatus,
    SecretRequestValidationError,
)
from volundr.domain.services.secret_requests import SecretRequestService
from volundr.domain.services.session import SessionAccessDeniedError, SessionService

logger = logging.getLogger(__name__)

_TOKEN_USE_HEADER = "x-auth-token-use"
#: Credentials bound to one session, which may ask for and collect that session's secrets.
_SESSION_BOUND_TOKEN_USES = frozenset({FORGE_SESSION_TOKEN_USE, OPENSHELL_SESSION_TOKEN_USE})
#: Statuses in which a session no longer runs, so nothing would ever clean a new request up.
_ENDED_SESSION_STATUSES = frozenset({SessionStatus.STOPPED, SessionStatus.ARCHIVED})


class SecretRequestCreate(BaseModel):
    """Body of ``POST /secret-requests``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        max_length=SECRET_NAME_MAX_CHARS,
        pattern=SECRET_NAME_PATTERN,
        description="The secret's name; it becomes a file name in the session",
        examples=["GITLAB_TOKEN"],
    )
    purpose: str = Field(
        min_length=1, description="Why the agent needs it, shown to the user on the card"
    )
    target: str | None = Field(
        default=None, description="What it is for, such as a host, shown to the user"
    )


class SecretFulfillBody(BaseModel):
    """Body of ``POST /secret-requests/{id}/fulfill``."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, description="The secret, typed by the owner")
    keep: SecretKeep = Field(
        description="`session` keeps it only until the agent claims it; `saved` also saves it "
        "to the owner's credentials under the secret's kebab-case name, overwriting"
    )


class SecretRequestResponse(BaseModel):
    """A secret request. Never contains the value."""

    id: UUID
    session_id: UUID
    name: str
    purpose: str
    target: str | None = None
    status: SecretRequestStatus
    keep: SecretKeep | None = None
    credential_name: str | None = Field(
        default=None, description="The owner's saved credential; set only for keep=saved"
    )
    created_at: datetime
    resolved_at: datetime | None = None
    delivered_at: datetime | None = None

    @classmethod
    def build(cls, request: SecretRequest) -> SecretRequestResponse:
        return cls(
            id=request.id,
            session_id=request.session_id,
            name=request.name,
            purpose=request.purpose,
            target=request.target,
            status=request.status,
            keep=request.keep,
            credential_name=request.credential_name if request.keep is SecretKeep.SAVED else None,
            created_at=request.created_at,
            resolved_at=request.resolved_at,
            delivered_at=request.delivered_at,
        )


class SecretClaimResponse(BaseModel):
    """The one response that carries the value."""

    name: str
    value: str


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail)


def _bearer(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return ""
    return authorization[len("bearer ") :].strip()


def _is_scoped_credential(request: Request, principal: Principal) -> bool:
    """True when the caller presents any scoped credential (a session token, a workload
    token, a scoped PAT), by whichever way the host learns of it."""
    return bool(
        principal.token_use
        or request.headers.get(_TOKEN_USE_HEADER)
        or scoped_credential_claims(_bearer(request)) is not None
    )


def create_secret_requests_router(
    service: SecretRequestService,
    session_service: SessionService,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """Create the secret request routes."""
    router = APIRouter(prefix=prefix, tags=["Secret requests"])
    base = "/sessions/{session_id}/secret-requests"

    async def _session_for(session_id: UUID, principal: Principal) -> Session:
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        try:
            await session_service._check_access(session, principal, "update")
        except SessionAccessDeniedError:
            raise _forbidden(f"Not authorized to access secret requests of session {session_id}")
        if principal.user_id != session.owner_id:
            raise _forbidden("Only the session's owner may use its secret requests")
        return session

    async def _session_caller(request: Request, session_id: UUID, principal: Principal) -> Session:
        """The session, for its own bound credential or its owner."""
        require_bound_session(request, session_id)
        if principal.token_use and principal.token_use not in _SESSION_BOUND_TOKEN_USES:
            raise _forbidden("This credential may not use secret requests")
        header_use = request.headers.get(_TOKEN_USE_HEADER)
        if header_use and header_use not in _SESSION_BOUND_TOKEN_USES:
            raise _forbidden("This credential may not use secret requests")
        if principal.token_use == FORGE_SESSION_TOKEN_USE and principal.bound_session_id != str(
            session_id
        ):
            raise _forbidden("This Forge session token is bound to another session")
        return await _session_for(session_id, principal)

    async def _owner_only(request: Request, session_id: UUID, principal: Principal) -> Session:
        """The session, for its owner acting as themself: no scoped credential at all."""
        if _is_scoped_credential(request, principal):
            raise _forbidden("Only the session's owner may supply or decline a secret request")
        return await _session_for(session_id, principal)

    def _failure(exc: SecretRequestError) -> HTTPException:
        if isinstance(exc, SecretRequestNotFoundError):
            return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        if isinstance(exc, SecretRequestValidationError):
            return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))

    async def _read_fulfill_body(request: Request) -> SecretFulfillBody:
        """Parse the body by hand: FastAPI's own 422 echoes the offending input, which
        here is the secret."""
        try:
            raw: Any = await request.json()
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Body must be JSON")
        try:
            return SecretFulfillBody.model_validate(raw)
        except ValidationError as exc:
            problems = [{"loc": list(err["loc"]), "msg": err["msg"]} for err in exc.errors()]
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problems)

    @router.post(
        base,
        response_model=SecretRequestResponse,
        status_code=status.HTTP_201_CREATED,
        responses={200: {"model": SecretRequestResponse, "description": "Already open"}},
        dependencies=[Depends(require_scope(FORGE_SESSION_SECRET_SCOPE))],
    )
    async def create_secret_request(
        request: Request,
        body: SecretRequestCreate,
        response: Response,
        session_id: UUID = Path(description="Session id"),
        principal: Principal = Depends(extract_principal),
    ) -> SecretRequestResponse:
        """Open a request for a secret, or return the open one for this name (200)."""
        session = await _session_caller(request, session_id, principal)
        if session.status in _ENDED_SESSION_STATUSES:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Session {session_id} is {session.status.value}"
            )
        try:
            created, is_new = await service.create(
                session, name=body.name, purpose=body.purpose, target=body.target
            )
        except SecretRequestError as exc:
            raise _failure(exc)
        if not is_new:
            response.status_code = status.HTTP_200_OK
        return SecretRequestResponse.build(created)

    @router.get(
        base,
        response_model=list[SecretRequestResponse],
        dependencies=[Depends(require_scope(FORGE_SESSION_SECRET_SCOPE))],
    )
    async def list_secret_requests(
        request: Request,
        session_id: UUID = Path(description="Session id"),
        request_status: SecretRequestStatus | None = Query(default=None, alias="status"),
        principal: Principal = Depends(extract_principal),
    ) -> list[SecretRequestResponse]:
        """The session's requests, newest first."""
        await _session_caller(request, session_id, principal)
        found = await service.list(session_id, request_status)
        return [SecretRequestResponse.build(item) for item in found]

    @router.get(
        base + "/{request_id}",
        response_model=SecretRequestResponse,
        dependencies=[Depends(require_scope(FORGE_SESSION_SECRET_SCOPE))],
    )
    async def get_secret_request(
        request: Request,
        session_id: UUID = Path(description="Session id"),
        request_id: UUID = Path(description="Secret request id"),
        principal: Principal = Depends(extract_principal),
    ) -> SecretRequestResponse:
        await _session_caller(request, session_id, principal)
        try:
            found = await service.get(session_id, request_id)
        except SecretRequestError as exc:
            raise _failure(exc)
        return SecretRequestResponse.build(found)

    @router.post(
        base + "/{request_id}/fulfill",
        response_model=SecretRequestResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": SecretFulfillBody.model_json_schema()}},
            }
        },
    )
    async def fulfill_secret_request(
        request: Request,
        session_id: UUID = Path(description="Session id"),
        request_id: UUID = Path(description="Secret request id"),
        principal: Principal = Depends(extract_principal),
    ) -> SecretRequestResponse:
        """The owner supplies the value. The response never contains it."""
        await _owner_only(request, session_id, principal)
        body = await _read_fulfill_body(request)
        try:
            fulfilled = await service.fulfill(
                session_id, request_id, value=body.value, keep=body.keep
            )
        except SecretRequestError as exc:
            raise _failure(exc)
        return SecretRequestResponse.build(fulfilled)

    @router.post(base + "/{request_id}/decline", response_model=SecretRequestResponse)
    async def decline_secret_request(
        request: Request,
        session_id: UUID = Path(description="Session id"),
        request_id: UUID = Path(description="Secret request id"),
        principal: Principal = Depends(extract_principal),
    ) -> SecretRequestResponse:
        """The owner declines a pending request."""
        await _owner_only(request, session_id, principal)
        try:
            declined = await service.decline(session_id, request_id)
        except SecretRequestError as exc:
            raise _failure(exc)
        return SecretRequestResponse.build(declined)

    @router.post(
        base + "/{request_id}/claim",
        response_model=SecretClaimResponse,
        dependencies=[Depends(require_scope(FORGE_SESSION_SECRET_SCOPE))],
    )
    async def claim_secret_request(
        request: Request,
        response: Response,
        session_id: UUID = Path(description="Session id"),
        request_id: UUID = Path(description="Secret request id"),
        principal: Principal = Depends(extract_principal),
    ) -> SecretClaimResponse:
        """Collect the value of a fulfilled request, once. Never cached."""
        await _session_caller(request, session_id, principal)
        try:
            delivered, value = await service.claim(session_id, request_id)
        except SecretRequestError as exc:
            raise _failure(exc)
        response.headers["Cache-Control"] = "no-store"
        return SecretClaimResponse(name=delivered.name, value=value)

    @router.post(
        base + "/{request_id}/cancel",
        response_model=SecretRequestResponse,
        dependencies=[Depends(require_scope(FORGE_SESSION_SECRET_SCOPE))],
    )
    async def cancel_secret_request(
        request: Request,
        session_id: UUID = Path(description="Session id"),
        request_id: UUID = Path(description="Secret request id"),
        principal: Principal = Depends(extract_principal),
    ) -> SecretRequestResponse:
        """Withdraw a pending or fulfilled request; a session-only value is deleted."""
        await _session_caller(request, session_id, principal)
        try:
            cancelled = await service.cancel(session_id, request_id)
        except SecretRequestError as exc:
            raise _failure(exc)
        return SecretRequestResponse.build(cancelled)

    return router
