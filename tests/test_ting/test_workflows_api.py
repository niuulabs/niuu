"""Tests for Ting workflow catalog REST API."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.models import Principal
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflows import create_workflows_router, resolve_workflow_repo
from ting.config import AuthConfig, Settings
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.ports.volundr import SpawnRequest, VolundrPort, VolundrSession
from ting.ports.workflow_repository import WorkflowRepository


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


class RecordingVolundrPort(VolundrPort):
    def __init__(
        self,
        *,
        name: str = "local",
        target_id: str = "local",
        chat_endpoint: str = "wss://sessions.example/s/session-123/session",
    ) -> None:
        self._name = name
        self._target_id = target_id
        self._chat_endpoint = chat_endpoint
        self.requests: list[SpawnRequest] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def target_id(self) -> str:
        return self._target_id

    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession:
        self.requests.append(request)
        return VolundrSession(
            id="session-123",
            name=request.name,
            status="starting",
            chat_endpoint=self._chat_endpoint,
            tracker_issue_id=request.tracker_issue_id,
            cluster_name=self._name,
            repo=request.repo,
            branch=request.branch,
            base_branch=request.base_branch,
            workload_type=request.workload_type,
        )

    async def get_session(self, session_id: str, *, auth_token=None, principal=None):
        return None

    async def list_sessions(self, *, auth_token=None, principal=None):
        return []

    async def get_pr_status(self, session_id: str):
        raise NotImplementedError

    async def get_chronicle_summary(self, session_id: str) -> str:
        raise NotImplementedError

    async def send_message(self, session_id: str, message: str, *, auth_token=None, principal=None):
        raise NotImplementedError

    async def stop_session(self, session_id: str, *, auth_token=None, principal=None) -> None:
        raise NotImplementedError

    async def list_integration_ids(self, *, auth_token=None, principal=None):
        return []

    async def list_repos(self, *, auth_token=None, principal=None):
        return []

    async def get_last_assistant_message(self, session_id: str) -> str:
        raise NotImplementedError

    async def get_conversation(self, session_id: str) -> dict:
        raise NotImplementedError

    async def subscribe_activity(self):
        if False:
            yield None


class RecordingVolundrFactory:
    def __init__(self, adapters: list[VolundrPort]) -> None:
        self._adapters = adapters

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        return list(self._adapters)

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        return self._adapters[0] if self._adapters else None

    async def for_principal(self, principal: Principal) -> list[VolundrPort]:
        return list(self._adapters)


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
        graph={"nodes": [{"id": "n1", "kind": "stage"}], "edges": []},
        created_at=now,
        updated_at=now,
    )


def _make_research_workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Research Campaign",
        description="Research workflow",
        version="1.0.0",
        scope=WorkflowScope.SYSTEM,
        owner_id=None,
        graph={
            "nodes": [
                {
                    "id": "trigger-1",
                    "kind": "trigger",
                    "dispatchEvent": "research.requested",
                },
                {
                    "id": "memory",
                    "kind": "resource",
                    "resourceType": "mimir",
                    "bindingMode": "registry",
                    "registryEntryId": "tmp-mimir",
                    "path": "/tmp/mimir",
                    "authRef": "integration:volundr",
                },
                {
                    "id": "stage-1",
                    "kind": "stage",
                    "label": "Frame",
                    "stageMembers": [
                        {
                            "personaId": "research-framer",
                            "budget": 12,
                            "model": "gpt-5.5",
                            "consumesEventTypes": ["research.requested"],
                        }
                    ],
                },
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "stage-1"}],
            "resourceBindings": [
                {
                    "id": "binding-1",
                    "resourceNodeId": "memory",
                    "targetType": "workflow",
                    "targetId": "research-campaign",
                    "access": "read_write",
                    "writePrefixes": ["research/", "learnings/", "followups/"],
                    "readPriority": 1,
                }
            ],
        },
        created_at=now,
        updated_at=now,
    )


_SIGNING_KEY = "test-only-signing-key-32-bytes-long!"


def _build_token(scopes: list[str]) -> str:
    """Mint a Valkyrie build token JWT (signature ignored downstream)."""
    return jwt.encode(
        {"sub": "user-1", "token_use": "valkyrie_build", "scopes": scopes},
        _SIGNING_KEY,
        algorithm="HS256",
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


def _make_client(
    repo: WorkflowRepository,
    *,
    volundr_factory: RecordingVolundrFactory | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(create_workflows_router())
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.dependency_overrides[resolve_workflow_repo] = lambda: repo
    if volundr_factory is not None:
        app.dependency_overrides[resolve_volundr_factory] = lambda: volundr_factory
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

        response = client.get("/api/v1/ting/workflows", headers=_headers())

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

        response = client.get("/api/v1/ting/workflows?scope=user", headers=_headers())

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Mine"
        assert body[0]["scope"] == "user"

    def test_create_user_workflow_assigns_owner(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/ting/workflows",
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

    def test_non_admin_cannot_create_system_workflow(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/ting/workflows",
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
            "/api/v1/ting/workflows",
            headers=_headers(roles="ting:admin"),
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

    def test_resource_admin_can_create_system_workflow(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/ting/workflows",
            headers=_headers(roles="admin"),
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
            f"/api/v1/ting/workflows/{workflow.id}",
            headers=_headers(user_id="user-2"),
        )

        assert response.status_code == 404

    def test_create_workflow_preserves_resource_bindings(self) -> None:
        repo = InMemoryWorkflowRepository()
        client = _make_client(repo)

        response = client.post(
            "/api/v1/ting/workflows",
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
                "artifactPaths": ["project/{slug}/result.json"],
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
        assert body["artifactPaths"] == ["project/{slug}/result.json"]

    def test_owner_can_update_user_workflow(self) -> None:
        workflow = _make_workflow(owner_id="user-1")
        repo = InMemoryWorkflowRepository([workflow])
        client = _make_client(repo)

        response = client.put(
            f"/api/v1/ting/workflows/{workflow.id}",
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
            f"/api/v1/ting/workflows/{workflow.id}",
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
            f"/api/v1/ting/workflows/{workflow.id}",
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

    def test_resource_admin_can_update_system_workflow(self) -> None:
        workflow = _make_workflow(
            scope=WorkflowScope.SYSTEM,
            owner_id=None,
            name="Shared",
        )
        repo = InMemoryWorkflowRepository([workflow])
        client = _make_client(repo)

        response = client.put(
            f"/api/v1/ting/workflows/{workflow.id}",
            headers=_headers(roles="admin"),
            json={
                "name": "Shared Updated",
                "description": "Updated",
                "version": "2.0.0",
                "scope": "system",
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Shared Updated"
        assert body["scope"] == "system"
        assert body["owner_id"] is None

    def test_launch_workflow_spawns_direct_flock_session(self) -> None:
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort()
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={
                "prompt": "Research whether AI grief companions are a credible product category.",
                "sessionName": "grief-companions",
                "repo": "https://github.com/niuulabs/volundr.git",
                "branch": "feat/research",
                "model": "gpt-5.5",
                "definition": "skuldCodex",
                "context": {
                    "question": ("Are AI grief companions a credible product category?"),
                    "mode": "evaluative",
                },
                "provenance": {
                    "signal_id": "sig-1",
                    "valkyrie_id": "valkyrie-ymir",
                    "policy": "k8s-signals",
                },
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["workflowId"] == str(workflow.id)
        assert body["slug"] == "grief-companions"
        assert body["sessionId"] == "session-123"
        assert body["chatEndpoint"] == "wss://sessions.example/s/session-123/session"
        assert len(adapter.requests) == 1
        spawn = adapter.requests[0]
        assert spawn.definition == "skuldCodex"
        assert spawn.model == "gpt-5.5"
        assert spawn.workload_type == "ravn_flock"
        assert spawn.name == "grief-companions"
        assert spawn.repo == "https://github.com/niuulabs/volundr.git"
        assert spawn.branch == "feat/research"
        assert spawn.tracker_issue_id == "workflow:grief-companions"
        assert spawn.workload_config["workflow"]["name"] == "Research Campaign"
        assert spawn.credential_names == []
        assert spawn.workload_config["provenance"] == {
            "signal_id": "sig-1",
            "valkyrie_id": "valkyrie-ymir",
            "policy": "k8s-signals",
        }
        assert spawn.workload_config["personas"][0]["name"] == "research-framer"
        assert "Workflow Launch" in spawn.initial_prompt
        assert "Launch Context" in spawn.initial_prompt
        assert "Are AI grief companions a credible product category?" in spawn.initial_prompt

    def test_launch_workflow_trims_trailing_dash_from_generated_session_name(self) -> None:
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort()
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={
                "prompt": (
                    "Final public-session-endpoint observer join smoke from Muninn. "
                    "Produce one short sentence and stop."
                ),
                "definition": "skuldCodex",
            },
        )

        assert response.status_code == 201
        assert adapter.requests[0].name == "research-campaign-final-public-session-endpoint"

    def test_launch_workflow_returns_public_chat_endpoint(self) -> None:
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort(chat_endpoint="ws://127.0.0.1:8080/s/session-123/session")
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "Launch a small research smoke test."},
        )

        assert response.status_code == 201
        assert response.json()["chatEndpoint"] == "ws://testserver:8080/s/session-123/session"

    def test_launch_workflow_uses_first_available_connection(self) -> None:
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        primary = RecordingVolundrPort(name="primary", target_id="primary")
        secondary = RecordingVolundrPort(name="secondary", target_id="secondary")
        client = _make_client(
            repo,
            volundr_factory=RecordingVolundrFactory([primary, secondary]),
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={
                "prompt": "Research slow productivity systems for knowledge workers.",
            },
        )

        assert response.status_code == 201
        assert len(primary.requests) == 1
        assert len(secondary.requests) == 0

    def test_launch_workflow_scoped_build_token_missing_scope_is_403(self) -> None:
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort()
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        # A build token scoped only for Forge session creation must NOT be able
        # to launch a Ting workflow.
        token = _build_token(["forge:session:create"])
        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers={**_headers(roles="ting:admin"), "Authorization": f"Bearer {token}"},
            json={"prompt": "Attempt a launch with the wrong scope."},
        )

        assert response.status_code == 403
        assert "ting:workflow:launch" in response.json()["detail"]
        assert len(adapter.requests) == 0

    def test_launch_workflow_scoped_build_token_with_scope_admitted(self) -> None:
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort()
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        token = _build_token(["ting:workflow:launch"])
        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers={**_headers(roles="ting:admin"), "Authorization": f"Bearer {token}"},
            json={"prompt": "Launch with the matching build scope."},
        )

        assert response.status_code == 201
        assert len(adapter.requests) == 1

    def test_launch_workflow_normal_token_unaffected(self) -> None:
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort()
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        # A plain (non-build) bearer token has no scope claim and must pass.
        token = jwt.encode({"type": "pat", "sub": "user-1"}, _SIGNING_KEY, algorithm="HS256")
        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers={**_headers(roles="ting:admin"), "Authorization": f"Bearer {token}"},
            json={"prompt": "A human PAT launches normally."},
        )

        assert response.status_code == 201
        assert len(adapter.requests) == 1
