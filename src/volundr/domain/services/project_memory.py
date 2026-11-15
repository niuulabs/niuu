"""The memory a project owns: owners add, change and remove mounts; every member lists them.

The same rules as the project's team integrations: a person who is not on the project is told
it does not exist, a member without the owner role is told what role the change needs. A mount
must resolve against the registry for the project's organization when it is added, so one cannot
be pointed at an entry another organization owns; the refusal for a missing and an unreachable
entry is one message.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from volundr.domain.models import Principal
from volundr.domain.project_memory import (
    MemoryAccess,
    MemoryMountNotFoundError,
    ProjectMemoryMount,
    ProjectMemoryRepository,
    ProjectMemoryResolver,
)
from volundr.domain.project_ports import ProjectAccessDeniedError
from volundr.domain.projects import ForgeProject, ProjectRole

if TYPE_CHECKING:
    from volundr.domain.services.projects import ProjectService


class ProjectMemoryService:
    def __init__(
        self,
        projects: ProjectService,
        repository: ProjectMemoryRepository,
        resolver: ProjectMemoryResolver,
    ) -> None:
        self._projects = projects
        self._repository = repository
        self._resolver = resolver

    @staticmethod
    def _acting_user(principal: Principal | None) -> str:
        if principal is None or not principal.user_id:
            raise ProjectAccessDeniedError("Sign in to manage project memory")
        return principal.user_id

    async def list(self, project_id: UUID, principal: Principal | None) -> list[ProjectMemoryMount]:
        """Every mount, for anyone on the project."""
        project = await self._projects.get(project_id, principal)
        return await self._repository.list_for_project(project.id)

    async def _owned(self, project_id: UUID, principal: Principal | None) -> ForgeProject:
        self._acting_user(principal)
        return await self._projects.get(project_id, principal, ProjectRole.OWNER)

    async def add(
        self,
        project_id: UUID,
        principal: Principal | None,
        *,
        name: str,
        registry_entry_id: str,
        access: MemoryAccess,
        write_prefixes: tuple[str, ...] = (),
    ) -> ProjectMemoryMount:
        project = await self._owned(project_id, principal)
        mount = ProjectMemoryMount(
            project_id=project.id,
            name=name,
            registry_entry_id=registry_entry_id,
            access=access,
            write_prefixes=write_prefixes,
            added_by=self._acting_user(principal),
        )
        await self._resolver.target(mount, tenant_id=project.tenant_id, owner_id=mount.added_by)
        return await self._repository.add(mount)

    async def change(
        self,
        project_id: UUID,
        principal: Principal | None,
        name: str,
        *,
        access: MemoryAccess,
        write_prefixes: tuple[str, ...] = (),
    ) -> ProjectMemoryMount:
        project = await self._owned(project_id, principal)
        existing = await self._repository.get(project.id, name)
        if existing is None:
            raise MemoryMountNotFoundError(f"The project has no memory mount named {name!r}")
        changed = ProjectMemoryMount.model_validate(
            {
                **existing.model_dump(),
                "access": access,
                "write_prefixes": write_prefixes,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._resolver.target(
            changed, tenant_id=project.tenant_id, owner_id=self._acting_user(principal)
        )
        updated = await self._repository.update(
            project.id, name, access=changed.access, write_prefixes=changed.write_prefixes
        )
        if updated is None:
            raise MemoryMountNotFoundError(f"The project has no memory mount named {name!r}")
        return updated

    async def remove(self, project_id: UUID, principal: Principal | None, name: str) -> None:
        project = await self._owned(project_id, principal)
        if not await self._repository.remove(project.id, name):
            raise MemoryMountNotFoundError(f"The project has no memory mount named {name!r}")
