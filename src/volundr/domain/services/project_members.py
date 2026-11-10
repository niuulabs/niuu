"""Who is on a Forge project and with which role.

Only members of the project's own org (tenant) can be added; the identity provider owns
who is in the org, this service owns who shares the project. Platform roles are never
read or granted here. An owner (or a tenant admin) manages members and may grant any
project role, ``owner`` included; a member may edit their own expertise and skills.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from identity.models import UserStatus
from niuu.domain.agent_directory import AgentSkill
from volundr.domain.models import Principal
from volundr.domain.project_ports import (
    MemberNotAllowedError,
    ProjectAccessDeniedError,
    ProjectMemberNotFoundError,
)
from volundr.domain.projects import (
    ForgeProject,
    ProjectMember,
    ProjectMemberCandidate,
    ProjectRole,
)

if TYPE_CHECKING:
    from volundr.domain.services.projects import ProjectService


class ProjectMemberService:
    def __init__(self, projects: ProjectService) -> None:
        self._projects = projects

    @staticmethod
    def _acting_user(principal: Principal | None) -> str:
        if principal is None or not principal.user_id:
            raise ProjectAccessDeniedError("Sign in to manage project members")
        return principal.user_id

    async def list(self, project_id: UUID, principal: Principal | None) -> list[ProjectMember]:
        """Every member, for anyone on the project."""
        await self._projects.get(project_id, principal)
        return await self._projects.members.list_for_project(project_id)

    async def candidates(
        self, project_id: UUID, principal: Principal | None, query: str
    ) -> list[ProjectMemberCandidate]:
        """Active members of the project's org who are not yet on it, for owners."""
        self._acting_user(principal)
        project = await self._projects.get(project_id, principal, ProjectRole.OWNER)
        return await self._projects.members.candidates(project.id, project.tenant_id, query.strip())

    async def _require_org_member(self, project: ForgeProject, user_id: str) -> None:
        """Refuse anyone who is not an active member of the project's tenant, generically."""
        user = await self._projects.users.get(user_id)
        if user is None or user.status != UserStatus.ACTIVE or not project.tenant_id:
            raise MemberNotAllowedError("Cannot add this person to the project")
        memberships = await self._projects.users.get_memberships(user_id)
        if not any(m.tenant_id == project.tenant_id for m in memberships):
            raise MemberNotAllowedError("Cannot add this person to the project")

    async def add(
        self,
        project_id: UUID,
        principal: Principal | None,
        *,
        user_id: str,
        role: ProjectRole,
        expertise: str = "",
        skills: list[AgentSkill] | None = None,
    ) -> ProjectMember:
        acting = self._acting_user(principal)
        project = await self._projects.get(project_id, principal, ProjectRole.OWNER)
        await self._require_org_member(project, user_id)
        now = datetime.now(UTC)
        return await self._projects.members.add(
            ProjectMember(
                project_id=project.id,
                user_id=user_id,
                tenant_id=project.tenant_id,
                role=role,
                expertise=expertise,
                skills=skills or [],
                added_by=acting,
                created_at=now,
                updated_at=now,
            )
        )

    async def change(
        self,
        project_id: UUID,
        principal: Principal | None,
        user_id: str,
        *,
        role: ProjectRole | None = None,
        expertise: str | None = None,
        skills: list[AgentSkill] | None = None,
    ) -> ProjectMember:
        """Change a member's role (owners) and/or expertise and skills (the member or an owner)."""
        acting = self._acting_user(principal)
        project = await self._projects.get(project_id, principal)
        own_profile_only = role is None and acting == user_id
        if not own_profile_only:
            project = await self._projects.get(project_id, principal, ProjectRole.OWNER)
        updated = await self._projects.members.update(
            project.id, user_id, role=role, expertise=expertise, skills=skills
        )
        if updated is None:
            raise ProjectMemberNotFoundError("That person is not a member of this project")
        return updated

    async def remove(self, project_id: UUID, principal: Principal | None, user_id: str) -> None:
        self._acting_user(principal)
        project = await self._projects.get(project_id, principal, ProjectRole.OWNER)
        if not await self._projects.members.remove(project.id, user_id):
            raise ProjectMemberNotFoundError("That person is not a member of this project")
