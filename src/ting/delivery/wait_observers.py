"""Forge-backed observers for the developer delivery workflow's wait conditions.

The generic wait machinery in ``ting.domain.services.workflow_wait`` knows
nothing about git, checks, or merges. These two observers are what give
``forge.checks`` and ``forge.merge`` their meaning for the developer delivery
workflow: they parse the exact review, head, and base identity pinned into a
wait's request and read remote state through the execution's bound Forge
connection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from niuu.domain.delivery import CheckConclusion, MergeRequest, PublicationState
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from niuu.ports.workload_identity import WorkloadTokenIssuer
from ting.delivery.domain import DeliveryExecution
from ting.domain.workflow_execution import WorkflowExecution
from ting.domain.workflow_wait import WaitObservation, WaitObservationStatus, WorkflowWait
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_wait import WaitConditionObserver

_GIT_SHA = r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$"


class _ForgeWaitRequest(BaseModel):
    """Exact remote review identity pinned into a ``forge.checks`` wait request."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    repository: str = Field(min_length=1)
    review_number: int = Field(gt=0, validation_alias=AliasChoices("review_number", "reviewNumber"))
    expected_head_sha: str = Field(
        pattern=_GIT_SHA,
        validation_alias=AliasChoices("expected_head_sha", "expectedHeadSha"),
    )
    expected_base_sha: str = Field(
        pattern=_GIT_SHA,
        validation_alias=AliasChoices("expected_base_sha", "expectedBaseSha"),
    )
    expected_target_branch: str = Field(
        min_length=1,
        validation_alias=AliasChoices("expected_target_branch", "expectedTargetBranch"),
    )
    policy_id: str = Field(min_length=1, validation_alias=AliasChoices("policy_id", "policyId"))


class _ForgeMergeWaitRequest(_ForgeWaitRequest):
    """Exact remote review identity pinned into a ``forge.merge`` wait request."""

    method: str = Field(pattern=r"^(merge|squash|rebase)$")
    provider_operation_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("provider_operation_id", "providerOperationId"),
    )


class _ForgeWaitObserverBase(WaitConditionObserver):
    def __init__(
        self,
        *,
        volundr_factory: VolundrFactory,
        policy_id: str,
        token_issuer: WorkloadTokenIssuer | None = None,
        admission_roles: tuple[str, ...] = ("volundr:developer",),
    ) -> None:
        if not policy_id.strip():
            raise ValueError("Wait observation requires a configured policy ID")
        self._factory = volundr_factory
        self._policy_id = policy_id
        self._token_issuer = token_issuer
        if not admission_roles or any(not role.strip() for role in admission_roles):
            raise ValueError("Wait observation admission roles must be non-empty")
        self._admission_roles = tuple(role.strip() for role in admission_roles)

    def _check_policy(self, parsed: _ForgeWaitRequest) -> None:
        if parsed.policy_id != self._policy_id:
            raise ValueError("Wait policy does not match the configured integration policy")

    @staticmethod
    def _check_candidate(parsed: _ForgeWaitRequest, execution: WorkflowExecution) -> None:
        """Bind the request to the candidate this execution inspected and recorded.

        Without this a coordinator could wait on any commit, receive a passing
        observation for it, and complete an execution whose verified candidate
        is a different one.
        """
        if not isinstance(execution, DeliveryExecution):
            raise ValueError("A forge wait belongs to a delivery execution")
        candidate = execution.integration_candidate or {}
        candidate_sha = str(candidate.get("candidate_sha") or "")
        if not candidate_sha or parsed.expected_head_sha != candidate_sha:
            raise ValueError("Wait head must match the current inspected integration candidate")
        if (
            candidate.get("repository") != parsed.repository
            or candidate.get("base_sha") != parsed.expected_base_sha
        ):
            raise ValueError("Wait base identity must match the current inspected candidate")

    async def _adapter(self, execution: DeliveryExecution) -> Any:
        adapter = (
            await self._factory.for_connection(execution.owner_id, execution.connection_id)
            if execution.connection_id
            else await self._factory.primary_for_owner(execution.owner_id)
        )
        if adapter is None:
            raise RuntimeError("The execution's configured Forge connection is unavailable")
        return adapter

    def _auth_token(self, execution: DeliveryExecution, principal: Principal) -> str | None:
        if self._token_issuer is None:
            return None
        if not execution.parent_session_id:
            raise RuntimeError("Wait observation requires the durable parent session")
        session_key = f"workflow:execution-{execution.id.hex}"
        return self._token_issuer.issue_token(
            principal=principal,
            workload_subject=session_key,
            workload_name=execution.policy.coordinator_id,
            audiences=[],
            token_use=VALKYRIE_BUILD_TOKEN_USE,
            claims={
                "scopes": ["ting:workflow:coordinate"],
                "workflow_execution_id": str(execution.id),
                "parent_node_id": execution.parent_node_id,
                "parent_session_key": session_key,
                "coordinator_id": execution.policy.coordinator_id,
                "forge_session_id": execution.parent_session_id,
            },
        ).token

    def _principal(self, execution: DeliveryExecution) -> Principal:
        return Principal(
            user_id=execution.owner_id,
            email="",
            tenant_id=execution.tenant_id,
            roles=list(self._admission_roles),
        )


class ForgeChecksWaitObserver(_ForgeWaitObserverBase):
    """Observes whether a review candidate's required Forge checks are terminal."""

    @property
    def condition_type(self) -> str:
        return "forge.checks"

    def validate(self, request: dict[str, Any], execution: WorkflowExecution) -> None:
        parsed = _ForgeWaitRequest.model_validate(request)
        self._check_policy(parsed)
        self._check_candidate(parsed, execution)

    async def observe(self, wait: WorkflowWait, execution: DeliveryExecution) -> WaitObservation:
        request = _ForgeWaitRequest.model_validate(wait.request)
        self._check_policy(request)
        adapter = await self._adapter(execution)
        principal = self._principal(execution)
        auth_token = self._auth_token(execution, principal)
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
            return _observation(
                WaitObservationStatus.FAILED,
                reason=stale_reason,
                detail=_detail(candidate=candidate, checks=checks),
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
            return _observation(
                WaitObservationStatus.FAILED,
                reason="Forge check receipt contains duplicate check names",
                detail=_detail(candidate=candidate, checks=checks),
            )
        required = [by_name.get(name) for name in policy.required_check_names]
        conclusions = {
            item.conclusion if item is not None else CheckConclusion.UNKNOWN for item in required
        }
        failures = conclusions.intersection(
            {CheckConclusion.FAILING, CheckConclusion.CANCELED, CheckConclusion.SKIPPED}
        )
        if failures:
            return _observation(
                WaitObservationStatus.FAILED,
                reason="One or more required Forge checks did not pass",
                detail=_detail(candidate=candidate, checks=checks),
            )
        if conclusions.intersection({CheckConclusion.PENDING, CheckConclusion.UNKNOWN}):
            return _observation(
                WaitObservationStatus.PENDING,
                reason="Required Forge checks are not terminal",
                detail=_detail(candidate=candidate, checks=checks),
            )
        return _observation(
            WaitObservationStatus.SATISFIED,
            reason="All configured Forge checks passed",
            detail=_detail(candidate=candidate, checks=checks),
        )


class ForgeMergeWaitObserver(_ForgeWaitObserverBase):
    """Observes whether an exact remote merge operation has resolved."""

    @property
    def condition_type(self) -> str:
        return "forge.merge"

    def validate(self, request: dict[str, Any], execution: WorkflowExecution) -> None:
        parsed = _ForgeMergeWaitRequest.model_validate(request)
        self._check_policy(parsed)
        self._check_candidate(parsed, execution)

    async def observe(self, wait: WorkflowWait, execution: DeliveryExecution) -> WaitObservation:
        request = _ForgeMergeWaitRequest.model_validate(wait.request)
        self._check_policy(request)
        adapter = await self._adapter(execution)
        principal = self._principal(execution)
        auth_token = self._auth_token(execution, principal)
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
            or receipt.provider_operation_id != request.provider_operation_id
        ):
            return _observation(
                WaitObservationStatus.FAILED,
                reason="Remote merge receipt does not match the persisted wait identity",
                detail=_detail(merge_receipt=receipt),
            )
        if (
            receipt.source_sha != request.expected_head_sha
            or receipt.base_sha != request.expected_base_sha
            or receipt.target_branch != request.expected_target_branch
        ):
            return _observation(
                WaitObservationStatus.FAILED,
                reason="Remote merge receipt does not match the persisted wait identity",
                detail=_detail(merge_receipt=receipt),
            )
        if receipt.state is PublicationState.QUEUED:
            return _observation(
                WaitObservationStatus.PENDING,
                reason="The exact remote candidate remains queued for merge",
                detail=_detail(merge_receipt=receipt),
            )
        if receipt.state is PublicationState.FAILED:
            return _observation(
                WaitObservationStatus.FAILED,
                reason="The exact remote publication operation failed or was closed",
                detail=_detail(merge_receipt=receipt),
            )
        if receipt.result_sha != receipt.canonical_target_sha:
            return _observation(
                WaitObservationStatus.FAILED,
                reason="Remote canonical target differs from the verified merge result",
                detail=_detail(merge_receipt=receipt),
            )
        return _observation(
            WaitObservationStatus.SATISFIED,
            reason="Remote merge and canonical target were verified",
            detail=_detail(merge_receipt=receipt),
        )


def _observation(
    status: WaitObservationStatus, *, reason: str, detail: dict[str, Any]
) -> WaitObservation:
    return WaitObservation(
        status=status, observed_at=datetime.now(UTC), reason=reason, detail=detail
    )


def _detail(
    *, candidate: Any = None, checks: Any = None, merge_receipt: Any = None
) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    if candidate is not None:
        detail["candidate"] = candidate.model_dump(mode="json")
    if checks is not None:
        detail["checks"] = checks.model_dump(mode="json")
    if merge_receipt is not None:
        detail["mergeReceipt"] = merge_receipt.model_dump(mode="json")
    return detail


def _stale_reason(request: _ForgeWaitRequest, candidate: Any, checks: Any) -> str:
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
