"""Code-delivery specialization of the shared execution authorization engine."""

from __future__ import annotations

from fastapi import Request

from ting.api.workflow_execution_auth import assert_child_credential_lineage
from ting.delivery.domain import DeliveryExecution
from ting.delivery.ports import DeliveryExecutionRepository
from ting.domain.workflow_execution import ExecutionState

#: States in which a delivery operation may still be authorized. A canceling,
#: canceled, completed, or failed execution accepts none of them.
_ACTIVE_DELIVERY_STATES = frozenset(
    {ExecutionState.RUNNING, ExecutionState.WAITING, ExecutionState.BLOCKED}
)

#: Operations a child (workstream) credential may authorize, as opposed to
#: operations that only the parent coordinator session may authorize.
CHILD_DELIVERY_OPERATIONS = frozenset({"run_verification", "validate_evidence"})

#: Every delivery operation `/delivery-authorizations` may be asked to
#: authorize, and the execution states in which each remains allowed.
DELIVERY_OPERATION_STATES: dict[str, frozenset[ExecutionState]] = {
    operation: _ACTIVE_DELIVERY_STATES
    for operation in (
        "allocate_workstream",
        "run_verification",
        "validate_evidence",
        "integrate_candidate",
        "publish_branch",
        "open_review",
        "inspect_candidate",
        "inspect_integration",
        "conditional_merge",
        "reconcile_merge",
    )
}


async def assert_delivery_claims(
    request: Request,
    bearer_token: str | None,
    execution: DeliveryExecution,
    repository: DeliveryExecutionRepository,
    *,
    operation: str,
) -> None:
    """Bind a scoped workload JWT to the parent session, or to one active workstream."""
    await assert_child_credential_lineage(
        request,
        bearer_token,
        execution,
        repository,
        operation=operation,
        allowed_child_operations=CHILD_DELIVERY_OPERATIONS,
        credential_label="Delivery credential",
    )
