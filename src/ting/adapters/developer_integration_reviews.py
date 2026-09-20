"""Project trusted developer activity onto its bound execution."""

from __future__ import annotations

import hashlib
import json
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
    TERMINAL_EXECUTION_STATES,
    DeveloperExecutionError,
    ExecutionState,
)
from ting.ports.developer_execution import DeveloperExecutionRepository
from ting.ports.volundr import ActivityEvent

_INTEGRATION_PERSONA = "developer-integration-verifier"


class TrustedIntegrationReviewProjector:
    """Project parent failures and signed reviews onto their exact execution."""

    def __init__(
        self,
        *,
        repository: DeveloperExecutionRepository,
        authenticator: EvidenceAuthenticator,
        producer_id: str,
    ) -> None:
        if not producer_id.strip():
            raise ValueError("Integration review producer is required")
        self._repository = repository
        self._authenticator = authenticator
        self._producer_id = producer_id

    async def handle_activity(self, event: ActivityEvent, owner_id: str) -> bool:
        if event.state == "error" or event.session_status == "failed":
            return await self._project_parent_failure(event, owner_id)
        raw = event.metadata.get("developer_review")
        if raw is None:
            return False
        if not isinstance(raw, dict) or str(raw.get("scope") or "") != "integration":
            return False
        execution = await self._repository.get_by_parent_session(
            owner_id=owner_id,
            session_id=event.session_id,
        )
        if execution is None:
            raise DeveloperExecutionError(
                "Integration review session is not bound to an active developer execution"
            )
        allocation = execution.integration_allocation or {}
        candidate = execution.integration_candidate or {}
        if not allocation or not candidate or not execution.integration_receipts:
            raise DeveloperExecutionError(
                "Integration review arrived before trusted candidate inspection"
            )
        self._validate(raw, event, allocation, candidate)
        receipt = self._receipt(execution, raw, allocation, candidate)
        provenance = self._authenticator.sign(
            evidence_payload(receipt),
            self._producer_id,
        )
        signed = receipt.model_copy(update={"provenance": provenance}).model_dump(mode="json")
        await self._repository.record_integration_review(
            execution.id,
            event_id=str(raw["eventId"]),
            candidate_sha=str(candidate["candidate_sha"]),
            candidate_tree=str(candidate["candidate_tree"]),
            receipt=signed,
        )
        return True

    async def _project_parent_failure(self, event: ActivityEvent, owner_id: str) -> bool:
        if not event.session_id or event.owner_id != owner_id:
            return False
        execution = await self._repository.get_by_parent_session(
            owner_id=owner_id,
            session_id=event.session_id,
        )
        if (
            execution is None
            or execution.state in TERMINAL_EXECUTION_STATES
            or execution.cancel_requested
        ):
            return False
        reason = str(event.metadata.get("error") or "").strip()
        if not reason:
            reason = f"Parent session {event.session_status or event.state}"
        await self._repository.update_execution_state(
            execution.id,
            state=ExecutionState.FAILED.value,
            suspension_reason=reason,
            expected_revision=execution.revision,
        )
        return True

    @staticmethod
    def _validate(
        raw: dict[str, Any],
        event: ActivityEvent,
        allocation: dict[str, Any],
        candidate: dict[str, Any],
    ) -> None:
        findings = raw.get("findings") or []
        if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
            raise DeveloperExecutionError("Integration review findings are malformed")
        if (
            int(raw.get("schemaVersion") or 0) != 1
            or raw.get("valid") is not True
            or str(raw.get("eventId") or "") == ""
            or str(raw.get("sessionId") or "") != event.session_id
            or str(raw.get("role") or "") != "integration"
            or str(raw.get("personaId") or "") != _INTEGRATION_PERSONA
            or str(raw.get("attemptId") or "") != str(allocation.get("allocation_id") or "")
            or str(raw.get("candidateSha") or "") != str(candidate.get("candidate_sha") or "")
            or str(raw.get("candidateTree") or "") != str(candidate.get("candidate_tree") or "")
        ):
            raise DeveloperExecutionError("Integration review lineage is invalid")
        reviewer_id = str(raw.get("reviewerId") or "")
        if not reviewer_id or reviewer_id == str(allocation.get("worker_id") or ""):
            raise DeveloperExecutionError("Integration reviewer identity is invalid")
        if str(raw.get("verdict") or "").casefold() not in {
            "pass",
            "changes_required",
            "blocked",
        }:
            raise DeveloperExecutionError("Integration review verdict is invalid")

    @staticmethod
    def _receipt(execution, raw, allocation, candidate) -> ReviewReceipt:
        verdict = (
            ReviewVerdict.PASS
            if str(raw.get("verdict") or "").casefold() == "pass"
            else ReviewVerdict.FAIL
        )
        try:
            findings = tuple(
                _finding(str(raw["eventId"]), index, item, verdict)
                for index, item in enumerate(raw.get("findings") or ())
            )
            return ReviewReceipt(
                receipt_id="review-"
                + hashlib.sha256(
                    (
                        f"{raw['eventId']}:{allocation['allocation_id']}:"
                        f"{candidate['candidate_sha']}"
                    ).encode()
                ).hexdigest(),
                campaign_id=str(execution.id),
                workstream_key=str(allocation["workstream_key"]),
                attempt_id=str(allocation["allocation_id"]),
                repository=execution.repository,
                candidate_sha=str(candidate["candidate_sha"]),
                candidate_tree=str(candidate["candidate_tree"]),
                base_sha=execution.base_sha,
                reviewer_id=str(raw["reviewerId"]),
                worker_id=str(allocation["worker_id"]),
                role="integration",
                verdict=verdict,
                summary=str(raw.get("summary") or f"integration review {verdict.value}"),
                findings=findings,
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise DeveloperExecutionError("Integration review receipt is invalid") from exc


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
