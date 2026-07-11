"""PostgreSQL adapter for user persistence."""

from __future__ import annotations

import asyncpg

from identity.models import TenantMembership, TenantRole, User, UserStatus
from identity.ports import UserRepository


class PostgresUserRepository(UserRepository):
    """PostgreSQL implementation of UserRepository."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def _row_to_user(self, row: asyncpg.Record) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            status=UserStatus(row["status"]),
            home_pvc=row["home_pvc"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_membership(self, row: asyncpg.Record) -> TenantMembership:
        return TenantMembership(
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            role=TenantRole(row["role"]),
            granted_at=row["granted_at"],
        )

    async def create(self, user: User) -> User:
        row = await self._pool.fetchrow(
            """
            INSERT INTO users (id, email, display_name, status, home_pvc)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            user.id,
            user.email,
            user.display_name,
            user.status.value,
            user.home_pvc,
        )
        return self._row_to_user(row)

    async def get(self, user_id: str) -> User | None:
        row = await self._pool.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if row is None:
            return None
        return self._row_to_user(row)

    async def get_by_email(self, email: str) -> User | None:
        row = await self._pool.fetchrow("SELECT * FROM users WHERE email = $1", email)
        if row is None:
            return None
        return self._row_to_user(row)

    async def list(self) -> list[User]:
        rows = await self._pool.fetch("SELECT * FROM users ORDER BY created_at DESC")
        return [self._row_to_user(r) for r in rows]

    async def update(self, user: User) -> User:
        row = await self._pool.fetchrow(
            """
            UPDATE users SET email = $2, display_name = $3, status = $4,
                             home_pvc = $5, updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            user.id,
            user.email,
            user.display_name,
            user.status.value,
            user.home_pvc,
        )
        if row is None:
            raise ValueError(f"User {user.id} not found")
        return self._row_to_user(row)

    async def delete(self, user_id: str) -> bool:
        result = await self._pool.execute("DELETE FROM users WHERE id = $1", user_id)
        return result == "DELETE 1"

    async def add_membership(self, membership: TenantMembership) -> TenantMembership:
        row = await self._pool.fetchrow(
            """
            INSERT INTO tenant_memberships (user_id, tenant_id, role)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, tenant_id) DO UPDATE SET role = $3
            RETURNING *
            """,
            membership.user_id,
            membership.tenant_id,
            membership.role.value,
        )
        return self._row_to_membership(row)

    async def get_memberships(self, user_id: str) -> list[TenantMembership]:
        rows = await self._pool.fetch(
            "SELECT * FROM tenant_memberships WHERE user_id = $1",
            user_id,
        )
        return [self._row_to_membership(r) for r in rows]

    async def get_members(self, tenant_id: str) -> list[TenantMembership]:
        rows = await self._pool.fetch(
            "SELECT * FROM tenant_memberships WHERE tenant_id = $1",
            tenant_id,
        )
        return [self._row_to_membership(r) for r in rows]

    async def remove_membership(self, user_id: str, tenant_id: str) -> bool:
        result = await self._pool.execute(
            "DELETE FROM tenant_memberships WHERE user_id = $1 AND tenant_id = $2",
            user_id,
            tenant_id,
        )
        return result == "DELETE 1"
