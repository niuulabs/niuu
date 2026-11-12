"""REST adapter for a session's memory context (under ``/api/v1/forge``).

``GET /sessions/{session_id}/memory-context`` tells the memory gateway which layers a session
has: its owner, tenant, project, workflow run and the project layer's write prefixes, and what
it needs for the session's context pack: its model, objective, the project's brief, when this
owner's previous session in the project began (``since``) and the memories the launch attached
(``attached``). The gateway calls it on every request with the session's own ``forge_session``
credential, so Forge's live-session check (session exists, same launch, not stopped or
archived) is the gateway's proof of the session.

* 200 the context (``project_memory`` is null when the session has no project)
* 401 the credential is revoked (session gone, restarted, stopped or archived)
* 403 not a ``forge_session`` credential, or one bound to another session. **This is a
  session-only route**: a person, a PAT or any other credential is refused even if it could
  read the session, because the answer is only meaningful as the session's own proof and a
  person has their own login at the gateway.
* 404 no such session
* 409 the session's project no longer exists, or a memory it carries cannot be opened by the
  gateway (no registry entry); the message names the fix
* 503 this Forge runs without projects
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
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


class AttachedMemoryResponse(BaseModel):
    name: str = Field(description="The mount's name; the gateway's layer is attached:<name>")
    registry_entry_id: str = Field(description="The Mimir registry entry the gateway opens")
    access: Literal["read", "read_write"]
    write_prefixes: list[str] = Field(
        description="With read_write: the page prefixes the session may write; empty means any"
    )
    source: str = Field(
        description="Where the attachment comes from: project, launch_spec, or session (set "
        "by the session itself)"
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
    model: str | None = Field(description="The model the session was launched with")
    objective: str | None = Field(description="The session's coordination objective")
    project_brief: str | None = Field(description="The project's brief")
    since: datetime | None = Field(
        description="When this owner's previous session in the project began; null if none"
    )
    attached: list[AttachedMemoryResponse] = Field(
        description="Every memory mount the launch carries (project, launch spec, session)"
    )


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
            409: {
                "description": "The session's project is gone, or an attached memory is unopenable"
            },
            503: {"description": "This Forge runs without projects"},
        },
    )
    async def get_memory_context(
        request: Request,
        response: Response,
        session_id: UUID = Path(description="Session id"),
    ) -> MemoryContextResponse:
        """The session's owner, tenant, project, run, prefixes, brief and attached memories."""
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
            model=context.model,
            objective=context.objective,
            project_brief=context.project_brief,
            since=context.since,
            attached=[
                AttachedMemoryResponse(
                    name=mount.name,
                    registry_entry_id=mount.registry_entry_id,
                    access=mount.access.value,
                    write_prefixes=list(mount.write_prefixes),
                    source=mount.source,
                )
                for mount in context.attached
            ],
        )

    return router
