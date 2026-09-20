"""Shared authorization helpers for the workflow-execution routers.

Both the generic workflow-execution router and the delivery-execution router
authorize their mutating routes the same way: an owner/tenant-scoped lookup,
one coordinator scope, a workload JWT bound to the exact execution and parent
session it claims to act on, and — for a handful of operations — a bounded
child credential instead. This module holds that domain-neutral machinery so
neither router duplicates or weakens it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import jwt
from fastapi import HTTPException, Request

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE, require_scope
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ExpansionPolicy,
    WorkflowExecution,
    WorkflowExecutionError,
)
from ting.ports.workflow_execution import WorkflowExecutionRepository

COORDINATE_SCOPE = "ting:workflow:coordinate"
"""The scope a workload credential needs to coordinate an execution's lifecycle."""

require_coordinate_scope = require_scope(COORDINATE_SCOPE)
"""FastAPI dependency rejecting a scoped workload token missing COORDINATE_SCOPE."""


async def owned_execution(
    repository: WorkflowExecutionRepository,
    execution_id: UUID,
    principal: Principal,
) -> WorkflowExecution:
    """Fetch one execution scoped to its owner and tenant, or raise 404."""
    execution = await repository.get(
        execution_id,
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )
    if execution is None:
        raise HTTPException(status_code=404, detail="Workflow execution not found")
    return execution


def resolve_launch_expansion_policy(
    workflow: Any, node_id: str
) -> tuple[dict[str, Any], ExpansionPolicy]:
    """Resolve the subworkflow node a launch expands into and its policy.

    Shared by every launch route: the caller names a node id, and that node
    must be a schema-v2 ``subworkflow`` declaring a workflow dependency this
    workflow actually has. Nothing about the node's launch (its coordinator,
    its child template, its bounds) is inferred from the caller.
    """
    node = next(
        (
            candidate
            for candidate in workflow.graph.get("nodes", [])
            if isinstance(candidate, dict) and str(candidate.get("id") or "") == node_id
        ),
        None,
    )
    if node is None or node.get("kind") != "subworkflow":
        raise WorkflowExecutionError(
            f"Workflow does not declare a subworkflow node {node_id!r} to expand"
        )
    dependency_alias = str(node.get("workflowDependency") or "")
    dependency = workflow.workflow_dependencies.get(dependency_alias)
    if dependency is None:
        raise WorkflowExecutionError("subworkflow references an undeclared workflow dependency")
    return node, ExpansionPolicy(
        coordinator_id=str(node.get("allowedCoordinator") or ""),
        workflow_dependency=dependency_alias,
        template_id=dependency.id,
        template_revision=dependency.revision,
        template_digest=dependency.digest,
        input_schema=dict(node.get("inputSchema") or {}),
        result_schema=dict(node.get("resultSchema") or {}),
        max_children=int(node.get("maxChildren") or 0),
        max_attempts=int(node.get("maxAttempts") or 0),
        max_active_children=int(node.get("maxActiveChildren") or node.get("maxChildren") or 0),
        join_mode=str(node.get("joinMode") or ""),
    )


def assert_coordinator_claims(
    request: Request,
    bearer_token: str | None,
    execution: WorkflowExecution,
) -> None:
    """Bind a workload JWT to the exact execution node and parent session it claims."""
    settings = getattr(request.app.state, "settings", None)
    if not bearer_token and settings is not None and settings.auth.allow_anonymous_dev:
        return
    try:
        claims = jwt.decode(
            bearer_token or "",
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=403, detail="Coordinator credential is invalid") from exc
    expected_session_key = f"workflow:execution-{execution.id.hex}"
    expected = {
        "token_use": VALKYRIE_BUILD_TOKEN_USE,
        "workload_workflow_execution_id": str(execution.id),
        "workload_parent_node_id": execution.parent_node_id,
        "workload_parent_session_key": expected_session_key,
        "workload_coordinator_id": execution.policy.coordinator_id,
        "workload_forge_session_id": execution.parent_session_id,
    }
    if any(claims.get(key) != value for key, value in expected.items()):
        raise HTTPException(
            status_code=403,
            detail="Coordinator credential is not bound to this execution node and session",
        )
    scopes = claims.get("scopes")
    if not isinstance(scopes, list) or COORDINATE_SCOPE not in scopes:
        raise HTTPException(status_code=403, detail="Coordinator credential scope is missing")


def assert_parent_workload_claims_if_scoped(
    request: Request,
    bearer_token: str | None,
    execution: WorkflowExecution,
) -> None:
    """Bind workload JWTs to the parent session without changing human/PAT access."""
    try:
        claims = jwt.decode(
            bearer_token or "",
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return
    if claims.get("token_use") != VALKYRIE_BUILD_TOKEN_USE:
        return
    assert_coordinator_claims(request, bearer_token, execution)


async def assert_child_credential_lineage(
    request: Request,
    bearer_token: str | None,
    execution: WorkflowExecution,
    repository: WorkflowExecutionRepository,
    *,
    operation: str,
    allowed_child_operations: frozenset[str],
    credential_label: str = "Child credential",
) -> None:
    """Bind a scoped workload JWT to the parent session, or to one exact active child.

    The generic engine behind "may this credential authorize `operation` on
    this execution": a parent-session-bound credential always may; a
    child-bound credential may only for the operations named in
    `allowed_child_operations`, and only while that exact attempt is still
    the current, active one for its key and generation.
    """
    settings = getattr(request.app.state, "settings", None)
    if not bearer_token and settings is not None and settings.auth.allow_anonymous_dev:
        return
    try:
        claims = jwt.decode(
            bearer_token or "",
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=403, detail=f"{credential_label} is invalid") from exc
    common = {
        "token_use": VALKYRIE_BUILD_TOKEN_USE,
        "workload_workflow_execution_id": str(execution.id),
        "workload_parent_node_id": execution.parent_node_id,
        "workload_coordinator_id": execution.policy.coordinator_id,
    }
    scopes = claims.get("scopes")
    if any(claims.get(key) != value for key, value in common.items()) or not (
        isinstance(scopes, list) and COORDINATE_SCOPE in scopes
    ):
        raise HTTPException(status_code=403, detail=f"{credential_label} lineage is invalid")
    parent_key = f"workflow:execution-{execution.id.hex}"
    if claims.get("workload_parent_session_key") == parent_key:
        if (
            not execution.parent_session_id
            or claims.get("workload_forge_session_id") != execution.parent_session_id
        ):
            raise HTTPException(
                status_code=403,
                detail=f"{credential_label} is not bound to the parent Forge session",
            )
        return
    if operation not in allowed_child_operations:
        raise HTTPException(
            status_code=403,
            detail=f"Child credentials cannot authorize operation {operation}",
        )
    raw_attempt_id = str(claims.get("workload_child_attempt_id") or "")
    try:
        attempt_id = UUID(raw_attempt_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Child attempt is invalid") from exc
    child = await repository.get_child(attempt_id)
    child_session_key = str(claims.get("workload_sub") or "")
    child_task_id = str(claims.get("workload_child_task_id") or "")
    if (
        child is None
        or child.execution_id != execution.id
        or not child_session_key.startswith("workflow:a2a-")
        or claims.get("workload_parent_session_key") != child_session_key
        or not child.task_id
        or child.task_id != child_task_id
    ):
        raise HTTPException(status_code=403, detail="Child lineage is invalid")
    campaign_repo = getattr(request.app.state, "workflow_campaign_repo", None)
    campaign = (
        await campaign_repo.get_campaign_by_slug(child_task_id, owner_id=execution.owner_id)
        if campaign_repo is not None
        else None
    )
    if (
        campaign is None
        or campaign.tenant_id != execution.tenant_id
        or not campaign.session_id
        or claims.get("workload_forge_session_id") != campaign.session_id
    ):
        raise HTTPException(
            status_code=403,
            detail=f"{credential_label} is not bound to the child Forge session",
        )
    children = await repository.list_children(execution.id)
    current = max(
        (
            candidate
            for candidate in children
            if candidate.generation == child.generation and candidate.key == child.key
        ),
        key=lambda candidate: candidate.attempt,
        default=None,
    )
    if (
        current is None
        or current.id != child.id
        or child.generation != execution.current_generation
        or child.state
        not in {
            ChildExecutionState.LAUNCHING,
            ChildExecutionState.SUBMITTED,
            ChildExecutionState.RUNNING,
            ChildExecutionState.BLOCKED,
        }
    ):
        raise HTTPException(status_code=403, detail="Child attempt is no longer active")
