"""REST routes for the memory a project owns (``/projects/{id}/memory``).

Every member lists the mounts; project owners add, change and remove them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from volundr.domain.project_memory import (
    MOUNT_NAME_PATTERN,
    MemoryAccess,
    MemoryDirectoryUnavailableError,
    MemoryMountExistsError,
    MemoryMountNotFoundError,
    ProjectMemoryError,
    ProjectMemoryMount,
)
from volundr.domain.project_ports import ProjectAccessDeniedError
from volundr.domain.services.project_memory import ProjectMemoryService
from volundr.domain.services.projects import ProjectNotFoundError


@contextmanager
def memory_http_errors() -> Iterator[None]:
    """Map project memory failures to HTTP: 404 hidden, 403 role, 409 taken, 422 fix."""
    try:
        yield
    except (ProjectNotFoundError, MemoryMountNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProjectAccessDeniedError as exc:
        raise HTTPException(403, str(exc)) from exc
    except MemoryMountExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MemoryDirectoryUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (ProjectMemoryError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


class MemoryMountResponse(BaseModel):
    name: str
    registry_entry_id: str
    access: MemoryAccess
    write_prefixes: list[str]
    added_by: str = Field(description="The owner who added it")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, mount: ProjectMemoryMount) -> MemoryMountResponse:
        return cls(
            name=mount.name,
            registry_entry_id=mount.registry_entry_id,
            access=mount.access,
            write_prefixes=list(mount.write_prefixes),
            added_by=mount.added_by,
            created_at=mount.created_at,
            updated_at=mount.updated_at,
        )


class MemoryMountCreate(BaseModel):
    name: str = Field(
        pattern=MOUNT_NAME_PATTERN,
        description="The mount's name in the project; sessions see it as mimir-<name>",
    )
    registry_entry_id: str = Field(min_length=1, max_length=200)
    access: MemoryAccess = MemoryAccess.READ
    write_prefixes: list[str] = Field(
        default_factory=list,
        description="Page prefixes a read_write mount may write; empty means any page",
    )


class MemoryMountChange(BaseModel):
    access: MemoryAccess
    write_prefixes: list[str] = Field(default_factory=list)


def create_project_memory_router(service: ProjectMemoryService, principal_for_request) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}/memory", tags=["Projects"])

    @router.get("")
    async def list_mounts(request: Request, project_id: UUID) -> list[MemoryMountResponse]:
        principal = await principal_for_request(request)
        with memory_http_errors():
            return [MemoryMountResponse.of(m) for m in await service.list(project_id, principal)]

    @router.post("", status_code=201)
    async def add_mount(
        request: Request, project_id: UUID, data: MemoryMountCreate
    ) -> MemoryMountResponse:
        principal = await principal_for_request(request)
        with memory_http_errors():
            mount = await service.add(
                project_id,
                principal,
                name=data.name,
                registry_entry_id=data.registry_entry_id,
                access=data.access,
                write_prefixes=tuple(data.write_prefixes),
            )
        return MemoryMountResponse.of(mount)

    @router.patch("/{name}")
    async def change_mount(
        request: Request, project_id: UUID, name: str, data: MemoryMountChange
    ) -> MemoryMountResponse:
        principal = await principal_for_request(request)
        with memory_http_errors():
            mount = await service.change(
                project_id,
                principal,
                name,
                access=data.access,
                write_prefixes=tuple(data.write_prefixes),
            )
        return MemoryMountResponse.of(mount)

    @router.delete("/{name}", status_code=204)
    async def remove_mount(request: Request, project_id: UUID, name: str) -> None:
        principal = await principal_for_request(request)
        with memory_http_errors():
            await service.remove(project_id, principal, name)

    return router
