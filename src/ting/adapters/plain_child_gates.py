"""Result gates for executions that no pack specialises.

A child's result is checked against its node's result schema by the execution
service. These gates add nothing to that, and refuse what they cannot provide.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from niuu.domain.evidence import EvidenceValidationReport
from ting.domain.execution_snapshot import pinned_child_workflow
from ting.domain.workflow_document import workflow_review_attestation
from ting.domain.workflow_execution import (
    WorkflowChildExecution,
    WorkflowExecution,
    WorkflowExecutionError,
)
from ting.ports.child_evidence import ChildEvidenceVerifier, ChildReviewAttestor


class SchemaOnlyChildResultVerifier(ChildEvidenceVerifier):
    """Accept a schema-valid result and record the digest of what was accepted."""

    async def validate(
        self,
        execution: WorkflowExecution,
        child: WorkflowChildExecution,
        result: dict[str, Any],
    ) -> EvidenceValidationReport:
        del execution, child
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str)
        return EvidenceValidationReport(
            accepted=True,
            blocking_reasons=(),
            manifest_digest=hashlib.sha256(encoded.encode()).hexdigest(),
        )


class UndeclaredReviewAttestor(ChildReviewAttestor):
    """Pass a result through only when its workflow asks for no attested review.

    A child workflow that declares ``reviewAttestation`` expects every review
    verdict in its result to be bound to an authenticated reviewer. This gate
    has no authenticator, so it refuses such a result instead of returning it
    as though it had been attested.
    """

    async def attest(
        self,
        execution: WorkflowExecution,
        child: WorkflowChildExecution,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        workflow = pinned_child_workflow(execution, child)
        binding = workflow_review_attestation(
            workflow.graph,
            persona_dependencies=workflow.persona_dependencies,
        )
        if binding is not None:
            raise WorkflowExecutionError(
                "Child workflow declares reviewAttestation but this execution has no review "
                "attestor. Launch it through a pack that configures one, or remove "
                "reviewAttestation from the child workflow."
            )
        return result
