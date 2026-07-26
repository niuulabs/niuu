"""Tests for the dynamic A2A agent card served by Ting."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from a2a.client.card_resolver import parse_agent_card
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.api.a2a_card import (
    A2A_ENDPOINT_PATH,
    BEARER_SECURITY_SCHEME,
    create_agent_card_router,
)
from ting.api.workflows import resolve_workflow_repo
from ting.config import A2AConfig
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.ports.workflow_repository import WorkflowRepository

CARD_PATH = "/.well-known/agent-card.json"


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
        return workflows

    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None:
        return self._workflows.get(workflow_id)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        self._workflows[workflow.id] = workflow
        return workflow

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        return self._workflows.pop(workflow_id, None) is not None


def _workflow(
    *,
    name: str = "tool-builder",
    description: str = "Build a learned tool from a capability gap.",
    scope: WorkflowScope = WorkflowScope.SYSTEM,
    tags: list[str] | None = None,
) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name=name,
        description=description,
        version="draft",
        scope=scope,
        owner_id=None if scope == WorkflowScope.SYSTEM else "user-1",
        graph={"tags": tags or ["tool-builder"], "nodes": [], "edges": []},
        created_at=now,
        updated_at=now,
    )


def _client(
    repo: WorkflowRepository,
    config: A2AConfig | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(create_agent_card_router(config or A2AConfig()))

    async def _resolve_repo() -> WorkflowRepository:
        return repo

    app.dependency_overrides[resolve_workflow_repo] = _resolve_repo
    return TestClient(app)


class TestAgentCard:
    def test_card_parses_and_projects_system_workflows_only(self) -> None:
        system = _workflow(tags=["tool-builder", "build"])
        user = _workflow(name="private-flow", scope=WorkflowScope.USER)
        client = _client(InMemoryWorkflowRepository([system, user]))

        response = client.get(CARD_PATH)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        card = parse_agent_card(response.json())
        assert card.name == A2AConfig().agent_name
        assert [skill.id for skill in card.skills] == [str(system.id)]
        assert card.skills[0].name == "tool-builder"
        assert list(card.skills[0].tags) == ["tool-builder", "build"]
        assert card.capabilities.streaming is False
        assert card.capabilities.push_notifications is False
        assert card.capabilities.extended_agent_card is True
        interface = card.supported_interfaces[0]
        assert interface.url.endswith(A2A_ENDPOINT_PATH)
        assert interface.protocol_binding == "JSONRPC"
        assert interface.protocol_version == "1.0"
        assert BEARER_SECURITY_SCHEME in card.security_schemes
        scheme = card.security_schemes[BEARER_SECURITY_SCHEME].http_auth_security_scheme
        assert scheme.scheme == "bearer"

    def test_card_with_no_workflows_is_valid(self) -> None:
        client = _client(InMemoryWorkflowRepository())

        response = client.get(CARD_PATH)

        assert response.status_code == 200
        card = parse_agent_card(response.json())
        assert list(card.skills) == []

    def test_workflow_without_declared_tags_gets_protocol_tag(self) -> None:
        workflow = _workflow()
        workflow.graph["tags"] = []
        client = _client(InMemoryWorkflowRepository([workflow]))

        response = client.get(CARD_PATH)

        assert response.status_code == 200
        card = parse_agent_card(response.json())
        assert list(card.skills[0].tags) == ["workflow"]

    def test_new_workflow_appears_without_restart(self) -> None:
        repo = InMemoryWorkflowRepository([_workflow()])
        client = _client(repo)

        first = client.get(CARD_PATH)
        assert len(first.json()["skills"]) == 1
        first_etag = first.headers["etag"]

        added = _workflow(name="deploy-review", tags=["deploy"])
        asyncio.run(repo.save_workflow(added))

        second = client.get(CARD_PATH)
        skills = {skill["name"] for skill in second.json()["skills"]}
        assert skills == {"tool-builder", "deploy-review"}
        assert second.headers["etag"] != first_etag

    def test_etag_revalidation_returns_304(self) -> None:
        client = _client(InMemoryWorkflowRepository([_workflow()]))

        first = client.get(CARD_PATH)
        etag = first.headers["etag"]

        second = client.get(CARD_PATH, headers={"If-None-Match": etag})

        assert second.status_code == 304
        assert second.headers["etag"] == etag
        assert second.content == b""

    def test_cache_control_uses_configured_max_age(self) -> None:
        client = _client(
            InMemoryWorkflowRepository(),
            config=A2AConfig(card_max_age_seconds=123),
        )

        response = client.get(CARD_PATH)

        assert response.headers["cache-control"] == "public, max-age=123"

    def test_public_base_url_overrides_interface_origin(self) -> None:
        client = _client(
            InMemoryWorkflowRepository(),
            config=A2AConfig(public_base_url="https://niuu.example/"),
        )

        response = client.get(CARD_PATH)

        card = parse_agent_card(response.json())
        assert card.supported_interfaces[0].url == f"https://niuu.example{A2A_ENDPOINT_PATH}"
