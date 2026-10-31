"""Additive project API. Sessions retain the ordinary Forge lifecycle and replay."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from volundr.domain.execution_catalog import ExecutionSelectionError
from volundr.domain.project_ports import ProjectConflictError
from volundr.domain.projects import (
    PROJECT_BRIEF_MAX_CHARS,
    ForgeProject,
    ProjectReceipt,
    SessionReference,
)
from volundr.domain.services.projects import UNCHANGED, ProjectNotFoundError, ProjectService
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
    brief: str | None = Field(default=None, max_length=PROJECT_BRIEF_MAX_CHARS)
    status: Literal["active", "archived"] | None = None
    repo_url: str | None = Field(default=None, max_length=2048)
    workspace_path: str | None = Field(default=None, max_length=2048)


class SessionProjectAssignment(BaseModel):
    """Attach, move, re-parent or detach. ``project_id: null`` detaches unless a local
    ``parent`` is named, in which case the session joins that parent's project.
    Omitting ``parent`` keeps the current one within a project (cleared on a move)."""

    model_config = ConfigDict(extra="forbid")
    project_id: UUID | None = None
    expected_revision: int = Field(ge=0)
    role: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    parent: SessionReference | None = None


def create_session_projects_router(service: ProjectService, principal_for_request) -> APIRouter:
    router = APIRouter(prefix="/sessions", tags=["Projects"])

    def membership(session):
        return {
            "session_id": session.id,
            "revision": session.coordination_revision,
            "coordination": session.coordination,
        }

    @router.get("/{session_id}/project")
    async def get_membership(request: Request, session_id: UUID) -> dict:
        principal = await principal_for_request(request)
        return membership(
            await project_result(service.session_membership(session_id, principal, access="update"))
        )

    @router.put("/{session_id}/project")
    async def assign_project(
        request: Request, session_id: UUID, data: SessionProjectAssignment
    ) -> dict:
        principal = await principal_for_request(request)
        session = await project_result(
            service.assign_session(
                session_id,
                data.project_id,
                data.expected_revision,
                principal,
                role=data.role,
                parent=data.parent if "parent" in data.model_fields_set else UNCHANGED,
            )
        )
        return membership(session)

    return router


async def project_result(operation):
    try:
        return await operation
    except ProjectNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProjectConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except SessionAccessDeniedError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ExecutionSelectionError:
        raise
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
        # A name is a complete request. Send a client-generated id to make retries
        # idempotent; a repository and checkout can be attached later.
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
        changes = {
            key: "" if value is None else value
            for key, value in data.model_dump(exclude={"revision"}, exclude_unset=True).items()
            if value is not None or key in {"description", "brief", "repo_url", "workspace_path"}
        }
        return await project_result(service.update(project_id, changes, data.revision, principal))

    @router.get("/{project_id}/context")
    async def project_context(request: Request, project_id: UUID) -> dict:
        principal = await principal_for_request(request)
        project = await project_result(service.get(project_id, principal))
        context, revision = await project_result(service.context(project))
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
