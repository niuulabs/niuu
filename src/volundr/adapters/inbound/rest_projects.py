"""Additive project API. Sessions retain the ordinary Forge lifecycle and replay."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from volundr.domain.project_ports import ProjectConflictError
from volundr.domain.projects import ForgeProject, ProjectReceipt
from volundr.domain.services.projects import ProjectNotFoundError, ProjectService
from volundr.domain.services.session import SessionAccessDeniedError


class ProjectCheckout(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    workspace_path: str = Field(min_length=1, max_length=2048)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    status: Literal["active", "archived"] | None = None
    workspace_path: str | None = Field(default=None, max_length=2048)


async def project_result(operation):
    try:
        return await operation
    except ProjectNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProjectConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except SessionAccessDeniedError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            503, "Project checkout is unavailable; retry after restoring it"
        ) from exc


def create_projects_router(service: ProjectService, principal_for_request) -> APIRouter:
    router = APIRouter(prefix="/projects", tags=["Projects"])

    @router.get("")
    async def list_projects(request: Request) -> list[ForgeProject]:
        principal = await principal_for_request(request)
        return await project_result(service.list(principal))

    @router.post("", status_code=201)
    async def register_project(request: Request, project: ForgeProject) -> ForgeProject:
        if "id" not in project.model_fields_set:
            raise HTTPException(
                422, "Registration requires the stable project UUID from project.json"
            )
        principal = await principal_for_request(request)
        return await project_result(service.register(project, principal))

    @router.post("/discover")
    async def discover_project(request: Request, data: ProjectCheckout) -> ForgeProject:
        principal = await principal_for_request(request)
        return await project_result(service.discover(data.workspace_path, principal))

    @router.post("/connect", status_code=201)
    async def connect_project(request: Request, data: ProjectCheckout) -> ForgeProject:
        principal = await principal_for_request(request)
        return await project_result(service.connect(data.workspace_path, data.name, principal))

    @router.get("/{project_id}")
    async def get_project(request: Request, project_id: UUID) -> ForgeProject:
        principal = await principal_for_request(request)
        return await project_result(service.get(project_id, principal))

    @router.patch("/{project_id}")
    async def update_project(
        request: Request, project_id: UUID, data: ProjectUpdate
    ) -> ForgeProject:
        principal = await principal_for_request(request)
        changes = data.model_dump(exclude={"revision"}, exclude_unset=True)
        return await project_result(service.update(project_id, changes, data.revision, principal))

    @router.get("/{project_id}/context")
    async def project_context(request: Request, project_id: UUID) -> dict:
        principal = await principal_for_request(request)
        project = await project_result(service.get(project_id, principal))
        context, revision = await project_result(service.workspace.context(project))
        return {"project_id": project.id, "context": context, "revision": revision}

    @router.get("/{project_id}/receipts")
    async def receipts(
        request: Request,
        project_id: UUID,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict:
        principal = await principal_for_request(request)
        await project_result(service.get(project_id, principal))
        items = await service.repository.receipts(project_id, after, limit)
        return {"items": items, "next_cursor": items[-1]["seq"] if items else after}

    @router.post("/{project_id}/receipts", status_code=201)
    async def record_receipt(
        request: Request, project_id: UUID, data: ProjectReceipt
    ) -> ProjectReceipt:
        if data.project_id != project_id:
            raise HTTPException(422, "Receipt project_id must match its URL")
        principal = await principal_for_request(request)
        return await project_result(service.record(data, principal))

    @router.post("/{project_id}/receipts/{receipt_id}/ack")
    async def acknowledge(request: Request, project_id: UUID, receipt_id: UUID) -> dict:
        principal = await principal_for_request(request)
        await project_result(service.get(project_id, principal))
        if not await service.repository.acknowledge(project_id, receipt_id):
            raise HTTPException(404, "Receipt not found")
        return {"acknowledged": True}

    @router.post("/{project_id}/export")
    async def export_receipts(
        request: Request,
        project_id: UUID,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict:
        principal = await principal_for_request(request)
        return await project_result(service.export(project_id, principal, after=after, limit=limit))

    return router
