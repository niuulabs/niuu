"""Verification and route allow-list for ``forge_session`` credentials.

A Forge session's broker holds a session-bound token (``token_use =
forge_session``) for the Forge MCP. Forge verifies it itself, in every identity
mode, including allow-all/mini mode where no other bearer is checked:

1. **Verify.** Signature, expiry, issuer and audience, then revocation against
   the live session row: the session must exist, still belong to the token's
   owner, be on the token's launch, and not be stopped or archived. Any failure
   is a 401. An invalid session token never falls back to the anonymous dev
   principal.
2. **Allow-list.** Only the routes in
   :data:`niuu.domain.services.forge_session_policy.ROUTE_POLICIES` accept a
   session token. Every other route answers 403, so a route added later is closed
   to session tokens until someone decides otherwise.
3. **Scope and binding.** Each allowed route names the scope it needs and how its
   ``{session_id}`` relates to the token's own session: the session itself
   (``OWN``), any session of the same owner (``OWNED``), or any *other* session of
   the same owner (``PEER``).

The verified claims are stored on the request (``request.state.forge_session``);
``extract_principal`` turns them into the principal, so the owner-scoped listing
and access checks apply to the token as a non-admin user.

The middleware wraps the whole Volundr app, so it also guards requests the Guild
facade forwards to an embedded Forge.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import HTTPException, Request, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from niuu.domain.services.forge_session_policy import (
    ROUTE_POLICIES,
    Binding,
    RoutePolicy,
    SessionPolicyRefusedError,
    admitted_route,
    match_policy,
)
from niuu.domain.services.forge_session_token import ForgeSessionClaims, ForgeSessionTokenError
from niuu.domain.services.token_scope import OPENSHELL_SESSION_TOKEN_USE
from niuu.forge_mcp.credentials import is_forge_session_token
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.forge_session_launch import launch_id_of

__all__ = [
    "ROUTE_POLICIES",
    "Binding",
    "ForgeSessionAuthMiddleware",
    "RoutePolicy",
    "authorize",
    "forge_session_claims",
    "match_policy",
    "require_bound_session",
    "session_token_in",
]

STATE_KEY = "forge_session"
_TOKEN_QUERY_PARAM = "token"
_BEARER_PREFIX = "bearer "
#: WebSocket close codes mirroring HTTP 401 / 403 (the 4xxx range is application-defined).
_WS_UNAUTHORIZED = 4401
_WS_FORBIDDEN = 4403
#: A WebSocket close reason must fit a 125-byte control frame.
_WS_REASON_CHARS = 120
#: A session credential is revoked once its session reaches one of these states.
_REVOKED_STATUSES = frozenset({SessionStatus.STOPPED, SessionStatus.ARCHIVED})


class ForgeSessionDeniedError(Exception):
    """A session-token request is refused. ``status_code`` is 401, 403 or 404."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _presented_tokens(scope: Scope) -> list[str]:
    tokens: list[str] = []
    for name, value in scope.get("headers") or ():
        if name.lower() == b"authorization":
            text = value.decode("latin-1").strip()
            if text.lower().startswith(_BEARER_PREFIX):
                tokens.append(text[len(_BEARER_PREFIX) :].strip())
    query = scope.get("query_string") or b""
    for key, value in parse_qsl(query.decode("latin-1"), keep_blank_values=False):
        if key == _TOKEN_QUERY_PARAM:
            tokens.append(value)
    return tokens


def session_token_in(scope: Scope) -> str:
    """The ``forge_session`` token a request presents, or ``""``.

    Envoy accepts a bearer in the Authorization header and in ``?token=``, so
    both are inspected; a session token in either one makes this a session-token
    request, whatever else the other carries.
    """
    for token in _presented_tokens(scope):
        if is_forge_session_token(token):
            return token
    return ""


def _session_uuid(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        raise ForgeSessionDeniedError(status.HTTP_403_FORBIDDEN, "Not a session id") from None


SessionLookup = Callable[[UUID], Awaitable[Session | None]]


async def authorize(
    claims: ForgeSessionClaims,
    *,
    method: str,
    path: str,
    get_session: SessionLookup,
) -> None:
    """Revocation, allow-list, scope and binding checks for one verified token."""
    bound = await get_session(UUID(claims.session_id))
    _check_not_revoked(claims, bound)

    try:
        policy, params = admitted_route(method, path, claims.scopes)
    except SessionPolicyRefusedError as refusal:
        raise ForgeSessionDeniedError(status.HTTP_403_FORBIDDEN, str(refusal)) from refusal
    if policy.binding is Binding.NONE:
        return
    target_id = _session_uuid(params.get("session_id", ""))
    own = str(target_id) == claims.session_id
    if policy.binding is Binding.OWN:
        if not own:
            raise ForgeSessionDeniedError(
                status.HTTP_403_FORBIDDEN,
                "This Forge session token is bound to another session",
            )
        return
    if policy.binding is Binding.PEER and own:
        raise ForgeSessionDeniedError(
            status.HTTP_403_FORBIDDEN,
            "A session cannot use this route on itself",
        )
    target = bound if own else await get_session(target_id)
    if target is None:
        raise ForgeSessionDeniedError(status.HTTP_404_NOT_FOUND, f"Session not found: {target_id}")
    if target.owner_id != claims.owner_id:
        raise ForgeSessionDeniedError(
            status.HTTP_403_FORBIDDEN,
            "A Forge session token may only act on sessions of the same owner",
        )


def _check_not_revoked(claims: ForgeSessionClaims, bound: Session | None) -> None:
    if bound is None:
        raise ForgeSessionDeniedError(
            status.HTTP_401_UNAUTHORIZED, "Forge session token revoked: its session is gone"
        )
    if bound.owner_id != claims.owner_id:
        raise ForgeSessionDeniedError(
            status.HTTP_401_UNAUTHORIZED, "Forge session token revoked: session owner changed"
        )
    if launch_id_of(bound.workload_config) != claims.launch_id:
        raise ForgeSessionDeniedError(
            status.HTTP_401_UNAUTHORIZED,
            "Forge session token revoked: the session has been restarted since it was issued",
        )
    if bound.status in _REVOKED_STATUSES:
        raise ForgeSessionDeniedError(
            status.HTTP_401_UNAUTHORIZED,
            f"Forge session token revoked: the session is {bound.status.value}",
        )


class ForgeSessionAuthMiddleware:
    """Verify and gate every request that presents a ``forge_session`` token.

    Requests with any other credential (or none) pass through untouched. The token
    service and session service are read from ``app.state`` at request time
    (``forge_session_tokens``, ``session_service``); without a token service a
    session token cannot be verified and is refused.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        token = session_token_in(scope)
        if not token:
            await self._app(scope, receive, send)
            return
        try:
            claims = await self._authorize(scope, token)
        except ForgeSessionDeniedError as denied:
            await _refuse(scope, receive, send, denied)
            return
        scope.setdefault("state", {})[STATE_KEY] = claims
        await self._app(scope, receive, send)

    async def _authorize(self, scope: Scope, token: str) -> ForgeSessionClaims:
        state = getattr(scope.get("app"), "state", None)
        tokens = getattr(state, "forge_session_tokens", None)
        if tokens is None:
            raise ForgeSessionDeniedError(
                status.HTTP_401_UNAUTHORIZED,
                "Forge session tokens are not enabled on this Forge (forge_mcp.session_tokens)",
            )
        try:
            claims = tokens.verify(token)
        except ForgeSessionTokenError as exc:
            raise ForgeSessionDeniedError(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        sessions = getattr(state, "session_service", None)
        if sessions is None:
            raise ForgeSessionDeniedError(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Forge is not ready to verify session tokens"
            )
        method = "GET" if scope["type"] == "websocket" else str(scope.get("method", ""))
        await authorize(
            claims,
            method=method,
            path=str(scope.get("path", "")),
            get_session=sessions.get_session,
        )
        return claims


async def _refuse(
    scope: Scope, receive: Receive, send: Send, denied: ForgeSessionDeniedError
) -> None:
    unauthorized = denied.status_code == status.HTTP_401_UNAUTHORIZED
    if scope["type"] == "websocket":
        code = _WS_UNAUTHORIZED if unauthorized else _WS_FORBIDDEN
        close = WebSocketClose(code=code, reason=denied.detail[:_WS_REASON_CHARS])
        await close(scope, receive, send)
        return
    response = JSONResponse(
        {"detail": denied.detail},
        status_code=denied.status_code,
        headers={"WWW-Authenticate": "Bearer"} if unauthorized else None,
    )
    await response(scope, receive, send)


def forge_session_claims(request: Request) -> ForgeSessionClaims | None:
    """The verified session-token claims of this request, if it presented one."""
    claims = getattr(request.state, STATE_KEY, None)
    return claims if isinstance(claims, ForgeSessionClaims) else None


def presents_unverified_session_token(request: Request) -> bool:
    """True when a session token is presented but the middleware did not verify it."""
    if forge_session_claims(request) is not None:
        return False
    return bool(session_token_in(request.scope))


def require_bound_session(request: Request, session_id: UUID) -> None:
    """A session-bound credential may only write to its own session.

    Covers both kinds: an OpenShell session token (bound through Envoy's
    ``x-auth-workload-session-id`` header) and a verified ``forge_session`` token.
    Other callers are not restricted here.
    """
    claims = forge_session_claims(request)
    if claims is not None and claims.session_id != str(session_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This Forge session token is bound to another session",
        )
    if request.headers.get("x-auth-token-use") != OPENSHELL_SESSION_TOKEN_USE:
        return
    if request.headers.get("x-auth-workload-session-id") == str(session_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="OpenShell workload token is not bound to this session",
    )
