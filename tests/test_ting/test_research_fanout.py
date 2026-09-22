"""Proof the first non-delivery adopter of the workflow-execution blocks works.

The bundled "Research Campaign" workflow expands its `research-threads`
subworkflow node into parallel exploration threads backed by the bundled
"Research Thread" child workflow. This loads both real documents, builds the
pinned snapshot the way a launch does, launches a generic execution for that
node, expands it with the node's own declared children through the generic
`WorkflowExecutionService`, completes the threads with results validated
against the node's `resultSchema`, and asserts the join publishes exactly the
event the graph's outgoing edge declares.

No import here reaches into `ting.delivery`: this is proof the generic blocks
serve a workflow with no code-delivery concept at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from tests.test_ting.test_workflow_execution_service import (
    AcceptingEvidenceVerifier,
    InMemoryWorkflowRepository,
    PassthroughReviewAttestor,
    RecordingContinuation,
)
from ting.api.workflow_execution_auth import resolve_launch_expansion_policy
from ting.api.workflow_executions import _declared_children, _generic_proposal_from_payload
from ting.domain.execution_snapshot import pinned_child_workflow
from ting.domain.services.workflow_execution import WorkflowExecutionService
from ting.domain.workflow_continuation_events import subworkflow_joined_event
from ting.domain.workflow_document import workflow_document_revision
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ChildTaskHandle,
    ChildTaskObservation,
    ExecutionBudget,
    FailureKind,
    WorkflowExecution,
    WorkflowExecutionError,
    digest_json,
    validate_expansion,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.system_workflows import load_system_workflows

CAMPAIGN_SLUG = "test-campaign-fanout"


class ResultGateway:
    """A recording gateway whose completed result is exactly what the test sets.

    Unlike the delivery-flavored `RecordingGateway` fixture (which always
    wraps a bare attemptId), this returns whatever result payload the test
    queued for a child's key — the shape a workflow with its own resultSchema
    actually needs.
    """

    def __init__(self) -> None:
        self.results: dict[str, dict] = {}
        self.launched: dict[str, str] = {}

    async def launch_child(self, request, *, auth_token=""):
        del auth_token
        task_id = f"task-{request.work_order['childKey']}"
        self.launched[task_id] = request.work_order["childKey"]
        return ChildTaskHandle(request.agent_id, task_id, "context-1")

    async def get_child(self, handle, *, auth_token=""):
        del auth_token
        key = self.launched[handle.task_id]
        return ChildTaskObservation(
            handle=handle,
            state=ChildExecutionState.COMPLETED,
            result=self.results[key],
            observed_at=datetime.now(UTC),
            event_id=f"event-{handle.task_id}-{id(self.results[key])}",
        )

    async def cancel_child(self, handle, *, auth_token=""):
        del auth_token
        return ChildTaskObservation(
            handle=handle,
            state=ChildExecutionState.CANCELED,
            observed_at=datetime.now(UTC),
            event_id=f"cancel-{handle.task_id}",
        )

    async def reply_child(self, handle, *, answer, metadata, message_id, auth_token=""):
        raise NotImplementedError


def _load_research_campaign():
    workflows = {workflow.name: workflow for workflow in load_system_workflows()}
    return workflows["Research Campaign"]


def _build_execution() -> tuple[WorkflowExecution, dict]:
    """Launch-shape a research-threads execution from the real bundled documents."""
    workflow = _load_research_campaign()
    node, policy = resolve_launch_expansion_policy(workflow, "research-threads")
    persona_source = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    snapshot = build_workflow_snapshot(workflow, persona_source=persona_source)
    now = datetime.now(UTC)
    execution = WorkflowExecution(
        id=uuid4(),
        name=workflow.name,
        prompt="Investigate whether durable subworkflow fan-out serves plain research.",
        owner_id="researcher-1",
        tenant_id="niuu-labs",
        workflow_id=workflow.id,
        workflow_revision=workflow.revision or workflow_document_revision(workflow),
        workflow_digest=workflow_document_revision(workflow),
        parent_session_id="session-research-1",
        parent_node_id=str(node["id"]),
        connection_id="platform-1",
        policy=policy,
        budget=ExecutionBudget(total_units=100),
        deadline=now + timedelta(hours=2),
        workflow_snapshot=snapshot,
        created_at=now,
        updated_at=now,
    )
    return execution, node


def _service(repository, *, gateway=None, continuation=None) -> WorkflowExecutionService:
    return WorkflowExecutionService(
        repository=repository,
        gateway=gateway or ResultGateway(),
        continuation=continuation or RecordingContinuation(),
        evidence_verifier=AcceptingEvidenceVerifier(),
        review_attestor=PassthroughReviewAttestor(),
        worker_id="worker-research-1",
        launch_claim_limit=8,
        reconcile_limit=100,
        lease_seconds=30,
    )


def _proposal_payload(entry: dict, *, plan_revision: str, deadline: datetime) -> dict:
    """Turn one declared child into a full expansion proposal, the way the
    research-coordinator persona is instructed to: keep key/objective/
    dependencies/template, merge the real campaign slug into input, and add
    the execution-time fields the graph does not (and should not) know
    about."""
    return {
        "key": entry["key"],
        "objective": entry["objective"],
        "dependencies": entry.get("dependencies", []),
        "template": entry["template"],
        "input": {**entry.get("input", {}), "slug": CAMPAIGN_SLUG},
        "budgetUnits": 8,
        "deadline": deadline.isoformat(),
        "agentId": "research-agent",
        "skillId": "research-thread-skill",
    }


def _valid_result(angle: str) -> dict:
    return {
        "question": f"What does the {angle} pass reveal about the framed question?",
        "findings": [f"{angle} finding one", f"{angle} finding two"],
        "sources": [f"src_{angle}_1", f"src_{angle}_2"],
        "confidence": "medium",
        "openQuestions": [f"What remains open for {angle}"],
    }


@pytest.mark.asyncio
async def test_research_threads_declared_children_join_publishes_the_graph_event() -> None:
    execution, node = _build_execution()
    assert node.get("kind") == "subworkflow"
    declared = _declared_children(execution)
    assert declared is not None
    assert {entry["key"] for entry in declared} == {"breadth", "depth", "contrarian"}
    assert {entry["template"] for entry in declared} == {"breadth", "depth", "contrarian"}

    plan_revision = "framing-pass-1"
    plan_digest = digest_json({"planRevision": plan_revision})
    proposals = [
        _generic_proposal_from_payload(
            _proposal_payload(entry, plan_revision=plan_revision, deadline=execution.deadline),
            plan_digest=plan_digest,
        )
        for entry in declared
    ]
    for proposal, entry in zip(proposals, declared, strict=True):
        assert proposal.key == entry["key"]
        assert proposal.template == entry["template"]
        assert proposal.input["angle"] == entry["input"]["angle"]
        assert proposal.input["slug"] == CAMPAIGN_SLUG

    # A fourth, runtime-invented thread the coordinator adds beyond the
    # declared defaults, using the plain angle-driven `general` template.
    wildcard_entry = {
        "key": "wildcard",
        "objective": "Chase a lead the frame surfaced that none of the fixed angles cover.",
        "template": "general",
        "input": {"slug": CAMPAIGN_SLUG, "angle": "wildcard"},
    }
    proposals.append(
        _generic_proposal_from_payload(
            {
                "key": wildcard_entry["key"],
                "objective": wildcard_entry["objective"],
                "template": wildcard_entry["template"],
                "input": wildcard_entry["input"],
                "budgetUnits": 8,
                "deadline": execution.deadline.isoformat(),
                "agentId": "research-agent",
                "skillId": "research-thread-skill",
            },
            plan_digest=plan_digest,
        )
    )

    repository = InMemoryWorkflowRepository(execution)
    gateway = ResultGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway=gateway, continuation=continuation)

    reserved = await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id="research-coordinator",
        generation=1,
        plan_revision=plan_revision,
        children=proposals,
    )
    assert reserved.current_generation == 1
    assert {child.key for child in repository.children} == {
        "breadth",
        "depth",
        "contrarian",
        "wildcard",
    }

    # Each declared child resolves to a DIFFERENT frozen child workflow
    # document, each naming its own dedicated explorer persona; the runtime
    # "wildcard" child resolves to the shared generic thread and persona.
    resolved_by_key = {
        child.key: pinned_child_workflow(execution, child) for child in repository.children
    }
    assert len({document.id for document in resolved_by_key.values()}) == 4
    persona_by_key = {
        key: set(document.persona_dependencies) for key, document in resolved_by_key.items()
    }
    assert persona_by_key["breadth"] == {"research-explorer-breadth"}
    assert persona_by_key["depth"] == {"research-explorer-depth"}
    assert persona_by_key["contrarian"] == {"research-explorer-contrarian"}
    assert persona_by_key["wildcard"] == {"research-explorer"}

    await repository.seal_generation(execution.id, 1)

    # Each child's gateway result must validate against the node's own
    # resultSchema, not some ambient delivery contract.
    for child in repository.children:
        angle = child.input["angle"]
        gateway.results[child.key] = _valid_result(angle)

    result = await service.reconcile(execution.id)

    assert result["projected"] == 4
    assert all(child.state == ChildExecutionState.COMPLETED for child in repository.children)
    assert len(continuation.resumed) == 1
    resumed_id, generation, results = continuation.resumed[0]
    assert resumed_id == execution.id
    assert generation == 1
    assert {item["childKey"] for item in results} == {
        "breadth",
        "depth",
        "contrarian",
        "wildcard",
    }
    for item in results:
        assert set(item["result"]) == {
            "question",
            "findings",
            "sources",
            "confidence",
            "openQuestions",
        }

    # The join is a generic projection: it never names an event itself. The
    # event a real continuation adapter would publish comes straight from the
    # graph's own outgoing edge off this node.
    graph = execution.workflow_snapshot["graph"]
    assert subworkflow_joined_event(graph, "research-threads") == "research.threads.joined"


@pytest.mark.asyncio
async def test_research_thread_result_violating_result_schema_is_rejected() -> None:
    execution, _node = _build_execution()
    declared = _declared_children(execution)
    plan_revision = "framing-pass-1"
    plan_digest = digest_json({"planRevision": plan_revision})
    proposal = _generic_proposal_from_payload(
        _proposal_payload(declared[0], plan_revision=plan_revision, deadline=execution.deadline),
        plan_digest=plan_digest,
    )

    repository = InMemoryWorkflowRepository(execution)
    gateway = ResultGateway()
    service = _service(repository, gateway=gateway)

    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id="research-coordinator",
        generation=1,
        plan_revision=plan_revision,
        children=[proposal],
    )
    await repository.seal_generation(execution.id, 1)

    child = repository.children[0]
    # Missing "sources" and an out-of-enum confidence: invalid against the
    # research-threads resultSchema.
    gateway.results[child.key] = {
        "question": "Incomplete result",
        "findings": ["one finding"],
        "confidence": "extremely-sure",
        "openQuestions": [],
    }

    await service.reconcile(execution.id)

    blocked = repository.children[0]
    assert blocked.state == ChildExecutionState.BLOCKED
    assert blocked.failure_kind == FailureKind.CONTRACT_INVALID
    assert blocked.error


def test_research_workflow_declares_no_reviewattestation_and_stays_generic() -> None:
    """Sanity check the fixture: research-campaign has no delivery baggage."""
    execution, node = _build_execution()
    assert node["allowedCoordinator"] == "research-coordinator"
    assert node["templates"] == {
        "breadth": "breadth",
        "depth": "depth",
        "contrarian": "contrarian",
        "general": "general",
    }
    assert execution.policy.join_mode == "all"
    with pytest.raises(WorkflowExecutionError):
        # A coordinator identity that does not match the pinned node must be
        # rejected the same way the generic engine rejects any mismatch.
        validate_expansion(
            execution,
            coordinator_id="someone-else",
            generation=1,
            children=[],
        )
