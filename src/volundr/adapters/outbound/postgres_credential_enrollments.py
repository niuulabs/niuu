"""PostgreSQL adapter for durable credential enrollment attempts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from volundr.domain.models import CredentialEnrollment, CredentialEnrollmentState
from volundr.domain.ports import CredentialEnrollmentRepository


class PostgresCredentialEnrollmentRepository(CredentialEnrollmentRepository):
    """Raw-SQL credential enrollment repository."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        await self._pool.execute(
            """
            INSERT INTO credential_enrollments
                (id, connection_id, owner_id, tenant_id, provider_slug, credential_name,
                 method, state, runner_ref, verification_uri, user_code, expires_at,
                 error_code, created_at, updated_at)
            VALUES
                ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12,
                 $13, $14, $15)
            ON CONFLICT (id) DO UPDATE SET
                state = EXCLUDED.state,
                runner_ref = EXCLUDED.runner_ref,
                verification_uri = EXCLUDED.verification_uri,
                user_code = EXCLUDED.user_code,
                expires_at = EXCLUDED.expires_at,
                error_code = EXCLUDED.error_code,
                updated_at = EXCLUDED.updated_at
            """,
            enrollment.id,
            enrollment.connection_id,
            enrollment.owner_id,
            enrollment.tenant_id,
            enrollment.provider_slug,
            enrollment.credential_name,
            enrollment.method,
            enrollment.state.value,
            json.dumps(enrollment.runner_ref),
            enrollment.verification_uri,
            enrollment.user_code,
            enrollment.expires_at,
            enrollment.error_code,
            enrollment.created_at,
            enrollment.updated_at,
        )
        return enrollment

    async def get(self, enrollment_id: UUID) -> CredentialEnrollment | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM credential_enrollments WHERE id = $1",
            enrollment_id,
        )
        return self._row_to_enrollment(row) if row is not None else None

    async def find_active(self, connection_id: str) -> CredentialEnrollment | None:
        row = await self._pool.fetchrow(
            """
            SELECT * FROM credential_enrollments
            WHERE connection_id = $1::uuid
              AND state IN ('pending', 'awaiting_user')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            connection_id,
        )
        return self._row_to_enrollment(row) if row is not None else None

    async def list_expired_active(self, now: datetime) -> list[CredentialEnrollment]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM credential_enrollments
            WHERE state IN ('pending', 'awaiting_user')
              AND expires_at <= $1
            ORDER BY expires_at
            LIMIT 100
            """,
            now,
        )
        return [self._row_to_enrollment(row) for row in rows]

    @staticmethod
    def _row_to_enrollment(row: asyncpg.Record) -> CredentialEnrollment:
        runner_ref_raw = row["runner_ref"]
        runner_ref = (
            json.loads(runner_ref_raw)
            if isinstance(runner_ref_raw, str)
            else dict(runner_ref_raw or {})
        )
        expires_at = row["expires_at"]
        created_at = row["created_at"]
        updated_at = row["updated_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return CredentialEnrollment(
            id=row["id"],
            connection_id=str(row["connection_id"]),
            owner_id=row["owner_id"],
            tenant_id=row["tenant_id"],
            provider_slug=row["provider_slug"],
            credential_name=row["credential_name"],
            method=row["method"],
            state=CredentialEnrollmentState(row["state"]),
            runner_ref=runner_ref,
            verification_uri=row["verification_uri"],
            user_code=row["user_code"],
            expires_at=expires_at,
            error_code=row["error_code"],
            created_at=created_at,
            updated_at=updated_at,
        )
