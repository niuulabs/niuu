"""Port for the records that let a workload act for its owner."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from niuu.domain.models import WorkloadDelegation


class WorkloadDelegationExistsError(ValueError):
    """An active delegation already names this workload for a different owner."""


class WorkloadDelegationRepository(ABC):
    """Persistence for :class:`~niuu.domain.models.WorkloadDelegation` records."""

    @abstractmethod
    async def get_active(self, verifier: str, subject: str) -> WorkloadDelegation | None:
        """The unrevoked delegation for this workload, or ``None``."""

    @abstractmethod
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
        """Record a delegation; idempotent for the same owner.

        Raises :class:`WorkloadDelegationExistsError` when an active record names
        the workload for a different owner: moving a workload between owners is a
        revoke and a new create, never a silent overwrite.
        """

    @abstractmethod
    async def list(
        self, *, tenant_id: str, owner_id: str | None = None
    ) -> list[WorkloadDelegation]:
        """Active delegations in a tenant, optionally only one owner's."""

    @abstractmethod
    async def get(self, delegation_id: UUID) -> WorkloadDelegation | None:
        """One delegation by id, revoked or not."""

    @abstractmethod
    async def revoke(self, delegation_id: UUID) -> WorkloadDelegation | None:
        """Revoke an active delegation; ``None`` when there is no active one."""
