"""Router tests for the code-delivery specialization of workflow executions.

These are the delivery-shaped routes split out of
``ting.api.workflow_executions``: git-bound launch, workstream expansion,
integration, completion, evidence, and delivery authorization, all exposed
under ``/api/v1/ting/delivery-executions``.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import ting.delivery.api as delivery_api
from niuu.domain.agent_directory import configured_agent_id
from niuu.domain.delivery import (
    IntegrationCandidateInspection,
    IntegrationReceipt,
    ResolvedRef,
    WorkspaceAllocation,
)
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from tests.test_ting.test_delivery_execution import (
    MemoryRepository,
    RecordingGateway,
    _execution,
    _proposal,
    _service,
)
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflow_executions import (
    create_workflow_executions_router,
    resolve_workflow_execution_repo,
    resolve_workflow_execution_service,
)
from ting.api.workflows import _build_workflow_initiative_context, resolve_workflow_repo
from ting.delivery.api import (
    create_delivery_executions_router,
    resolve_delivery_execution_repo,
    resolve_delivery_execution_service,
)
from ting.delivery.domain import make_children
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ExecutionConflictError,
    ExecutionState,
    WorkflowExecutionError,
)
from ting.system_workflows import load_system_workflows


def _context():
    execution = _execution(current_generation=1, state=ExecutionState.RUNNING)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.COMPLETED,
        result={"candidateSha": "b" * 40, "candidateTree": "c" * 40},
    )
    repository = MemoryRepository(execution)
    repository.children = [child]
    repository.sealed = True
    allocation = WorkspaceAllocation(
        allocation_id="integration-attempt-1",
        campaign_id=str(execution.id),
        workstream_key="integration",
        repository=execution.repository,
        workspace_path="/worktrees/integration",
        repository_path="/repositories/project",
        base_sha=execution.base_sha,
        branch_name="integration/candidate",
        worker_id="integrator",
        allowed_paths=("src",),
    )
    receipt = IntegrationReceipt(
        receipt_id="integration-receipt-1",
        campaign_id=str(execution.id),
        integration_allocation_id=allocation.allocation_id,
        workstream_key=allocation.workstream_key,
        attempt_id=str(child.id),
        repository=execution.repository,
        base_sha=execution.base_sha,
        candidate_sha="b" * 40,
        candidate_tree="c" * 40,
        previous_integration_sha=execution.base_sha,
        integrated_commits=("b" * 40,),
        resulting_sha="b" * 40,
        resulting_tree="c" * 40,
        completed_at=datetime.now(UTC),
    )
    inspection = IntegrationCandidateInspection(
        receipt_id=receipt.receipt_id,
        campaign_id=str(execution.id),
        integration_allocation_id=allocation.allocation_id,
        repository=execution.repository,
        base_sha=execution.base_sha,
        candidate_sha=receipt.resulting_sha,
        candidate_tree=receipt.resulting_tree,
        receipt_ids=(receipt.receipt_id,),
        integrated_candidates=(
            {
                "workstream_key": child.key,
                "attempt_id": str(child.id),
                "candidate_sha": "b" * 40,
                "candidate_tree": "c" * 40,
            },
        ),
        changed_paths=("src/api.py",),
        patch="diff --git a/src/api.py b/src/api.py",
        inspected_at=datetime.now(UTC),
    )
    return execution, repository, allocation, receipt, inspection


def _token(execution, *, forge_session_id: str | None = None) -> str:
    session_key = f"workflow:execution-{execution.id.hex}"
    return jwt.encode(
        {
            "sub": execution.owner_id,
            "tenant_id": execution.tenant_id,
            "token_use": "valkyrie_build",
            "scopes": ["ting:workflow:coordinate"],
            "workload_workflow_execution_id": str(execution.id),
            "workload_parent_node_id": execution.parent_node_id,
            "workload_parent_session_key": session_key,
            "workload_coordinator_id": execution.policy.coordinator_id,
            "workload_forge_session_id": forge_session_id or execution.parent_session_id,
        },
        "test-key-for-workflow-execution-32-bytes",
        algorithm="HS256",
    )


def _child_token(execution) -> str:
    return jwt.encode(
        {
            "sub": execution.owner_id,
            "tenant_id": execution.tenant_id,
            "token_use": "valkyrie_build",
            "scopes": ["ting:workflow:coordinate"],
            "workload_workflow_execution_id": str(execution.id),
            "workload_parent_node_id": execution.parent_node_id,
            "workload_parent_session_key": "workflow:a2a-child",
            "workload_coordinator_id": execution.policy.coordinator_id,
            "workload_child_attempt_id": "attempt-1",
            "workload_child_task_id": "task-1",
            "workload_forge_session_id": "child-session-1",
        },
        "test-key-for-workflow-execution-32-bytes",
        algorithm="HS256",
    )


def _settings(*, anonymous: bool = False):
    return SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=anonymous),
        a2a=SimpleNamespace(public_base_url="https://ting.example"),
        workflow_execution=SimpleNamespace(
            lease_seconds=60.0,
            default_deadline_seconds=3600,
            default_budget_units=100,
            list_page_size=50,
            delivery=SimpleNamespace(
                evidence_policy_id="developer-workstream",
                integration_policy_id="developer-integration",
            ),
        ),
    )


def _headers(execution=None, *, owner="owner-1", tenant="tenant-1"):
    headers = {"x-auth-user-id": owner, "x-auth-tenant": tenant}
    if execution is not None:
        headers["authorization"] = f"Bearer {_token(execution)}"
    return headers


def _developer_workflow():
    return next(item for item in load_system_workflows() if item.name == "Developer Delivery")


def test_launch_reserves_frozen_snapshot_before_parent_session(monkeypatch) -> None:
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())
    captured = {}

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow if workflow_id == workflow.id else None

    class Adapter:
        target_id = "forge-1"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            return ResolvedRef(
                provider="gitlab",
                repository=repository_url,
                ref=ref,
                sha="a" * 40,
                observed_at=datetime.now(UTC),
            )

        async def list_sessions(self, **kwargs):
            return []

    class Factory:
        async def primary_for_owner(self, owner_id):
            assert owner_id == "owner-1"
            return Adapter()

    async def launch(**kwargs):
        captured.update(kwargs)
        assert repository.execution.workflow_snapshot
        assert kwargs["pinned_workflow_snapshot"] == repository.execution.workflow_snapshot
        developer = kwargs["launch"].provenance["workflow_execution"]
        assert developer["execution_id"] == str(repository.execution.id)
        return SimpleNamespace(
            session=SimpleNamespace(id="parent-session-1"),
            connection_id="forge-1",
        )

    monkeypatch.setattr(delivery_api, "launch_workflow_execution", launch)
    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "ticket-123"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 201, response.text
    assert repository.execution.parent_session_id == "parent-session-1"
    assert repository.execution.launch_key == "ticket-123"
    assert repository.execution.workflow_snapshot["workflow_definitions"]
    assert captured["workflow"].id == workflow.id


def test_launch_rejects_an_unknown_parent_node_id() -> None:
    """The caller must name an existing subworkflow node; nothing is inferred."""
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow if workflow_id == workflow.id else None

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "unknown-node"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "does-not-exist",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 422, response.text
    assert "does-not-exist" in response.text


def test_launch_rejects_a_parent_node_id_that_is_not_a_subworkflow() -> None:
    """A real node id of the wrong kind is rejected just like an unknown one."""
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow if workflow_id == workflow.id else None

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "wrong-kind-node"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-request",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 422, response.text
    assert "delivery-request" in response.text


def test_launch_recovers_reserved_parent_session_without_duplicate_spawn(monkeypatch) -> None:
    workflow = _developer_workflow()

    class Repository(MemoryRepository):
        async def reserve_parent_launch(self, execution):
            self.execution = replace(execution, updated_at=datetime.now(UTC) - timedelta(minutes=2))
            return self.execution, False

    repository = Repository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow

    class Adapter:
        target_id = "forge-recovery"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            return ResolvedRef(
                provider="gitlab",
                repository=repository_url,
                ref=ref,
                sha="a" * 40,
                observed_at=datetime.now(UTC),
            )

        async def list_sessions(self, **kwargs):
            return [
                SimpleNamespace(
                    id="recovered-session",
                    tracker_issue_id=f"workflow:execution-{repository.execution.id.hex}",
                )
            ]

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    async def duplicate_launch(**kwargs):
        raise AssertionError("recovered reservations must not spawn a second parent")

    monkeypatch.setattr(delivery_api, "launch_workflow_execution", duplicate_launch)
    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "recover-parent"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Recover this launch",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 201, response.text
    assert repository.execution.parent_session_id == "recovered-session"


def test_replayed_launch_context_uses_reserved_identity_and_excludes_token(monkeypatch) -> None:
    workflow = _developer_workflow()
    reserved_id = _execution().id
    candidate_id = None
    captured = {}

    class Repository(MemoryRepository):
        async def reserve_parent_launch(self, execution):
            nonlocal candidate_id
            candidate_id = execution.id
            self.execution = replace(
                execution,
                id=reserved_id,
                current_generation=2,
                updated_at=datetime.now(UTC) - timedelta(minutes=2),
            )
            return self.execution, False

    repository = Repository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow if workflow_id == workflow.id else None

    class Adapter:
        target_id = "forge-1"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            return ResolvedRef(
                provider="gitlab",
                repository=repository_url,
                ref=ref,
                sha="a" * 40,
                observed_at=datetime.now(UTC),
            )

        async def list_sessions(self, **kwargs):
            return []

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    async def launch(**kwargs):
        captured.update(kwargs)
        context = kwargs["launch"].context
        developer = context["workflow_execution"]
        assert developer == {
            "campaign_id": str(reserved_id),
            "execution_id": str(reserved_id),
            "parent_node_id": repository.execution.parent_node_id,
            "coordinator_id": repository.execution.policy.coordinator_id,
            "repository": repository.execution.repository,
            "base_ref": repository.execution.base_ref,
            "base_sha": repository.execution.base_sha,
            "deadline": repository.execution.deadline.isoformat(),
            "budget": {
                "total_units": 100,
                "reserved_units": 0,
                "spent_units": 0,
                "available_units": 100,
            },
            "evidence_policy_id": "developer-workstream",
            "integration_policy_id": "developer-integration",
            "current_generation": 2,
            "workflow": {
                "id": str(repository.execution.workflow_id),
                "revision": repository.execution.workflow_revision,
                "digest": repository.execution.workflow_digest,
            },
            "workstream_dependency": {
                "alias": repository.execution.policy.sole_template.dependency_alias,
                "template_id": str(repository.execution.policy.sole_template.id),
                "template_revision": repository.execution.policy.sole_template.revision,
                "template_digest": repository.execution.policy.sole_template.digest,
                "agent_id": configured_agent_id("https://ting.example/.well-known/agent-card.json"),
                "skill_id": str(repository.execution.policy.sole_template.id),
                "agent_card_url": "https://ting.example/.well-known/agent-card.json",
            },
        }
        assert "auth_token" not in kwargs["launch"].provenance["workflow_execution"]
        rendered = _build_workflow_initiative_context(
            workflow=workflow,
            launch=kwargs["launch"],
            slug="developer-launch",
        )
        assert "## Launch Context" in rendered
        assert f'"campaign_id": "{reserved_id}"' in rendered
        assert "auth_token" not in rendered
        assert "auth_token" not in json.dumps(context)
        return SimpleNamespace(
            session=SimpleNamespace(id="parent-session-replay"),
            connection_id="forge-1",
        )

    monkeypatch.setattr(delivery_api, "launch_workflow_execution", launch)
    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "replay-context"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Resume the reserved delivery",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 201, response.text
    assert candidate_id is not None
    assert candidate_id != reserved_id
    assert captured["launch"].context["workflow_execution"]["execution_id"] == str(reserved_id)
    assert repository.execution.parent_session_id == "parent-session-replay"


def test_real_service_routes_expand_reconcile_retry_cancel_and_evidence() -> None:
    """Delivery expansion/evidence and the generic reconcile/retry/cancel lifecycle
    operate on the same execution through their two respective routers — exactly
    how the real coordinator client calls them (see `HttpWorkflowExecutionClient`)."""
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway=gateway)
    proposal = _proposal(execution, "api")
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_delivery_execution_service] = lambda: service
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_workflow_execution_service] = lambda: service
    client = TestClient(app)
    headers = _headers(execution)
    url = f"/api/v1/ting/delivery-executions/{execution.id}"
    generic_url = f"/api/v1/ting/workflow-executions/{execution.id}"
    workstream = {
        "key": proposal.key,
        "objective": proposal.objective,
        "requirementIds": list(proposal.requirement_ids),
        "dependencies": list(proposal.dependencies),
        "input": proposal.input,
        "repository": proposal.repository,
        "baseSha": proposal.base_sha,
        "budgetUnits": proposal.budget_units,
        "deadline": proposal.deadline.isoformat(),
        "agentId": proposal.agent_id,
        "skillId": proposal.skill_id,
        "workspace": proposal.workspace,
    }

    expanded = client.post(
        url + "/expansions",
        headers=headers,
        json={
            "campaign_id": str(execution.id),
            "parent_node_id": execution.parent_node_id,
            "generation": 1,
            "plan_revision": "approved-v1",
            "workstreams": [workstream],
        },
    )
    reconciled = client.post(generic_url + "/reconcile", headers=headers, json={})
    assert expanded.status_code == 200, expanded.text
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["join"]["ready"] is True
    completed_child = repository.children[0]
    repository._replace(
        replace(
            completed_child,
            gate_report=repository.gate_reports[completed_child.id],
            gate_validated_at=datetime.now(UTC),
        )
    )
    evidence = client.get(url + "/evidence", headers=headers)
    assert evidence.json()["verification"]["status"] == "accepted"

    failed = replace(
        repository.children[0],
        state=ChildExecutionState.FAILED,
        error="transient worker failure",
    )
    repository._replace(failed)
    retried = client.post(
        generic_url + "/children/api/retry",
        headers=headers,
        json={"attempt_id": str(failed.id)},
    )
    assert retried.status_code == 200, retried.text
    assert max(child.attempt for child in repository.children) == 2

    canceled = client.post(generic_url + "/cancel", headers=headers, json={})
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["state"] == "canceled"
    assert gateway.cancelled


def test_evidence_reports_rejection_without_a_blocking_reason() -> None:
    execution, repository, _, _, _ = _context()
    repository._replace(
        replace(
            repository.children[0],
            gate_report={"accepted": False, "blocking_reasons": []},
            gate_validated_at=datetime.now(UTC),
        )
    )
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository

    response = TestClient(app).get(
        f"/api/v1/ting/delivery-executions/{execution.id}/evidence",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["verification"] == {
        "status": "rejected",
        "blockingReasons": [],
    }


def test_record_integration_persists_only_forge_inspected_candidate() -> None:
    execution, repository, allocation, receipt, inspection = _context()

    class Adapter:
        async def inspect_delivery_integration_chain(self, *args, **kwargs):
            assert args == (allocation, (receipt,))
            assert kwargs["policy_id"] == "developer-integration"
            return inspection

    class Factory:
        async def for_connection(self, owner_id, connection_id):
            assert (owner_id, connection_id) == (execution.owner_id, execution.connection_id)
            return Adapter()

    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/integration-candidate",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "integration_receipts": [receipt.model_dump(mode="json")],
            "integration_allocation": allocation.model_dump(mode="json"),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["integrationCandidate"]["candidate_sha"] == "b" * 40
    assert repository.execution.integration_receipts[0]["receipt_id"] == receipt.receipt_id


def test_record_integration_rejects_before_ready_child_join() -> None:
    execution, repository, allocation, receipt, _ = _context()
    repository.sealed = False
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: None

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/integration-candidate",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "integration_receipts": [receipt.model_dump(mode="json")],
            "integration_allocation": allocation.model_dump(mode="json"),
        },
    )

    assert response.status_code == 409
    assert repository.execution.integration_candidate is None


def test_record_integration_rejects_forge_chain_identity_mismatch() -> None:
    execution, repository, allocation, receipt, inspection = _context()
    mismatched = inspection.model_copy(update={"receipt_ids": ("other-receipt",)})

    class Adapter:
        async def inspect_delivery_integration_chain(self, *args, **kwargs):
            return mismatched

    class Factory:
        async def for_connection(self, owner_id, connection_id):
            return Adapter()

    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/integration-candidate",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "integration_receipts": [receipt.model_dump(mode="json")],
            "integration_allocation": allocation.model_dump(mode="json"),
        },
    )

    assert response.status_code == 409
    assert "chain identity" in response.json()["detail"]
    assert repository.execution.integration_candidate is None


def test_conditional_merge_authorization_requires_stored_review_and_exact_policy() -> None:
    execution, repository, _, _, inspection = _context()
    repository.execution = replace(
        execution,
        integration_candidate=inspection.model_dump(mode="json"),
        integration_review_receipt={
            "candidate_sha": inspection.candidate_sha,
            "candidate_tree": inspection.candidate_tree,
            "verdict": "pass",
        },
    )
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    client = TestClient(app)
    headers = {
        "authorization": f"Bearer {_token(execution)}",
        "x-auth-user-id": execution.owner_id,
        "x-auth-tenant": execution.tenant_id,
    }
    body = {
        "operation": "conditional_merge",
        "repository": execution.repository,
        "base_sha": execution.base_sha,
        "candidate_sha": inspection.candidate_sha,
        "candidate_tree": inspection.candidate_tree,
        "target_branch": execution.base_ref,
        "policy_id": "developer-integration",
    }

    accepted = client.post(
        f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations",
        headers=headers,
        json=body,
    )
    wrong_policy = client.post(
        f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations",
        headers=headers,
        json={**body, "policy_id": "permissive-policy"},
    )
    wrong_target = client.post(
        f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations",
        headers=headers,
        json={**body, "target_branch": "release"},
    )
    wrong_candidate = client.post(
        f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations",
        headers=headers,
        json={**body, "candidate_sha": "d" * 40},
    )

    assert accepted.status_code == 204, accepted.text
    assert wrong_policy.status_code == 409
    assert wrong_target.status_code == 409
    assert wrong_candidate.status_code == 409


def test_publish_branch_authorization_requires_exact_allocated_non_base_branch() -> None:
    execution, repository, allocation, _, inspection = _context()
    repository.execution = replace(
        execution,
        integration_allocation=allocation.model_dump(mode="json"),
        integration_candidate=inspection.model_dump(mode="json"),
    )
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    client = TestClient(app)
    headers = {
        "authorization": f"Bearer {_token(execution)}",
        "x-auth-user-id": execution.owner_id,
        "x-auth-tenant": execution.tenant_id,
    }
    body = {
        "operation": "publish_branch",
        "repository": execution.repository,
        "base_sha": execution.base_sha,
        "candidate_sha": inspection.candidate_sha,
        "candidate_tree": inspection.candidate_tree,
        "target_branch": allocation.branch_name,
        "policy_id": "developer-integration",
    }
    url = f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations"

    accepted = client.post(url, headers=headers, json=body)
    base_branch = client.post(
        url,
        headers=headers,
        json={**body, "target_branch": execution.base_ref},
    )
    arbitrary_branch = client.post(
        url,
        headers=headers,
        json={**body, "target_branch": "campaign/arbitrary"},
    )
    missing_branch = client.post(
        url,
        headers=headers,
        json={key: value for key, value in body.items() if key != "target_branch"},
    )

    assert accepted.status_code == 204, accepted.text
    assert base_branch.status_code == 409
    assert arbitrary_branch.status_code == 409
    assert missing_branch.status_code == 409


def test_delivery_authorization_rejects_token_from_different_forge_session() -> None:
    execution, repository, _, _, _ = _context()
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    client = TestClient(app)

    response = client.post(
        f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations",
        headers={
            "authorization": f"Bearer {_token(execution, forge_session_id='forged-session')}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "operation": "inspect_candidate",
            "repository": execution.repository,
            "base_sha": execution.base_sha,
        },
    )

    assert response.status_code == 403
    assert "parent Forge session" in response.json()["detail"]


def test_delivery_launch_requires_git_fields() -> None:
    """Unlike the generic launch, delivery's launch body demands a repo and base branch."""
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow if workflow_id == workflow.id else None

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    missing_both = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "missing-git-fields"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
        },
    )
    missing_branch = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "missing-base-branch"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
        },
    )

    assert missing_both.status_code == 422, missing_both.text
    assert missing_branch.status_code == 422, missing_branch.text


def test_delivery_router_returns_404_for_foreign_owner() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    client = TestClient(app)

    response = client.get(
        f"/api/v1/ting/delivery-executions/{execution.id}",
        headers=_headers(owner="foreign-owner"),
    )

    assert response.status_code == 404


def test_delivery_router_denies_scoped_token_missing_coordinate_scope() -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    client = TestClient(app)
    token = jwt.encode(
        {
            "sub": execution.owner_id,
            "tenant_id": execution.tenant_id,
            "token_use": VALKYRIE_BUILD_TOKEN_USE,
            "scopes": [],
        },
        "test-key-for-workflow-execution-32-bytes",
        algorithm="HS256",
    )

    response = client.post(
        f"/api/v1/ting/delivery-executions/{execution.id}/complete",
        headers={
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
            "authorization": f"Bearer {token}",
        },
        json={"merge": {}, "evidence": {}},
    )

    assert response.status_code == 403
    assert "missing the required scope" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Coverage for the default (unconfigured) dependency stubs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_delivery_execution_repo_default_raises_503() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await resolve_delivery_execution_repo()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Delivery execution repository not configured"


@pytest.mark.asyncio
async def test_resolve_delivery_execution_service_default_raises_503() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await resolve_delivery_execution_service()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Delivery execution service not configured"


# ---------------------------------------------------------------------------
# launch_execution() validation and reservation branches
# ---------------------------------------------------------------------------


def test_launch_requires_idempotency_key_header() -> None:
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow if workflow_id == workflow.id else None

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers=_headers(),
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 422, response.text
    assert "Idempotency-Key" in response.json()["detail"]


def test_launch_returns_404_when_workflow_not_found() -> None:
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return None

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "missing-workflow"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Developer workflow not found"


def test_launch_rejects_schema_version_below_2() -> None:
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return SimpleNamespace(schema_version=1)

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "old-schema"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Developer workflow requires schema v2"


def test_launch_rejects_connection_that_cannot_resolve_delivery_ref() -> None:
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow

    class Adapter:
        target_id = "forge-1"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            raise NotImplementedError("no immutable refs here")

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "no-immutable-refs"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 422, response.text
    assert "cannot resolve immutable delivery refs" in response.json()["detail"]


def test_launch_rejects_deadline_without_timezone() -> None:
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow

    class Adapter:
        target_id = "forge-1"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            return ResolvedRef(
                provider="gitlab",
                repository=repository_url,
                ref=ref,
                sha="a" * 40,
                observed_at=datetime.now(UTC),
            )

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "naive-deadline"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
            "deadline": "2030-01-01T00:00:00",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "deadline must include a timezone"


def test_launch_rejects_deadline_in_the_past() -> None:
    workflow = _developer_workflow()
    repository = MemoryRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow

    class Adapter:
        target_id = "forge-1"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            return ResolvedRef(
                provider="gitlab",
                repository=repository_url,
                ref=ref,
                sha="a" * 40,
                observed_at=datetime.now(UTC),
            )

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "past-deadline"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
            "deadline": "2020-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "deadline must be in the future"


def test_launch_reports_conflict_when_launch_key_digest_differs() -> None:
    workflow = _developer_workflow()

    class Repository(MemoryRepository):
        async def reserve_parent_launch(self, execution):
            raise ExecutionConflictError("Idempotency-Key already used with a different request")

    repository = Repository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow

    class Adapter:
        target_id = "forge-1"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            return ResolvedRef(
                provider="gitlab",
                repository=repository_url,
                ref=ref,
                sha="a" * 40,
                observed_at=datetime.now(UTC),
            )

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/delivery-executions",
        headers={**_headers(), "idempotency-key": "conflicting-key"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "delivery-workstreams",
            "prompt": "Implement ticket 123",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 409, response.text
    assert "different request" in response.json()["detail"]


def test_launch_returns_existing_detail_when_parent_session_already_attached() -> None:
    """When the reservation already carries a parent session, launch dedups
    without attempting session recovery or spawning a new one."""
    workflow = _developer_workflow()
    already_launched = _execution(parent_session_id="already-running")

    class Repository(MemoryRepository):
        async def reserve_parent_launch(self, execution):
            return already_launched, False

    repository = Repository(already_launched)

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow

    class Adapter:
        target_id = "forge-1"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            return ResolvedRef(
                provider="gitlab",
                repository=repository_url,
                ref=ref,
                sha="a" * 40,
                observed_at=datetime.now(UTC),
            )

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    async def duplicate_launch(**kwargs):
        raise AssertionError("must not attempt to launch when already attached")

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    import ting.delivery.api as delivery_api_module

    original_launch = delivery_api_module.launch_workflow_execution
    delivery_api_module.launch_workflow_execution = duplicate_launch
    try:
        response = TestClient(app).post(
            "/api/v1/ting/delivery-executions",
            headers={**_headers(), "idempotency-key": "already-attached"},
            json={
                "workflowId": str(workflow.id),
                "parentNodeId": "delivery-workstreams",
                "prompt": "Implement ticket 123",
                "repo": "https://gitlab.example/org/repo",
                "baseBranch": "main",
            },
        )
    finally:
        delivery_api_module.launch_workflow_execution = original_launch

    assert response.status_code == 201, response.text
    assert response.json()["executionId"] == str(already_launched.id)


def test_launch_returns_existing_detail_when_within_lease_and_unrecovered() -> None:
    """A concurrent in-flight reservation with no matching recoverable Forge
    session and a fresh lease returns the current state instead of racing a
    second launch."""
    workflow = _developer_workflow()

    class Repository(MemoryRepository):
        async def reserve_parent_launch(self, execution):
            self.execution = replace(execution, updated_at=datetime.now(UTC))
            return self.execution, False

    repository = Repository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow

    class Adapter:
        target_id = "forge-1"

        async def resolve_delivery_ref(self, repository_url, ref, **kwargs):
            return ResolvedRef(
                provider="gitlab",
                repository=repository_url,
                ref=ref,
                sha="a" * 40,
                observed_at=datetime.now(UTC),
            )

        async def list_sessions(self, **kwargs):
            return []

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    async def duplicate_launch(**kwargs):
        raise AssertionError("must not launch while a fresh reservation is in flight")

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    import ting.delivery.api as delivery_api_module

    original_launch = delivery_api_module.launch_workflow_execution
    delivery_api_module.launch_workflow_execution = duplicate_launch
    try:
        response = TestClient(app).post(
            "/api/v1/ting/delivery-executions",
            headers={**_headers(), "idempotency-key": "in-flight"},
            json={
                "workflowId": str(workflow.id),
                "parentNodeId": "delivery-workstreams",
                "prompt": "Implement ticket 123",
                "repo": "https://gitlab.example/org/repo",
                "baseBranch": "main",
            },
        )
    finally:
        delivery_api_module.launch_workflow_execution = original_launch

    assert response.status_code == 201, response.text
    assert response.json()["executionId"] == str(repository.execution.id)


# ---------------------------------------------------------------------------
# get_execution()
# ---------------------------------------------------------------------------


def test_get_execution_returns_detail_for_owner() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository

    response = TestClient(app).get(
        f"/api/v1/ting/delivery-executions/{execution.id}",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["executionId"] == str(execution.id)


# ---------------------------------------------------------------------------
# complete_execution()
# ---------------------------------------------------------------------------


def _completion_body(execution) -> dict:
    return {
        "merge": {
            "campaign_id": str(execution.id),
            "repository": execution.repository,
            "review_number": 1,
            "expected_head_sha": "b" * 40,
            "expected_base_sha": execution.base_sha,
            "expected_target_branch": execution.base_ref,
            "method": "squash",
        },
        "evidence": {
            "campaign_id": str(execution.id),
            "workstream_key": "api",
            "attempt_id": "attempt-1",
            "worker_id": "worker-1",
            "repository": execution.repository,
            "base_sha": execution.base_sha,
            "candidate_sha": "b" * 40,
            "candidate_tree": "c" * 40,
            "requirements": [
                {
                    "requirement_id": "REQ-1",
                    "implementation_paths": ["src/api.py"],
                    "verification_contract_ids": ["contract-1"],
                }
            ],
        },
    }


def test_complete_execution_returns_detail_on_success(monkeypatch) -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)

    class FakeCompletionService:
        def __init__(self, **kwargs):
            pass

        async def complete(self, execution, **kwargs):
            completed = replace(execution, state=ExecutionState.COMPLETED)
            repository.execution = completed
            return completed

    monkeypatch.setattr(delivery_api, "DeliveryCompletionService", FakeCompletionService)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/complete",
        headers=_headers(execution),
        json=_completion_body(execution),
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "completed"


def test_complete_execution_reports_service_conflict_as_409(monkeypatch) -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)

    class FakeCompletionService:
        def __init__(self, **kwargs):
            pass

        async def complete(self, execution, **kwargs):
            raise WorkflowExecutionError("Execution cannot complete in its current state")

    monkeypatch.setattr(delivery_api, "DeliveryCompletionService", FakeCompletionService)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/complete",
        headers=_headers(execution),
        json=_completion_body(execution),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Execution cannot complete in its current state"


# ---------------------------------------------------------------------------
# record_integration_candidate() branches
# ---------------------------------------------------------------------------


def test_record_integration_requires_a_sealed_generation_before_join_check() -> None:
    execution, repository, allocation, receipt, _ = _context()
    repository.execution = replace(execution, current_generation=0)

    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: None

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/integration-candidate",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "integration_receipts": [receipt.model_dump(mode="json")],
            "integration_allocation": allocation.model_dump(mode="json"),
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Integration requires a sealed successful child generation"


def test_record_integration_rejects_receipt_identity_mismatch() -> None:
    execution, repository, allocation, receipt, _ = _context()
    mismatched_receipt = receipt.model_copy(update={"campaign_id": "some-other-campaign"})

    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: None

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/integration-candidate",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "integration_receipts": [mismatched_receipt.model_dump(mode="json")],
            "integration_allocation": allocation.model_dump(mode="json"),
        },
    )

    assert response.status_code == 409, response.text
    assert (
        response.json()["detail"]
        == "Integration receipt and allocation do not match this execution"
    )


def test_record_integration_skips_children_from_other_generations() -> None:
    """A stale child from an earlier generation must not affect the accepted
    set built from the current generation's children."""
    execution, repository, allocation, receipt, inspection = _context()
    stale_child = replace(
        repository.children[0],
        id=uuid4(),
        generation=execution.current_generation - 0 + 99,
        key="stale",
    )
    repository.children = [stale_child, *repository.children]

    class Adapter:
        async def inspect_delivery_integration_chain(self, *args, **kwargs):
            return inspection

    class Factory:
        async def for_connection(self, owner_id, connection_id):
            return Adapter()

    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/integration-candidate",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "integration_receipts": [receipt.model_dump(mode="json")],
            "integration_allocation": allocation.model_dump(mode="json"),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["integrationCandidate"]["candidate_sha"] == "b" * 40


def test_record_integration_rejects_when_a_current_child_is_not_accepted() -> None:
    """The candidate a child actually produced must exactly match what the
    inspected integration chain says it integrated."""
    execution, repository, allocation, receipt, inspection = _context()
    repository.children = [
        replace(
            repository.children[0],
            result={"candidateSha": "f" * 40, "candidateTree": "e" * 40},
        )
    ]

    class Adapter:
        async def inspect_delivery_integration_chain(self, *args, **kwargs):
            return inspection

    class Factory:
        async def for_connection(self, owner_id, connection_id):
            return Adapter()

    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/integration-candidate",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "integration_receipts": [receipt.model_dump(mode="json")],
            "integration_allocation": allocation.model_dump(mode="json"),
        },
    )

    assert response.status_code == 409, response.text
    assert (
        response.json()["detail"]
        == "Inspected integration chain does not exactly cover accepted children"
    )


def test_record_integration_reports_repository_conflict_as_409() -> None:
    execution, repository, allocation, receipt, inspection = _context()

    class ConflictingRepository(type(repository)):
        async def record_integration_candidate(self, *args, **kwargs):
            raise ExecutionConflictError("execution revision changed underneath this write")

    repository.__class__ = ConflictingRepository

    class Adapter:
        async def inspect_delivery_integration_chain(self, *args, **kwargs):
            return inspection

    class Factory:
        async def for_connection(self, owner_id, connection_id):
            return Adapter()

    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/integration-candidate",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "integration_receipts": [receipt.model_dump(mode="json")],
            "integration_allocation": allocation.model_dump(mode="json"),
        },
    )

    assert response.status_code == 409, response.text
    assert "execution revision changed underneath this write" in response.json()["detail"]


# ---------------------------------------------------------------------------
# expand_execution()
# ---------------------------------------------------------------------------


def test_expand_execution_rejects_campaign_id_mismatch() -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)
    service = _service(repository)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_delivery_execution_service] = lambda: service

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/expansions",
        headers=_headers(execution),
        json={
            "campaign_id": str(uuid4()),
            "generation": 1,
            "plan_revision": "approved-v1",
            "workstreams": [{"key": "api"}],
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "campaign_id does not match route"


def test_expand_execution_rejects_parent_node_id_mismatch() -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)
    service = _service(repository)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_delivery_execution_service] = lambda: service

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/expansions",
        headers=_headers(execution),
        json={
            "parent_node_id": "not-the-real-node",
            "generation": 1,
            "plan_revision": "approved-v1",
            "workstreams": [{"key": "api"}],
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "parent_node_id does not match execution"


def test_expand_execution_reports_coordinator_conflict_as_409() -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)
    service = _service(repository)
    proposal = _proposal(execution, "api")
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_delivery_execution_service] = lambda: service

    workstream = {
        "key": proposal.key,
        "objective": proposal.objective,
        "requirementIds": list(proposal.requirement_ids),
        "dependencies": list(proposal.dependencies),
        "input": proposal.input,
        "repository": proposal.repository,
        "baseSha": proposal.base_sha,
        "budgetUnits": proposal.budget_units,
        "deadline": proposal.deadline.isoformat(),
        "agentId": proposal.agent_id,
        "skillId": proposal.skill_id,
        "workspace": proposal.workspace,
    }

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/expansions",
        headers=_headers(execution),
        json={
            "generation": 2,
            "plan_revision": "approved-v1",
            "workstreams": [workstream],
        },
    )

    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# authorize_delivery_operation() branches
# ---------------------------------------------------------------------------


def test_authorize_rejects_repository_outside_execution() -> None:
    execution, repository, _, _, _ = _context()
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "operation": "inspect_candidate",
            "repository": "https://example.test/some-other-repo.git",
            "base_sha": execution.base_sha,
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Delivery repository is outside execution"


def test_authorize_rejects_base_sha_outside_execution() -> None:
    execution, repository, _, _, _ = _context()
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "operation": "inspect_candidate",
            "repository": execution.repository,
            "base_sha": "f" * 40,
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Delivery base SHA is outside execution"


def test_authorize_rejects_when_execution_state_disallows_the_operation() -> None:
    execution, repository, _, _, _ = _context()
    repository.execution = replace(execution, cancel_requested=True)
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository

    response = TestClient(app).post(
        f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations",
        headers={
            "authorization": f"Bearer {_token(execution)}",
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
        },
        json={
            "operation": "inspect_candidate",
            "repository": execution.repository,
            "base_sha": execution.base_sha,
        },
    )

    assert response.status_code == 409, response.text
    assert "not allowed while execution is" in response.json()["detail"]


def test_authorize_open_review_requires_target_and_exact_candidate() -> None:
    execution, repository, _, _, inspection = _context()
    repository.execution = replace(
        execution,
        integration_candidate=inspection.model_dump(mode="json"),
    )
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        auth=SimpleNamespace(allow_anonymous_dev=False),
        workflow_execution=SimpleNamespace(
            delivery=SimpleNamespace(integration_policy_id="developer-integration")
        ),
    )
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository
    client = TestClient(app)
    headers = {
        "authorization": f"Bearer {_token(execution)}",
        "x-auth-user-id": execution.owner_id,
        "x-auth-tenant": execution.tenant_id,
    }
    url = f"/api/v1/ting/delivery-executions/{execution.id}/delivery-authorizations"

    accepted = client.post(
        url,
        headers=headers,
        json={
            "operation": "open_review",
            "repository": execution.repository,
            "base_sha": execution.base_sha,
            "candidate_sha": inspection.candidate_sha,
            "candidate_tree": inspection.candidate_tree,
            "target_branch": execution.base_ref,
        },
    )
    wrong_target = client.post(
        url,
        headers=headers,
        json={
            "operation": "open_review",
            "repository": execution.repository,
            "base_sha": execution.base_sha,
            "candidate_sha": inspection.candidate_sha,
            "candidate_tree": inspection.candidate_tree,
            "target_branch": "not-base",
        },
    )
    wrong_candidate = client.post(
        url,
        headers=headers,
        json={
            "operation": "open_review",
            "repository": execution.repository,
            "base_sha": execution.base_sha,
            "candidate_sha": "d" * 40,
            "candidate_tree": inspection.candidate_tree,
            "target_branch": execution.base_ref,
        },
    )

    assert accepted.status_code == 204, accepted.text
    assert wrong_target.status_code == 409
    assert "Review publication requires" in wrong_target.json()["detail"]
    assert wrong_candidate.status_code == 409


# ---------------------------------------------------------------------------
# execution_evidence()
# ---------------------------------------------------------------------------


def test_execution_evidence_skips_children_from_other_generations() -> None:
    execution, repository, _, _, _ = _context()
    stale_child = replace(
        repository.children[0],
        id=uuid4(),
        generation=execution.current_generation + 5,
        key="stale",
        gate_report={"accepted": False, "blocking_reasons": ["stale generation"]},
    )
    repository.children = [stale_child, *repository.children]
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_delivery_executions_router())
    app.dependency_overrides[resolve_delivery_execution_repo] = lambda: repository

    response = TestClient(app).get(
        f"/api/v1/ting/delivery-executions/{execution.id}/evidence",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert "stale generation" not in response.json()["verification"]["blockingReasons"]
    assert len(response.json()["children"]) == 2
