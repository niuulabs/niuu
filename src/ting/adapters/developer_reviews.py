"""Attest Skuld-authenticated reviewer outcomes for a durable child attempt."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from niuu.domain.delivery import (
    FindingDisposition,
    ReviewFinding,
    ReviewReceipt,
    ReviewVerdict,
    evidence_payload,
)
from niuu.ports.delivery import EvidenceAuthenticator
from ting.domain.developer_execution import (
    ChildExecution,
    DeveloperExecution,
    DeveloperExecutionError,
)
from ting.domain.developer_snapshot import pinned_child_workflow
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDefinition
from ting.domain.workflow_document import (
    ReviewAttestationBinding,
    workflow_review_attestation,
)
from ting.ports.developer_evidence import ChildReviewAttestor


class TrustedChildReviewAttestor(ChildReviewAttestor):
    def __init__(
        self,
        *,
        authenticator: EvidenceAuthenticator,
        role_producers: dict[str, str],
        workflow_resolver: Callable[
            [DeveloperExecution, ChildExecution], WorkflowDefinition
        ] = pinned_child_workflow,
    ) -> None:
        if not role_producers or any(
            not isinstance(role, str)
            or not role.strip()
            or not isinstance(producer, str)
            or not producer.strip()
            for role, producer in role_producers.items()
        ):
            raise ValueError("Review attestation must configure non-empty role producers")
        self._authenticator = authenticator
        self._role_producers = dict(role_producers)
        self._workflow_resolver = workflow_resolver

    async def attest(
        self, execution: DeveloperExecution, child: ChildExecution, result: dict[str, Any]
    ) -> dict[str, Any]:
        workflow = self._workflow_resolver(execution, child)
        binding = self._review_binding(workflow)
        missing_producers = set(binding.roles) - set(self._role_producers)
        if missing_producers:
            raise DeveloperExecutionError(
                "review attestation has no configured producer for role(s): "
                + ", ".join(sorted(missing_producers))
            )
        envelope = result.get("_trustedReviewEnvelope")
        if not isinstance(envelope, dict):
            raise DeveloperExecutionError("completed child has no trusted reviewer envelope")
        if (
            str(envelope.get("taskId") or "") != child.task_id
            or str(envelope.get("workflowId") or "") != str(child.template_id)
            or str(envelope.get("workflowRevision") or "") != child.template_revision
            or str(envelope.get("workflowDigest") or "") != child.template_digest
            or not str(envelope.get("sessionId") or "")
        ):
            raise DeveloperExecutionError("trusted reviewer envelope lineage is invalid")
        if result.get("reviewReceipts"):
            raise DeveloperExecutionError("child result must not supply its own review receipts")
        candidate_sha = str(result.get("candidateSha") or "")
        candidate_tree = str(result.get("candidateTree") or "")
        allocation = child.workspace or {}
        worker_id = str(allocation.get("worker_id") or "")
        raw_reviews = envelope.get("reviews")
        if not worker_id or not isinstance(raw_reviews, list):
            raise DeveloperExecutionError("trusted reviewer envelope is incomplete")
        by_role: dict[str, dict[str, Any]] = {}
        event_ids: set[str] = set()
        for raw in raw_reviews:
            if not isinstance(raw, dict) or raw.get("valid") is not True:
                raise DeveloperExecutionError("reviewer outcome was not validated by Skuld")
            role = str(raw.get("role") or "")
            event_id = str(raw.get("eventId") or "")
            if role not in binding.roles or role in by_role:
                raise DeveloperExecutionError("reviewer roles must be complete and unique")
            if not event_id or event_id in event_ids:
                raise DeveloperExecutionError("review event identities must be complete and unique")
            event_ids.add(event_id)
            if (
                str(raw.get("personaId") or "") != binding.roles[role]
                or str(raw.get("scope") or "") != binding.scope
                or str(raw.get("sessionId") or "") != str(envelope["sessionId"])
                or str(raw.get("attemptId") or "") != str(child.id)
                or str(raw.get("candidateSha") or "") != candidate_sha
                or str(raw.get("candidateTree") or "") != candidate_tree
            ):
                raise DeveloperExecutionError(f"{role} review is bound to another attempt")
            reviewer_id = str(raw.get("reviewerId") or "")
            if not reviewer_id or reviewer_id == worker_id:
                raise DeveloperExecutionError(f"{role} reviewer identity is invalid")
            by_role[role] = raw
            findings = raw.get("findings") or []
            if not isinstance(findings, list) or any(
                not isinstance(item, dict) for item in findings
            ):
                raise DeveloperExecutionError(f"{role} review findings are malformed")
            if str(raw.get("verdict") or "").casefold() not in {
                "pass",
                "changes_required",
                "blocked",
            }:
                raise DeveloperExecutionError(f"{role} review verdict is invalid")
        if set(by_role) != set(binding.roles):
            raise DeveloperExecutionError("all pinned reviewer roles must report")

        receipts = []
        for role in sorted(by_role):
            raw = by_role[role]
            try:
                verdict_text = str(raw.get("verdict") or "").casefold()
                verdict = ReviewVerdict.PASS if verdict_text == "pass" else ReviewVerdict.FAIL
                findings = tuple(
                    _finding(str(raw["eventId"]), index, item, verdict)
                    for index, item in enumerate(raw.get("findings") or ())
                )
                receipt = ReviewReceipt(
                    receipt_id="review-"
                    + hashlib.sha256(
                        f"{raw['eventId']}:{child.id}:{candidate_sha}".encode()
                    ).hexdigest(),
                    campaign_id=str(execution.id),
                    workstream_key=child.key,
                    attempt_id=str(child.id),
                    repository=execution.repository,
                    candidate_sha=candidate_sha,
                    candidate_tree=candidate_tree,
                    base_sha=child.base_sha,
                    reviewer_id=str(raw["reviewerId"]),
                    worker_id=worker_id,
                    role=role,
                    verdict=verdict,
                    summary=str(raw.get("summary") or f"{role} review {verdict.value}"),
                    findings=findings,
                )
            except (ValidationError, ValueError, TypeError) as exc:
                raise DeveloperExecutionError(f"{role} review receipt is invalid") from exc
            provenance = self._authenticator.sign(
                evidence_payload(receipt), self._role_producers[role]
            )
            receipts.append(
                receipt.model_copy(update={"provenance": provenance}).model_dump(mode="json")
            )
        return {
            **{key: value for key, value in result.items() if key != "_trustedReviewEnvelope"},
            "reviewReceipts": receipts,
        }

    @staticmethod
    def _review_binding(workflow: WorkflowDefinition) -> ReviewAttestationBinding:
        try:
            binding = workflow_review_attestation(
                workflow.graph,
                persona_dependencies=workflow.persona_dependencies,
                allow_legacy=True,
            )
        except WorkflowDocumentError as exc:
            raise DeveloperExecutionError("frozen child review attestation is invalid") from exc
        if binding is None:
            raise DeveloperExecutionError("frozen child workflow has no review attestation binding")
        return binding


def _finding(
    event_id: str,
    index: int,
    raw: dict[str, Any],
    verdict: ReviewVerdict,
) -> ReviewFinding:
    blocking = bool(raw.get("blocking", verdict == ReviewVerdict.FAIL))
    evidence = str(raw.get("evidence") or raw.get("summary") or "").strip()
    if not evidence:
        evidence = json.dumps(raw, sort_keys=True, default=str)[:4096]
    return ReviewFinding(
        finding_id=f"{event_id}:{index}",
        blocking=blocking,
        disposition=FindingDisposition.OPEN if blocking else FindingDisposition.RESOLVED,
        evidence=evidence[:4096],
    )
