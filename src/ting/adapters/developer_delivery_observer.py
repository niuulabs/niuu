"""Observe exact Forge delivery state through the execution's bound connection."""

from __future__ import annotations

from datetime import UTC, datetime

from niuu.domain.delivery import CheckConclusion, MergeRequest, PublicationState
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from niuu.ports.workload_identity import WorkloadTokenIssuer
from ting.domain.developer_delivery_wait import (
    DeveloperDeliveryObservation,
    DeveloperDeliveryObservationStatus,
    DeveloperDeliveryWaitMode,
    DeveloperDeliveryWaitRequest,
)
from ting.domain.developer_execution import DeveloperExecution
from ting.ports.developer_delivery_wait import DeveloperDeliveryObserver
from ting.ports.volundr import VolundrFactory


class ForgeDeveloperDeliveryObserver(DeveloperDeliveryObserver):
    def __init__(
        self,
        *,
        volundr_factory: VolundrFactory,
        policy_id: str,
        token_issuer: WorkloadTokenIssuer | None = None,
        admission_roles: tuple[str, ...] = ("volundr:developer",),
    ) -> None:
        if not policy_id.strip():
            raise ValueError("Delivery observation requires a configured policy ID")
        self._factory = volundr_factory
        self._policy_id = policy_id
        self._token_issuer = token_issuer
        if not admission_roles or any(not role.strip() for role in admission_roles):
            raise ValueError("Delivery observation admission roles must be non-empty")
        self._admission_roles = tuple(role.strip() for role in admission_roles)

    async def observe(
        self,
        execution: DeveloperExecution,
        request: DeveloperDeliveryWaitRequest,
    ) -> DeveloperDeliveryObservation:
        if request.policy_id != self._policy_id:
            raise ValueError(
                "Delivery wait policy does not match the configured integration policy"
            )
        adapter = await self._adapter(execution)
        principal = Principal(
            user_id=execution.owner_id,
            email="",
            tenant_id=execution.tenant_id,
            roles=list(self._admission_roles),
        )
        auth_token = self._auth_token(execution, principal)
        if request.mode is DeveloperDeliveryWaitMode.MERGE:
            return await self._observe_merge(execution, request, adapter, principal, auth_token)
        candidate, checks = await adapter.inspect_delivery_candidate(
            request.repository,
            request.review_number,
            campaign_id=str(execution.id),
            policy_id=request.policy_id,
            auth_token=auth_token,
            principal=principal,
        )
        stale_reason = _stale_reason(request, candidate, checks)
        if stale_reason:
            return self._observation(
                request,
                DeveloperDeliveryObservationStatus.STALE_CANDIDATE,
                reason=stale_reason,
                candidate=candidate,
                checks=checks,
            )
        policy = await adapter.describe_delivery_policy(
            campaign_id=str(execution.id),
            repository=request.repository,
            policy_id=request.policy_id,
            auth_token=auth_token,
            principal=principal,
        )
        by_name = {item.name: item for item in checks.checks}
        if len(by_name) != len(checks.checks):
            return self._observation(
                request,
                DeveloperDeliveryObservationStatus.CHECKS_FAILED,
                reason="Forge check receipt contains duplicate check names",
                candidate=candidate,
                checks=checks,
            )
        required = [by_name.get(name) for name in policy.required_check_names]
        conclusions = {
            item.conclusion if item is not None else CheckConclusion.UNKNOWN for item in required
        }
        failures = conclusions.intersection(
            {CheckConclusion.FAILING, CheckConclusion.CANCELED, CheckConclusion.SKIPPED}
        )
        if failures:
            status = DeveloperDeliveryObservationStatus.CHECKS_FAILED
            reason = "One or more required Forge checks did not pass"
        elif conclusions.intersection({CheckConclusion.PENDING, CheckConclusion.UNKNOWN}):
            status = DeveloperDeliveryObservationStatus.CHECKS_PENDING
            reason = "Required Forge checks are not terminal"
        else:
            status = DeveloperDeliveryObservationStatus.CHECKS_PASSED
            reason = "All configured Forge checks passed"
        return self._observation(
            request,
            status,
            reason=reason,
            candidate=candidate,
            checks=checks,
        )

    async def _observe_merge(self, execution, request, adapter, principal, auth_token):
        # Reconcile the merge before inspecting an open candidate: a successful
        # merge necessarily changes the target head from the tested base.
        merge_request = MergeRequest(
            campaign_id=str(execution.id),
            repository=request.repository,
            review_number=request.review_number,
            expected_head_sha=request.expected_head_sha,
            expected_base_sha=request.expected_base_sha,
            expected_target_branch=request.expected_target_branch,
            method=request.method,
            provider_operation_id=request.provider_operation_id,
        )
        receipt = await adapter.reconcile_delivery_merge(
            merge_request,
            auth_token=auth_token,
            principal=principal,
        )
        if (
            receipt.campaign_id != str(execution.id)
            or receipt.repository != request.repository
            or receipt.review_number != request.review_number
            or receipt.method != request.method
            or (
                request.provider_operation_id is not None
                and receipt.provider_operation_id != request.provider_operation_id
            )
        ):
            return self._observation(
                request,
                DeveloperDeliveryObservationStatus.MERGE_FAILED,
                reason="Remote merge receipt does not match the persisted wait identity",
                merge_receipt=receipt,
            )
        if (
            receipt.source_sha != request.expected_head_sha
            or receipt.base_sha != request.expected_base_sha
            or receipt.target_branch != request.expected_target_branch
        ):
            return self._observation(
                request,
                DeveloperDeliveryObservationStatus.STALE_CANDIDATE,
                reason="Remote merge receipt does not match the persisted wait identity",
                merge_receipt=receipt,
            )
        if receipt.state is PublicationState.QUEUED:
            return self._observation(
                request,
                DeveloperDeliveryObservationStatus.MERGE_PENDING,
                reason="The exact remote candidate remains queued for merge",
                merge_receipt=receipt,
            )
        if receipt.state is PublicationState.FAILED:
            return self._observation(
                request,
                DeveloperDeliveryObservationStatus.MERGE_FAILED,
                reason="The exact remote publication operation failed or was closed",
                merge_receipt=receipt,
            )
        if receipt.result_sha != receipt.canonical_target_sha:
            return self._observation(
                request,
                DeveloperDeliveryObservationStatus.MERGE_FAILED,
                reason="Remote canonical target differs from the verified merge result",
                merge_receipt=receipt,
            )
        return self._observation(
            request,
            DeveloperDeliveryObservationStatus.MERGED,
            reason="Remote merge and canonical target were verified",
            merge_receipt=receipt,
        )

    async def _adapter(self, execution: DeveloperExecution):
        adapter = (
            await self._factory.for_connection(execution.owner_id, execution.connection_id)
            if execution.connection_id
            else await self._factory.primary_for_owner(execution.owner_id)
        )
        if adapter is None:
            raise RuntimeError("The execution's configured Forge connection is unavailable")
        return adapter

    def _auth_token(self, execution: DeveloperExecution, principal: Principal) -> str | None:
        if self._token_issuer is None:
            return None
        if not execution.parent_session_id:
            raise RuntimeError("Delivery observation requires the durable parent session")
        session_key = f"workflow:developer-{execution.id.hex}"
        return self._token_issuer.issue_token(
            principal=principal,
            workload_subject=session_key,
            workload_name=execution.policy.coordinator_id,
            audiences=[],
            token_use=VALKYRIE_BUILD_TOKEN_USE,
            claims={
                "scopes": ["ting:developer:coordinate"],
                "developer_execution_id": str(execution.id),
                "parent_node_id": execution.parent_node_id,
                "parent_session_key": session_key,
                "coordinator_id": execution.policy.coordinator_id,
                "forge_session_id": execution.parent_session_id,
            },
        ).token

    @staticmethod
    def _observation(request, status, **kwargs) -> DeveloperDeliveryObservation:
        return DeveloperDeliveryObservation(
            status=status,
            repository=request.repository,
            review_number=request.review_number,
            expected_head_sha=request.expected_head_sha,
            expected_base_sha=request.expected_base_sha,
            expected_target_branch=request.expected_target_branch,
            observed_at=datetime.now(UTC),
            **kwargs,
        )


def _stale_reason(request, candidate, checks) -> str:
    if candidate.repository != request.repository or checks.repository != request.repository:
        return "Forge observation repository differs from the persisted wait"
    if (
        candidate.review_number != request.review_number
        or checks.review_number != request.review_number
    ):
        return "Forge observation review number differs from the persisted wait"
    if (
        candidate.candidate_sha != request.expected_head_sha
        or checks.candidate_sha != request.expected_head_sha
    ):
        return "Forge review head moved from the persisted candidate"
    if (
        candidate.tested_base_sha != request.expected_base_sha
        or candidate.current_target_sha != request.expected_base_sha
        or checks.tested_base_sha != request.expected_base_sha
    ):
        return "Forge review base moved from the persisted candidate"
    if candidate.target_branch != request.expected_target_branch:
        return "Forge review target branch differs from the persisted wait"
    return ""
