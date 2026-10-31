"""REST adapter for workload delegations: which workload may act for which owner.

Mounted by the host that serves the workload exchange (niuu-shared). A record lets
an ``owner_from: delegation`` mapping admit one workload, e.g. a resident's own
ServiceAccount, as its owner with the workload as actor. Owners delegate to
themselves; ``volundr:admin`` may delegate for anyone in the tenant. A delegated
caller (a token carrying an actor) may never manage delegations: a workload must
not be able to grant itself, or another workload, more reach.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.models import Principal, WorkloadDelegation
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.ports.workload_delegations import (
    WorkloadDelegationExistsError,
    WorkloadDelegationRepository,
)

ADMIN_ROLE = "volundr:admin"


class CreateWorkloadDelegationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    verifier: str = Field(min_length=1, description="Exchange verifier of the workload's cluster")
    subject: str = Field(min_length=1, description="Workload proof subject (ServiceAccount)")
    name: str = Field(default="", max_length=200)
    resident_id: str = Field(default="", alias="residentId", max_length=200)
    owner_id: str = Field(
        default="",
        alias="ownerId",
        description="Owner the workload acts for; defaults to the caller (admin only otherwise)",
    )


class WorkloadDelegationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    verifier: str
    subject: str
    owner_id: str = Field(serialization_alias="ownerId")
    tenant_id: str = Field(serialization_alias="tenantId")
    name: str
    resident_id: str = Field(serialization_alias="residentId")
    created_by: str = Field(serialization_alias="createdBy")
    created_at: str = Field(serialization_alias="createdAt")
    revoked_at: str | None = Field(default=None, serialization_alias="revokedAt")

    @classmethod
    def of(cls, record: WorkloadDelegation) -> WorkloadDelegationResponse:
        return cls(
            id=str(record.id),
            verifier=record.verifier,
            subject=record.subject,
            owner_id=record.owner_id,
            tenant_id=record.tenant_id,
            name=record.name,
            resident_id=record.resident_id,
            created_by=record.created_by,
            created_at=record.created_at.isoformat(),
            revoked_at=record.revoked_at.isoformat() if record.revoked_at else None,
        )


def create_workload_delegations_router(
    extract_principal: Callable[..., Awaitable[Principal]],
    *,
    prefix: str = "/api/v1/tokens",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Workload identity"])

    def repository(request: Request) -> WorkloadDelegationRepository:
        repo = getattr(request.app.state, "workload_delegations", None)
        if repo is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Workload delegations are not configured on this host",
            )
        return repo

    def owner_principal(principal: Principal = Depends(extract_principal)) -> Principal:
        if principal.actor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A delegated caller cannot manage workload delegations",
            )
        return principal

    def is_admin(principal: Principal) -> bool:
        return ADMIN_ROLE in principal.roles

    @router.get("/workload/delegations", response_model=list[WorkloadDelegationResponse])
    async def list_delegations(
        request: Request,
        principal: Principal = Depends(owner_principal),
        resident_id: str | None = Query(default=None, alias="residentId"),
    ) -> list[WorkloadDelegationResponse]:
        records = await repository(request).list(
            tenant_id=principal.tenant_id,
            owner_id=None if is_admin(principal) else principal.user_id,
        )
        if resident_id:
            records = [record for record in records if record.resident_id == resident_id]
        return [WorkloadDelegationResponse.of(record) for record in records]

    @router.post(
        "/workload/delegations",
        response_model=WorkloadDelegationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_delegation(
        body: CreateWorkloadDelegationRequest,
        request: Request,
        principal: Principal = Depends(owner_principal),
    ) -> WorkloadDelegationResponse:
        owner_id = body.owner_id.strip() or principal.user_id
        if owner_id != principal.user_id and not is_admin(principal):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an admin may delegate a workload to another owner",
            )
        service: WorkloadIdentityService | None = getattr(
            request.app.state, "workload_identity_service", None
        )
        verifier, subject = body.verifier.strip(), body.subject.strip()
        if service is None or service.delegation_mapping_for(verifier, subject) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"No owner_from: delegation mapping admits {subject!r} on {verifier!r}; "
                    "the record could never be used"
                ),
            )
        try:
            record = await repository(request).create(
                verifier=verifier,
                subject=subject,
                owner_id=owner_id,
                tenant_id=principal.tenant_id,
                created_by=principal.user_id,
                name=body.name.strip(),
                resident_id=body.resident_id.strip(),
            )
        except WorkloadDelegationExistsError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return WorkloadDelegationResponse.of(record)

    @router.delete("/workload/delegations/{delegation_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_delegation(
        delegation_id: UUID,
        request: Request,
        principal: Principal = Depends(owner_principal),
    ) -> Response:
        repo = repository(request)
        record = await repo.get(delegation_id)
        visible = record is not None and record.tenant_id == principal.tenant_id
        if not visible or (record.owner_id != principal.user_id and not is_admin(principal)):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        await repo.revoke(delegation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
