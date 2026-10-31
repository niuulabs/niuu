"""The Forge-hosted MCP endpoint: ``POST /api/v1/forge/mcp`` (Streamable HTTP, JSON).

External agents (Lexi, OpenClaw, a Claude Code or Codex outside Forge) reach the
same tools the injected session MCP has, authenticated with their *own*
credentials: a PAT or an Envoy-verified identity, or a ``forge_session`` token,
which keeps its scopes. Every tool call is a REST call into this same Forge app,
in process, carrying the caller's credential headers, so Forge's normal
authentication, owner checks and the session-token allow-list apply to each one.

The endpoint acts on the Forge node that serves it. On a Guild host the facade
forwards ``/api/v1/forge/mcp`` to the local (embedded) node, or to the node named
by ``?instance_id=`` for callers that are not session tokens.

``notify`` here is a feed-only direct submit (``POST /sessions/{id}/notifications``)
with a required ``idempotency_key``. A ``forge_session`` caller notifies its own
session by default and is recorded as ``source=agent``.
"""

from __future__ import annotations

import hashlib
import json
import socket
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from niuu.build_identity import build_identity
from niuu.domain.notifications import NotificationDraft
from niuu.domain.services.token_scope import FORGE_SESSION_TOKEN_USE
from niuu.forge_mcp.credentials import grants_from_scopes
from niuu.forge_mcp.http import StreamableHttpMcpServer
from niuu.forge_mcp.models import ForgeApiError, ForgeMcpGrant, ForgeMcpLimits, ForgeMcpNotifyMode
from niuu.forge_mcp.ports import ForgeClient, ForgeMcpHost
from niuu.forge_mcp.rest_client import FORGE_API_PREFIX, RestForgeClient
from niuu.forge_mcp.tools import ForgeMcpToolbox
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.forge_session_auth import session_token_in
from volundr.config import ForgeMcpHttpConfig
from volundr.domain.models import Principal

MCP_PATH = "/mcp"
#: Caller headers every in-process tool call carries.
_CREDENTIAL_HEADERS = (
    "authorization",
    "x-auth-user-id",
    "x-auth-email",
    "x-auth-tenant",
    "x-auth-roles",
)
_INTERNAL_BASE_URL = "http://forge.internal"
_DERIVED_KEY_PREFIX = "mcp-"
_DERIVED_KEY_HEX_CHARS = 32

INSTRUCTIONS = (
    "Forge tools for this Forge node: list and inspect sessions, read their "
    "transcripts and notifications, and `notify` a session owner through the "
    "Notifications feed (feed-only; always pass an idempotency_key). Tools that steer "
    "or start/stop other sessions need the caller's permission; with a Forge session "
    "credential they also need the session's operator grant."
)


def _caller_headers(request: Request) -> dict[str, str]:
    headers = {name: value for name in _CREDENTIAL_HEADERS if (value := request.headers.get(name))}
    if "authorization" not in headers:
        token = session_token_in(request.scope) or request.query_params.get("token", "")
        if token:
            headers["authorization"] = f"Bearer {token}"
    return headers


def _grants_for(principal: Principal) -> frozenset[ForgeMcpGrant]:
    """A session credential offers its own grants; a user may use every tool.

    Forge still authorizes each call as that user, so offering a tool never lets a
    caller do more than it could through the REST API.
    """
    if principal.token_use == FORGE_SESSION_TOKEN_USE:
        return grants_from_scopes(principal.scopes)
    return frozenset(ForgeMcpGrant)


class CallerForgeMcpHost(ForgeMcpHost):
    """The HTTP caller as the Forge MCP host: its identity and, maybe, its session."""

    def __init__(self, principal: Principal, client: ForgeClient) -> None:
        self._principal = principal
        self._client = client

    @property
    def session_id(self) -> str | None:
        return self._principal.bound_session_id

    async def environment(self) -> dict[str, Any]:
        principal = self._principal
        session_credential = principal.token_use == FORGE_SESSION_TOKEN_USE
        return {
            "transport": "streamable-http",
            "forge": {
                "node": {"hostname": socket.gethostname()},
                "api": FORGE_API_PREFIX,
                "mcp_endpoint": f"{FORGE_API_PREFIX}{MCP_PATH}",
                "runtime": build_identity(),
                "scope": "this node only",
            },
            "caller": {
                "user_id": principal.user_id,
                "tenant_id": principal.tenant_id,
                "credential": "forge_session" if session_credential else "user",
                "session_id": principal.bound_session_id,
                "scopes": list(principal.scopes) if session_credential else None,
            },
            "notify": "feed",
        }

    async def notify(self, draft: NotificationDraft) -> dict[str, Any]:
        """Feed-submit about the caller's own session, deduplicated by content."""
        if not self.session_id:
            raise ForgeApiError("notify needs a session: the caller has none", status=422)
        canonical = json.dumps(draft.model_dump(mode="json"), sort_keys=True)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        recorded = await self._client.submit_notification(
            self.session_id,
            draft,
            idempotency_key=f"{_DERIVED_KEY_PREFIX}{digest[:_DERIVED_KEY_HEX_CHARS]}",
            instance_id=None,
        )
        return {
            "notification_id": recorded.get("id"),
            "turn_id": None,
            "session_seq": recorded.get("session_seq"),
            "state": "committed",
        }


def create_forge_mcp_router(
    config: ForgeMcpHttpConfig,
    *,
    prefix: str = FORGE_API_PREFIX,
) -> APIRouter:
    """``POST {prefix}/mcp`` plus 405 answers for ``GET`` / ``DELETE``."""
    router = APIRouter(prefix=prefix, tags=["Forge MCP"])
    server = StreamableHttpMcpServer(
        allowed_origins=config.allowed_origins,
        max_body_bytes=config.max_body_bytes,
        instructions=INSTRUCTIONS,
    )
    limits = ForgeMcpLimits(
        list_default_limit=config.list_default_limit,
        list_max_limit=config.list_max_limit,
        transcript_default_turns=config.transcript_default_turns,
        transcript_max_turns=config.transcript_max_turns,
        transcript_turn_max_chars=config.transcript_turn_max_chars,
        output_max_chars=config.output_max_chars,
    )

    async def _read_body(request: Request) -> bytes | None:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > server.max_body_bytes:
            return None
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > server.max_body_bytes:
                return None
        return bytes(body)

    @router.post(MCP_PATH, include_in_schema=True)
    async def forge_mcp(request: Request) -> Response:
        """One MCP JSON-RPC message over Streamable HTTP (JSON response mode)."""
        rejected = server.reject_transport(request.headers)
        if rejected is not None:
            return JSONResponse(rejected.body, status_code=rejected.status)
        body = await _read_body(request)
        if body is None:
            too_large = server.body_too_large()
            return JSONResponse(too_large.body, status_code=too_large.status)
        principal = await extract_principal(request)
        app = request.app
        http_client: httpx.AsyncClient | None = None

        async def _client() -> httpx.AsyncClient:
            nonlocal http_client
            if http_client is None:
                http_client = httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url=_INTERNAL_BASE_URL
                )
            return http_client

        async def _toolbox() -> ForgeMcpToolbox:
            client = RestForgeClient(
                http_client=_client,
                headers=_caller_headers(request),
                timeout_s=config.request_timeout_seconds,
                single_node=True,
            )
            return ForgeMcpToolbox(
                client=client,
                host=CallerForgeMcpHost(principal, client),
                grants=_grants_for(principal),
                limits=limits,
                notify_mode=ForgeMcpNotifyMode.FEED,
            )

        try:
            answer = await server.handle(body, _toolbox)
        finally:
            if http_client is not None:
                await http_client.aclose()
        if answer.body is None:
            return Response(status_code=answer.status)
        return JSONResponse(answer.body, status_code=answer.status)

    async def _not_offered() -> Response:
        return Response(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            headers={"Allow": "POST"},
        )

    router.add_api_route(MCP_PATH, _not_offered, methods=["GET", "DELETE"], include_in_schema=False)
    return router
