"""Typed REST surface for coordinator-scoped delivery operations."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.delivery import (
    AcceptancePolicy,
    BranchPublicationReceipt,
    BranchPublicationRequest,
    CandidateEvidence,
    CheckReceipt,
    EvidenceValidationError,
    EvidenceValidationReport,
    IntegrationCandidateInspection,
    IntegrationReceipt,
    MergeReceipt,
    MergeRequest,
    ResolvedRef,
    ReviewCandidate,
    ReviewPublication,
    ReviewRequest,
    VerificationReceipt,
    WorkspaceAllocation,
    WorkstreamSpec,
)
from niuu.domain.models import Principal
from niuu.ports.delivery import DeliveryAuthorizer
from volundr.domain.services.delivery import DeliveryPolicyNotFoundError, DeliveryService


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolveRefRequest(_Request):
    repository: str = Field(min_length=1)
    ref: str = Field(min_length=1)


class VerifyRequest(_Request):
    allocation: WorkspaceAllocation
    attempt_id: str = Field(min_length=1)
    candidate_sha: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)


class ValidateEvidenceRequest(_Request):
    evidence: CandidateEvidence
    policy_id: str = Field(min_length=1)


class DescribePolicyRequest(_Request):
    campaign_id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)


class PublishBranchRequest(_Request):
    allocation: WorkspaceAllocation
    integration_receipt: IntegrationReceipt
    publication: BranchPublicationRequest
    policy_id: str = Field(min_length=1)


class InspectIntegrationRequest(_Request):
    allocation: WorkspaceAllocation
    integration_receipt: IntegrationReceipt
    policy_id: str = Field(min_length=1)


class InspectIntegrationChainRequest(_Request):
    allocation: WorkspaceAllocation
    integration_receipts: tuple[IntegrationReceipt, ...] = Field(min_length=1)
    policy_id: str = Field(min_length=1)


class IntegrateRequest(ValidateEvidenceRequest):
    integration: WorkspaceAllocation
    expected_integration_head: str = Field(min_length=1)


class InspectRequest(_Request):
    campaign_id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    review_number: int = Field(gt=0)
    policy_id: str = Field(min_length=1)


class InspectResponse(BaseModel):
    candidate: ReviewCandidate
    checks: CheckReceipt


class ConditionalMergeRequest(ValidateEvidenceRequest):
    merge: MergeRequest


def create_delivery_router(
    service_for_principal: Callable[[Principal], DeliveryService | Awaitable[DeliveryService]],
    principal_for_request: Callable[[Request], Awaitable[Principal]],
    authorizer: DeliveryAuthorizer,
    *,
    prefix: str = "/api/v1/forge/delivery",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Delivery"])

    @router.post("/evidence/policy", response_model=AcceptancePolicy)
    async def describe_policy(
        http_request: Request, data: DescribePolicyRequest
    ) -> AcceptancePolicy:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.campaign_id,
            "validate_evidence",
            http_request,
            repository=data.repository,
            policy_id=data.policy_id,
        )
        try:
            return service.describe_policy(data.policy_id)
        except DeliveryPolicyNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/refs/resolve", response_model=ResolvedRef)
    async def resolve_ref(http_request: Request, data: ResolveRefRequest) -> ResolvedRef:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            None,
            "resolve_ref",
            http_request,
            repository=data.repository,
        )
        return await _invoke(service.resolve_ref(data.repository, data.ref))

    @router.post("/forge/reviews", response_model=ReviewPublication)
    async def open_review(http_request: Request, data: ReviewRequest) -> ReviewPublication:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.campaign_id,
            "open_review",
            http_request,
            repository=data.repository,
            candidate_sha=data.expected_head_sha,
            target_branch=data.target_branch,
        )
        return await _invoke(service.open_review(data))

    @router.post("/forge/branches", response_model=BranchPublicationReceipt)
    async def publish_branch(
        http_request: Request, data: PublishBranchRequest
    ) -> BranchPublicationReceipt:
        campaign_ids = {
            data.allocation.campaign_id,
            data.integration_receipt.campaign_id,
            data.publication.campaign_id,
        }
        repositories = {
            data.allocation.repository,
            data.integration_receipt.repository,
            data.publication.repository,
        }
        if len(campaign_ids) != 1 or len(repositories) != 1:
            raise HTTPException(422, "Publication identities must match")
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.publication.campaign_id,
            "publish_branch",
            http_request,
            repository=data.publication.repository,
            base_sha=data.allocation.base_sha,
            candidate_sha=data.integration_receipt.resulting_sha,
            candidate_tree=data.integration_receipt.resulting_tree,
            target_branch=data.publication.branch,
            policy_id=data.policy_id,
        )
        return await _invoke(
            service.publish_branch(
                data.allocation,
                data.integration_receipt,
                data.publication,
                policy_id=data.policy_id,
            )
        )

    @router.post(
        "/workspaces/allocate",
        response_model=WorkspaceAllocation,
        status_code=status.HTTP_201_CREATED,
    )
    async def allocate(http_request: Request, spec: WorkstreamSpec) -> WorkspaceAllocation:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            spec.campaign_id,
            "allocate_workstream",
            http_request,
            repository=spec.repository,
            base_sha=spec.base_sha,
        )
        return await _invoke(service.allocate_workstream(spec))

    @router.post("/workspaces/verify", response_model=VerificationReceipt)
    async def verify(http_request: Request, data: VerifyRequest) -> VerificationReceipt:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.allocation.campaign_id,
            "run_verification",
            http_request,
            repository=data.allocation.repository,
            base_sha=data.allocation.base_sha,
        )
        return await _invoke(
            service.run_verification(
                data.allocation,
                attempt_id=data.attempt_id,
                candidate_sha=data.candidate_sha,
                contract_id=data.contract_id,
            )
        )

    @router.post(
        "/workspaces/integration/inspect",
        response_model=IntegrationCandidateInspection,
    )
    async def inspect_integration(
        http_request: Request,
        data: InspectIntegrationRequest,
    ) -> IntegrationCandidateInspection:
        if (
            data.allocation.campaign_id != data.integration_receipt.campaign_id
            or data.allocation.repository != data.integration_receipt.repository
        ):
            raise HTTPException(422, "Integration inspection identities must match")
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.allocation.campaign_id,
            "inspect_integration",
            http_request,
            repository=data.allocation.repository,
            base_sha=data.allocation.base_sha,
        )
        return await _invoke(
            service.inspect_integration(
                data.allocation,
                data.integration_receipt,
                policy_id=data.policy_id,
            )
        )

    @router.post(
        "/workspaces/integration/inspect-chain",
        response_model=IntegrationCandidateInspection,
    )
    async def inspect_integration_chain(
        http_request: Request,
        data: InspectIntegrationChainRequest,
    ) -> IntegrationCandidateInspection:
        if any(
            data.allocation.campaign_id != receipt.campaign_id
            or data.allocation.repository != receipt.repository
            for receipt in data.integration_receipts
        ):
            raise HTTPException(422, "Integration chain identities must match")
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.allocation.campaign_id,
            "inspect_integration",
            http_request,
            repository=data.allocation.repository,
            base_sha=data.allocation.base_sha,
        )
        return await _invoke(
            service.inspect_integration_chain(
                data.allocation,
                data.integration_receipts,
                policy_id=data.policy_id,
            )
        )

    @router.post("/evidence/validate", response_model=EvidenceValidationReport)
    async def validate(
        http_request: Request, data: ValidateEvidenceRequest
    ) -> EvidenceValidationReport:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.evidence.campaign_id,
            "validate_evidence",
            http_request,
            repository=data.evidence.repository,
            base_sha=data.evidence.base_sha,
        )
        try:
            return service.validate_evidence(data.evidence, policy_id=data.policy_id)
        except DeliveryPolicyNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.post("/workspaces/integrate", response_model=IntegrationReceipt)
    async def integrate(http_request: Request, data: IntegrateRequest) -> IntegrationReceipt:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        if data.integration.campaign_id != data.evidence.campaign_id:
            raise HTTPException(422, "Integration and evidence campaign IDs must match")
        if data.integration.repository != data.evidence.repository:
            raise HTTPException(422, "Integration and evidence repositories must match")
        await _authorize(
            authorizer,
            principal,
            data.evidence.campaign_id,
            "integrate_candidate",
            http_request,
            repository=data.evidence.repository,
            base_sha=data.evidence.base_sha,
        )
        return await _invoke(
            service.integrate_candidate(
                data.integration,
                expected_integration_head=data.expected_integration_head,
                evidence=data.evidence,
                policy_id=data.policy_id,
            )
        )

    @router.post("/forge/inspect", response_model=InspectResponse)
    async def inspect(http_request: Request, data: InspectRequest) -> InspectResponse:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.campaign_id,
            "inspect_candidate",
            http_request,
            repository=data.repository,
        )
        candidate, checks = await _invoke(
            service.inspect_candidate(
                data.repository,
                data.review_number,
                policy_id=data.policy_id,
            )
        )
        return InspectResponse(candidate=candidate, checks=checks)

    @router.post("/forge/merge", response_model=MergeReceipt)
    async def merge(http_request: Request, data: ConditionalMergeRequest) -> MergeReceipt:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        if data.merge.campaign_id != data.evidence.campaign_id:
            raise HTTPException(422, "Merge and evidence campaign IDs must match")
        await _authorize(
            authorizer,
            principal,
            data.merge.campaign_id,
            "conditional_merge",
            http_request,
            repository=data.merge.repository,
            base_sha=data.merge.expected_base_sha,
            candidate_sha=data.evidence.candidate_sha,
            candidate_tree=data.evidence.candidate_tree,
            target_branch=data.merge.expected_target_branch,
            policy_id=data.policy_id,
        )
        return await _invoke(
            service.conditional_merge(
                data.merge,
                evidence=data.evidence,
                policy_id=data.policy_id,
            )
        )

    @router.post("/forge/reconcile", response_model=MergeReceipt)
    async def reconcile(http_request: Request, data: MergeRequest) -> MergeReceipt:
        principal, service = await _context(
            http_request, principal_for_request, service_for_principal
        )
        await _authorize(
            authorizer,
            principal,
            data.campaign_id,
            "reconcile_merge",
            http_request,
            repository=data.repository,
            base_sha=data.expected_base_sha,
        )
        return await _invoke(service.reconcile_merge(data))

    return router


async def _invoke[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except EvidenceValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (DeliveryPolicyNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


async def _context(
    request: Request,
    principal_for_request: Callable[[Request], Awaitable[Principal]],
    service_for_principal: Callable[[Principal], DeliveryService | Awaitable[DeliveryService]],
) -> tuple[Principal, DeliveryService]:
    principal = await principal_for_request(request)
    service = service_for_principal(principal)
    if inspect.isawaitable(service):
        service = await service
    return principal, service


async def _authorize(
    authorizer: DeliveryAuthorizer,
    principal: Principal,
    campaign_id: str | None,
    operation: str,
    request: Request,
    *,
    repository: str,
    base_sha: str | None = None,
    candidate_sha: str | None = None,
    candidate_tree: str | None = None,
    target_branch: str | None = None,
    policy_id: str | None = None,
) -> None:
    try:
        authorization = request.headers.get("authorization", "")
        credential = authorization if authorization.lower().startswith("bearer ") else None
        await authorizer.authorize(
            principal,
            campaign_id,
            operation,
            repository=repository,
            base_sha=base_sha,
            candidate_sha=candidate_sha,
            candidate_tree=candidate_tree,
            target_branch=target_branch,
            policy_id=policy_id,
            credential=credential,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
