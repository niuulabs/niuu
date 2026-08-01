"""Tests for PostgreSQL credential enrollment persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from volundr.adapters.outbound.postgres_credential_enrollments import (
    PostgresCredentialEnrollmentRepository,
)
from volundr.domain.models import CredentialEnrollment, CredentialEnrollmentState


def _enrollment() -> CredentialEnrollment:
    now = datetime.now(UTC)
    return CredentialEnrollment(
        id=uuid4(),
        connection_id=str(uuid4()),
        owner_id="user-1",
        tenant_id="tenant-1",
        provider_slug="codex",
        credential_name="codex-credentials",
        method="codex_device",
        state=CredentialEnrollmentState.AWAITING_USER,
        runner_ref={"sandbox_name": "enroll-1"},
        verification_uri="https://auth.openai.com/codex/device",
        user_code="ABCD-EFGH",
        expires_at=now + timedelta(minutes=15),
        error_code="",
        created_at=now,
        updated_at=now,
    )


async def test_save_uses_upsert_without_secret_payload() -> None:
    pool = AsyncMock()
    repository = PostgresCredentialEnrollmentRepository(pool)
    enrollment = _enrollment()

    result = await repository.save(enrollment)

    assert result is enrollment
    sql = pool.execute.await_args.args[0]
    assert "INSERT INTO credential_enrollments" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert "credential_data" not in sql
    assert "auth.json" not in str(pool.execute.await_args.args)


async def test_get_and_find_active_map_durable_state() -> None:
    enrollment = _enrollment()
    row_data = {
        "id": enrollment.id,
        "connection_id": enrollment.connection_id,
        "owner_id": enrollment.owner_id,
        "tenant_id": enrollment.tenant_id,
        "provider_slug": enrollment.provider_slug,
        "credential_name": enrollment.credential_name,
        "method": enrollment.method,
        "state": enrollment.state.value,
        "runner_ref": enrollment.runner_ref,
        "verification_uri": enrollment.verification_uri,
        "user_code": enrollment.user_code,
        "expires_at": enrollment.expires_at,
        "error_code": enrollment.error_code,
        "created_at": enrollment.created_at,
        "updated_at": enrollment.updated_at,
    }
    row = MagicMock()
    row.__getitem__ = lambda _self, key: row_data[key]
    pool = AsyncMock()
    pool.fetchrow.return_value = row
    repository = PostgresCredentialEnrollmentRepository(pool)

    fetched = await repository.get(enrollment.id)
    active = await repository.find_active(enrollment.connection_id)
    pool.fetch.return_value = [row]
    expired = await repository.list_expired_active(datetime.now(UTC))

    assert fetched == enrollment
    assert active == enrollment
    assert expired == [enrollment]
    assert "state IN ('pending', 'awaiting_user')" in pool.fetchrow.await_args_list[1].args[0]
    assert "expires_at <= $1" in pool.fetch.await_args.args[0]
