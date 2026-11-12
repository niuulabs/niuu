"""REST adapter for a session's memory context (under ``/api/v1/forge``).

``GET /sessions/{session_id}/memory-context`` tells the memory gateway which layers a session
has: its owner, tenant, project, workflow run and the project layer's write prefixes. The
gateway calls it on every request with the session's own ``forge_session`` credential, so
Forge's live-session check (session exists, same launch, not stopped or archived) is the
gateway's proof of the session.

* 200 the context (``project_memory`` is null when the session has no project)
* 401 the credential is revoked (session gone, restarted, stopped or archived)
* 403 not a ``forge_session`` credential, or one bound to another session. **This is a
  session-only route**: a person, a PAT or any other credential is refused even if it could
  read the session, because the answer is only meaningful as the session's own proof and a
  person has their own login at the gateway.
* 404 no such session
* 409 the session's project no longer exists
* 503 this Forge runs without projects
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field

from niuu.domain.services.token_scope import FORGE_MEMORY_SCOPE, require_scope
from volundr.adapters.inbound.forge_session_auth import (
    forge_session_claims,
    require_bound_session,
)
from volundr.domain.services.memory_context import (
    MemoryContextError,
    MemoryContextService,
    MemoryContextUnavailableError,
)
from volundr.domain.services.session import SessionService


class ProjectMemoryResponse(BaseModel):
    write_prefixes: list[str] = Field(
        description="Page path prefixes under which the session may write straight into the "
        "project's own memory layer; anything else becomes a proposal. Owner-set, may be empty."
    )


class MemoryContextResponse(BaseModel):
    """The layers of a session's memory, as Forge knows them."""

    session_id: UUID
    launch_id: str
    owner_id: str
    tenant_id: str
    project_id: UUID | None
    workflow_run_id: str | None
    project_memory: ProjectMemoryResponse | None


def create_memory_context_router(
    service: MemoryContextService,
    session_service: SessionService,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Memory"])

    @router.get(
        "/sessions/{session_id}/memory-context",
        response_model=MemoryContextResponse,
        dependencies=[Depends(require_scope(FORGE_MEMORY_SCOPE))],
        responses={
            403: {"description": "Not the session's own Forge session credential"},
            404: {"description": "No such session"},
            409: {"description": "The session's project no longer exists"},
            503: {"description": "This Forge runs without projects"},
        },
    )
    async def get_memory_context(
        request: Request,
        response: Response,
        session_id: UUID = Path(description="Session id"),
    ) -> MemoryContextResponse:
        """The session's owner, tenant, project, run and the project's write prefixes."""
        credential = forge_session_claims(request)
        if credential is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "The memory context is read only with the session's own Forge session "
                "credential; a person uses their own login at the memory gateway",
            )
        require_bound_session(request, session_id)
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        try:
            context = await service.for_session(session, credential)
        except MemoryContextUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        except MemoryContextError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        project_memory = context.project_memory
        return MemoryContextResponse(
            session_id=context.session_id,
            launch_id=context.launch_id,
            owner_id=context.owner_id,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            workflow_run_id=context.workflow_run_id,
            project_memory=(
                None
                if project_memory is None
                else ProjectMemoryResponse(write_prefixes=list(project_memory.write_prefixes))
            ),
        )

    return router
