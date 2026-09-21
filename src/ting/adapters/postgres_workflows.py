"""PostgreSQL implementation of WorkflowRepository."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import asyncpg

from identity.ports import AuthorizationDeniedError
from ravn.domain.persona_document import PersonaDependency
from ting.domain.exceptions import WorkflowConflictError, WorkflowReadOnlyError
from ting.domain.models import WorkflowDefinition, WorkflowScope, WorkflowVersionSummary
from ting.domain.workflow_document import WorkflowDependency, workflow_document_revision
from ting.domain.workflow_versioning import (
    deserialize_workflow_version,
    next_workflow_version,
    serialize_workflow_version,
)
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
        async with self._pool.acquire() as connection, connection.transaction():
            await self._lock(connection, workflow.id)
            row = await connection.fetchrow(
                "SELECT * FROM workflows WHERE id = $1 FOR UPDATE", workflow.id
            )
            current = self._row_to_workflow(row) if row else None
            if current is None:
                if workflow.revision is not None:
                    raise WorkflowConflictError("Workflow no longer exists")
                saved = await self._write_workflow(connection, workflow)
                await self._archive(connection, saved)
                return saved
            if workflow.revision != current.revision:
                raise WorkflowConflictError("Workflow changed after it was read")
            self._assert_identity(current, workflow)
            return await self._advance(
                connection,
                current,
                workflow,
                base_revision=workflow_document_revision(current),
                bundled_version=workflow.version if workflow.origin == "bundled" else None,
            )

    async def save_workflow_version(
        self,
        workflow: WorkflowDefinition,
        *,
        expected_revision: str | None,
        base_revision: str,
        bump: Literal["patch", "minor", "major"] = "patch",
    ) -> WorkflowDefinition:
        async with self._pool.acquire() as connection, connection.transaction():
            await self._lock(connection, workflow.id)
            row = await connection.fetchrow(
                "SELECT * FROM workflows WHERE id = $1 FOR UPDATE", workflow.id
            )
            current = self._row_to_workflow(row) if row else None
            if current is None or current.revision != expected_revision:
                raise WorkflowConflictError(
                    "Workflow changed after it was read; reload before saving"
                )
            self._assert_identity(current, workflow)
            if base_revision != workflow_document_revision(current):
                base = await connection.fetchrow(
                    "SELECT snapshot FROM workflow_versions "
                    "WHERE workflow_id = $1 AND document_revision = $2",
                    workflow.id,
                    base_revision,
                )
                if base is None:
                    raise WorkflowConflictError("The selected base version no longer exists")
            return await self._advance(
                connection, current, workflow, base_revision=base_revision, bump=bump
            )

    @staticmethod
    async def _lock(connection, workflow_id: UUID) -> None:
        # Also serializes creation, where there is no row to lock yet.
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", str(workflow_id)
        )

    @staticmethod
    def _assert_identity(current: WorkflowDefinition, proposed: WorkflowDefinition) -> None:
        if (current.tenant_id, current.owner_id, current.scope) != (
            proposed.tenant_id,
            proposed.owner_id,
            proposed.scope,
        ):
            raise AuthorizationDeniedError("Resource ownership is immutable")

    async def _advance(
        self, connection, current, proposed, *, base_revision, bump="patch", bundled_version=None
    ):
        await self._archive(connection, current)
        successor = replace(
            proposed,
            version=bundled_version or next_workflow_version(current.version, bump),
            revision=current.revision,
            created_at=current.created_at,
            updated_at=datetime.now(UTC),
            read_only=bool(bundled_version),
            origin="bundled" if bundled_version else "authored",
            is_head=True,
            based_on_revision=base_revision,
        )
        saved = await self._write_workflow(connection, successor)
        await self._archive(connection, saved)
        return saved

    @staticmethod
    async def _archive(connection, workflow):
        payload = serialize_workflow_version(workflow)
        result = await connection.execute(
            """INSERT INTO workflow_versions
                (workflow_id, version, document_revision, snapshot, created_at)
                VALUES ($1, $2, $3, $4::jsonb, $5)
                ON CONFLICT (workflow_id, version) DO NOTHING""",
            workflow.id,
            workflow.version,
            payload["document_revision"],
            json.dumps(payload),
            workflow.updated_at,
        )
        if result == "INSERT 0 0":
            row = await connection.fetchrow(
                "SELECT snapshot FROM workflow_versions WHERE workflow_id = $1 AND version = $2",
                workflow.id,
                workflow.version,
            )
            stored = row["snapshot"] if row else None
            if isinstance(stored, str):
                stored = json.loads(stored)
            if stored != payload:
                raise WorkflowConflictError("A different immutable workflow version already exists")

    async def list_workflow_versions(self, workflow_id: UUID) -> list[WorkflowVersionSummary]:
        head = await self.get_workflow(workflow_id)
        if head is None:
            return []
        rows = await self._pool.fetch(
            "SELECT snapshot FROM workflow_versions WHERE workflow_id = $1 "
            "ORDER BY created_at DESC, version DESC",
            workflow_id,
        )
        versions = [self._deserialize(row, head) for row in rows]
        if not any(item.version == head.version for item in versions):
            versions.insert(0, head)
        return [
            WorkflowVersionSummary(
                workflow_id=item.id,
                version=item.version,
                document_revision=workflow_document_revision(item),
                created_at=item.updated_at,
                is_head=item.version == head.version,
                based_on_revision=item.based_on_revision,
                origin=item.origin,
            )
            for item in versions
        ]

    async def get_workflow_version(
        self, workflow_id: UUID, *, version: str | None = None, document_revision: str | None = None
    ) -> WorkflowDefinition | None:
        if version is not None and document_revision is not None:
            raise ValueError("Select a workflow version by version or document revision, not both")
        head = await self.get_workflow(workflow_id)
        if head is None:
            return None
        if (version is None or version == head.version) and (
            document_revision is None or document_revision == workflow_document_revision(head)
        ):
            return head
        row = await self._pool.fetchrow(
            """SELECT snapshot FROM workflow_versions WHERE workflow_id = $1
               AND ($2::text IS NULL OR version = $2)
               AND ($3::text IS NULL OR document_revision = $3)""",
            workflow_id,
            version,
            document_revision,
        )
        return self._deserialize(row, head) if row else None

    @staticmethod
    def _deserialize(row, head):
        payload = row["snapshot"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        result = deserialize_workflow_version(payload, head_revision=head.revision, is_head=False)
        return replace(result, is_head=result.version == head.version)

    async def _write_workflow(self, connection, workflow: WorkflowDefinition) -> WorkflowDefinition:
        result = await connection.execute(
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
                    workflow_definitions_json,
                    version_origin,
                    based_on_revision
                )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10,
                $11::jsonb, $12::jsonb, $13::jsonb,
                $15, $16::jsonb, $17::jsonb, $18, $19
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
                workflow_definitions_json = EXCLUDED.workflow_definitions_json,
                version_origin = EXCLUDED.version_origin,
                based_on_revision = EXCLUDED.based_on_revision
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
            workflow.origin,
            workflow.based_on_revision,
        )
        if result == "INSERT 0 0":
            row = await connection.fetchrow("SELECT * FROM workflows WHERE id = $1", workflow.id)
            current = self._row_to_workflow(row) if row else None
            if current is not None and workflow.revision != current.revision:
                raise WorkflowConflictError(f"Workflow {workflow.id} changed after it was read")
            raise AuthorizationDeniedError("Resource ownership is immutable")
        return replace(
            workflow,
            revision=self._database_revision(workflow.updated_at),
            document_revision=workflow_document_revision(workflow),
        )

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        result = await self._pool.execute(
            """DELETE FROM workflows w WHERE id = $1
               AND version_origin <> 'bundled'
               AND NOT EXISTS (
                   SELECT 1 FROM workflow_versions v
                   WHERE v.workflow_id = w.id AND v.snapshot->>'origin' = 'bundled'
               )""",
            workflow_id,
        )
        if result == "DELETE 1":
            return True
        if await self.get_workflow(workflow_id) is not None:
            raise WorkflowReadOnlyError(
                "Workflows with bundled version history cannot be deleted; "
                "their identity must remain available to preserve package history"
            )
        return False

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

        workflow = WorkflowDefinition(
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
            read_only=row.get("version_origin") == "bundled",
            origin=row.get("version_origin") or "authored",
            based_on_revision=row.get("based_on_revision"),
        )
        return replace(workflow, document_revision=workflow_document_revision(workflow))

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
