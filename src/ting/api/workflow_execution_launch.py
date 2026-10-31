"""Shared launch of a durable workflow-execution parent session.

A workflow whose graph fans out through a ``subworkflow`` node needs its
coordinator persona to hold the ``workflow_execution_*`` tools, and those tools
only exist when the parent session was launched with an owner-bound
``workflow_execution`` runtime context. That context is minted here, and only
here, for every route that launches such a parent:

1. :func:`new_parent_execution` builds the durable ``WorkflowExecution`` row a
   route reserves before it spawns anything.
2. :func:`parent_launch_body` turns the reserved row into the trusted
   ``context.workflow_execution`` + ``provenance.workflow_execution`` launch
   body Forge projects into the parent's Ravn config and credential.
3. :func:`launch_reserved_parent` spawns the parent with that trusted
   provenance and attaches the session to the reserved row.

``POST /workflow-executions`` and ``POST /research/campaigns`` both go through
these, so the two cannot drift on what a coordinator is handed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request

from niuu.domain.agent_directory import configured_agent_id
from niuu.domain.models import Principal
from ting.api.a2a_identity import local_agent_card_url
from ting.api.workflow_execution_auth import resolve_launch_expansion_policy
from ting.api.workflows import (
    WorkflowLaunchBody,
    WorkflowLaunchExecution,
    launch_workflow_execution,
)
from ting.domain.models import WorkflowDefinition
from ting.domain.workflow_document import workflow_document_revision
from ting.domain.workflow_execution import (
    ExecutionBudget,
    WorkflowExecution,
    WorkflowExecutionError,
    digest_json,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_execution import WorkflowExecutionRepository

#: Tool-name prefix Ravn's tool builder treats as needing an owner-bound
#: ``workflow_execution`` runtime context (``ravn.cli.tool_builders``).
_WORKFLOW_EXECUTION_TOOL = "workflow_execution"


def parent_session_key(execution_id: UUID) -> str:
    """The workload subject a parent execution's coordinator credential binds to."""
    return f"workflow:execution-{execution_id.hex}"


def durable_parent_node_id(
    workflow: WorkflowDefinition,
    workflow_snapshot: dict[str, Any] | None,
) -> str | None:
    """Return the subworkflow node a launch must expand durably, if it needs one.

    A workflow needs a durable parent execution when its graph declares a
    ``subworkflow`` node, or when any persona it runs holds
    ``workflow_execution_*`` tools — Ravn refuses to start such a persona
    without the owner-bound runtime context only a durable launch provides.

    Raises ``HTTPException(422)`` when the need is real but cannot be met
    from one launch: several subworkflow nodes (a parent execution expands
    exactly one), or coordinator tools with no subworkflow node to expand.
    """
    subworkflow_ids = [
        str(node.get("id") or "")
        for node in (workflow.graph or {}).get("nodes", [])
        if isinstance(node, dict) and node.get("kind") == "subworkflow"
    ]
    if len(subworkflow_ids) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "Workflow declares several subworkflow nodes; launch it through "
                "/workflow-executions with an explicit parentNodeId"
            ),
        )
    if subworkflow_ids:
        return subworkflow_ids[0]
    if _personas_need_workflow_execution(workflow_snapshot):
        raise HTTPException(
            status_code=422,
            detail=(
                "Workflow personas require durable workflow execution tools, but the "
                "workflow declares no subworkflow node for them to coordinate"
            ),
        )
    return None


def _personas_need_workflow_execution(workflow_snapshot: dict[str, Any] | None) -> bool:
    definitions = (
        workflow_snapshot.get("persona_definitions")
        if isinstance(workflow_snapshot, dict)
        else None
    )
    if not isinstance(definitions, dict):
        return False
    for document in definitions.values():
        definition = document.get("definition") if isinstance(document, dict) else None
        tools = definition.get("allowed_tools") if isinstance(definition, dict) else None
        if not isinstance(tools, list):
            continue
        if any(
            str(tool) == _WORKFLOW_EXECUTION_TOOL
            or str(tool).startswith(f"{_WORKFLOW_EXECUTION_TOOL}_")
            for tool in tools
        ):
            return True
    return False


def new_parent_execution(
    *,
    request: Request,
    principal: Principal,
    workflow: WorkflowDefinition,
    execution_id: UUID,
    parent_node_id: str,
    prompt: str,
    name: str,
    launch_key: str,
    launch_request: dict[str, Any],
    input: dict[str, Any] | None = None,
    budget_units: int | None = None,
    deadline: datetime | None = None,
    workflow_snapshot: dict[str, Any] | None = None,
) -> WorkflowExecution:
    """Build the unreserved parent execution row for one subworkflow node.

    ``launch_request`` is the caller's own request payload; it is digested
    with the workflow revision so a replayed launch key can be told apart
    from a conflicting one. ``workflow_snapshot`` is the pinned snapshot the
    caller already built, if any; otherwise it is built here once the launch
    has been validated. Raises ``HTTPException(422)`` for a workflow the
    engine cannot expand durably or an invalid deadline.
    """
    if workflow.schema_version < 2:
        raise HTTPException(status_code=422, detail="Workflow requires schema v2")
    try:
        node, policy = resolve_launch_expansion_policy(workflow, parent_node_id)
    except WorkflowExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    settings = request.app.state.settings.workflow_execution
    now = datetime.now(UTC)
    resolved_deadline = deadline or now + timedelta(seconds=settings.default_deadline_seconds)
    if resolved_deadline.tzinfo is None:
        raise HTTPException(status_code=422, detail="deadline must include a timezone")
    if resolved_deadline <= now:
        raise HTTPException(status_code=422, detail="deadline must be in the future")
    if workflow_snapshot is None:
        workflow_snapshot = build_workflow_snapshot(
            workflow, persona_source=getattr(request.app.state, "persona_source", None)
        )
    revision = workflow_document_revision(workflow)
    launch_digest = digest_json(
        {
            "workflowId": str(workflow.id),
            "workflowDigest": revision,
            "request": launch_request,
        }
    )
    return WorkflowExecution(
        id=execution_id,
        name=name or workflow.name,
        prompt=prompt,
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
        workflow_id=workflow.id,
        workflow_revision=workflow.revision or revision,
        workflow_digest=revision,
        workflow_snapshot=workflow_snapshot,
        parent_session_id="",
        parent_node_id=str(node["id"]),
        connection_id="",
        policy=policy,
        budget=ExecutionBudget(total_units=budget_units or settings.default_budget_units),
        deadline=resolved_deadline,
        input=dict(input or {}),
        launch_key=launch_key,
        launch_digest=launch_digest,
        suspension_reason="launching_parent",
        created_at=now,
        updated_at=now,
    )


def parent_launch_body(
    reserved: WorkflowExecution,
    *,
    request: Request,
    prompt: str,
    session_name: str,
    model: str = "",
    connection_id: str | None = None,
    repo: str = "",
    branch: str = "",
    definition: str | None = None,
    workflow_version: str | None = None,
    gate_auto_forward_after: str | None = None,
) -> WorkflowLaunchBody:
    """The trusted launch body that binds a parent session to ``reserved``.

    ``context.workflow_execution`` tells the coordinator what it coordinates;
    ``provenance.workflow_execution`` is what Forge turns into the parent's
    Ravn ``workflow_execution`` config and owner-bound credential. It carries
    no credential itself — :func:`launch_workflow_execution` rejects one.
    """
    settings = request.app.state.settings
    api_base_url = settings.a2a.public_base_url.rstrip("/") or str(request.base_url).rstrip("/")
    card_url = local_agent_card_url(
        public_base_url=settings.a2a.public_base_url,
        request_base_url=str(request.base_url),
    )
    local_agent_id = configured_agent_id(card_url)
    return WorkflowLaunchBody(
        prompt=prompt,
        sessionName=session_name,
        repo=repo,
        branch=branch,
        model=model,
        connectionId=connection_id,
        definition=definition,
        workflowVersion=workflow_version,
        gateAutoForwardAfter=gate_auto_forward_after,
        context={
            "workflow_execution": {
                "campaign_id": str(reserved.id),
                "execution_id": str(reserved.id),
                "parent_node_id": reserved.parent_node_id,
                "coordinator_id": reserved.policy.coordinator_id,
                "deadline": reserved.deadline.isoformat(),
                "budget": {
                    "total_units": reserved.budget.total_units,
                    "reserved_units": reserved.budget.reserved_units,
                    "spent_units": reserved.budget.spent_units,
                    "available_units": reserved.budget.available_units,
                },
                "current_generation": reserved.current_generation,
                "workflow": {
                    "id": str(reserved.workflow_id),
                    "revision": reserved.workflow_revision,
                    "digest": reserved.workflow_digest,
                },
                "templates": launch_template_context(
                    reserved, agent_id=local_agent_id, card_url=card_url
                ),
            }
        },
        provenance={
            "surface": "workflow_execution",
            "workflow_execution_id": str(reserved.id),
            "workflow_execution": {
                "base_url": api_base_url,
                "execution_id": str(reserved.id),
                "parent_node_id": reserved.parent_node_id,
                "parent_session_key": parent_session_key(reserved.id),
                "coordinator_id": reserved.policy.coordinator_id,
            },
        },
    )


async def launch_reserved_parent(
    *,
    request: Request,
    workflow: WorkflowDefinition,
    reserved: WorkflowExecution,
    launch: WorkflowLaunchBody,
    execution_repo: WorkflowExecutionRepository,
    volundr_factory: VolundrFactory,
    principal: Principal,
    bearer_token: str | None,
) -> tuple[WorkflowLaunchExecution, WorkflowExecution]:
    """Spawn the trusted parent session for ``reserved`` and attach it."""
    launched = await launch_workflow_execution(
        request=request,
        workflow=workflow,
        pinned_workflow_snapshot=reserved.workflow_snapshot,
        launch=launch,
        volundr_factory=volundr_factory,
        principal=principal,
        bearer_token=bearer_token,
        trusted_workflow_execution=True,
    )
    attached = await execution_repo.attach_parent_session(
        reserved.id,
        session_id=launched.session.id,
        connection_id=launched.connection_id or "",
    )
    return launched, attached


def launch_template_context(
    execution: WorkflowExecution, *, agent_id: str, card_url: str
) -> dict[str, Any]:
    """Give the coordinator each named template's identity and A2A skill id.

    A node offering several templates hands the coordinator one entry per
    template so it can pick the right `skillId` for whichever `template` name
    it proposes for a child — see `template_descriptions` for the read-only
    projection used once the execution already exists.
    """
    descriptions = template_descriptions(execution)
    return {
        name: {
            "alias": template.dependency_alias,
            "template_id": str(template.id),
            "template_revision": template.revision,
            "template_digest": template.digest,
            "agent_id": agent_id,
            "skill_id": str(template.id),
            "agent_card_url": card_url,
            "description": descriptions.get(name, {}).get("description", ""),
        }
        for name, template in execution.policy.templates.items()
    }


def template_descriptions(execution: WorkflowExecution) -> dict[str, dict[str, Any]]:
    """Project each named template's identity and its child workflow's own description."""
    snapshot = execution.workflow_snapshot
    definitions = snapshot.get("workflow_definitions") if isinstance(snapshot, dict) else None
    definitions = definitions if isinstance(definitions, dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for name, template in execution.policy.templates.items():
        aggregate = definitions.get(template.dependency_alias)
        document = aggregate.get("document") if isinstance(aggregate, dict) else None
        description = str(document.get("description") or "") if isinstance(document, dict) else ""
        result[name] = {
            "templateId": str(template.id),
            "templateRevision": template.revision,
            "templateDigest": template.digest,
            "description": description,
        }
    return result
