"""REST API for memory imports: a project's tickets brought into its memory.

Every route is the project owner's, with their own login: a session, a scoped workload
credential or a workload acting for someone is refused. Ownership is asked of the project's
Forge, as a workflow launch asks it about a project. A caller who is not on the project is
told it was not found, never that it exists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.dispatch import resolve_volundr_factory
from ting.domain.memory_imports import (
    CONNECTOR_LINEAR,
    ImportConnectionError,
    ImportRunResult,
    MemoryImport,
    MemoryImportExistsError,
)
from ting.domain.services.memory_imports import MemoryImportNotFoundError, MemoryImportService
from ting.ports.volundr import VolundrFactory

#: Role on a Forge project that manages its memory imports.
OWNER_ROLE = "owner"


class MemoryImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID = Field(description="The Forge project whose memory the tickets feed")
    connector: Literal["linear"] = Field(default=CONNECTOR_LINEAR)
    connection_id: str = Field(
        min_length=1, description="The project's team integration to read with"
    )
    tracker_project_id: str = Field(
        min_length=1, description="The tracker's id of what to read (a Linear project id)"
    )
    compile: bool = Field(
        description="Start the memory-compile workflow when a run brings new sources"
    )


class MemoryImportResponse(BaseModel):
    id: UUID
    project_id: UUID
    owner_id: str
    connector: str
    connection_id: str
    tracker_project_id: str
    compile: bool
    cursor: datetime | None
    created_at: datetime
    last_run_at: datetime | None
    last_error: str
    pending_source_ids: list[str]


class MemoryImportRunResponse(BaseModel):
    read: int
    new_sources: int
    unchanged: int
    cursor: datetime | None
    compile_run: str | None
    new_source_ids: list[str]


def _response(record: MemoryImport) -> MemoryImportResponse:
    return MemoryImportResponse(
        id=record.id,
        project_id=record.project_id,
        owner_id=record.owner_id,
        connector=record.connector,
        connection_id=record.connection_id,
        tracker_project_id=record.tracker_project_id,
        compile=record.compile,
        cursor=record.cursor,
        created_at=record.created_at,
        last_run_at=record.last_run_at,
        last_error=record.last_error,
        pending_source_ids=list(record.pending_source_ids),
    )


def _run_response(result: ImportRunResult) -> MemoryImportRunResponse:
    return MemoryImportRunResponse(
        read=result.read,
        new_sources=result.new_sources,
        unchanged=result.unchanged,
        cursor=result.cursor,
        compile_run=result.compile_run,
        new_source_ids=list(result.new_source_ids),
    )


async def resolve_memory_import_service(request: Request) -> MemoryImportService:
    service = getattr(request.app.state, "memory_import_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory imports are not enabled: set memory_imports.enabled: true",
        )
    return service


async def person_login(principal: Principal = Depends(extract_principal)) -> Principal:
    """The caller, who must be a person using their own login."""
    if principal.token_use or principal.scopes or principal.actor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Memory imports answer only to a person's own login, not to a session or "
            "another scoped or delegated credential",
        )
    return principal


async def _require_project_owner(
    project_id: UUID,
    principal: Principal,
    bearer_token: str | None,
    volundr_factory: VolundrFactory,
) -> str:
    """Refuse unless the caller owns the project on one of their Forges.

    Returns the project's org as that Forge names it: an import writes into the memory of
    the project's org, never into whatever org the caller's own login carries.
    """
    forges = await volundr_factory.for_principal(principal)
    if not forges:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No Volundr connection is available for this user",
        )
    role: str | None = None
    for forge in forges:
        try:
            role = await forge.get_project_role(
                str(project_id), auth_token=bearer_token, principal=principal
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Forge could not tell your role on project {project_id}: {exc}",
            ) from exc
        if role is not None:
            break
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if role != OWNER_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a project owner manages the project's memory imports",
        )
    try:
        project = await forge.get_project(
            str(project_id), auth_token=bearer_token, principal=principal
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Forge could not read project {project_id}: {exc}",
        ) from exc
    if project is None or not project.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Forge did not name the org of project {project_id}, so the import cannot "
                "know whose memory to write into"
            ),
        )
    return project.tenant_id


def create_memory_imports_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/memory/imports", tags=["Memory imports"])

    async def owned_import(
        import_id: UUID,
        principal: Principal,
        bearer_token: str | None,
        volundr_factory: VolundrFactory,
        service: MemoryImportService,
    ) -> MemoryImport:
        record = await service.get(import_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Memory import not found")
        await _require_project_owner(record.project_id, principal, bearer_token, volundr_factory)
        return record

    @router.post("", response_model=MemoryImportResponse, status_code=status.HTTP_201_CREATED)
    async def create_import(
        body: MemoryImportBody,
        principal: Principal = Depends(person_login),
        bearer_token: str | None = Depends(extract_bearer_token),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
        service: MemoryImportService = Depends(resolve_memory_import_service),
    ) -> MemoryImportResponse:
        tenant_id = await _require_project_owner(
            body.project_id, principal, bearer_token, volundr_factory
        )
        try:
            record = await service.create(
                project_id=body.project_id,
                tenant_id=tenant_id,
                owner_id=principal.user_id,
                connector=body.connector,
                connection_id=body.connection_id,
                tracker_project_id=body.tracker_project_id,
                compile=body.compile,
            )
        except ImportConnectionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MemoryImportExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _response(record)

    @router.get("", response_model=list[MemoryImportResponse])
    async def list_imports(
        project_id: UUID = Query(description="The project whose imports to list"),
        principal: Principal = Depends(person_login),
        bearer_token: str | None = Depends(extract_bearer_token),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
        service: MemoryImportService = Depends(resolve_memory_import_service),
    ) -> list[MemoryImportResponse]:
        await _require_project_owner(project_id, principal, bearer_token, volundr_factory)
        return [_response(record) for record in await service.list_for_project(project_id)]

    @router.delete("/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_import(
        import_id: UUID,
        principal: Principal = Depends(person_login),
        bearer_token: str | None = Depends(extract_bearer_token),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
        service: MemoryImportService = Depends(resolve_memory_import_service),
    ) -> Response:
        await owned_import(import_id, principal, bearer_token, volundr_factory, service)
        if not await service.delete(import_id):
            raise HTTPException(status_code=404, detail="Memory import not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/{import_id}/run", response_model=MemoryImportRunResponse)
    async def run_import(
        import_id: UUID,
        principal: Principal = Depends(person_login),
        bearer_token: str | None = Depends(extract_bearer_token),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
        service: MemoryImportService = Depends(resolve_memory_import_service),
    ) -> MemoryImportRunResponse:
        """Run the import now: the same code the loop runs, once."""
        await owned_import(import_id, principal, bearer_token, volundr_factory, service)
        try:
            result = await service.run(import_id)
        except MemoryImportNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Memory import not found") from exc
        except Exception as exc:
            # The service recorded the failure on the import, logged it and counted it.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"The import failed: {type(exc).__name__}: {exc}",
            ) from exc
        return _run_response(result)

    return router
