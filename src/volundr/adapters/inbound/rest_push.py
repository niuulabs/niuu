"""REST routes of the Guild push gateway.

* ``GET  /push/status``  what the gateway has done since it started
* ``POST /push/test``    push once to the caller's own registered tokens
* ``POST /push/call``    ring the caller's owner's phone with a resident's call request

All exist only on a node that runs the gateway (``push.gateway.enabled``). The
test push goes to the caller's tokens and nobody else's, so it needs no admin role.
A ring needs a resident's delegated workload credential with ``forge:push:call``: it
reaches only that credential's owner, for a frame that names that resident.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.services.token_scope import (
    FORGE_PUSH_CALL_SCOPE,
    VALKYRIE_BUILD_TOKEN_USE,
    credential_scopes,
    require_scope,
    scoped_credential_claims,
)
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import Principal
from volundr.domain.push import (
    CALL_BODY_MAX_BYTES,
    CallRequestError,
    PushTestKind,
    validate_call_request,
)
from volundr.domain.push_ports import GuildFeedError
from volundr.domain.services.push_gateway import CallRateLimitedError, PushGateway

logger = logging.getLogger(__name__)

#: Bounds of a test alert's text; a real alert's come from the notification.
MAX_TEST_TITLE_CHARS = 120
MAX_TEST_BODY_CHARS = 280
#: The registration limit of a token (``DeviceToken.token``).
MAX_TOKEN_CHARS = 512
#: The longest request or session id a ring takes.
MAX_CALL_ID_CHARS = 200
#: The claim a resident's delegated workload credential names its resident by.
RESIDENT_ID_CLAIM = "workload_resident_id"


class PushTestRequest(BaseModel):
    """Which of the caller's tokens to push to, and the test alert's text."""

    model_config = ConfigDict(extra="forbid")

    kind: PushTestKind = PushTestKind.ALERT
    token: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_TOKEN_CHARS,
        description="Only this registered token; omitted pushes to every token for the kind",
    )
    title: str = Field(default="Forge test push", min_length=1, max_length=MAX_TEST_TITLE_CHARS)
    body: str = Field(
        default="Push from Forge reaches this device.", max_length=MAX_TEST_BODY_CHARS
    )
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CALL_ID_CHARS,
        description=(
            "voip only: the session the test call names (e.g. the selected resident's id), "
            "so a session already on the phone's May call me list rings at once"
        ),
    )


class PushTestDevice(BaseModel):
    """APNs' answer for one token. Only the token's last characters are shown."""

    kind: str
    environment: str
    token_suffix: str
    accepted: bool
    detail: str
    apns_id: str


class PushTestResponse(BaseModel):
    kind: str
    sent: int
    accepted: int
    devices: list[PushTestDevice]


class PushCallRequest(BaseModel):
    """A resident's call request: the ``call.request`` frame, sent as it is."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=MAX_CALL_ID_CHARS)
    frame: dict[str, Any]


class PushCallDevice(BaseModel):
    """APNs' answer for one VoIP token, by fingerprint."""

    fingerprint: str
    status: int | None
    reason: str


class PushCallResponse(BaseModel):
    sent: bool
    tokens: int
    results: list[PushCallDevice]


def _calling_resident(request: Request) -> str:
    """The resident a ring speaks for, or 403.

    Only a scoped workload credential holding ``forge:push:call`` and naming its
    resident (a delegated resident's token) may ring: ``require_scope`` alone admits
    unscoped tokens, which never ring a phone.
    """
    auth = request.headers.get("authorization", "")
    claims = scoped_credential_claims(auth[7:] if auth.startswith("Bearer ") else "")
    if (
        claims is None
        or claims.get("token_use") != VALKYRIE_BUILD_TOKEN_USE
        or FORGE_PUSH_CALL_SCOPE not in credential_scopes(claims)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Ringing needs a resident's workload credential with {FORGE_PUSH_CALL_SCOPE}",
        )
    resident_id = claims.get(RESIDENT_ID_CLAIM)
    if not isinstance(resident_id, str) or not resident_id.strip():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Ringing needs a resident's delegated workload credential (it names no resident)",
        )
    return resident_id.strip()


def create_push_router(gateway: PushGateway, *, prefix: str = "/api/v1/forge") -> APIRouter:
    """Create the push gateway's status and test routes."""
    router = APIRouter(prefix=prefix, tags=["Push"])

    @router.get("/push/status")
    async def push_status(principal: Principal = Depends(extract_principal)) -> dict[str, Any]:
        """Counters since start, the nodes being read and the last error, and the
        caller's own org's alerts per status (never another org's)."""
        return {
            **gateway.status(),
            "tokens_by_environment": await gateway.tokens_by_environment(),
            "tenant_alerts": await gateway.alerts_for_tenant(principal.tenant_id or None),
        }

    @router.post("/push/test", response_model=PushTestResponse)
    async def send_test_push(
        data: PushTestRequest, principal: Principal = Depends(extract_principal)
    ) -> PushTestResponse:
        """Push once to the caller's registered tokens for ``kind``.

        ``alert`` shows the given title and body and opens the app's activity list;
        ``background`` is the silent push to the alert token; ``live_activity`` and
        ``live_activity_start`` carry the caller's current notifications; ``widget``
        reloads the widgets; ``voip`` rings the phone with a ``call.request`` saying
        "Test call" (for ``session_id``, or a test session the phone then lists under
        May call me). ``token`` narrows it to one registered token. Throttles do not
        apply.
        """
        try:
            results = await gateway.send_test(
                principal.user_id,
                data.kind,
                title=data.title,
                body=data.body,
                token=data.token,
                call_session_id=data.session_id,
            )
        except GuildFeedError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        if not results:
            token_kind = data.kind.token_kind.value
            detail = (
                f"That token is not one of your registered {token_kind} tokens on this Forge"
                if data.token
                else f"You have no registered {token_kind} token on this Forge"
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail)
        return PushTestResponse(
            kind=data.kind.value,
            sent=len(results),
            accepted=sum(1 for result in results if result.accepted),
            devices=[
                PushTestDevice(
                    kind=result.kind,
                    environment=result.environment,
                    token_suffix=result.token_suffix,
                    accepted=result.accepted,
                    detail=result.detail,
                    apns_id=result.apns_id,
                )
                for result in results
            ],
        )

    @router.post(
        "/push/call",
        response_model=PushCallResponse,
        dependencies=[Depends(require_scope(FORGE_PUSH_CALL_SCOPE))],
    )
    async def ring_owner(
        request: Request,
        data: PushCallRequest,
        principal: Principal = Depends(extract_principal),
    ) -> PushCallResponse:
        """Ring the credential's owner with a resident's ``call.request`` (a VoIP push).

        The frame must be a call request for ``request_id`` from the calling resident
        (``payload.session.id``), with a text of at most 600 characters, in a body of
        at most 4096 bytes (422 otherwise). Only the owner's VoIP tokens for the app's
        bundle are pushed; the owner's rings are capped per hour (429).
        """
        if not gateway.calls_enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Rings are off on this Forge")
        resident_id = _calling_resident(request)
        if len(await request.body()) > CALL_BODY_MAX_BYTES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"The body is larger than {CALL_BODY_MAX_BYTES} bytes",
            )
        try:
            call = validate_call_request(data.request_id, data.frame, now=gateway.now())
        except CallRequestError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if call.session_id.strip().casefold() != resident_id.casefold():
            # No claim or frame values in the log: the owner is enough to find the caller.
            logger.warning(
                "Refused a ring for owner %s: its frame names a session other than its resident",
                principal.user_id,
            )
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "A resident may only ring as itself (payload.session.id)"
            )
        try:
            result = await gateway.ring(principal.user_id, call)
        except CallRateLimitedError as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                str(exc),
                headers={"Retry-After": str(int(exc.retry_after_seconds) + 1)},
            ) from exc
        return PushCallResponse(
            sent=result.sent,
            tokens=result.tokens,
            results=[
                PushCallDevice(fingerprint=r.fingerprint, status=r.status, reason=r.reason)
                for r in result.results
            ],
        )

    return router
