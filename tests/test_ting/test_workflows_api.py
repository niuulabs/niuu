"""Tests for Ting workflow catalog REST API."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from niuu.domain.models import Principal
from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflows import (
    _configure_child_task_a2a_runtime,
    create_workflows_router,
    resolve_workflow_launch_campaign_repo,
    resolve_workflow_repo,
)
from ting.config import AuthConfig, Settings
from ting.domain.exceptions import WorkflowConflictError
from ting.domain.models import (
    WorkflowCampaign,
    WorkflowDefinition,
    WorkflowDependency,
    WorkflowScope,
    WorkflowVersionSummary,
)
from ting.domain.workflow_document import workflow_document_payload, workflow_document_revision
from ting.domain.workflow_versioning import next_workflow_version
from ting.ports.volundr import SpawnRequest, VolundrPort, VolundrSession
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository


class InMemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: list[WorkflowDefinition] | None = None) -> None:
        self._workflows = {workflow.id: workflow for workflow in workflows or []}
        self._versions = {workflow.id: {workflow.version: workflow} for workflow in workflows or []}

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
        self._versions.setdefault(workflow.id, {})[workflow.version] = workflow
        return workflow

    async def list_workflow_versions(self, workflow_id):
        head = self._workflows.get(workflow_id)
        return [
            WorkflowVersionSummary(
                workflow_id=workflow_id,
                version=item.version,
                document_revision=item.document_revision or workflow_document_revision(item),
                created_at=item.updated_at,
                is_head=head is not None and item.version == head.version,
                based_on_revision=item.based_on_revision,
                origin=item.origin,
            )
            for item in self._versions.get(workflow_id, {}).values()
        ]

    async def get_workflow_version(self, workflow_id, *, version=None, document_revision=None):
        head = self._workflows.get(workflow_id)
        for item in self._versions.get(workflow_id, {}).values():
            item_revision = item.document_revision or workflow_document_revision(item)
            if (version is None or item.version == version) and (
                document_revision is None or item_revision == document_revision
            ):
                return replace(
                    item,
                    revision=head.revision if head else None,
                    document_revision=item_revision,
                    is_head=head is not None and item.version == head.version,
                )
        return None

    async def save_workflow_version(
        self, workflow, *, expected_revision, base_revision, bump="patch"
    ):
        head = self._workflows[workflow.id]
        if head.revision is not None and expected_revision != head.revision:
            raise ValueError("stale")
        version = next_workflow_version(head.version, bump)
        saved = replace(
            workflow,
            version=version,
            revision=f"head:{version}",
            document_revision=None,
            is_head=True,
            read_only=False,
        )
        saved = replace(saved, document_revision=workflow_document_revision(saved))
        self._versions.setdefault(workflow.id, {})[version] = saved
        self._workflows[workflow.id] = saved
        return saved

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        removed = self._workflows.pop(workflow_id, None)
        return removed is not None


class RecordingCampaignRepository(WorkflowCampaignRepository):
    def __init__(self) -> None:
        self.items: dict[UUID, WorkflowCampaign] = {}

    async def list_campaigns(self, *, owner_id: str):
        return [item for item in self.items.values() if item.owner_id == owner_id]

    async def list_active_campaigns(self):
        return list(self.items.values())

    async def get_campaign(self, campaign_id: UUID):
        return self.items.get(campaign_id)

    async def get_campaign_by_slug(self, slug: str, *, owner_id: str | None = None):
        return next(
            (
                item
                for item in self.items.values()
                if item.slug == slug and (owner_id is None or item.owner_id == owner_id)
            ),
            None,
        )

    async def save_campaign(self, campaign: WorkflowCampaign):
        self.items[campaign.id] = campaign
        return campaign

    async def delete_campaign(self, campaign_id: UUID):
        return self.items.pop(campaign_id, None) is not None


class FailingCampaignRepository(RecordingCampaignRepository):
    async def save_campaign(self, campaign: WorkflowCampaign):
        raise RuntimeError("database credentials must stay private")


class RecordingVolundrPort(VolundrPort):
    def __init__(
        self,
        *,
        name: str = "local",
        target_id: str = "local",
        chat_endpoint: str = "wss://sessions.example/s/session-123/session",
        tags: list[str] | None = None,
    ) -> None:
        self._name = name
        self._target_id = target_id
        self._chat_endpoint = chat_endpoint
        self._tags = tags or []
        self.requests: list[SpawnRequest] = []
        self.stopped: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def tags(self) -> list[str]:
        return self._tags

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
        self.stopped.append(session_id)

    async def list_integration_ids(self, *, auth_token=None, principal=None):
        self.integration_principal = principal
        return ["integration-github", "integration-memory"]

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


def test_developer_runtime_configures_local_a2a_card_without_mutating_defaults() -> None:
    source = {
        "gateway": {
            "platform": {
                "base_url": "https://platform.example",
                "a2a_agent_card_urls": ["https://peer.example/card"],
                "a2a_trusted_origins": ["https://peer.example"],
            }
        }
    }

    configured = _configure_child_task_a2a_runtime(
        source,
        card_url="https://ting.example/.well-known/agent-card.json",
    )

    platform = configured["gateway"]["platform"]
    assert platform["enabled"] is True
    assert platform["base_url"] == "https://platform.example"
    assert platform["a2a_agent_card_urls"] == [
        "https://peer.example/card",
        "https://ting.example/.well-known/agent-card.json",
    ]
    assert platform["a2a_trusted_origins"] == [
        "https://peer.example",
        "https://ting.example",
    ]
    assert "enabled" not in source["gateway"]["platform"]


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


def _make_research_workflow_with_placement(placement: dict) -> WorkflowDefinition:
    workflow = _make_research_workflow()
    return replace(
        workflow,
        schema_version=2,
        graph={**workflow.graph, "placement": placement},
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
    settings: Settings | None = None,
    campaign_repo: WorkflowCampaignRepository | None = None,
) -> TestClient:
    app = FastAPI()
    app.state.authorization = AllowAllAuthorizationAdapter()
    app.state.persona_source = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True)
    app.include_router(create_workflows_router())
    app.state.settings = settings or Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.dependency_overrides[resolve_workflow_repo] = lambda: repo
    app.dependency_overrides[resolve_workflow_launch_campaign_repo] = lambda: (
        campaign_repo or RecordingCampaignRepository()
    )
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
        assert body["version"] == "1.0.1"
        assert body["nodes"][0]["id"] == "stage-2"

    def test_versions_list_and_historical_get_keep_latest_head_token(self) -> None:
        original = _make_workflow(owner_id="user-1")
        original_revision = workflow_document_revision(original)
        original = replace(
            original,
            revision="head:1",
            document_revision=original_revision,
        )
        repo = InMemoryWorkflowRepository([original])
        client = _make_client(repo)
        response = client.post(
            f"/api/v1/ting/workflows/{original.id}/versions",
            headers=_headers(),
            json={
                "name": "Updated",
                "scope": "user",
                "expected_revision": "head:1",
                "base_revision": original_revision,
                "nodes": [],
                "edges": [],
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["id"] == str(original.id)
        assert response.json()["version"] == "1.0.1"

        versions = client.get(
            f"/api/v1/ting/workflows/{original.id}/versions",
            headers=_headers(),
        )
        assert versions.status_code == 200
        assert {item["version"] for item in versions.json()} == {"1.0.0", "1.0.1"}

        historical = client.get(
            f"/api/v1/ting/workflows/{original.id}/versions/1.0.0",
            headers=_headers(),
        )
        assert historical.status_code == 200
        assert historical.json()["is_head"] is False
        assert historical.json()["revision"] == "head:1.0.1"
        assert historical.json()["document_revision"] == original_revision

    def test_version_routes_report_missing_identity_version_and_base(self) -> None:
        workflow = _make_workflow(owner_id="user-1")
        revision = workflow_document_revision(workflow)
        workflow = replace(workflow, revision="head:1", document_revision=revision)
        client = _make_client(InMemoryWorkflowRepository([workflow]))
        missing_id = uuid4()

        assert (
            client.get(
                f"/api/v1/ting/workflows/{missing_id}/versions", headers=_headers()
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/ting/workflows/{missing_id}/versions/1.0.0",
                headers=_headers(),
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/ting/workflows/{workflow.id}/versions/9.9.9",
                headers=_headers(),
            ).status_code
            == 404
        )
        no_base = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/versions",
            headers=_headers(),
            json={"name": "No base", "scope": "user", "nodes": [], "edges": []},
        )
        assert no_base.status_code == 422
        unknown_base = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/versions",
            headers=_headers(),
            json={
                "name": "Unknown base",
                "scope": "user",
                "expected_revision": "head:1",
                "base_revision": "sha256:" + "f" * 64,
                "nodes": [],
                "edges": [],
            },
        )
        assert unknown_base.status_code == 404
        unknown_update = client.put(
            f"/api/v1/ting/workflows/{missing_id}",
            headers=_headers(),
            json={"name": "Missing", "scope": "user", "nodes": [], "edges": []},
        )
        assert unknown_update.status_code == 404
        changed_scope = client.put(
            f"/api/v1/ting/workflows/{workflow.id}",
            headers=_headers(roles="admin"),
            json={
                "name": "Changed scope",
                "scope": "system",
                "expected_revision": "head:1",
                "nodes": [],
                "edges": [],
            },
        )
        assert changed_scope.status_code == 422

    def test_repository_version_conflict_is_an_http_conflict(self) -> None:
        workflow = _make_workflow(owner_id="user-1")
        revision = workflow_document_revision(workflow)
        workflow = replace(workflow, revision="head:1", document_revision=revision)

        class ConflictingRepository(InMemoryWorkflowRepository):
            async def save_workflow_version(self, workflow, **kwargs):
                raise WorkflowConflictError("concurrent successor")

        client = _make_client(ConflictingRepository([workflow]))
        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/versions",
            headers=_headers(),
            json={
                "name": "Conflicting edit",
                "scope": "user",
                "expected_revision": "head:1",
                "base_revision": revision,
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "concurrent successor"

    def test_historical_successor_resolves_dependency_by_immutable_digest(self) -> None:
        child_base = _make_workflow(name="Child v1")
        child_revision = workflow_document_revision(child_base)
        child_base = replace(
            child_base,
            revision="child-head:1",
            document_revision=child_revision,
        )
        child_head = replace(
            child_base,
            version="1.1.0",
            name="Child v2",
            graph={"nodes": [{"id": "new-child", "kind": "stage"}], "edges": []},
            revision="child-head:2",
            document_revision=None,
        )
        child_head = replace(
            child_head,
            document_revision=workflow_document_revision(child_head),
        )
        parent_base = replace(
            _make_workflow(name="Parent v1"),
            schema_version=2,
            workflow_dependencies={
                "child": WorkflowDependency(
                    id=child_base.id,
                    revision=child_revision,
                    digest=child_revision,
                )
            },
            revision="parent-head:1",
        )
        parent_base_revision = workflow_document_revision(parent_base)
        parent_base = replace(parent_base, document_revision=parent_base_revision)
        parent_head = replace(
            parent_base,
            version="1.1.0",
            name="Parent v2",
            workflow_dependencies={},
            revision="parent-head:2",
            document_revision=None,
        )
        parent_head = replace(
            parent_head,
            document_revision=workflow_document_revision(parent_head),
        )
        repo = InMemoryWorkflowRepository([child_base, parent_base])
        repo._versions[child_base.id][child_head.version] = child_head
        repo._workflows[child_base.id] = child_head
        repo._versions[parent_base.id][parent_head.version] = parent_head
        repo._workflows[parent_base.id] = parent_head
        client = _make_client(repo)

        response = client.post(
            f"/api/v1/ting/workflows/{parent_base.id}/versions",
            headers=_headers(),
            json={
                "name": "Branch from v1",
                "scope": "user",
                "schema_version": 2,
                "expected_revision": "parent-head:2",
                "base_revision": parent_base_revision,
                "nodes": [{"id": "branched-parent", "kind": "stage"}],
                "edges": [],
            },
        )

        assert response.status_code == 201, response.text
        saved = repo._workflows[parent_base.id]
        child_document = saved.workflow_definitions["child"]["document"]
        assert child_document["name"] == "Child v1"
        assert child_document["graph"]["nodes"][0]["id"] == "n1"

    def test_successor_revalidates_embedded_dependency_closure(self) -> None:
        child = _make_workflow(name="Embedded Child")
        child_revision = workflow_document_revision(child)
        dependency = WorkflowDependency(
            id=child.id,
            revision=child_revision,
            digest=child_revision,
        )
        parent = replace(
            _make_workflow(name="Parent"),
            schema_version=2,
            workflow_dependencies={"child": dependency},
            workflow_definitions={
                "child": {
                    "document": workflow_document_payload(child),
                    "persona_definitions": {},
                    "workflow_definitions": {},
                }
            },
            revision="parent-head:1",
        )
        parent_revision = workflow_document_revision(parent)
        parent = replace(parent, document_revision=parent_revision)
        repo = InMemoryWorkflowRepository([parent])
        client = _make_client(repo)

        response = client.post(
            f"/api/v1/ting/workflows/{parent.id}/versions",
            headers=_headers(),
            json={
                "name": "Edited Parent",
                "scope": "user",
                "schema_version": 2,
                "expected_revision": "parent-head:1",
                "base_revision": parent_revision,
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 201, response.text
        assert repo._workflows[parent.id].workflow_definitions["child"]["document"]["name"] == (
            "Embedded Child"
        )

    def test_changed_child_pin_replaces_stale_aggregate_with_exact_historical_child(self) -> None:
        child_v1 = _make_workflow(name="Child v1")
        child_v1_revision = workflow_document_revision(child_v1)
        child_v1 = replace(child_v1, document_revision=child_v1_revision)
        child_v2 = replace(
            child_v1,
            name="Child v2",
            version="2.0.0",
            graph={"nodes": [{"id": "child-v2", "kind": "stage"}], "edges": []},
            document_revision=None,
        )
        child_v2_revision = workflow_document_revision(child_v2)
        child_v2 = replace(child_v2, document_revision=child_v2_revision)
        parent = replace(
            _make_workflow(name="Parent"),
            schema_version=2,
            workflow_dependencies={
                "child": WorkflowDependency(
                    id=child_v2.id,
                    revision=child_v2_revision,
                    digest=child_v2_revision,
                )
            },
            workflow_definitions={
                "child": {
                    "document": workflow_document_payload(child_v2),
                    "persona_definitions": {},
                    "workflow_definitions": {},
                }
            },
            revision="parent-head:1",
        )
        parent_revision = workflow_document_revision(parent)
        parent = replace(parent, document_revision=parent_revision)
        repo = InMemoryWorkflowRepository([child_v1, parent])
        repo._versions[child_v1.id][child_v2.version] = child_v2
        repo._workflows[child_v1.id] = child_v2
        client = _make_client(repo)

        response = client.post(
            f"/api/v1/ting/workflows/{parent.id}/versions",
            headers=_headers(),
            json={
                "name": "Parent using historical child",
                "scope": "user",
                "schema_version": 2,
                "expected_revision": "parent-head:1",
                "base_revision": parent_revision,
                "workflow_dependencies": {
                    "child": {
                        "id": str(child_v1.id),
                        "revision": child_v1_revision,
                        "digest": child_v1_revision,
                    }
                },
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 201, response.text
        successor = repo._workflows[parent.id]
        assert successor.workflow_dependencies["child"].digest == child_v1_revision
        assert successor.workflow_definitions["child"]["document"]["name"] == "Child v1"
        archived_parent = repo._versions[parent.id]["1.0.0"]
        assert archived_parent.workflow_dependencies["child"].digest == child_v2_revision
        assert archived_parent.workflow_definitions["child"]["document"]["name"] == "Child v2"

    def test_changed_child_pin_rejects_private_exact_child(self) -> None:
        embedded = _make_workflow(name="Embedded")
        embedded_revision = workflow_document_revision(embedded)
        private = _make_workflow(name="Private", owner_id="user-2")
        private_revision = workflow_document_revision(private)
        private = replace(private, document_revision=private_revision)
        parent = replace(
            _make_workflow(name="Parent"),
            schema_version=2,
            workflow_dependencies={
                "child": WorkflowDependency(
                    id=embedded.id,
                    revision=embedded_revision,
                    digest=embedded_revision,
                )
            },
            workflow_definitions={
                "child": {
                    "document": workflow_document_payload(embedded),
                    "persona_definitions": {},
                    "workflow_definitions": {},
                }
            },
            revision="parent-head:1",
        )
        parent_revision = workflow_document_revision(parent)
        parent = replace(parent, document_revision=parent_revision)
        client = _make_client(InMemoryWorkflowRepository([parent, private]))

        response = client.post(
            f"/api/v1/ting/workflows/{parent.id}/versions",
            headers=_headers(),
            json={
                "name": "Unauthorized child",
                "scope": "user",
                "schema_version": 2,
                "expected_revision": "parent-head:1",
                "base_revision": parent_revision,
                "workflow_dependencies": {
                    "child": {
                        "id": str(private.id),
                        "revision": private_revision,
                        "digest": private_revision,
                    }
                },
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "Pinned workflow dependency unavailable: child"

    def test_changed_child_pin_validates_repository_result_without_fallback(self) -> None:
        embedded = _make_workflow(name="Embedded")
        embedded_revision = workflow_document_revision(embedded)
        selected = _make_workflow(name="Selected")
        selected_revision = workflow_document_revision(selected)
        wrong = _make_workflow(name="Wrong")

        class WrongExactVersionRepository(InMemoryWorkflowRepository):
            async def get_workflow_version(self, workflow_id, **kwargs):
                if workflow_id == selected.id:
                    return wrong
                return await super().get_workflow_version(workflow_id, **kwargs)

        parent = replace(
            _make_workflow(name="Parent"),
            schema_version=2,
            workflow_dependencies={
                "child": WorkflowDependency(
                    id=embedded.id,
                    revision=embedded_revision,
                    digest=embedded_revision,
                )
            },
            workflow_definitions={
                "child": {
                    "document": workflow_document_payload(embedded),
                    "persona_definitions": {},
                    "workflow_definitions": {},
                }
            },
            revision="parent-head:1",
        )
        parent_revision = workflow_document_revision(parent)
        parent = replace(parent, document_revision=parent_revision)
        client = _make_client(WrongExactVersionRepository([parent]))

        response = client.post(
            f"/api/v1/ting/workflows/{parent.id}/versions",
            headers=_headers(),
            json={
                "name": "Mismatched child",
                "scope": "user",
                "schema_version": 2,
                "expected_revision": "parent-head:1",
                "base_revision": parent_revision,
                "workflow_dependencies": {
                    "child": {
                        "id": str(selected.id),
                        "revision": selected_revision,
                        "digest": selected_revision,
                    }
                },
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "Workflow dependency pin mismatch: child"

    def test_successor_rejects_cyclic_embedded_dependency(self) -> None:
        parent = replace(_make_workflow(name="Parent"), schema_version=2)
        child_document = workflow_document_payload(parent)
        child_revision = workflow_document_revision(parent)
        parent = replace(
            parent,
            workflow_dependencies={
                "self": WorkflowDependency(
                    id=parent.id,
                    revision=child_revision,
                    digest=child_revision,
                )
            },
            workflow_definitions={
                "self": {
                    "document": child_document,
                    "persona_definitions": {},
                    "workflow_definitions": {},
                }
            },
            revision="parent-head:1",
        )
        parent_revision = workflow_document_revision(parent)
        parent = replace(parent, document_revision=parent_revision)
        client = _make_client(InMemoryWorkflowRepository([parent]))

        response = client.post(
            f"/api/v1/ting/workflows/{parent.id}/versions",
            headers=_headers(),
            json={
                "name": "Cyclic edit",
                "scope": "user",
                "schema_version": 2,
                "expected_revision": "parent-head:1",
                "base_revision": parent_revision,
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 422
        assert "Cyclic" in response.json()["detail"]

    def test_successor_reconciles_persisted_resource_requirements(self, monkeypatch) -> None:
        workflow = _make_workflow(owner_id="user-1")
        workflow = replace(
            workflow,
            requirements=[
                {"id": "child/memory", "kind": "mimir", "resolved": False},
                {"id": "note", "kind": "other", "resolved": True},
                {"id": "removed", "kind": "mimir", "resolved": False},
                {
                    "id": "unchanged",
                    "kind": "mimir",
                    "resolved": False,
                    "source_binding": "same",
                },
                {
                    "id": "changed",
                    "kind": "mimir",
                    "resolved": False,
                    "source_binding": "old",
                },
            ],
            revision="head:1",
        )
        revision = workflow_document_revision(workflow)
        workflow = replace(workflow, document_revision=revision)
        repo = InMemoryWorkflowRepository([workflow])
        client = _make_client(repo)
        monkeypatch.setattr("ting.api.workflows.binding_errors", lambda *args, **kwargs: [])

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/versions",
            headers=_headers(),
            json={
                "name": "Bound successor",
                "scope": "user",
                "expected_revision": "head:1",
                "base_revision": revision,
                "nodes": [
                    {
                        "id": "unchanged",
                        "kind": "resource",
                        "registryEntryId": "same",
                    },
                    {
                        "id": "changed",
                        "kind": "resource",
                        "registryEntryId": "new",
                        "path": "/legacy",
                        "authRef": "legacy-secret",
                    },
                ],
                "edges": [],
            },
        )

        assert response.status_code == 201, response.text
        saved = repo._workflows[workflow.id]
        assert [item["id"] for item in saved.requirements] == [
            "child/memory",
            "note",
            "unchanged",
            "changed",
        ]
        assert saved.requirements[-2]["resolved"] is False
        assert saved.requirements[-1]["resolved"] is True
        assert saved.requirements[-1]["binding"] == "new"
        changed_node = next(node for node in saved.graph["nodes"] if node["id"] == "changed")
        assert "path" not in changed_node
        assert "authRef" not in changed_node

    def test_successor_rejects_invalid_resource_binding(self, monkeypatch) -> None:
        workflow = replace(
            _make_workflow(owner_id="user-1"),
            requirements=[
                {
                    "id": "memory",
                    "kind": "mimir",
                    "resolved": False,
                    "source_binding": "old",
                }
            ],
            revision="head:1",
        )
        revision = workflow_document_revision(workflow)
        workflow = replace(workflow, document_revision=revision)
        client = _make_client(InMemoryWorkflowRepository([workflow]))
        monkeypatch.setattr(
            "ting.api.workflows.binding_errors",
            lambda *args, **kwargs: ["registry binding is unavailable"],
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/versions",
            headers=_headers(),
            json={
                "name": "Invalid binding",
                "scope": "user",
                "expected_revision": "head:1",
                "base_revision": revision,
                "nodes": [{"id": "memory", "kind": "resource", "registryEntryId": "missing"}],
                "edges": [],
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "registry binding is unavailable"

    def test_admin_can_create_authored_successor_for_same_bundled_identity(self) -> None:
        bundled = _make_workflow(scope=WorkflowScope.SYSTEM, owner_id=None)
        base_revision = workflow_document_revision(bundled)
        bundled = replace(
            bundled,
            revision=base_revision,
            document_revision=base_revision,
            read_only=True,
            origin="bundled",
        )
        repo = InMemoryWorkflowRepository([bundled])
        client = _make_client(repo)
        response = client.post(
            f"/api/v1/ting/workflows/{bundled.id}/versions",
            headers=_headers(roles="admin"),
            json={
                "name": "System successor",
                "scope": "system",
                "expected_revision": base_revision,
                "base_revision": base_revision,
                "nodes": [],
                "edges": [],
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["id"] == str(bundled.id)
        assert response.json()["origin"] == "authored"
        assert response.json()["read_only"] is False
        assert response.json()["can_edit"] is True

    def test_bundled_workflow_identity_cannot_be_deleted(self) -> None:
        bundled = replace(
            _make_workflow(scope=WorkflowScope.SYSTEM, owner_id=None),
            read_only=True,
            origin="bundled",
        )
        response = _make_client(InMemoryWorkflowRepository([bundled])).delete(
            f"/api/v1/ting/workflows/{bundled.id}",
            headers=_headers(roles="admin"),
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Bundled workflows cannot be deleted"

    def test_non_admin_cannot_create_system_successor(self) -> None:
        bundled = _make_workflow(scope=WorkflowScope.SYSTEM, owner_id=None)
        base_revision = workflow_document_revision(bundled)
        bundled = replace(
            bundled,
            revision=base_revision,
            document_revision=base_revision,
            read_only=True,
            origin="bundled",
        )
        client = _make_client(InMemoryWorkflowRepository([bundled]))

        response = client.post(
            f"/api/v1/ting/workflows/{bundled.id}/versions",
            headers=_headers(),
            json={
                "name": "Unauthorized successor",
                "scope": "system",
                "expected_revision": base_revision,
                "base_revision": base_revision,
                "nodes": [],
                "edges": [],
            },
        )

        assert response.status_code == 403

    def test_concurrent_successor_rejects_stale_head_revision(self) -> None:
        original = _make_workflow(owner_id="user-1")
        base_revision = workflow_document_revision(original)
        original = replace(
            original,
            revision="head:1",
            document_revision=base_revision,
        )
        client = _make_client(InMemoryWorkflowRepository([original]))
        body = {
            "name": "Concurrent edit",
            "scope": "user",
            "expected_revision": "head:1",
            "base_revision": base_revision,
            "nodes": [],
            "edges": [],
        }

        first = client.post(
            f"/api/v1/ting/workflows/{original.id}/versions",
            headers=_headers(),
            json=body,
        )
        stale = client.post(
            f"/api/v1/ting/workflows/{original.id}/versions",
            headers=_headers(),
            json=body,
        )

        assert first.status_code == 201
        assert stale.status_code == 409

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
        campaign_repo = RecordingCampaignRepository()
        client = _make_client(
            repo,
            volundr_factory=RecordingVolundrFactory([adapter]),
            campaign_repo=campaign_repo,
        )

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
                "inheritedResultSchema": {
                    "type": "object",
                    "properties": {"evidence": {"type": "array", "items": {"type": "string"}}},
                    "required": ["evidence"],
                    "additionalProperties": False,
                },
                "provenance": {
                    "signal_id": "sig-1",
                    "valkyrie_id": "valkyrie-ymir",
                    "policy": "k8s-signals",
                },
            },
        )

        assert response.status_code == 201
        assert response.json()["workflowVersion"] == "1.0.0"
        assert response.json()["documentRevision"].startswith("sha256:")
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
        assert spawn.workload_config["workflow_result_schema"] == {
            "type": "object",
            "properties": {"evidence": {"type": "array", "items": {"type": "string"}}},
            "required": ["evidence"],
            "additionalProperties": False,
        }
        assert spawn.credential_names == []
        assert spawn.integration_ids == ["integration-github", "integration-memory"]
        assert adapter.integration_principal is not None
        assert spawn.workload_config["provenance"] == {
            "signal_id": "sig-1",
            "valkyrie_id": "valkyrie-ymir",
            "policy": "k8s-signals",
        }
        assert spawn.workload_config["personas"][0]["name"] == "research-framer"
        assert "Workflow Launch" in spawn.initial_prompt
        assert "Launch Context" in spawn.initial_prompt
        assert "Are AI grief companions a credible product category?" in spawn.initial_prompt
        [campaign] = campaign_repo.items.values()
        assert campaign.owner_id == "user-1"
        assert campaign.tenant_id == ""
        assert campaign.session_id == "session-123"
        assert campaign.connection_id == "local"
        assert campaign.workflow_id == workflow.id
        assert campaign.workflow_version == "1.0.0"
        assert campaign.workflow_snapshot["workflow_revision"].startswith("sha256:")
        assert campaign.metadata["surface"] == "ting.workflow-launch"

    def test_launch_historical_version_uses_its_graph_and_dependency_closure(self) -> None:
        old = _make_research_workflow()
        child = _make_workflow(name="Historical Child")
        child = replace(
            child,
            graph={"nodes": [{"id": "old-child-stage", "kind": "stage"}], "edges": []},
        )
        child_revision = workflow_document_revision(child)
        dependency = WorkflowDependency(
            id=child.id,
            revision=child_revision,
            digest=child_revision,
        )
        old = replace(
            old,
            schema_version=2,
            revision="head:old",
            document_revision=workflow_document_revision(old),
            workflow_dependencies={"child": dependency},
            workflow_definitions={
                "child": {
                    "document": workflow_document_payload(child),
                    "persona_definitions": {},
                    "workflow_definitions": {},
                }
            },
        )
        old = replace(old, document_revision=workflow_document_revision(old))
        head = replace(
            old,
            version="1.1.0",
            graph={
                "nodes": [
                    {
                        "id": "trigger-head",
                        "kind": "trigger",
                        "dispatchEvent": "head.requested",
                    }
                ],
                "edges": [],
            },
            workflow_dependencies={},
            workflow_definitions={},
            revision="head:new",
            document_revision=None,
        )
        head = replace(head, document_revision=workflow_document_revision(head))
        repo = InMemoryWorkflowRepository([old])
        repo._versions[old.id][head.version] = head
        repo._workflows[old.id] = head
        adapter = RecordingVolundrPort()
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        response = client.post(
            f"/api/v1/ting/workflows/{old.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "run the historical workflow", "workflowVersion": "1.0.0"},
        )

        assert response.status_code == 201, response.text
        assert response.json()["workflowVersion"] == "1.0.0"
        snapshot = adapter.requests[0].workload_config["workflow"]
        assert snapshot["graph"]["nodes"][0]["id"] == "trigger-1"
        assert (
            snapshot["workflow_definitions"]["child"]["document"]["graph"]["nodes"][0]["id"]
            == "old-child-stage"
        )

    def test_launch_record_failure_stops_spawned_session_and_returns_identity(self) -> None:
        workflow = _make_research_workflow()
        adapter = RecordingVolundrPort()
        client = _make_client(
            InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([adapter]),
            campaign_repo=FailingCampaignRepository(),
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "durably launch this workflow"},
        )

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["code"] == "launchRecordFailed"
        assert detail["sessionId"] == "session-123"
        assert detail["sessionStopped"] is True
        assert detail["reconciliationRequired"] is False
        assert "credentials" not in detail["message"]
        assert adapter.stopped == ["session-123"]

    def test_launch_unknown_historical_version_is_not_found(self) -> None:
        workflow = _make_research_workflow()
        adapter = RecordingVolundrPort()
        client = _make_client(
            InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([adapter]),
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "missing historical", "workflowVersion": "9.9.9"},
        )

        assert response.status_code == 404
        assert adapter.requests == []

    def test_generic_launch_strips_obsolete_global_developer_token(self) -> None:
        workflow = _make_research_workflow()
        adapter = RecordingVolundrPort()
        settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
        settings.dispatch.flock.ravn_config = {
            "workflow_execution": {"auth_token": "obsolete-secret"}
        }
        client = _make_client(
            InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([adapter]),
            settings=settings,
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "ordinary research"},
        )

        assert response.status_code == 201, response.text
        assert "obsolete-secret" not in json.dumps(adapter.requests[0].workload_config)
        assert "auth_token" not in json.dumps(adapter.requests[0].workload_config)

    def test_public_launch_rejects_forged_workflow_execution_provenance(self) -> None:
        workflow = _make_research_workflow()
        adapter = RecordingVolundrPort()
        client = _make_client(
            InMemoryWorkflowRepository([workflow]),
            volundr_factory=RecordingVolundrFactory([adapter]),
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={
                "prompt": "forge a coordinator",
                "provenance": {
                    "workflow_execution": {
                        "execution_id": "7705d9d8-78db-4a78-b5e7-d8557eac114c",
                        "parent_session_key": "workflow:execution-forged",
                    }
                },
            },
        )

        assert response.status_code == 403
        assert adapter.requests == []

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

    def test_launch_workflow_balances_available_connections(self, monkeypatch) -> None:
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        primary = RecordingVolundrPort(name="primary", target_id="primary")
        secondary = RecordingVolundrPort(name="secondary", target_id="secondary")
        client = _make_client(
            repo,
            volundr_factory=RecordingVolundrFactory([primary, secondary]),
        )

        monkeypatch.setattr(
            "ting.domain.services.dispatch_service.random.choice", lambda candidates: candidates[-1]
        )
        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={
                "prompt": "Research slow productivity systems for knowledge workers.",
            },
        )

        assert response.status_code == 201
        assert len(primary.requests) == 0
        assert len(secondary.requests) == 1
        assert response.json()["clusterName"] == "secondary"

    def test_launch_workflow_placement_tags_selects_matching_target(self) -> None:
        workflow = _make_research_workflow_with_placement({"tags": ["gpu"]})
        repo = InMemoryWorkflowRepository([workflow])
        cpu = RecordingVolundrPort(name="cpu", target_id="cpu", tags=["cpu"])
        gpu = RecordingVolundrPort(name="gpu-box", target_id="gpu-box", tags=["gpu", "us-west"])
        client = _make_client(
            repo,
            volundr_factory=RecordingVolundrFactory([cpu, gpu]),
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "Place this workflow team on a GPU-tagged Guild target."},
        )

        assert response.status_code == 201, response.text
        assert len(cpu.requests) == 0
        assert len(gpu.requests) == 1
        assert response.json()["clusterName"] == "gpu-box"

    def test_launch_workflow_placement_tags_balances_multiple_matches(self, monkeypatch) -> None:
        workflow = _make_research_workflow_with_placement({"tags": ["gpu"]})
        repo = InMemoryWorkflowRepository([workflow])
        first = RecordingVolundrPort(name="first", target_id="first", tags=["gpu"])
        second = RecordingVolundrPort(name="second", target_id="second", tags=["gpu"])
        client = _make_client(
            repo,
            volundr_factory=RecordingVolundrFactory([first, second]),
        )

        monkeypatch.setattr(
            "ting.domain.services.dispatch_service.random.choice", lambda candidates: candidates[-1]
        )
        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "Two GPU targets are eligible; use the existing balancing rule."},
        )

        assert response.status_code == 201, response.text
        assert len(first.requests) == 0
        assert len(second.requests) == 1

    def test_launch_workflow_placement_instance_pins_the_connection(self) -> None:
        workflow = _make_research_workflow_with_placement({"instance": "spark-01"})
        repo = InMemoryWorkflowRepository([workflow])
        other = RecordingVolundrPort(name="other", target_id="other")
        spark = RecordingVolundrPort(name="spark-01", target_id="spark-01")
        client = _make_client(
            repo,
            volundr_factory=RecordingVolundrFactory([other, spark]),
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "Pin this workflow team to the Spark instance."},
        )

        assert response.status_code == 201, response.text
        assert len(other.requests) == 0
        assert len(spark.requests) == 1
        assert response.json()["clusterName"] == "spark-01"

    def test_launch_workflow_placement_with_no_match_is_rejected_and_spawns_nothing(self) -> None:
        workflow = _make_research_workflow_with_placement({"tags": ["dgx-spark"]})
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort(tags=["cpu"])
        campaign_repo = RecordingCampaignRepository()
        client = _make_client(
            repo,
            volundr_factory=RecordingVolundrFactory([adapter]),
            campaign_repo=campaign_repo,
        )

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "No visible target carries the requested tag."},
        )

        assert response.status_code == 422
        assert "dgx-spark" in response.json()["detail"]
        assert "cpu" in response.json()["detail"]
        assert adapter.requests == []
        assert campaign_repo.items == {}

    def test_launch_workflow_placement_instance_with_no_match_is_rejected(self) -> None:
        workflow = _make_research_workflow_with_placement({"instance": "missing-instance"})
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort(name="local", target_id="local")
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "Pin to an instance that is not visible to this principal."},
        )

        assert response.status_code == 422
        assert "missing-instance" in response.json()["detail"]
        assert adapter.requests == []

    def test_launch_workflow_without_placement_behaves_as_before(self) -> None:
        """No graph.placement: the pre-existing untargeted balancing rule applies."""
        workflow = _make_research_workflow()
        repo = InMemoryWorkflowRepository([workflow])
        only = RecordingVolundrPort(name="only", target_id="only", tags=["gpu"])
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([only]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "Launch without declaring a placement."},
        )

        assert response.status_code == 201, response.text
        assert len(only.requests) == 1

    def test_launch_workflow_connection_id_conflicting_with_placement_is_rejected(self) -> None:
        """An explicit connectionId cannot silently strip a workflow's placement."""
        workflow = _make_research_workflow_with_placement({"tags": ["gpu"]})
        repo = InMemoryWorkflowRepository([workflow])
        cpu = RecordingVolundrPort(name="cpu", target_id="cpu", tags=["cpu"])
        gpu = RecordingVolundrPort(name="gpu-box", target_id="gpu-box", tags=["gpu"])
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([cpu, gpu]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={
                "prompt": "A connectionId that does not satisfy graph.placement is a conflict.",
                "connectionId": "cpu",
            },
        )

        assert response.status_code == 422, response.text
        assert "cpu" in response.json()["detail"]
        assert "gpu" in response.json()["detail"]
        assert len(cpu.requests) == 0
        assert len(gpu.requests) == 0

    def test_launch_workflow_connection_id_compatible_with_placement_is_admitted(self) -> None:
        """A connectionId that also satisfies graph.placement resolves directly."""
        workflow = _make_research_workflow_with_placement({"tags": ["gpu"]})
        repo = InMemoryWorkflowRepository([workflow])
        cpu = RecordingVolundrPort(name="cpu", target_id="cpu", tags=["cpu"])
        gpu = RecordingVolundrPort(name="gpu-box", target_id="gpu-box", tags=["gpu"])
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([cpu, gpu]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={
                "prompt": "A connectionId that also satisfies graph.placement is admitted.",
                "connectionId": "gpu-box",
            },
        )

        assert response.status_code == 201, response.text
        assert len(cpu.requests) == 0
        assert len(gpu.requests) == 1

    def test_launch_workflow_connection_id_instance_placement_conflict_is_rejected(self) -> None:
        workflow = _make_research_workflow_with_placement({"instance": "spark-01"})
        repo = InMemoryWorkflowRepository([workflow])
        other = RecordingVolundrPort(name="other", target_id="other")
        spark = RecordingVolundrPort(name="spark-01", target_id="spark-01")
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([other, spark]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={
                "prompt": "connectionId names a different instance than graph.placement.",
                "connectionId": "other",
            },
        )

        assert response.status_code == 422, response.text
        assert "other" in response.json()["detail"]
        assert "spark-01" in response.json()["detail"]
        assert len(other.requests) == 0
        assert len(spark.requests) == 0

    def test_launch_workflow_malformed_pinned_placement_is_422_not_500(self) -> None:
        """A stored graph.placement bypassing authoring validation (a row

        written before this rule existed, or by another tool) must fail the
        launch loudly with a 422, not bubble up as an unhandled 500.
        """
        workflow = _make_research_workflow_with_placement({"tags": ["gpu"], "instance": "x"})
        repo = InMemoryWorkflowRepository([workflow])
        adapter = RecordingVolundrPort()
        client = _make_client(repo, volundr_factory=RecordingVolundrFactory([adapter]))

        response = client.post(
            f"/api/v1/ting/workflows/{workflow.id}/launch",
            headers=_headers(roles="ting:admin"),
            json={"prompt": "This workflow's stored placement is malformed."},
        )

        assert response.status_code == 422, response.text
        assert "exactly one of 'tags' or 'instance'" in response.json()["detail"]
        assert adapter.requests == []

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


def test_workload_memory_ref_is_not_a_credential_name():
    from ting.api.workflows import _mimir_auth_credential_names

    assert _mimir_auth_credential_names(
        {
            "registry_refs": [
                {"auth_ref": "workload:mimir"},
                {"auth_ref": "brain-token"},
            ]
        }
    ) == ["brain-token"]
