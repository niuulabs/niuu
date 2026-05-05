"""Tests for Tyr workflow catalog REST API."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tyr.api.workflows import create_workflows_router, resolve_workflow_repo
from tyr.config import AuthConfig, Settings
from tyr.domain.models import WorkflowDefinition, WorkflowScope
from tyr.ports.workflow_repository import WorkflowRepository


class InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition] | None = None) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows or []}

    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        workflows = list(self._workflows.values())
        if scope == WorkflowScope.SYSTEM:
            return [workflow for workflow in workflows if workflow.scope == WorkflowScope.SYSTEM]
        if scope == WorkflowScope.USER:
            return [
                workflow
                for workflow in workflows
                if workflow.scope == WorkflowScope.USER and workflow.owner_id == owner_id
            ]
        return [
            workflow
            for workflow in workflows
            if workflow.scope == WorkflowScope.SYSTEM or workflow.owner_id == owner_id
        ]

    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None:
        return self._workflows.get(workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[workflow.id] = workflow
        return workflow

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        removed = self._workflows.pop(workflow_id, None)
        return removed is not None


def _make_workflow(
    *,
    workflow_id: UUID | None = None,
    scope: WorkflowScope = WorkflowScope.USER,
    owner_id: str | None = "user-1",
    name: str = "Workflow",
) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=workflow_id or uuid4(),
        name=name,
        description="Workflow description",
        version="1.0.0",
        scope=scope,
        owner_id=owner_id,
        definition_yaml="name: Workflow",
        graph={"nodes": [{"id": "n1", "kind": "stage"}], "edges": []},
        created_at=now,
        updated_at=now,
    )


def _headers(
    *,
    user_id: str = "user-1",
    roles: str = "product:user",
) -> dict[str, str]:
    return {
        "x-auth-user-id": user_id,
        "x-auth-roles": roles,
    }


def _make_client(repo: WorkflowRepository) -> TestClient:
    app = FastAPI()
    app.include_router(create_workflows_router())
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.dependency_overrides[resolve_workflow_repo] = lambda: repo
    return TestClient(app)


class TestWorkflowCatalogAPI:
    def test_list_returns_system_and_owned_workflows(self) -> None:
        repo = InMemoryWorkflowRepository(
            [
                _make_workflow(scope=WorkflowScope.SYSTEM, owner_id=None, name="System"),
                _make_workflow(owner_id="user-1", name="Mine"),
                _make_workflow(owner_id="user-2", name="Theirs"),
            ]
        )
        client = _make_client(repo)

        response = client.get("/api/v1/tyr/workflows", headers=_headers())

        assert response.status_code == 200
        names = {workflow["name"] for workflow in response.json()}
        assert names == {"System", "Mine"}

    def test_list_scope_user_filters_to_owned_workflows(self) -> None:
        repo = InMemoryWorkflowRepository(
            [
                _make_workflow(scope=WorkflowScope.SYSTEM, owner_id=None, name="System"),
                _make_workflow(owner_id="user-1", name="Mine"),
            ]
        )
        client = _make_client(repo)

        response = client.get("/api/v1/tyr/workflows?scope=user", headers=_headers())

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Mine"
        assert body[0]["scope"] == "user"

    def test_create_user_workflow_assigns_owner(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/tyr/workflows",
            headers=_headers(),
            json={
                "name": "Dispatch Review",
                "description": "User workflow",
                "version": "1.0.0",
                "scope": "user",
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger", "label": "Start", "source": "manual"},
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "personaIds": ["reviewer"],
                        "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                        "executionMode": "parallel",
                        "joinMode": "all",
                    },
                    {"id": "end-1", "kind": "end", "label": "Done"},
                ],
                "edges": [
                    {"id": "e1", "source": "trigger-1", "target": "stage-1"},
                    {"id": "e2", "source": "stage-1", "target": "end-1"},
                ],
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["owner_id"] == "user-1"
        assert body["scope"] == "user"
        node_ids = {node["id"] for node in body["nodes"]}
        assert node_ids == {"trigger-1", "stage-1", "end-1"}
        assert "stages:" in body["definition_yaml"]
        assert body["compile_errors"] == []

    def test_non_admin_cannot_create_system_workflow(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/tyr/workflows",
            headers=_headers(),
            json={
                "name": "Shared Flow",
                "scope": "system",
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 403

    def test_admin_can_create_system_workflow(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/tyr/workflows",
            headers=_headers(roles="tyr:admin"),
            json={
                "name": "Shared Flow",
                "scope": "system",
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["scope"] == "system"
        assert body["owner_id"] is None

    def test_non_owner_cannot_get_private_workflow(self) -> None:
        workflow = _make_workflow(owner_id="user-1")
        repo = InMemoryWorkflowRepository([workflow])
        client = _make_client(repo)

        response = client.get(
            f"/api/v1/tyr/workflows/{workflow.id}",
            headers=_headers(user_id="user-2"),
        )

        assert response.status_code == 404

    def test_compile_endpoint_returns_errors_for_conditional_nodes(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/tyr/workflows/compile",
            headers=_headers(),
            json={
                "name": "Conditional Flow",
                "scope": "user",
                "nodes": [
                    {"id": "trigger-1", "kind": "trigger", "label": "Start", "source": "manual"},
                    {
                        "id": "cond-1",
                        "kind": "cond",
                        "label": "Branch",
                        "predicate": "stages.review.verdict == pass",
                    },
                ],
                "edges": [],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["definition_yaml"] is None
        assert body["compile_errors"]

    def test_create_workflow_preserves_resource_bindings(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/tyr/workflows",
            headers=_headers(),
            json={
                "name": "Knowledge Flow",
                "description": "Workflow with explicit Mimir resources",
                "version": "1.0.0",
                "scope": "user",
                "nodes": [
                    {
                        "id": "trigger-1",
                        "kind": "trigger",
                        "label": "Start",
                        "source": "manual dispatch",
                        "dispatchEvent": "code.requested",
                    },
                    {
                        "id": "stage-1",
                        "kind": "stage",
                        "label": "Review",
                        "personaIds": ["reviewer"],
                        "stageMembers": [{"personaId": "reviewer", "budget": 40}],
                        "executionMode": "parallel",
                        "maxConcurrent": 3,
                        "joinMode": "all",
                    },
                    {
                        "id": "mimir-1",
                        "kind": "resource",
                        "label": "Shared Mimir",
                        "resourceType": "mimir",
                        "bindingMode": "registry",
                        "registryEntryId": "shared-team-mimir",
                        "categories": ["decision", "entity"],
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "trigger-1", "target": "stage-1"},
                ],
                "resourceBindings": [
                    {
                        "id": "binding-1",
                        "resourceNodeId": "mimir-1",
                        "targetType": "stage",
                        "targetId": "stage-1",
                        "access": "read_write",
                        "writePrefixes": ["project/", "entity/"],
                        "readPriority": 5,
                    }
                ],
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["resourceBindings"] == [
            {
                "id": "binding-1",
                "resourceNodeId": "mimir-1",
                "targetType": "stage",
                "targetId": "stage-1",
                "access": "read_write",
                "writePrefixes": ["project/", "entity/"],
                "readPriority": 5,
            }
        ]

    def test_owner_can_update_user_workflow(self) -> None:
        workflow = _make_workflow(owner_id="user-1")
        repo = InMemoryWorkflowRepository([workflow])
        client = _make_client(repo)

        response = client.put(
            f"/api/v1/tyr/workflows/{workflow.id}",
            headers=_headers(),
            json={
                "name": "Updated Workflow",
                "description": "Updated",
                "version": "2.0.0",
                "scope": "user",
                "nodes": [{"id": "stage-2", "kind": "stage"}],
                "edges": [{"id": "edge-1", "source": "a", "target": "b"}],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Updated Workflow"
        assert body["version"] == "2.0.0"
        assert body["nodes"][0]["id"] == "stage-2"

    def test_non_owner_cannot_delete_private_workflow(self) -> None:
        workflow = _make_workflow(owner_id="user-1")
        repo = InMemoryWorkflowRepository([workflow])
        client = _make_client(repo)

        response = client.delete(
            f"/api/v1/tyr/workflows/{workflow.id}",
            headers=_headers(user_id="user-2"),
        )

        assert response.status_code == 404

    def test_non_admin_cannot_update_system_workflow(self) -> None:
        workflow = _make_workflow(
            scope=WorkflowScope.SYSTEM,
            owner_id=None,
            name="Shared",
        )
        repo = InMemoryWorkflowRepository([workflow])
        client = _make_client(repo)

        response = client.put(
            f"/api/v1/tyr/workflows/{workflow.id}",
            headers=_headers(),
            json={
                "name": "Shared Updated",
                "description": "Updated",
                "version": "2.0.0",
                "scope": "system",
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 403
