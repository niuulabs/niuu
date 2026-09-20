"""Complete a developer execution only from verified remote publication."""

from niuu.domain.delivery import (
    CandidateEvidence,
    MergeRequest,
    PublicationState,
    evidence_digest,
)
from niuu.domain.models import Principal
from ting.domain.developer_execution import (
    ChildExecutionState,
    DeveloperExecution,
    DeveloperExecutionError,
    ExecutionState,
)
from ting.ports.developer_execution import DeveloperExecutionRepository
from ting.ports.volundr import VolundrFactory


class DeveloperCompletionService:
    def __init__(
        self,
        *,
        repository: DeveloperExecutionRepository,
        volundr_factory: VolundrFactory,
        policy_id: str,
    ) -> None:
        if not policy_id.strip():
            raise ValueError("Developer completion requires an integration evidence policy")
        self._repository = repository
        self._factory = volundr_factory
        self._policy_id = policy_id

    async def complete(
        self,
        execution: DeveloperExecution,
        *,
        merge: MergeRequest,
        evidence: CandidateEvidence,
        principal: Principal,
        auth_token: str | None,
    ) -> DeveloperExecution:
        if (principal.user_id, principal.tenant_id) != (execution.owner_id, execution.tenant_id):
            raise DeveloperExecutionError("Completion identity differs from execution owner")
        if (
            merge.campaign_id != str(execution.id)
            or merge.repository != execution.repository
            or merge.expected_base_sha != execution.base_sha
            or merge.expected_target_branch != execution.base_ref
        ):
            raise DeveloperExecutionError(
                "Publication does not match the execution repository and base"
            )
        if (
            evidence.campaign_id != str(execution.id)
            or evidence.repository != execution.repository
            or evidence.candidate_sha != merge.expected_head_sha
            or evidence.base_sha != merge.expected_base_sha
        ):
            raise DeveloperExecutionError(
                "Integration evidence differs from the publication candidate"
            )
        if execution.state == ExecutionState.COMPLETED:
            receipt = execution.merge_receipt or {}
            if (
                receipt.get("source_sha") == merge.expected_head_sha
                and receipt.get("review_number") == merge.review_number
                and receipt.get("method") == merge.method
            ):
                return execution
            raise DeveloperExecutionError("Execution already completed with another publication")
        if execution.cancel_requested or execution.state not in {
            ExecutionState.RUNNING,
            ExecutionState.WAITING,
            ExecutionState.BLOCKED,
        }:
            raise DeveloperExecutionError("Execution cannot complete in its current state")
        candidate = execution.integration_candidate or {}
        allocation = execution.integration_allocation or {}
        if (
            not execution.integration_receipts
            or candidate.get("campaign_id") != str(execution.id)
            or candidate.get("repository") != execution.repository
            or candidate.get("base_sha") != execution.base_sha
            or candidate.get("candidate_sha") != evidence.candidate_sha
            or candidate.get("candidate_tree") != evidence.candidate_tree
            or candidate.get("integration_allocation_id") != evidence.attempt_id
            or allocation.get("allocation_id") != evidence.attempt_id
            or allocation.get("worker_id") != evidence.worker_id
            or allocation.get("workstream_key") != evidence.workstream_key
        ):
            raise DeveloperExecutionError(
                "Completion requires the exact server-inspected integration candidate"
            )
        review = execution.integration_review_receipt
        if (
            not review
            or not execution.integration_review_event_id
            or not any(receipt.model_dump(mode="json") == review for receipt in evidence.reviews)
        ):
            raise DeveloperExecutionError(
                "Completion requires the stored authenticated integration review"
            )
        join = await self._repository.join_status(execution.id, execution.current_generation)
        if not join.ready:
            raise DeveloperExecutionError(
                "Current child generation has not passed its evidence barrier"
            )
        children = await self._repository.list_children(execution.id)
        requirements = {
            requirement
            for child in children
            if child.generation == execution.current_generation
            and child.state == ChildExecutionState.COMPLETED
            for requirement in child.requirement_ids
        }
        if {item.requirement_id for item in evidence.requirements} != requirements:
            raise DeveloperExecutionError(
                "Integration evidence must cover every current workstream requirement"
            )
        if evidence.checks is None:
            raise DeveloperExecutionError("Completion requires authenticated remote CI evidence")
        if evidence.checks.review_number != merge.review_number:
            raise DeveloperExecutionError("CI evidence belongs to another review request")
        if execution.connection_id:
            forge = await self._factory.for_connection(execution.owner_id, execution.connection_id)
        else:
            forge = await self._factory.primary_for_owner(execution.owner_id)
        if forge is None:
            raise DeveloperExecutionError(
                "The execution's configured Forge connection is unavailable"
            )
        report = await forge.validate_delivery_evidence(
            evidence,
            policy_id=self._policy_id,
            auth_token=auth_token,
            principal=principal,
        )
        if report.manifest_digest != evidence_digest(evidence) or not report.accepted:
            reasons = "; ".join(report.blocking_reasons)
            raise DeveloperExecutionError(f"Integration evidence was rejected: {reasons}")
        receipt = await forge.reconcile_delivery_merge(
            merge,
            auth_token=auth_token,
            principal=principal,
        )
        if (
            receipt.state != PublicationState.MERGED
            or receipt.campaign_id != str(execution.id)
            or receipt.repository != execution.repository
            or receipt.target_branch != execution.base_ref
            or receipt.source_sha != merge.expected_head_sha
            or receipt.base_sha != merge.expected_base_sha
            or receipt.review_number != merge.review_number
            or receipt.method != merge.method
        ):
            raise DeveloperExecutionError("Forge has not confirmed the exact requested publication")
        return await self._repository.complete_execution(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            expected_revision=execution.revision,
            merge_receipt={
                **receipt.model_dump(mode="json"),
                "evidence_manifest_digest": report.manifest_digest,
            },
        )
