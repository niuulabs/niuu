"""PostgreSQL implementation of WorkflowRepository."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from identity.ports import AuthorizationDeniedError
from ravn.domain.persona_document import PersonaDependency
from ting.domain.exceptions import WorkflowConflictError
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import WorkflowDependency
from ting.ports.workflow_repository import WorkflowRepository


class PostgresWorkflowRepository(WorkflowRepository):
    """Workflow catalog persistence backed by asyncpg."""

    requires_database_pool = True

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

    async def list_all_workflows(self) -> list[WorkflowDefinition]:
        """Inventory every workflow for the explicit operator migration command."""
        rows = await self._pool.fetch("SELECT * FROM workflows ORDER BY updated_at, created_at, id")
        return [self._row_to_workflow(row) for row in rows]

    async def referenced_workflow_ids(self) -> set[UUID]:
        """Return workflow identities referenced by sagas or workflow campaigns."""
        rows = await self._pool.fetch(
            """
            SELECT workflow_id FROM sagas WHERE workflow_id IS NOT NULL
            UNION
            SELECT workflow_id FROM workflow_campaigns WHERE workflow_id IS NOT NULL
            """
        )
        return {row["workflow_id"] for row in rows}

    async def get_workflow(self, workflow_id: UUID) -> WorkflowDefinition | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM workflows WHERE id = $1",
            workflow_id,
        )
        if row is None:
            return None
        return self._row_to_workflow(row)

    async def save_workflow(self, workflow: WorkflowDefinition) -> WorkflowDefinition:
        result = await self._pool.execute(
            """
            INSERT INTO workflows
                (
                    id,
                    name,
                    description,
                    version,
                    scope,
                    owner_id,
                    graph_json,
                    created_at,
                    updated_at,
                    tenant_id,
                    persona_dependencies_json,
                    persona_definitions_json,
                    requirements_json,
                    schema_version,
                    workflow_dependencies_json,
                    workflow_definitions_json
                )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10,
                $11::jsonb, $12::jsonb, $13::jsonb,
                $15, $16::jsonb, $17::jsonb
            )
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                version = EXCLUDED.version,
                scope = EXCLUDED.scope,
                owner_id = EXCLUDED.owner_id,
                graph_json = EXCLUDED.graph_json,
                updated_at = EXCLUDED.updated_at,
                persona_dependencies_json = EXCLUDED.persona_dependencies_json,
                persona_definitions_json = EXCLUDED.persona_definitions_json,
                requirements_json = EXCLUDED.requirements_json,
                schema_version = EXCLUDED.schema_version,
                workflow_dependencies_json = EXCLUDED.workflow_dependencies_json,
                workflow_definitions_json = EXCLUDED.workflow_definitions_json
                WHERE workflows.tenant_id = EXCLUDED.tenant_id
                  AND workflows.owner_id IS NOT DISTINCT FROM EXCLUDED.owner_id
                  AND ($14::timestamptz IS NULL OR workflows.updated_at = $14)
            """,
            workflow.id,
            workflow.name,
            workflow.description,
            workflow.version,
            workflow.scope.value,
            workflow.owner_id,
            json.dumps(workflow.graph),
            workflow.created_at,
            workflow.updated_at,
            workflow.tenant_id,
            json.dumps(
                {
                    alias: dependency.to_dict()
                    for alias, dependency in workflow.persona_dependencies.items()
                }
            ),
            json.dumps(workflow.persona_definitions),
            json.dumps(workflow.requirements),
            self._expected_updated_at(workflow.revision),
            workflow.schema_version,
            json.dumps(
                {alias: pin.to_dict() for alias, pin in workflow.workflow_dependencies.items()}
            ),
            json.dumps(workflow.workflow_definitions),
        )
        if result == "INSERT 0 0":
            current = await self.get_workflow(workflow.id)
            if current is not None and workflow.revision != current.revision:
                raise WorkflowConflictError(f"Workflow {workflow.id} changed after it was read")
            raise AuthorizationDeniedError("Resource ownership is immutable")
        return replace(workflow, revision=self._database_revision(workflow.updated_at))

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

        raw_dependencies = row.get("persona_dependencies_json") or {}
        if isinstance(raw_dependencies, str):
            raw_dependencies = json.loads(raw_dependencies)
        dependencies = {
            str(alias): PersonaDependency.from_dict(value)
            for alias, value in dict(raw_dependencies).items()
        }
        raw_definitions = row.get("persona_definitions_json") or {}
        if isinstance(raw_definitions, str):
            raw_definitions = json.loads(raw_definitions)
        raw_requirements = row.get("requirements_json") or []
        if isinstance(raw_requirements, str):
            raw_requirements = json.loads(raw_requirements)
        workflow_dependencies = row.get("workflow_dependencies_json") or {}
        if isinstance(workflow_dependencies, str):
            workflow_dependencies = json.loads(workflow_dependencies)
        workflow_definitions = row.get("workflow_definitions_json") or {}
        if isinstance(workflow_definitions, str):
            workflow_definitions = json.loads(workflow_definitions)

        updated_at = row.get("updated_at") or datetime.now(UTC)

        return WorkflowDefinition(
            id=row["id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            description=row.get("description") or "",
            version=row.get("version") or "draft",
            scope=WorkflowScope(row.get("scope") or WorkflowScope.USER.value),
            owner_id=row.get("owner_id"),
            graph=graph,
            created_at=row.get("created_at") or datetime.now(UTC),
            updated_at=updated_at,
            persona_dependencies=dependencies,
            persona_definitions=dict(raw_definitions),
            requirements=list(raw_requirements),
            schema_version=row.get("schema_version") or 1,
            workflow_dependencies={
                alias: WorkflowDependency.from_dict(pin)
                for alias, pin in workflow_dependencies.items()
            },
            workflow_definitions=dict(workflow_definitions),
            revision=PostgresWorkflowRepository._database_revision(updated_at),
            source="postgres",
        )

    @staticmethod
    def _database_revision(updated_at: datetime) -> str:
        return f"db:{updated_at.isoformat()}"

    @staticmethod
    def _expected_updated_at(revision: str | None) -> datetime | None:
        if revision is None:
            return None
        if not revision.startswith("db:"):
            raise WorkflowConflictError("Workflow revision is not valid for PostgreSQL storage")
        try:
            return datetime.fromisoformat(revision.removeprefix("db:"))
        except ValueError as exc:
            raise WorkflowConflictError("Workflow revision is malformed") from exc
