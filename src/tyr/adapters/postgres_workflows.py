"""PostgreSQL implementation of WorkflowRepository."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from tyr.domain.models import WorkflowDefinition, WorkflowScope
from tyr.ports.workflow_repository import WorkflowRepository


class PostgresWorkflowRepository(WorkflowRepository):
    """Workflow catalog persistence backed by asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_workflows(
        self,
        *,
        owner_id: str,
        scope: WorkflowScope | None = None,
    ) -> list[WorkflowDefinition]:
        if scope == WorkflowScope.SYSTEM:
            rows = await self._pool.fetch(
                """
                SELECT *
                FROM workflows
                WHERE scope = 'system'
                ORDER BY updated_at DESC, created_at DESC
                """
            )
            return [self._row_to_workflow(row) for row in rows]

        if scope == WorkflowScope.USER:
            rows = await self._pool.fetch(
                """
                SELECT *
                FROM workflows
                WHERE scope = 'user'
                  AND owner_id = $1
                ORDER BY updated_at DESC, created_at DESC
                """,
                owner_id,
            )
            return [self._row_to_workflow(row) for row in rows]

        rows = await self._pool.fetch(
            """
            SELECT *
            FROM workflows
            WHERE scope = 'system'
               OR owner_id = $1
            ORDER BY updated_at DESC, created_at DESC
            """,
            owner_id,
        )
        return [self._row_to_workflow(row) for row in rows]

    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM workflows WHERE id = $1",
            workflow_id,
        )
        if row is None:
            return None
        return self._row_to_workflow(row)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        await self._pool.execute(
            """
            INSERT INTO workflows
                (id, name, description, version, scope, owner_id, definition_yaml,
                 graph_json, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                version = EXCLUDED.version,
                scope = EXCLUDED.scope,
                owner_id = EXCLUDED.owner_id,
                definition_yaml = EXCLUDED.definition_yaml,
                graph_json = EXCLUDED.graph_json,
                updated_at = EXCLUDED.updated_at
            """,
            workflow.id,
            workflow.name,
            workflow.description,
            workflow.version,
            workflow.scope.value,
            workflow.owner_id,
            workflow.definition_yaml,
            json.dumps(workflow.graph),
            workflow.created_at,
            workflow.updated_at,
        )
        return workflow

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        result = await self._pool.execute(
            "DELETE FROM workflows WHERE id = $1",
            workflow_id,
        )
        return result == "DELETE 1"

    @staticmethod
    def _row_to_workflow(row: asyncpg.Record) -> WorkflowDefinition:
        raw_graph = row.get("graph_json") or {}
        if isinstance(raw_graph, str):
            graph = json.loads(raw_graph)
        else:
            graph = dict(raw_graph)

        return WorkflowDefinition(
            id=row["id"],
            name=row["name"],
            description=row.get("description") or "",
            version=row.get("version") or "draft",
            scope=WorkflowScope(row.get("scope") or WorkflowScope.USER.value),
            owner_id=row.get("owner_id"),
            definition_yaml=row.get("definition_yaml"),
            graph=graph,
            created_at=row.get("created_at") or datetime.now(UTC),
            updated_at=row.get("updated_at") or datetime.now(UTC),
        )
