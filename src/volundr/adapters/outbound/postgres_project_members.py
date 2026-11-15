"""PostgreSQL store of the people on Forge projects (raw SQL, asyncpg)."""

from __future__ import annotations

import json
from uuid import UUID

from niuu.domain.agent_directory import AgentSkill
from volundr.domain.project_ports import (
    LastOwnerError,
    ProjectConflictError,
    ProjectMemberRepository,
)
from volundr.domain.projects import ProjectMember, ProjectMemberCandidate, ProjectRole

_MEMBER_SELECT = (
    "SELECT m.project_id, m.user_id, m.tenant_id, m.role, m.expertise, m.skills, m.added_by, "
    "m.created_at, m.updated_at, COALESCE(u.display_name, '') AS display_name, "
    "COALESCE(u.email, '') AS email "
    "FROM project_members m LEFT JOIN users u ON u.id = m.user_id "
)


def _skills_json(skills: list[AgentSkill]) -> str:
    return json.dumps([skill.model_dump(mode="json", by_alias=True) for skill in skills])


class PostgresProjectMemberRepository(ProjectMemberRepository):
    def __init__(self, pool=None, **_kwargs):
        self._pool = pool

    @staticmethod
    def _member(row) -> ProjectMember:
        skills = row["skills"]
        if isinstance(skills, str):
            skills = json.loads(skills)
        return ProjectMember(
            project_id=row["project_id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            role=ProjectRole(row["role"]),
            expertise=row["expertise"],
            skills=[AgentSkill.model_validate(skill) for skill in skills],
            added_by=row["added_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            display_name=row["display_name"],
            email=row["email"],
        )

    async def list_for_project(self, project_id: UUID) -> list[ProjectMember]:
        rows = await self._pool.fetch(
            _MEMBER_SELECT + "WHERE m.project_id = $1 ORDER BY m.created_at, m.user_id",
            project_id,
        )
        return [self._member(row) for row in rows]

    async def list_for_user(self, user_id: str, tenant_id: str) -> list[ProjectMember]:
        rows = await self._pool.fetch(
            _MEMBER_SELECT + "WHERE m.user_id = $1 AND m.tenant_id = $2 ORDER BY m.created_at",
            user_id,
            tenant_id,
        )
        return [self._member(row) for row in rows]

    async def get(self, project_id: UUID, user_id: str) -> ProjectMember | None:
        row = await self._pool.fetchrow(
            _MEMBER_SELECT + "WHERE m.project_id = $1 AND m.user_id = $2", project_id, user_id
        )
        return self._member(row) if row else None

    async def add(self, member: ProjectMember) -> ProjectMember:
        inserted = await self._pool.fetchrow(
            "INSERT INTO project_members (project_id, user_id, tenant_id, role, expertise, "
            "skills, added_by, created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9) "
            "ON CONFLICT (project_id, user_id) DO NOTHING RETURNING user_id",
            member.project_id,
            member.user_id,
            member.tenant_id,
            member.role.value,
            member.expertise,
            _skills_json(member.skills),
            member.added_by,
            member.created_at,
            member.updated_at,
        )
        if inserted is None:
            raise ProjectConflictError("That person is already a member of this project")
        stored = await self.get(member.project_id, member.user_id)
        if stored is None:
            raise ProjectConflictError("The member was removed while it was being added")
        return stored

    @staticmethod
    async def _lock_owners(connection, project_id: UUID) -> set[str]:
        """Lock the project's owner rows so concurrent demotions are serialized."""
        rows = await connection.fetch(
            "SELECT user_id FROM project_members WHERE project_id = $1 AND role = 'owner' "
            "FOR UPDATE",
            project_id,
        )
        return {row["user_id"] for row in rows}

    async def update(
        self,
        project_id: UUID,
        user_id: str,
        *,
        role: ProjectRole | None = None,
        expertise: str | None = None,
        skills: list[AgentSkill] | None = None,
    ) -> ProjectMember | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                owners = await self._lock_owners(connection, project_id)
                if role is not None and role != ProjectRole.OWNER and owners == {user_id}:
                    raise LastOwnerError(
                        "A project needs an owner; make someone else an owner first"
                    )
                row = await connection.fetchrow(
                    "UPDATE project_members SET role = COALESCE($3, role), "
                    "expertise = COALESCE($4, expertise), "
                    "skills = COALESCE($5::jsonb, skills), updated_at = NOW() "
                    "WHERE project_id = $1 AND user_id = $2 RETURNING user_id",
                    project_id,
                    user_id,
                    role.value if role is not None else None,
                    expertise,
                    _skills_json(skills) if skills is not None else None,
                )
        if row is None:
            return None
        return await self.get(project_id, user_id)

    async def remove(self, project_id: UUID, user_id: str) -> bool:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                owners = await self._lock_owners(connection, project_id)
                if owners == {user_id}:
                    raise LastOwnerError(
                        "A project needs an owner; make someone else an owner first"
                    )
                removed = await connection.fetchrow(
                    "DELETE FROM project_members WHERE project_id = $1 AND user_id = $2 "
                    "RETURNING user_id",
                    project_id,
                    user_id,
                )
        return removed is not None

    async def candidates(
        self, project_id: UUID, tenant_id: str, query: str
    ) -> list[ProjectMemberCandidate]:
        rows = await self._pool.fetch(
            "SELECT u.id, u.display_name, u.email FROM users u "
            "JOIN tenant_memberships t ON t.user_id = u.id "
            "WHERE t.tenant_id = $2 AND u.status = 'active' "
            "AND NOT EXISTS (SELECT 1 FROM project_members m "
            "WHERE m.project_id = $1 AND m.user_id = u.id) "
            "AND ($3 = '' OR strpos(lower(u.display_name), lower($3)) > 0 "
            "OR strpos(lower(u.email), lower($3)) > 0) "
            "ORDER BY u.display_name, u.email",
            project_id,
            tenant_id,
            query,
        )
        return [
            ProjectMemberCandidate(
                user_id=row["id"], display_name=row["display_name"], email=row["email"]
            )
            for row in rows
        ]
