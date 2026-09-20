"""Durable parent/child execution contracts for the code delivery workflow.

Ting owns structural expansion and the execution ledger.  The coordinator owns
the semantic decision about which workstreams to propose; this module only
checks the pinned contract, dependency graph, identity, and resource bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from pydantic import ValidationError

from niuu.domain.delivery import WorkspaceAllocation
from ting.domain.workflow_execution import (
    ChildExecutionState,
    WorkflowChildExecution,
    WorkflowChildProposal,
    WorkflowExecution,
    WorkflowExecutionError,
)
from ting.domain.workflow_execution import (
    validate_expansion as validate_workflow_expansion,
)

MISSING_COMMITS_FAILURE_KIND = "missing_commits"
"""Delivery-specific failure kind: a workstream produced no commits to review."""


@dataclass(frozen=True, kw_only=True)
class DeliveryExecution(WorkflowExecution):
    """Code delivery specialization of the reusable workflow lifecycle."""

    repository: str
    base_ref: str
    base_sha: str
    merge_receipt: dict[str, Any] | None = None
    integration_receipts: tuple[dict[str, Any], ...] = ()
    integration_allocation: dict[str, Any] | None = None
    integration_candidate: dict[str, Any] | None = None
    integration_review_receipt: dict[str, Any] | None = None
    integration_review_event_id: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.repository.strip() or not self.base_sha.strip():
            raise WorkflowExecutionError("repository and base SHA are required")


@dataclass(frozen=True, kw_only=True)
class WorkstreamProposal(WorkflowChildProposal):
    """Git workspace contract layered over a generic child proposal."""

    requirement_ids: tuple[str, ...]
    repository: str
    base_sha: str
    workspace: dict[str, Any]

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.requirement_ids or any(not item.strip() for item in self.requirement_ids):
            raise WorkflowExecutionError(f"workstream {self.key!r} needs requirement IDs")
        try:
            WorkspaceAllocation.model_validate(self.workspace)
        except ValidationError as exc:
            raise WorkflowExecutionError(
                f"workstream {self.key!r} workspace allocation is invalid"
            ) from exc


@dataclass(frozen=True, kw_only=True)
class ChildExecution(WorkflowChildExecution):
    """Code delivery child attempt with workspace and requirement state.

    ``requirement_ids``, ``repository``, ``base_sha``, and ``workspace`` have
    no columns of their own: the ledger persists only the generic ``input``
    JSON, and the delivery repository derives these fields from it on every
    read (see ``validate_expansion`` below, which requires the same keys in
    every workstream's ``input``).
    """

    requirement_ids: tuple[str, ...]
    repository: str
    base_sha: str
    workspace: dict[str, Any] | None = None


def validate_expansion(
    execution: DeliveryExecution,
    *,
    coordinator_id: str,
    generation: int,
    workstreams: list[WorkstreamProposal],
) -> tuple[WorkstreamProposal, ...]:
    """Apply generic lifecycle checks, then the code delivery contract."""
    ordered = validate_workflow_expansion(
        execution,
        coordinator_id=coordinator_id,
        generation=generation,
        children=cast(list[WorkflowChildProposal], workstreams),
    )
    for item in ordered:
        item = cast(WorkstreamProposal, item)
        if item.repository != execution.repository or item.base_sha != execution.base_sha:
            raise WorkflowExecutionError(
                f"workstream {item.key!r} repository/base do not match the parent execution"
            )
        allocation = WorkspaceAllocation.model_validate(item.workspace)
        if (
            allocation.campaign_id != str(execution.id)
            or allocation.workstream_key != item.key
            or allocation.repository != execution.repository
            or allocation.base_sha != item.base_sha
        ):
            raise WorkflowExecutionError(
                f"workstream {item.key!r} workspace is not bound to this execution and base"
            )
        # The ledger drops the dedicated repository/base_sha/workspace/requirementIds
        # columns and derives them solely from the generic child `input` JSON on
        # every read, so these keys must always be present and must agree with
        # the typed proposal fields — never merely checked when supplied.
        required_input_fields: tuple[tuple[str, object, object], ...] = (
            ("requirementIds", item.input.get("requirementIds"), list(item.requirement_ids)),
            ("repository", item.input.get("repository"), item.repository),
            ("baseSha", item.input.get("baseSha"), item.base_sha),
        )
        for field_name, actual, expected in required_input_fields:
            if field_name not in item.input:
                raise WorkflowExecutionError(
                    f"workstream {item.key!r} input must include {field_name!r}"
                )
            if actual != expected:
                raise WorkflowExecutionError(
                    f"workstream {item.key!r} input {field_name} differs from its proposal"
                )
        if "workspace" not in item.input:
            raise WorkflowExecutionError(f"workstream {item.key!r} input must include 'workspace'")
        try:
            input_allocation = WorkspaceAllocation.model_validate(item.input["workspace"])
        except ValidationError as exc:
            raise WorkflowExecutionError(
                f"workstream {item.key!r} input workspace allocation is invalid"
            ) from exc
        if input_allocation != allocation:
            raise WorkflowExecutionError(
                f"workstream {item.key!r} input workspace differs from its proposal"
            )
        optional_input_bindings: tuple[tuple[str, object, object], ...] = (
            ("childKey", item.input.get("childKey"), item.key),
            ("objective", item.input.get("objective"), item.objective),
            ("dependencies", item.input.get("dependencies"), list(item.dependencies)),
        )
        for field_name, actual, expected in optional_input_bindings:
            if field_name in item.input and actual != expected:
                raise WorkflowExecutionError(
                    f"workstream {item.key!r} input {field_name} differs from its proposal"
                )
        allowed_paths = tuple(str(value) for value in item.input.get("allowedPaths") or ())
        contract_ids = tuple(str(value) for value in item.input.get("testContractIds") or ())
        if "allowedPaths" in item.input and allocation.allowed_paths != allowed_paths:
            raise WorkflowExecutionError(
                f"workstream {item.key!r} workspace allowed paths differ from its input"
            )
        if "testContractIds" in item.input and allocation.test_contract_ids != contract_ids:
            raise WorkflowExecutionError(
                f"workstream {item.key!r} workspace test contracts differ from its input"
            )
    return cast(tuple[WorkstreamProposal, ...], ordered)


def make_children(
    execution: DeliveryExecution,
    generation: int,
    ordered: tuple[WorkstreamProposal, ...],
) -> tuple[ChildExecution, ...]:
    children: list[ChildExecution] = []
    now = datetime.now(UTC)
    for proposal in ordered:
        child_id = uuid4()
        intent_id = uuid4()
        children.append(
            ChildExecution(
                id=child_id,
                execution_id=execution.id,
                generation=generation,
                key=proposal.key,
                attempt=1,
                state=ChildExecutionState.RESERVED,
                dependencies=proposal.dependencies,
                requirement_ids=proposal.requirement_ids,
                objective=proposal.objective,
                template_id=execution.policy.template_id,
                template_revision=execution.policy.template_revision,
                template_digest=execution.policy.template_digest,
                plan_digest=proposal.plan_digest,
                input_digest=proposal.input_digest,
                input=proposal.input,
                repository=proposal.repository,
                base_sha=proposal.base_sha,
                budget_units=proposal.budget_units,
                deadline=proposal.deadline,
                agent_id=proposal.agent_id,
                skill_id=proposal.skill_id,
                intent_id=intent_id,
                message_id=f"workflow-execution:{execution.id}:{generation}:{proposal.key}:1",
                workspace=proposal.workspace,
                created_at=now,
                updated_at=now,
            )
        )
    return tuple(children)
