"""Provider-neutral orchestration for evidence-backed delivery operations."""

from __future__ import annotations

from niuu.domain.delivery import (
    AcceptancePolicy,
    BranchPublicationReceipt,
    BranchPublicationRequest,
    CandidateEvidence,
    CheckReceipt,
    EvidenceValidationError,
    EvidenceValidationReport,
    IntegratedCandidateIdentity,
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
    evidence_payload,
)
from niuu.domain.services.delivery import EvidenceVerifier
from niuu.ports.delivery import (
    DeliveryForgeProvider,
    EvidenceAuthenticator,
    WorkstreamRepository,
)


class DeliveryPolicyNotFoundError(ValueError):
    """A caller referenced a policy that is not registered by deployment config."""


class DeliveryService:
    """Expose narrow operations; command argv and policy bodies never come from callers."""

    def __init__(
        self,
        *,
        workstreams: WorkstreamRepository,
        forge: DeliveryForgeProvider,
        verifier: EvidenceVerifier,
        authenticator: EvidenceAuthenticator,
        policies: dict[str, AcceptancePolicy],
        producer_id: str,
    ) -> None:
        if not policies:
            raise ValueError("At least one delivery acceptance policy is required")
        self._workstreams = workstreams
        self._forge = forge
        self._verifier = verifier
        self._authenticator = authenticator
        self._policies = dict(policies)
        self._producer_id = producer_id

    async def resolve_ref(self, repository: str, ref: str) -> ResolvedRef:
        return await self._forge.resolve_ref(repository, ref)

    def describe_policy(self, policy_id: str) -> AcceptancePolicy:
        """Expose configured acceptance requirements without mutable policy input."""
        return self._policy(policy_id)

    async def allocate_workstream(self, spec: WorkstreamSpec) -> WorkspaceAllocation:
        return await self._workstreams.allocate(spec)

    async def open_review(self, request: ReviewRequest) -> ReviewPublication:
        publication = await self._forge.ensure_review(request)
        provenance = self._authenticator.sign(evidence_payload(publication), self._producer_id)
        return publication.model_copy(update={"provenance": provenance})

    async def publish_branch(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
        request: BranchPublicationRequest,
        *,
        policy_id: str,
    ) -> BranchPublicationReceipt:
        self._require_integration_receipt(allocation, receipt, policy_id=policy_id)
        if (
            allocation.campaign_id != request.campaign_id
            or receipt.campaign_id != request.campaign_id
            or allocation.repository != request.repository
            or receipt.repository != request.repository
            or receipt.resulting_sha != request.expected_head_sha
            or allocation.branch_name != request.branch
        ):
            raise EvidenceValidationError("Publication identities do not match")
        source = await self._workstreams.publication_source(allocation, receipt)
        publication = await self._forge.publish_branch(source, request)
        publication_provenance = self._authenticator.sign(
            evidence_payload(publication), self._producer_id
        )
        return publication.model_copy(update={"provenance": publication_provenance})

    async def inspect_integration(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
        *,
        policy_id: str,
    ) -> IntegrationCandidateInspection:
        self._require_integration_receipt(allocation, receipt, policy_id=policy_id)
        return await self._workstreams.inspect_integration(allocation, receipt)

    async def inspect_integration_chain(
        self,
        allocation: WorkspaceAllocation,
        receipts: tuple[IntegrationReceipt, ...],
        *,
        policy_id: str,
    ) -> IntegrationCandidateInspection:
        if not receipts:
            raise EvidenceValidationError("Integration receipt chain is empty")
        previous = allocation.base_sha
        workstream_keys: set[str] = set()
        attempt_ids: set[str] = set()
        identities: list[IntegratedCandidateIdentity] = []
        for receipt in receipts:
            self._require_integration_receipt(allocation, receipt, policy_id=policy_id)
            if receipt.previous_integration_sha != previous:
                raise EvidenceValidationError("Integration receipt chain is discontinuous")
            if receipt.workstream_key in workstream_keys or receipt.attempt_id in attempt_ids:
                raise EvidenceValidationError("Integration receipt chain contains a duplicate")
            workstream_keys.add(receipt.workstream_key)
            attempt_ids.add(receipt.attempt_id)
            identities.append(
                IntegratedCandidateIdentity(
                    workstream_key=receipt.workstream_key,
                    attempt_id=receipt.attempt_id,
                    candidate_sha=receipt.candidate_sha,
                    candidate_tree=receipt.candidate_tree,
                )
            )
            previous = receipt.resulting_sha
        inspection = await self._workstreams.inspect_integration(allocation, receipts[-1])
        return inspection.model_copy(
            update={
                "receipt_ids": tuple(receipt.receipt_id for receipt in receipts),
                "integrated_candidates": tuple(identities),
            }
        )

    async def run_verification(
        self,
        allocation: WorkspaceAllocation,
        *,
        attempt_id: str,
        candidate_sha: str,
        contract_id: str,
    ) -> VerificationReceipt:
        return await self._workstreams.verify(
            allocation,
            attempt_id=attempt_id,
            candidate_sha=candidate_sha,
            contract_id=contract_id,
        )

    def validate_evidence(
        self,
        evidence: CandidateEvidence,
        *,
        policy_id: str,
    ) -> EvidenceValidationReport:
        return self._verifier.validate(evidence, self._policy(policy_id))

    async def integrate_candidate(
        self,
        integration: WorkspaceAllocation,
        *,
        expected_integration_head: str,
        evidence: CandidateEvidence,
        policy_id: str,
    ) -> IntegrationReceipt:
        self._verifier.require(evidence, self._policy(policy_id))
        return await self._workstreams.integrate(
            integration,
            expected_integration_head=expected_integration_head,
            evidence=evidence,
        )

    async def inspect_candidate(
        self,
        repository: str,
        review_number: int,
        *,
        policy_id: str,
    ) -> tuple[ReviewCandidate, CheckReceipt]:
        policy = self._policy(policy_id)
        candidate, receipt = await self._forge.inspect_delivery_candidate(
            repository,
            review_number,
            policy.required_check_names,
        )
        provenance = self._authenticator.sign(evidence_payload(receipt), self._producer_id)
        return candidate, receipt.model_copy(update={"provenance": provenance})

    async def conditional_merge(
        self,
        request: MergeRequest,
        *,
        evidence: CandidateEvidence,
        policy_id: str,
    ) -> MergeReceipt:
        candidate, checks = await self.inspect_candidate(
            request.repository,
            request.review_number,
            policy_id=policy_id,
        )
        if candidate.candidate_sha != request.expected_head_sha:
            raise ValueError("Review head does not match the requested candidate")
        if candidate.current_target_sha != request.expected_base_sha:
            raise ValueError("Review target moved; a new candidate must be tested")
        if candidate.target_branch != request.expected_target_branch:
            raise ValueError("Review target branch does not match the merge request")
        current_evidence = evidence.model_copy(update={"checks": checks})
        if current_evidence.candidate_sha != request.expected_head_sha:
            raise ValueError("Evidence candidate does not match the merge request")
        if current_evidence.repository != request.repository:
            raise ValueError("Evidence repository does not match the merge request")
        if current_evidence.base_sha != request.expected_base_sha:
            raise ValueError("Evidence base does not match the merge request")
        self._verifier.require(current_evidence, self._policy(policy_id))
        receipt = await self._forge.conditional_merge(request)
        provenance = self._authenticator.sign(evidence_payload(receipt), self._producer_id)
        return receipt.model_copy(update={"provenance": provenance})

    async def reconcile_merge(self, request: MergeRequest) -> MergeReceipt:
        receipt = await self._forge.reconcile_merge(request)
        provenance = self._authenticator.sign(evidence_payload(receipt), self._producer_id)
        return receipt.model_copy(update={"provenance": provenance})

    def _policy(self, policy_id: str) -> AcceptancePolicy:
        policy = self._policies.get(policy_id)
        if policy is None:
            raise DeliveryPolicyNotFoundError(f"Unknown delivery policy: {policy_id}")
        return policy

    def _require_integration_receipt(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
        *,
        policy_id: str,
    ) -> None:
        policy = self._policy(policy_id)
        provenance = receipt.provenance
        if provenance is None or provenance.producer_id not in policy.integration_producers:
            raise EvidenceValidationError("Integration receipt producer is not trusted")
        if not self._authenticator.verify(evidence_payload(receipt), provenance):
            raise EvidenceValidationError("Integration receipt signature is invalid")
        if (
            receipt.campaign_id != allocation.campaign_id
            or receipt.integration_allocation_id != allocation.allocation_id
            or receipt.repository != allocation.repository
            or receipt.base_sha != allocation.base_sha
        ):
            raise EvidenceValidationError("Integration receipt identities do not match allocation")
