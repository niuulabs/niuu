"""Tests for PostgresWorkflowRepository with mocked asyncpg pool."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tyr.adapters.postgres_workflows import PostgresWorkflowRepository
from tyr.domain.models import WorkflowDefinition, WorkflowScope


@pytest.fixture
def mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def repo(mock_pool: MagicMock) -> PostgresWorkflowRepository:
    return PostgresWorkflowRepository(mock_pool)


@pytest.fixture
def workflow() -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        name="Review Workflow",
        description="Code review path",
        version="1.0.0",
        scope=WorkflowScope.USER,
        owner_id="user-1",
        definition_yaml="name: Review",
        graph={"nodes": [{"id": "n1"}], "edges": []},
        created_at=now,
        updated_at=now,
    )


class TestSaveWorkflow:
    @pytest.mark.asyncio
    async def test_upserts_workflow(
        self,
        repo: PostgresWorkflowRepository,
        workflow: WorkflowDefinition,
        mock_pool: MagicMock,
    ) -> None:
        await repo.save_workflow(workflow)

        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args
        assert "INSERT INTO workflows" in call_args[0][0]
        assert call_args[0][1] == workflow.id
        assert call_args[0][5] == workflow.scope.value
        assert call_args[0][8] == json.dumps(workflow.graph)


class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_lists_system_and_owned_user_workflows(
        self,
        repo: PostgresWorkflowRepository,
        workflow: WorkflowDefinition,
        mock_pool: MagicMock,
    ) -> None:
        mock_pool.fetch.return_value = [
            {
                "id": workflow.id,
                "name": workflow.name,
                "description": workflow.description,
                "version": workflow.version,
                "scope": workflow.scope.value,
                "owner_id": workflow.owner_id,
                "definition_yaml": workflow.definition_yaml,
                "graph_json": workflow.graph,
                "created_at": workflow.created_at,
                "updated_at": workflow.updated_at,
            }
        ]

        result = await repo.list_workflows(owner_id="user-1")

        assert len(result) == 1
        assert result[0].name == workflow.name
        call_args = mock_pool.fetch.call_args
        assert "scope = 'system'" in call_args[0][0]
        assert call_args[0][1] == "user-1"

    @pytest.mark.asyncio
    async def test_lists_user_scope_only_for_owner(
        self,
        repo: PostgresWorkflowRepository,
        mock_pool: MagicMock,
    ) -> None:
        await repo.list_workflows(owner_id="user-1", scope=WorkflowScope.USER)

        call_args = mock_pool.fetch.call_args
        assert "scope = 'user'" in call_args[0][0]
        assert "owner_id = $1" in call_args[0][0]
        assert call_args[0][1] == "user-1"

    @pytest.mark.asyncio
    async def test_lists_system_scope_without_owner_parameter(
        self,
        repo: PostgresWorkflowRepository,
        mock_pool: MagicMock,
    ) -> None:
        await repo.list_workflows(owner_id="user-1", scope=WorkflowScope.SYSTEM)

        call_args = mock_pool.fetch.call_args
        assert "scope = 'system'" in call_args[0][0]
        assert len(call_args[0]) == 1


class TestGetWorkflow:
    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self, repo: PostgresWorkflowRepository) -> None:
        result = await repo.get_workflow(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_json_string_graph(
        self,
        repo: PostgresWorkflowRepository,
        workflow: WorkflowDefinition,
        mock_pool: MagicMock,
    ) -> None:
        mock_pool.fetchrow.return_value = {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "version": workflow.version,
            "scope": WorkflowScope.SYSTEM.value,
            "owner_id": None,
            "definition_yaml": workflow.definition_yaml,
            "graph_json": json.dumps(workflow.graph),
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
        }

        result = await repo.get_workflow(workflow.id)

        assert result is not None
        assert result.scope == WorkflowScope.SYSTEM
        assert result.graph == workflow.graph


class TestDeleteWorkflow:
    @pytest.mark.asyncio
    async def test_returns_true_when_deleted(
        self,
        repo: PostgresWorkflowRepository,
        mock_pool: MagicMock,
    ) -> None:
        mock_pool.execute.return_value = "DELETE 1"

        result = await repo.delete_workflow(uuid4())

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_missing(
        self,
        repo: PostgresWorkflowRepository,
        mock_pool: MagicMock,
    ) -> None:
        mock_pool.execute.return_value = "DELETE 0"

        result = await repo.delete_workflow(uuid4())

        assert result is False
