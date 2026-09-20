import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ting.api.workflow_executions as execution_api
from niuu.domain.agent_directory import configured_agent_id
from niuu.domain.delivery import (
    IntegrationCandidateInspection,
    IntegrationReceipt,
    ResolvedRef,
    WorkspaceAllocation,
)
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
from ting.delivery.domain import make_children
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ChildPendingGate,
    ChildPendingQuestion,
    ExecutionState,
)
from ting.ports.volundr import PublicSessionLogEntry, PublicSessionLogPage
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


def test_child_api_preserves_pending_input_identities() -> None:
    execution = _execution()
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.BLOCKED,
        pending_questions=(ChildPendingQuestion(request_id="request-1", question="Which branch?"),),
        pending_gates=(ChildPendingGate(gate_id="gate-1", label="Approve?"),),
    )

    payload = execution_api._child_json(child)

    assert payload["pendingQuestions"][0]["requestId"] == "request-1"
    assert payload["pendingGates"][0]["gateId"] == "gate-1"


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
            evidence_policy_id="developer-workstream",
            integration_policy_id="developer-integration",
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

    monkeypatch.setattr(execution_api, "launch_workflow_execution", launch)
    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/workflow-executions",
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
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        "/api/v1/ting/workflow-executions",
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
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: SimpleNamespace()

    response = TestClient(app).post(
        "/api/v1/ting/workflow-executions",
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

    monkeypatch.setattr(execution_api, "launch_workflow_execution", duplicate_launch)
    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/workflow-executions",
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
                "alias": repository.execution.policy.workflow_dependency,
                "template_id": str(repository.execution.policy.template_id),
                "template_revision": repository.execution.policy.template_revision,
                "template_digest": repository.execution.policy.template_digest,
                "agent_id": configured_agent_id("https://ting.example/.well-known/agent-card.json"),
                "skill_id": str(repository.execution.policy.template_id),
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

    monkeypatch.setattr(execution_api, "launch_workflow_execution", launch)
    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/workflow-executions",
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


def test_execution_owner_isolation_and_list_shape() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    client = TestClient(app)

    listed = client.get("/api/v1/ting/workflow-executions", headers=_headers())
    foreign = client.get(
        f"/api/v1/ting/workflow-executions/{execution.id}",
        headers=_headers(owner="foreign-owner"),
    )

    assert listed.status_code == 200
    assert listed.json()["executions"][0]["executionId"] == str(execution.id)
    assert listed.json()["nextCursor"] is None
    assert foreign.status_code == 404


def test_execution_trace_uses_frozen_graph_and_public_outcomes_only() -> None:
    integration_event = "developer.integration.requested"
    execution = _execution(
        workflow_snapshot={
            "name": "Frozen delivery",
            "version": "1.0.0",
            "graph": {
                "nodes": [
                    {
                        "id": "start",
                        "kind": "trigger",
                        "label": "Start",
                        "dispatchEvent": "delivery.requested",
                        "privateConfig": "must-not-leak",
                    },
                    {
                        "id": "review",
                        "kind": "stage",
                        "label": "Review",
                        "stageMembers": [
                            {
                                "personaId": "reviewer",
                                "model": "gpt-test",
                                "consumesEventTypes": ["delivery.requested"],
                                "systemPrompt": "must-not-leak",
                            }
                        ],
                    },
                    {"id": "coordinate", "kind": "stage", "label": "Coordinate"},
                    {"id": "repair", "kind": "stage", "label": "Repair"},
                ],
                "edges": [
                    {
                        "id": "start-review",
                        "source": "start",
                        "target": "review",
                        "label": "delivery.requested -> delivery.requested",
                    },
                    {
                        "id": "coordinate-wait",
                        "source": "coordinate",
                        "target": "review",
                        "label": "developer.children.waiting -> developer.children.waiting",
                    },
                    {
                        "id": "repair-wait",
                        "source": "repair",
                        "target": "review",
                        "label": "developer.children.waiting -> developer.children.waiting",
                    },
                    {
                        "id": "coordinate-integrate",
                        "source": "coordinate",
                        "target": "review",
                        "label": f"{integration_event} -> {integration_event}",
                    },
                ],
            },
            "persona_definitions": {
                "coordinator": {
                    "definition": {
                        "produces": {
                            "event_type": "developer.coordination.decision",
                            "event_type_map": {
                                "expand": "developer.children.waiting",
                                "integrate": "developer.integration.requested",
                            },
                        }
                    }
                }
            },
            "workflow_definitions": {},
        }
    )
    repository = MemoryRepository(execution)
    observed = {}

    class Adapter:
        async def get_public_session_log_page(self, session_id, **kwargs):
            observed.update(session_id=session_id, **kwargs)
            now = datetime.now(UTC)
            entries = (
                PublicSessionLogEntry(
                    session_id=session_id,
                    seq=5,
                    kind="room_outcome",
                    payload={
                        "eventType": "delivery.requested",
                        "persona": "skuld",
                        "summary": "Dispatched",
                        "valid": True,
                        "fields": {"evidence": "[report](https://example.test/report)"},
                    },
                    ts=now,
                ),
                PublicSessionLogEntry(
                    session_id=session_id,
                    seq=6,
                    kind="tool_use",
                    payload={"secret": "never return transcript internals"},
                    ts=now,
                ),
                PublicSessionLogEntry(
                    session_id=session_id,
                    seq=7,
                    kind="room_outcome",
                    payload={
                        "eventType": "developer.coordination.decision",
                        "persona": "coordinator",
                        "verdict": "expand",
                        "fields": {},
                    },
                    ts=now,
                ),
                PublicSessionLogEntry(
                    session_id=session_id,
                    seq=8,
                    kind="room_outcome",
                    payload={
                        "eventType": "developer.coordination.decision",
                        "persona": "coordinator",
                        "verdict": "integrate",
                        "fields": {},
                    },
                    ts=now,
                ),
            )
            return PublicSessionLogPage(entries=entries, scanned_through=8, has_more=True)

    adapter = Adapter()

    class Factory:
        async def for_connection(self, owner_id, connection_id):
            assert (owner_id, connection_id) == (execution.owner_id, execution.connection_id)
            return adapter

        async def primary_for_owner(self, owner_id):
            raise AssertionError("persisted connection must be used")

    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()
    client = TestClient(app)

    response = client.get(
        f"/api/v1/ting/workflow-executions/{execution.id}/trace?after=3&limit=4",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workflow"]["name"] == "Frozen delivery"
    assert payload["workflow"]["graph"]["nodes"][0] == {
        "id": "start",
        "label": "Start",
        "kind": "trigger",
    }
    assert payload["workflow"]["graph"]["nodes"][1]["stageMembers"] == [
        {
            "personaId": "reviewer",
            "model": "gpt-test",
            "consumesEventTypes": ["delivery.requested"],
        }
    ]
    assert [event["seq"] for event in payload["events"]] == [5, 7, 8]
    assert payload["events"][0]["nodeIds"] == ["start"]
    assert payload["events"][0]["mapping"] == "workflow_graph_event"
    assert "occurredAt" in payload["events"][0]
    assert payload["events"][0]["fields"]["evidence"].startswith("[report]")
    assert payload["events"][1]["nodeIds"] == []
    assert payload["events"][1]["mapping"] == "pinned_persona_outcome"
    assert payload["events"][1]["canonicalEventType"] == "developer.children.waiting"
    assert payload["events"][1]["candidateNodeIds"] == ["coordinate", "repair"]
    assert payload["events"][1]["summary"] == ""
    assert payload["events"][1]["valid"] is True
    assert payload["events"][2]["nodeIds"] == ["coordinate"]
    assert payload["events"][2]["mapping"] == "pinned_persona_outcome"
    assert payload["nodeHistory"] == {
        "start": [f"{execution.parent_session_id}:5"],
        "coordinate": [f"{execution.parent_session_id}:8"],
    }
    assert payload["unattachedEventIds"] == [f"{execution.parent_session_id}:7"]
    assert payload["page"] == {"after": 3, "scannedThrough": 8, "hasMore": True}
    assert observed["session_id"] == execution.parent_session_id
    assert observed["after"] == 3
    assert observed["limit"] == 4

    foreign = client.get(
        f"/api/v1/ting/workflow-executions/{execution.id}/trace",
        headers=_headers(owner="foreign-owner"),
    )
    assert foreign.status_code == 404


def test_execution_trace_child_uses_persisted_campaign_session_and_child_graph() -> None:
    child_graph = {
        "nodes": [
            {"id": "implement", "kind": "stage", "label": "Implement"},
            {"id": "done", "kind": "end", "label": "Done", "completionEvent": "done"},
        ],
        "edges": [],
    }
    execution = _execution(
        current_generation=1,
        workflow_snapshot={
            "name": "Parent",
            "graph": {"nodes": [], "edges": []},
            "workflow_definitions": {
                "workstream": {
                    "document": {
                        "id": "98133eab-cd89-44d1-b32d-26c82f99dddc",
                        "name": "Frozen workstream",
                        "version": "2.0.0",
                        "graph": child_graph,
                    },
                    "persona_definitions": {},
                    "workflow_definitions": {},
                }
            },
        },
    )
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.RUNNING,
        task_id="a2a-task-api",
    )
    repository = MemoryRepository(execution)
    repository.children = [child]
    campaign = SimpleNamespace(
        tenant_id=execution.tenant_id,
        session_id="child-session-1",
        connection_id="forge-child",
    )

    class CampaignRepository:
        async def get_campaign_by_slug(self, slug, *, owner_id=None):
            assert (slug, owner_id) == (child.task_id, execution.owner_id)
            return campaign

    class Adapter:
        async def get_public_session_log_page(self, session_id, **kwargs):
            assert session_id == campaign.session_id
            return PublicSessionLogPage(
                entries=(
                    PublicSessionLogEntry(
                        session_id=session_id,
                        seq=9,
                        kind="room_outcome",
                        payload={"eventType": "done", "fields": {}},
                        ts=datetime.now(UTC),
                    ),
                ),
                scanned_through=9,
                has_more=False,
            )

    class Factory:
        async def for_connection(self, owner_id, connection_id):
            assert connection_id == campaign.connection_id
            return Adapter()

        async def primary_for_owner(self, owner_id):
            raise AssertionError("child campaign connection must be used")

    app = FastAPI()
    app.state.settings = _settings()
    app.state.workflow_campaign_repo = CampaignRepository()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).get(
        f"/api/v1/ting/workflow-executions/{execution.id}/trace?childId={child.id}",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workflow"]["name"] == "Frozen workstream"
    assert payload["workflow"]["workflowId"] == "98133eab-cd89-44d1-b32d-26c82f99dddc"
    assert payload["selectedChildId"] == str(child.id)
    assert payload["selectedSessionId"] == campaign.session_id
    assert payload["sessions"][1]["parentNodeId"] == execution.parent_node_id
    assert payload["sessions"][1]["sessionId"] == campaign.session_id
    assert payload["events"][0]["nodeIds"] == ["done"]

    campaign.tenant_id = "foreign-tenant"
    forbidden = TestClient(app).get(
        f"/api/v1/ting/workflow-executions/{execution.id}/trace?childId={child.id}",
        headers=_headers(),
    )
    assert forbidden.status_code == 404


def test_operator_mutations_bind_scoped_workloads_to_exact_parent_session() -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)
    service = SimpleNamespace(
        cancel=AsyncMock(),
        reconcile=AsyncMock(),
        retry=AsyncMock(),
        launch_ready=AsyncMock(),
    )
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_workflow_execution_service] = lambda: service
    client = TestClient(app)
    url = f"/api/v1/ting/workflow-executions/{execution.id}"

    sibling = {
        **_headers(),
        "authorization": f"Bearer {_token(execution, forge_session_id='sibling-session')}",
    }
    child = {**_headers(), "authorization": f"Bearer {_child_token(execution)}"}
    assert client.post(url + "/cancel", headers=sibling, json={}).status_code == 403
    assert client.post(url + "/reconcile", headers=child, json={}).status_code == 403
    assert (
        client.post(
            url + "/children/api/retry",
            headers=sibling,
            json={"attempt_id": str(uuid4())},
        ).status_code
        == 403
    )
    service.cancel.assert_not_awaited()
    service.reconcile.assert_not_awaited()
    service.retry.assert_not_awaited()

    assert client.post(url + "/reconcile", headers=_headers(execution), json={}).status_code == 200
    service.reconcile.assert_awaited_once_with(execution.id)

    # Human ingress remains owner-scoped and is not required to carry workload lineage.
    assert client.post(url + "/cancel", headers=_headers(), json={}).status_code == 200
    service.cancel.assert_awaited_once_with(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
    )


def test_real_service_routes_expand_reconcile_retry_cancel_and_evidence() -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway=gateway)
    proposal = _proposal(execution, "api")
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_workflow_execution_service] = lambda: service
    client = TestClient(app)
    headers = _headers(execution)
    url = f"/api/v1/ting/workflow-executions/{execution.id}"
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
    reconciled = client.post(url + "/reconcile", headers=headers, json={})
    assert expanded.status_code == 200, expanded.text
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["join"]["ready"] is True
    completed_child = repository.children[0]
    repository._replace(
        replace(
            completed_child,
            evidence_report=repository.evidence_reports[completed_child.id],
            evidence_validated_at=datetime.now(UTC),
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
        url + "/children/api/retry",
        headers=headers,
        json={"attempt_id": str(failed.id)},
    )
    assert retried.status_code == 200, retried.text
    assert max(child.attempt for child in repository.children) == 2

    canceled = client.post(url + "/cancel", headers=headers, json={})
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["state"] == "canceled"
    assert gateway.cancelled


def test_evidence_reports_rejection_without_a_blocking_reason() -> None:
    execution, repository, _, _, _ = _context()
    repository._replace(
        replace(
            repository.children[0],
            evidence_report={"accepted": False, "blocking_reasons": []},
            evidence_validated_at=datetime.now(UTC),
        )
    )
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository

    response = TestClient(app).get(
        f"/api/v1/ting/workflow-executions/{execution.id}/evidence",
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
        workflow_execution=SimpleNamespace(integration_policy_id="developer-integration"),
    )
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        f"/api/v1/ting/workflow-executions/{execution.id}/integration-candidate",
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
        workflow_execution=SimpleNamespace(integration_policy_id="developer-integration"),
    )
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: None

    response = TestClient(app).post(
        f"/api/v1/ting/workflow-executions/{execution.id}/integration-candidate",
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
        workflow_execution=SimpleNamespace(integration_policy_id="developer-integration"),
    )
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        f"/api/v1/ting/workflow-executions/{execution.id}/integration-candidate",
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
        workflow_execution=SimpleNamespace(integration_policy_id="developer-integration"),
    )
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
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
        f"/api/v1/ting/workflow-executions/{execution.id}/delivery-authorizations",
        headers=headers,
        json=body,
    )
    wrong_policy = client.post(
        f"/api/v1/ting/workflow-executions/{execution.id}/delivery-authorizations",
        headers=headers,
        json={**body, "policy_id": "permissive-policy"},
    )
    wrong_target = client.post(
        f"/api/v1/ting/workflow-executions/{execution.id}/delivery-authorizations",
        headers=headers,
        json={**body, "target_branch": "release"},
    )
    wrong_candidate = client.post(
        f"/api/v1/ting/workflow-executions/{execution.id}/delivery-authorizations",
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
        workflow_execution=SimpleNamespace(integration_policy_id="developer-integration"),
    )
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
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
    url = f"/api/v1/ting/workflow-executions/{execution.id}/delivery-authorizations"

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
        workflow_execution=SimpleNamespace(integration_policy_id="developer-integration"),
    )
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    client = TestClient(app)

    response = client.post(
        f"/api/v1/ting/workflow-executions/{execution.id}/delivery-authorizations",
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


def _wait_graph(
    node_id: str = "delivery-publication-wait", conditions=("forge.checks", "forge.merge")
):
    return {"graph": {"nodes": [{"id": node_id, "kind": "wait", "conditions": list(conditions)}]}}


def test_wait_route_binds_owner_and_exact_coordinator_before_registration():
    execution = _execution(
        state=ExecutionState.RUNNING,
        workflow_snapshot=_wait_graph(),
    )
    repository = MemoryRepository(execution)
    service = SimpleNamespace(
        request_wait=AsyncMock(
            return_value=SimpleNamespace(
                to_dict=lambda: {"waitId": "wait-1", "state": "pending"},
            )
        )
    )
    app = FastAPI()
    app.state.settings = _settings()
    app.state.workflow_wait_service = service
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    client = TestClient(app)
    url = f"/api/v1/ting/workflow-executions/{execution.id}/waits"
    payload = {
        "nodeId": "delivery-publication-wait",
        "conditionType": "forge.checks",
        "request": {"repository": execution.repository, "reviewNumber": 8},
    }
    response = client.post(url, headers=_headers(execution), json=payload)
    assert response.status_code == 200, response.text
    bound_execution = service.request_wait.await_args.args[0]
    wait_kwargs = service.request_wait.await_args.kwargs
    assert bound_execution.id == execution.id
    assert wait_kwargs["node_id"] == "delivery-publication-wait"
    assert wait_kwargs["condition_type"] == "forge.checks"
    assert wait_kwargs["request"] == payload["request"]
    assert wait_kwargs["allowed_condition_types"] == frozenset({"forge.checks", "forge.merge"})
    assert response.json()["state"] == "pending"

    another_owner = client.post(url, headers=_headers(execution, owner="other"), json=payload)
    assert another_owner.status_code == 404
    another_coordinator = client.post(url, headers=_headers(_execution()), json=payload)
    assert another_coordinator.status_code == 403
    missing_field = client.post(
        url, headers=_headers(execution), json={"nodeId": "delivery-publication-wait"}
    )
    assert missing_field.status_code == 422
    extra_field = client.post(url, headers=_headers(execution), json={**payload, "policy": {}})
    assert extra_field.status_code == 422
    assert service.request_wait.await_count == 1


def test_wait_route_rejects_unknown_or_non_wait_node():
    execution = _execution(state=ExecutionState.RUNNING, workflow_snapshot=_wait_graph())
    repository = MemoryRepository(execution)
    service = SimpleNamespace(request_wait=AsyncMock())
    app = FastAPI()
    app.state.settings = _settings()
    app.state.workflow_wait_service = service
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    client = TestClient(app)
    url = f"/api/v1/ting/workflow-executions/{execution.id}/waits"

    unknown_node = client.post(
        url,
        headers=_headers(execution),
        json={"nodeId": "no-such-node", "conditionType": "forge.checks", "request": {}},
    )
    assert unknown_node.status_code == 422
    service.request_wait.assert_not_awaited()

    no_graph_execution = _execution(state=ExecutionState.RUNNING)
    no_graph_repository = MemoryRepository(no_graph_execution)
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: no_graph_repository
    no_graph = client.post(
        url.replace(str(execution.id), str(no_graph_execution.id)),
        headers=_headers(no_graph_execution),
        json={
            "nodeId": "delivery-publication-wait",
            "conditionType": "forge.checks",
            "request": {},
        },
    )
    assert no_graph.status_code == 503
    service.request_wait.assert_not_awaited()


def test_wait_route_reports_configuration_and_contract_failures():
    execution = _execution(state=ExecutionState.RUNNING, workflow_snapshot=_wait_graph())
    repository = MemoryRepository(execution)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    client = TestClient(app)
    url = f"/api/v1/ting/workflow-executions/{execution.id}/waits"
    payload = {
        "nodeId": "delivery-publication-wait",
        "conditionType": "forge.merge",
        "request": {"repository": execution.repository, "reviewNumber": 8, "method": "merge"},
    }
    assert client.post(url, headers=_headers(execution), json=payload).status_code == 503
    service = SimpleNamespace(
        request_wait=AsyncMock(
            side_effect=execution_api.WorkflowExecutionError(
                "candidate differs from persisted integration"
            ),
        )
    )
    app.state.workflow_wait_service = service
    response = client.post(url, headers=_headers(execution), json=payload)
    assert response.status_code == 409
    assert response.json()["detail"] == "candidate differs from persisted integration"
    assert service.request_wait.await_count == 1


def test_wait_history_is_owner_scoped():
    execution = _execution(state=ExecutionState.WAITING)
    repository = MemoryRepository(execution)
    service = SimpleNamespace(
        list_waits=AsyncMock(
            return_value=[
                SimpleNamespace(
                    to_dict=lambda: {
                        "waitId": "wait-1",
                        "state": "pending",
                        "conditionType": "forge.checks",
                    },
                )
            ]
        )
    )
    app = FastAPI()
    app.state.settings = _settings()
    app.state.workflow_wait_service = service
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    client = TestClient(app)
    url = f"/api/v1/ting/workflow-executions/{execution.id}/waits"
    response = client.get(url, headers=_headers())
    assert response.status_code == 200, response.text
    assert response.json()[0]["conditionType"] == "forge.checks"
    service.list_waits.assert_awaited_once_with(execution)
    assert client.get(url, headers=_headers(owner="other")).status_code == 404
    assert service.list_waits.await_count == 1
