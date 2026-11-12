"""REST surface of the memory gateway (contract C2).

``GET {base}/memory/tools`` and ``POST {base}/memory/tools/{name}`` on the Mímir
API prefixes. Both authenticate through Forge, not through Mímir's identity
adapter: the caller presents its session's ``forge_session`` credential, the
gateway reads the session id from the token *without verifying it* (only to
know which session to ask about) and asks Forge for that session's memory
context with the same token. Forge's 200 is the verification; any other answer,
or none, is a 401. Nothing here is ever served anonymously.

The app's ``enforce_identity`` middleware skips exactly these two routes
(:func:`is_memory_tool_route`), and nothing else.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from mimir.memory.gateway import LayerUnavailableError, MemoryGateway, Session
from mimir.memory.systems import MemorySystems, TenantNotConfiguredError
from mimir.memory.tools import MemoryTools, UnknownToolError, instructions
from mimir.ports.session_directory import SessionDirectory, SessionNotConfirmedError
from niuu.domain.services.forge_session_token import SESSION_ID_CLAIM

logger = logging.getLogger(__name__)

_LIST = re.compile(r"(?:/api/v1)?/mimir/memory/tools")
_CALL = re.compile(r"(?:/api/v1)?/mimir/memory/tools/[A-Za-z0-9_]+")
#: A session id is only ever placed in Forge's URL path; nothing else may ride along.
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


def is_memory_tool_route(method: str, path: str) -> bool:
    """True for exactly the two C2 routes, which authenticate through Forge."""
    return (method == "GET" and _LIST.fullmatch(path) is not None) or (
        method == "POST" and _CALL.fullmatch(path) is not None
    )


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="memory tools need the session's Forge credential as a Bearer token",
        )
    return token.strip()


def _session_id(token: str) -> str:
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="the bearer token is not a JWT") from None
    session_id = claims.get(SESSION_ID_CLAIM)
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise HTTPException(
            status_code=401,
            detail=f"the bearer token names no session ({SESSION_ID_CLAIM}); use the session's "
            "Forge credential",
        )
    return session_id


class MemoryRouter:
    """Args:
    gateway: the memory gateway.
    directory: asks Forge who a session is.
    tenant_id: when this Mímir belongs to one tenant, sessions of any other are refused.
    systems: the memory systems to close with the router.
    """

    def __init__(
        self,
        gateway: MemoryGateway,
        directory: SessionDirectory,
        *,
        tenant_id: str = "",
        systems: MemorySystems | None = None,
    ) -> None:
        self._gateway = gateway
        self._directory = directory
        self._systems = systems
        self._tools = MemoryTools(gateway)
        self._tenant_id = tenant_id

    async def _session(self, request: Request) -> Session:
        token = _bearer(request)
        session_id = _session_id(token)
        try:
            context = await self._directory.memory_context(session_id, token)
        except SessionNotConfirmedError as exc:
            logger.info("memory: Forge did not confirm session %s: %s", session_id, exc.reason)
            raise HTTPException(
                status_code=401, detail=f"Forge did not confirm this session: {exc.reason}"
            ) from exc
        if self._tenant_id and context.tenant_id != self._tenant_id:
            raise HTTPException(
                status_code=403, detail="Knowledge instance belongs to a different tenant"
            )
        try:
            return await self._gateway.open(context)
        except TenantNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except LayerUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    def router(self) -> APIRouter:
        router = APIRouter(tags=["Memory"])

        @router.get("/memory/tools")
        async def list_tools(request: Request) -> dict[str, Any]:
            session = await self._session(request)
            return {"instructions": instructions(session), "tools": self._tools.definitions()}

        @router.post("/memory/tools/{name}")
        async def call_tool(name: str, request: Request) -> JSONResponse:
            session = await self._session(request)
            if not self._tools.exists(name):
                raise HTTPException(status_code=404, detail=f"no memory tool {name!r}")
            try:
                arguments = await request.json()
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="the body must be the tool's arguments as JSON"
                ) from None
            try:
                result = await self._tools.call(session, name, arguments)
            except UnknownToolError:
                raise HTTPException(status_code=404, detail=f"no memory tool {name!r}") from None
            return JSONResponse(result)

        return router

    async def close(self) -> None:
        await self._directory.close()
        if self._systems is not None:
            await self._systems.close()
