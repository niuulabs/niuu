"""Authenticated HTTP clients for coordinator-only developer operations."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from niuu.domain.delivery import (
    BranchPublicationRequest,
    CandidateEvidence,
    IntegrationReceipt,
    MergeRequest,
    ReviewRequest,
    WorkspaceAllocation,
    WorkstreamSpec,
)
from ravn.adapters.tool_build.http import AsyncJsonHttpClient


class HttpWorkflowExecutionClient:
    """Call the owner-bound Ting coordinator facade from a Ravn session."""

    def __init__(
        self,
        *,
        base_url: str,
        execution_id: str,
        client: AsyncJsonHttpClient,
    ) -> None:
        if not execution_id.strip():
            raise ValueError("developer execution_id is required")
        self._url = f"{base_url.rstrip('/')}/api/v1/ting/workflow-executions/{execution_id.strip()}"
        self._client = client

    async def expand(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/expansions", payload)

    async def reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        return await self._post("/reconcile", {})

    async def retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        child_key = str(payload.get("child_key") or "").strip()
        attempt_id = str(payload.get("attempt_id") or "").strip()
        if not child_key or not attempt_id:
            raise ValueError("retry requires the exact child_key and current attempt_id")
        return await self._post(
            f"/children/{quote(child_key, safe='')}/retry", {"attempt_id": attempt_id}
        )

    async def cancel(self) -> dict[str, Any]:
        return await self._post("/cancel", {})

    async def message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/messages", payload)

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/complete", payload)

    async def record_integration(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/integration-candidate", payload)

    async def wait(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/waits", payload)

    async def _post(self, suffix: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(self._url + suffix, payload)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(_http_error(response.status_code, response.body))
        if not isinstance(response.body, dict):
            raise RuntimeError("Ting developer execution returned a non-object response")
        return response.body


class HttpDeliveryServiceClient:
    """Provider-neutral client for Volundr's typed delivery surface."""

    def __init__(self, *, base_url: str, client: AsyncJsonHttpClient) -> None:
        self._url = f"{base_url.rstrip('/')}/api/v1/forge/delivery"
        self._client = client

    async def resolve_ref(self, repository: str, ref: str) -> dict[str, Any]:
        return await self._post("/refs/resolve", {"repository": repository, "ref": ref})

    async def describe_policy(
        self, *, campaign_id: str, repository: str, policy_id: str
    ) -> dict[str, Any]:
        return await self._post(
            "/evidence/policy",
            {"campaign_id": campaign_id, "repository": repository, "policy_id": policy_id},
        )

    async def open_review(self, request: ReviewRequest) -> dict[str, Any]:
        return await self._post("/forge/reviews", request.model_dump(mode="json"))

    async def publish_branch(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
        request: BranchPublicationRequest,
        *,
        policy_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/forge/branches",
            {
                "allocation": allocation.model_dump(mode="json"),
                "integration_receipt": receipt.model_dump(mode="json"),
                "publication": request.model_dump(mode="json"),
                "policy_id": policy_id,
            },
        )

    async def allocate_workstream(self, spec: WorkstreamSpec) -> dict[str, Any]:
        return await self._post("/workspaces/allocate", spec.model_dump(mode="json"))

    async def run_verification(
        self,
        allocation: WorkspaceAllocation,
        *,
        attempt_id: str,
        candidate_sha: str,
        contract_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/workspaces/verify",
            {
                "allocation": allocation.model_dump(mode="json"),
                "attempt_id": attempt_id,
                "candidate_sha": candidate_sha,
                "contract_id": contract_id,
            },
        )

    async def integrate_candidate(
        self,
        integration: WorkspaceAllocation,
        *,
        expected_integration_head: str,
        evidence: CandidateEvidence,
        policy_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/workspaces/integrate",
            {
                "integration": integration.model_dump(mode="json"),
                "expected_integration_head": expected_integration_head,
                "evidence": evidence.model_dump(mode="json"),
                "policy_id": policy_id,
            },
        )

    async def inspect_integration(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
        *,
        policy_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/workspaces/integration/inspect",
            {
                "allocation": allocation.model_dump(mode="json"),
                "integration_receipt": receipt.model_dump(mode="json"),
                "policy_id": policy_id,
            },
        )

    async def inspect_candidate(
        self,
        repository: str,
        review_number: int,
        *,
        policy_id: str,
        campaign_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/forge/inspect",
            {
                "repository": repository,
                "review_number": review_number,
                "policy_id": policy_id,
                "campaign_id": campaign_id,
            },
        )

    async def conditional_merge(
        self,
        request: MergeRequest,
        *,
        evidence: CandidateEvidence,
        policy_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/forge/merge",
            {
                "merge": request.model_dump(mode="json"),
                "evidence": evidence.model_dump(mode="json"),
                "policy_id": policy_id,
            },
        )

    async def reconcile_merge(self, request: MergeRequest) -> dict[str, Any]:
        return await self._post("/forge/reconcile", request.model_dump(mode="json"))

    async def validate_evidence(
        self,
        evidence: CandidateEvidence,
        *,
        policy_id: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/evidence/validate",
            {"evidence": evidence.model_dump(mode="json"), "policy_id": policy_id},
        )

    async def _post(self, suffix: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(self._url + suffix, payload)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(_http_error(response.status_code, response.body))
        if not isinstance(response.body, dict):
            raise RuntimeError("Delivery operation returned a non-object response")
        return response.body


def _http_error(status_code: int, body: object) -> str:
    detail = body.get("detail") if isinstance(body, dict) else None
    return f"HTTP {status_code}: {detail or 'operation failed'}"
