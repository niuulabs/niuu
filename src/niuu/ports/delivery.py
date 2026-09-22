"""Ports for isolated Git delivery and authenticated evidence."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.delivery import (
    BranchPublicationReceipt,
    BranchPublicationRequest,
    CandidateEvidence,
    CheckReceipt,
    IntegrationCandidateInspection,
    IntegrationReceipt,
    MergeReceipt,
    MergeRequest,
    PublicationSource,
    ResolvedRef,
    ReviewCandidate,
    ReviewPublication,
    ReviewRequest,
    VerificationReceipt,
    WorkspaceAllocation,
    WorkstreamSpec,
)
from niuu.domain.models import Principal
from niuu.ports.evidence import EvidenceAuthenticator as EvidenceAuthenticator


class DeliveryAuthorizer(ABC):
    @abstractmethod
    async def authorize(
        self,
        principal: Principal,
        campaign_id: str | None,
        operation: str,
        *,
        repository: str,
        base_sha: str | None = None,
        candidate_sha: str | None = None,
        candidate_tree: str | None = None,
        target_branch: str | None = None,
        policy_id: str | None = None,
        credential: str | None = None,
    ) -> None:
        """Raise when this principal does not own the campaign/tenant operation."""


class WorkstreamRepository(ABC):
    @abstractmethod
    async def allocate(self, spec: WorkstreamSpec) -> WorkspaceAllocation: ...

    @abstractmethod
    async def verify(
        self,
        allocation: WorkspaceAllocation,
        *,
        attempt_id: str,
        candidate_sha: str,
        contract_id: str,
    ) -> VerificationReceipt: ...

    @abstractmethod
    async def integrate(
        self,
        integration: WorkspaceAllocation,
        *,
        expected_integration_head: str,
        evidence: CandidateEvidence,
    ) -> IntegrationReceipt: ...

    @abstractmethod
    async def publication_source(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
    ) -> PublicationSource: ...

    @abstractmethod
    async def inspect_integration(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
    ) -> IntegrationCandidateInspection: ...


class DeliveryForgeProvider(ABC):
    @abstractmethod
    async def resolve_ref(self, repository: str, ref: str) -> ResolvedRef: ...

    @abstractmethod
    async def ensure_review(self, request: ReviewRequest) -> ReviewPublication: ...

    @abstractmethod
    async def publish_branch(
        self,
        source: PublicationSource,
        request: BranchPublicationRequest,
    ) -> BranchPublicationReceipt: ...

    @abstractmethod
    async def inspect_delivery_candidate(
        self,
        repo_url: str,
        review_number: int,
        required_checks: tuple[str, ...],
    ) -> tuple[ReviewCandidate, CheckReceipt]: ...

    @abstractmethod
    async def conditional_merge(self, request: MergeRequest) -> MergeReceipt: ...

    @abstractmethod
    async def reconcile_merge(self, request: MergeRequest) -> MergeReceipt: ...
