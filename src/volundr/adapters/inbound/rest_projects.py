"""Additive project API. Sessions retain the ordinary Forge lifecycle and replay."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from niuu.domain.agent_directory import AgentSkill
from niuu.domain.services.token_scope import FORGE_SESSION_LIFECYCLE_SCOPE, require_scope
from volundr.domain.execution_catalog import ExecutionSelectionError
from volundr.domain.project_memory import (
    MemoryDirectoryUnavailableError,
    MemoryMountClashError,
)
from volundr.domain.project_ports import (
    ProjectAccessDeniedError,
    ProjectConflictError,
    ProjectMemberNotFoundError,
)
from volundr.domain.projects import (
    PROJECT_BRIEF_MAX_CHARS,
    ForgeProject,
    ProjectMember,
    ProjectMemberCandidate,
    ProjectReceipt,
    ProjectRole,
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


class ProjectMemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_id: str = Field(min_length=1)
    role: ProjectRole
    expertise: str = ""
    skills: list[AgentSkill] = Field(default_factory=list)


class ProjectMemberChange(BaseModel):
    """A member's role (owners only) and/or their expertise and skills (the member or an
    owner). Omitted fields are left as they are."""

    model_config = ConfigDict(extra="forbid")
    role: ProjectRole | None = None
    expertise: str | None = None
    skills: list[AgentSkill] | None = None

    @model_validator(mode="after")
    def names_a_change(self):
        if self.role is None and self.expertise is None and self.skills is None:
            raise ValueError("Name a role, expertise or skills to change")
        return self


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
    except (SessionAccessDeniedError, ProjectAccessDeniedError) as exc:
        raise HTTPException(403, str(exc)) from exc
    except ProjectMemberNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ExecutionSelectionError:
        raise
    except MemoryMountClashError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MemoryDirectoryUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
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

    # A session credential reads a project only to create a session in it (lifecycle).
    @router.get(
        "/{project_id}", dependencies=[Depends(require_scope(FORGE_SESSION_LIFECYCLE_SCOPE))]
    )
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
        await project_result(service.get(project_id, principal, ProjectRole.CONTRIBUTOR))
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

    members = service.member_service

    @router.get("/{project_id}/members")
    async def list_members(request: Request, project_id: UUID) -> list[ProjectMember]:
        principal = await principal_for_request(request)
        return await project_result(members.list(project_id, principal))

    @router.get("/{project_id}/member-candidates")
    async def member_candidates(
        request: Request, project_id: UUID, q: str = Query("")
    ) -> list[ProjectMemberCandidate]:
        """Active people of the project's org who are not on it yet."""
        principal = await principal_for_request(request)
        return await project_result(members.candidates(project_id, principal, q))

    @router.post("/{project_id}/members", status_code=201)
    async def add_member(
        request: Request, project_id: UUID, data: ProjectMemberAdd
    ) -> ProjectMember:
        principal = await principal_for_request(request)
        return await project_result(
            members.add(
                project_id,
                principal,
                user_id=data.user_id,
                role=data.role,
                expertise=data.expertise,
                skills=data.skills,
            )
        )

    @router.patch("/{project_id}/members/{user_id}")
    async def change_member(
        request: Request, project_id: UUID, user_id: str, data: ProjectMemberChange
    ) -> ProjectMember:
        principal = await principal_for_request(request)
        return await project_result(
            members.change(
                project_id,
                principal,
                user_id,
                role=data.role,
                expertise=data.expertise,
                skills=data.skills,
            )
        )

    @router.delete("/{project_id}/members/{user_id}", status_code=204)
    async def remove_member(request: Request, project_id: UUID, user_id: str) -> None:
        principal = await principal_for_request(request)
        await project_result(members.remove(project_id, principal, user_id))

    return router
