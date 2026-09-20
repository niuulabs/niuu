from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ting.api.workflow_executions as execution_api
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE
from tests.test_ting.test_workflow_execution_service import (
    InMemoryWorkflowRepository,
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
from ting.api.workflows import resolve_workflow_repo
from ting.domain.models import WorkflowDefinition, WorkflowDependency, WorkflowScope
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ChildPendingGate,
    ChildPendingQuestion,
    ExecutionState,
    make_children,
)
from ting.ports.volundr import PublicSessionLogEntry, PublicSessionLogPage


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


def _headers(execution=None, *, owner="editor-1", tenant="publishing"):
    headers = {"x-auth-user-id": owner, "x-auth-tenant": tenant}
    if execution is not None:
        headers["authorization"] = f"Bearer {_token(execution)}"
    return headers


def test_execution_owner_isolation_and_list_shape() -> None:
    execution = _execution()
    repository = InMemoryWorkflowRepository(execution)
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
    repository = InMemoryWorkflowRepository(execution)
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
                "translation-assignment": {
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
    repository = InMemoryWorkflowRepository(execution)
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
    repository = InMemoryWorkflowRepository(execution)
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


def _wait_graph(
    node_id: str = "delivery-publication-wait", conditions=("forge.checks", "forge.merge")
):
    return {"graph": {"nodes": [{"id": node_id, "kind": "wait", "conditions": list(conditions)}]}}


def test_wait_route_binds_owner_and_exact_coordinator_before_registration():
    execution = _execution(
        state=ExecutionState.RUNNING,
        workflow_snapshot=_wait_graph(),
    )
    repository = InMemoryWorkflowRepository(execution)
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
        "request": {"target": "example-condition-target", "reviewNumber": 8},
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
    repository = InMemoryWorkflowRepository(execution)
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
    no_graph_repository = InMemoryWorkflowRepository(no_graph_execution)
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
    repository = InMemoryWorkflowRepository(execution)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    client = TestClient(app)
    url = f"/api/v1/ting/workflow-executions/{execution.id}/waits"
    payload = {
        "nodeId": "delivery-publication-wait",
        "conditionType": "forge.merge",
        "request": {"target": "example-condition-target", "reviewNumber": 8, "method": "merge"},
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
    repository = InMemoryWorkflowRepository(execution)
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


def _generic_workflow_definition() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Editorial Translation",
        description="",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "nodes": [
                {
                    "id": "commission-translations",
                    "kind": "subworkflow",
                    "workflowDependency": "translation-assignment",
                    "allowedCoordinator": "editorial-coordinator",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"attemptId": {"type": "string", "minLength": 1}},
                        "required": ["attemptId"],
                        "additionalProperties": False,
                    },
                    "resultSchema": {
                        "type": "object",
                        "properties": {"attemptId": {"type": "string", "minLength": 1}},
                        "required": ["attemptId"],
                        "additionalProperties": False,
                    },
                    "maxChildren": 10,
                    "maxAttempts": 3,
                    "maxActiveChildren": 4,
                    "joinMode": "all",
                }
            ],
            "edges": [],
        },
        created_at=now,
        updated_at=now,
        schema_version=2,
        workflow_dependencies={
            "translation-assignment": WorkflowDependency(
                id=uuid4(), revision="v1", digest="sha256:" + "b" * 64
            ),
        },
    )


def test_generic_launch_rejects_delivery_fields() -> None:
    """The generic launch body has no git surface; sending one is a 422, not silently ignored."""
    workflow = _generic_workflow_definition()
    repository = InMemoryWorkflowRepository(_execution())

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
        headers={**_headers(), "idempotency-key": "reject-delivery-fields"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "commission-translations",
            "prompt": "Translate the handbook",
            "repo": "https://gitlab.example/org/repo",
            "baseBranch": "main",
        },
    )

    assert response.status_code == 422, response.text


def test_generic_launch_creates_execution_with_no_delivery_row(monkeypatch) -> None:
    """A generic launch produces a plain execution readable with no delivery extension row."""
    workflow = _generic_workflow_definition()
    repository = InMemoryWorkflowRepository(_execution())

    class WorkflowRepo:
        async def get_workflow(self, workflow_id):
            return workflow if workflow_id == workflow.id else None

    class Adapter:
        target_id = "conn-1"

        async def list_sessions(self, **kwargs):
            return []

    class Factory:
        async def primary_for_owner(self, owner_id):
            return Adapter()

    async def launch(**kwargs):
        return SimpleNamespace(
            session=SimpleNamespace(id="parent-session-1"),
            connection_id="conn-1",
        )

    monkeypatch.setattr(execution_api, "launch_workflow_execution", launch)
    monkeypatch.setattr(
        execution_api, "build_workflow_snapshot", lambda workflow, persona_source=None: {}
    )

    app = FastAPI()
    app.state.settings = _settings(anonymous=True)
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_repo] = lambda: WorkflowRepo()
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
    app.dependency_overrides[resolve_volundr_factory] = lambda: Factory()

    response = TestClient(app).post(
        "/api/v1/ting/workflow-executions",
        headers={**_headers(), "idempotency-key": "generic-launch"},
        json={
            "workflowId": str(workflow.id),
            "parentNodeId": "commission-translations",
            "prompt": "Translate the handbook",
            "input": {"locale": "fr"},
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["input"] == {"locale": "fr"}
    assert "repo" not in body
    assert "baseBranch" not in body
    assert repository.execution.parent_session_id == "parent-session-1"

    fetched = TestClient(app).get(
        f"/api/v1/ting/workflow-executions/{body['executionId']}",
        headers=_headers(),
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["input"] == {"locale": "fr"}


def test_generic_router_denies_scoped_token_missing_coordinate_scope() -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = InMemoryWorkflowRepository(execution)
    app = FastAPI()
    app.state.settings = _settings()
    app.include_router(create_workflow_executions_router())
    app.dependency_overrides[resolve_workflow_execution_repo] = lambda: repository
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
        f"/api/v1/ting/workflow-executions/{execution.id}/cancel",
        headers={
            "x-auth-user-id": execution.owner_id,
            "x-auth-tenant": execution.tenant_id,
            "authorization": f"Bearer {token}",
        },
        json={},
    )

    assert response.status_code == 403
    assert "missing the required scope" in response.json()["detail"]


def test_real_service_routes_expand_reconcile_retry_and_cancel_for_generic_children() -> None:
    execution = _execution(state=ExecutionState.RUNNING)
    repository = InMemoryWorkflowRepository(execution)
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
    child_payload = {
        "key": proposal.key,
        "objective": proposal.objective,
        "dependencies": list(proposal.dependencies),
        "input": proposal.input,
        "budgetUnits": proposal.budget_units,
        "deadline": proposal.deadline.isoformat(),
        "agentId": proposal.agent_id,
        "skillId": proposal.skill_id,
    }

    expanded = client.post(
        url + "/expansions",
        headers=headers,
        json={
            "campaign_id": str(execution.id),
            "parent_node_id": execution.parent_node_id,
            "generation": 1,
            "plan_revision": "approved-v1",
            "children": [child_payload],
        },
    )
    reconciled = client.post(url + "/reconcile", headers=headers, json={})
    assert expanded.status_code == 200, expanded.text
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["join"]["ready"] is True

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
