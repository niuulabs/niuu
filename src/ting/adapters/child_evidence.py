"""Validate child evidence through its configured authenticated Forge connection."""

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from niuu.domain.delivery import CandidateEvidence, EvidenceValidationReport, evidence_digest
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from niuu.ports.workload_identity import WorkloadTokenIssuer
from ting.delivery.domain import ChildExecution, DeliveryExecution
from ting.domain.workflow_execution import ChildExecutionState
from ting.ports.child_evidence import ChildEvidenceVerifier
from ting.ports.volundr import VolundrFactory


class ForgeChildEvidenceVerifier(ChildEvidenceVerifier):
    def __init__(
        self,
        *,
        volundr_factory: VolundrFactory,
        policy_id: str,
        token_issuer: WorkloadTokenIssuer | None = None,
        admission_roles: tuple[str, ...] = ("volundr:developer",),
    ) -> None:
        if not policy_id.strip():
            raise ValueError("Child evidence verification requires a configured policy ID")
        self._factory = volundr_factory
        self._policy_id = policy_id
        self._token_issuer = token_issuer
        if not admission_roles or any(not role.strip() for role in admission_roles):
            raise ValueError("Child evidence admission roles must be non-empty")
        self._admission_roles = tuple(role.strip() for role in admission_roles)

    async def validate(
        self, execution: DeliveryExecution, child: ChildExecution, result: dict[str, Any]
    ) -> EvidenceValidationReport:
        digest = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

        def rejected(reason):
            return EvidenceValidationReport(
                accepted=False, blocking_reasons=(reason,), manifest_digest=digest
            )

        if child.execution_id != execution.id:
            return rejected("Evidence child belongs to another execution")
        if child.generation != execution.current_generation:
            return rejected("Evidence child belongs to a superseded generation")
        if child.state in {ChildExecutionState.SUPERSEDED, ChildExecutionState.CANCELED}:
            return rejected("Evidence child attempt is no longer active")
        allocation = child.workspace or {}
        worker_id = allocation.get("worker_id")
        if not worker_id:
            return rejected("Child has no durable workspace worker identity")
        if (
            allocation.get("campaign_id") != str(execution.id)
            or allocation.get("workstream_key") != child.key
        ):
            return rejected("Child workspace belongs to another workstream")
        if allocation.get("base_sha") != child.base_sha:
            return rejected("Child workspace base differs from its work order")
        if result.get("attemptId") != str(child.id):
            return rejected("Evidence belongs to another child attempt")
        try:
            evidence = CandidateEvidence(
                campaign_id=str(execution.id),
                workstream_key=child.key,
                attempt_id=str(child.id),
                worker_id=worker_id,
                repository=execution.repository,
                base_sha=child.base_sha,
                candidate_sha=result.get("candidateSha"),
                candidate_tree=result.get("candidateTree"),
                requirements=result.get("requirementEvidence", []),
                verifications=result.get("verificationReceipts", []),
                reviews=result.get("reviewReceipts", []),
            )
        except ValidationError:
            return rejected("Child evidence does not satisfy the typed receipt contract")
        if {item.requirement_id for item in evidence.requirements} != set(child.requirement_ids):
            return rejected("Child evidence does not cover exactly its assigned requirements")
        assigned_contracts = set(allocation.get("test_contract_ids") or ())
        reported_contracts = {receipt.contract_id for receipt in evidence.verifications}
        if not assigned_contracts or not assigned_contracts <= reported_contracts:
            return rejected("Child evidence is missing an assigned verification contract")
        if execution.connection_id:
            adapter = await self._factory.for_connection(
                execution.owner_id, execution.connection_id
            )
        else:
            adapter = await self._factory.primary_for_owner(execution.owner_id)
        if adapter is None:
            raise RuntimeError("The execution's configured Forge connection is unavailable")
        if self._token_issuer is not None and not execution.parent_session_id:
            raise RuntimeError("Child evidence requires the execution's durable parent session")
        principal = Principal(
            user_id=execution.owner_id,
            email="",
            tenant_id=execution.tenant_id,
            roles=list(self._admission_roles),
        )
        auth_token = None
        if self._token_issuer is not None:
            session_key = f"workflow:execution-{execution.id.hex}"
            auth_token = self._token_issuer.issue_token(
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
        report = await adapter.validate_delivery_evidence(
            evidence,
            policy_id=self._policy_id,
            auth_token=auth_token,
            principal=principal,
        )
        expected_digest = evidence_digest(evidence)
        if report.manifest_digest != expected_digest:
            return rejected(
                "Forge validation report does not match the submitted evidence manifest"
            )
        return report
