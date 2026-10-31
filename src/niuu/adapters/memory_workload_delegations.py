"""In-memory workload delegations, for tests and hosts without a database."""

from __future__ import annotations

import builtins
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.domain.models import WorkloadDelegation
from niuu.ports.workload_delegations import (
    WorkloadDelegationExistsError,
    WorkloadDelegationRepository,
)


class InMemoryWorkloadDelegationRepository(WorkloadDelegationRepository):
    def __init__(self) -> None:
        self._records: dict[UUID, WorkloadDelegation] = {}

    async def get_active(self, verifier: str, subject: str) -> WorkloadDelegation | None:
        return next(
            (
                record
                for record in self._records.values()
                if record.verifier == verifier
                and record.subject == subject
                and record.revoked_at is None
            ),
            None,
        )

    async def create(
        self,
        *,
        verifier: str,
        subject: str,
        owner_id: str,
        tenant_id: str,
        created_by: str,
        name: str = "",
        resident_id: str = "",
    ) -> WorkloadDelegation:
        existing = await self.get_active(verifier, subject)
        if existing is not None:
            if existing.owner_id != owner_id:
                raise WorkloadDelegationExistsError(
                    f"{subject!r} on {verifier!r} already acts for another owner"
                )
            return existing
        record = WorkloadDelegation(
            id=uuid4(),
            verifier=verifier,
            subject=subject,
            owner_id=owner_id,
            tenant_id=tenant_id,
            created_by=created_by,
            created_at=datetime.now(UTC),
            name=name,
            resident_id=resident_id,
        )
        self._records[record.id] = record
        return record

    async def list(
        self, *, tenant_id: str, owner_id: str | None = None
    ) -> builtins.list[WorkloadDelegation]:
        return sorted(
            (
                record
                for record in self._records.values()
                if record.tenant_id == tenant_id
                and record.revoked_at is None
                and (owner_id is None or record.owner_id == owner_id)
            ),
            key=lambda record: record.created_at,
        )

    async def get(self, delegation_id: UUID) -> WorkloadDelegation | None:
        return self._records.get(delegation_id)

    async def revoke(self, delegation_id: UUID) -> WorkloadDelegation | None:
        record = self._records.get(delegation_id)
        if record is None or record.revoked_at is not None:
            return None
        revoked = replace(record, revoked_at=datetime.now(UTC))
        self._records[delegation_id] = revoked
        return revoked
