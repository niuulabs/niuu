"""Port for resolving a workload-identity owner_id's real tenant.

A workload-identity mapping that derives ``owner_id`` per-caller
(``owner_id_claim`` — see ``niuu.config_models.WorkloadIdentityMappingConfig``)
cannot also guess that caller's tenant from a single static value on the
mapping: the mapping matches every caller of one subject/subject_prefix
shape, but each caller (e.g. each deployed resident) may belong to a
different tenant. The tenant is a fact owned by whichever service actually
created that principal (for a resident, Völundr's ``resident_runtimes``
table) — this port lets ``niuu.domain.services.workload_identity
.WorkloadIdentityService`` ask for it without importing that service
directly, preserving the module-boundary direction (niuu cannot import
volundr).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class OwnerTenantResolverPort(ABC):
    """Resolve the tenant_id that durably owns a given owner_id."""

    @abstractmethod
    async def tenant_id_for_owner(self, owner_id: str) -> str | None:
        """Return the tenant_id owning *owner_id*, or ``None`` if unknown.

        ``None`` is a real, expected answer (no matching durable record) —
        callers must treat it as "cannot resolve", not coerce it to a
        default tenant.
        """
