"""PostgreSQL adapter for chronicle repository."""

from __future__ import annotations

import json
from datetime import UTC
from decimal import Decimal
from uuid import UUID

import asyncpg

from volundr.domain.models import Chronicle, ChronicleStatus
from volundr.domain.ports import ChronicleRepository


class PostgresChronicleRepository(ChronicleRepository):
    """PostgreSQL implementation of ChronicleRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create(self, chronicle: Chronicle) -> Chronicle:
        """Persist a new chronicle."""
        await self._pool.execute(
            """
            INSERT INTO chronicles
                (id, session_id, status, project, repo, branch, model,
                 config_snapshot, summary, key_changes, unfinished_work,
                 token_usage, cost, duration_seconds, tags,
                 parent_chronicle_id, created_at, updated_at, owner_id, tenant_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17, $18, $19, $20)
            """,
            chronicle.id,
            chronicle.session_id,
            chronicle.status.value,
            chronicle.project,
            chronicle.repo,
            chronicle.branch,
            chronicle.model,
            json.dumps(chronicle.config_snapshot),
            chronicle.summary,
            json.dumps(chronicle.key_changes),
            chronicle.unfinished_work,
            chronicle.token_usage,
            chronicle.cost,
            chronicle.duration_seconds,
            chronicle.tags,
            chronicle.parent_chronicle_id,
            chronicle.created_at,
            chronicle.updated_at,
            chronicle.owner_id,
            chronicle.tenant_id,
        )
        return chronicle

    async def get(self, chronicle_id: UUID) -> Chronicle | None:
        """Retrieve a chronicle by ID."""
        row = await self._pool.fetchrow(
            "SELECT * FROM chronicles WHERE id = $1",
            chronicle_id,
        )
        if row is None:
            return None
        return self._row_to_chronicle(row)

    async def get_by_session(self, session_id: UUID) -> Chronicle | None:
        """Retrieve the most recent chronicle for a session."""
        row = await self._pool.fetchrow(
            "SELECT * FROM chronicles WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
            session_id,
        )
        if row is None:
            return None
        return self._row_to_chronicle(row)

    async def list(
        self,
        *,
        tenant_id: str | None,
        owner_id: str | None,
        project: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Chronicle]:
        """Retrieve chronicles within the given bounds, with optional filters."""
        conditions: list[str] = []
        params: list = []
        param_idx = 1

        # A bound compares with ``=`` and excludes the empty string, so it never
        # matches a NULL or blank attribution: those rows are unbounded-only.
        if tenant_id is not None:
            conditions.append(f"tenant_id = ${param_idx} AND tenant_id <> ''")
            params.append(tenant_id)
            param_idx += 1

        if owner_id is not None:
            conditions.append(f"owner_id = ${param_idx} AND owner_id <> ''")
            params.append(owner_id)
            param_idx += 1

        if project is not None:
            conditions.append(f"project = ${param_idx}")
            params.append(project)
            param_idx += 1

        if repo is not None:
            conditions.append(f"repo = ${param_idx}")
            params.append(repo)
            param_idx += 1

        if model is not None:
            conditions.append(f"model = ${param_idx}")
            params.append(model)
            param_idx += 1

        if tags is not None:
            conditions.append(f"tags @> ${param_idx}::text[]")
            params.append(tags)
            param_idx += 1

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = (
            f"SELECT * FROM chronicles {where_clause} "
            f"ORDER BY created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        )
        params.extend([limit, offset])

        rows = await self._pool.fetch(query, *params)
        return [self._row_to_chronicle(row) for row in rows]

    async def update(self, chronicle: Chronicle) -> Chronicle:
        """Update an existing chronicle."""
        await self._pool.execute(
            """
            UPDATE chronicles
            SET session_id = $2, status = $3, project = $4, repo = $5,
                branch = $6, model = $7, config_snapshot = $8, summary = $9,
                key_changes = $10, unfinished_work = $11, token_usage = $12,
                cost = $13, duration_seconds = $14, tags = $15,
                parent_chronicle_id = $16, updated_at = $17
            WHERE id = $1
            """,
            chronicle.id,
            chronicle.session_id,
            chronicle.status.value,
            chronicle.project,
            chronicle.repo,
            chronicle.branch,
            chronicle.model,
            json.dumps(chronicle.config_snapshot),
            chronicle.summary,
            json.dumps(chronicle.key_changes),
            chronicle.unfinished_work,
            chronicle.token_usage,
            chronicle.cost,
            chronicle.duration_seconds,
            chronicle.tags,
            chronicle.parent_chronicle_id,
            chronicle.updated_at,
        )
        return chronicle

    async def delete(self, chronicle_id: UUID) -> bool:
        """Delete a chronicle by ID."""
        result = await self._pool.execute(
            "DELETE FROM chronicles WHERE id = $1",
            chronicle_id,
        )
        return result == "DELETE 1"

    async def get_chain(self, chronicle_id: UUID) -> list[Chronicle]:
        """Retrieve the reforge chain by walking parent_chronicle_id links."""
        rows = await self._pool.fetch(
            """
            WITH RECURSIVE chain AS (
                SELECT * FROM chronicles WHERE id = $1
                UNION ALL
                SELECT c.* FROM chronicles c
                    INNER JOIN chain ch ON c.id = ch.parent_chronicle_id
            )
            SELECT * FROM chain ORDER BY created_at ASC
            """,
            chronicle_id,
        )
        return [self._row_to_chronicle(row) for row in rows]

    def _row_to_chronicle(self, row: asyncpg.Record) -> Chronicle:
        """Convert a database row to a Chronicle domain model."""
        created_at = row["created_at"]
        updated_at = row["updated_at"]

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)

        cost = row["cost"]
        if cost is not None:
            cost = Decimal(str(cost))

        config_snapshot = row["config_snapshot"]
        if isinstance(config_snapshot, str):
            config_snapshot = json.loads(config_snapshot)
        if not isinstance(config_snapshot, dict):
            config_snapshot = {}

        key_changes = row["key_changes"]
        if isinstance(key_changes, str):
            key_changes = json.loads(key_changes)
        if not isinstance(key_changes, list):
            key_changes = list(key_changes) if key_changes else []

        tags = row["tags"]
        if not isinstance(tags, list):
            tags = list(tags) if tags else []

        return Chronicle(
            id=row["id"],
            session_id=row["session_id"],
            status=ChronicleStatus(row["status"]),
            project=row["project"],
            repo=row["repo"],
            branch=row["branch"],
            model=row["model"],
            config_snapshot=config_snapshot,
            summary=row["summary"],
            key_changes=key_changes,
            unfinished_work=row["unfinished_work"],
            token_usage=row["token_usage"],
            cost=cost,
            duration_seconds=row["duration_seconds"],
            tags=tags,
            parent_chronicle_id=row["parent_chronicle_id"],
            owner_id=row["owner_id"],
            tenant_id=row["tenant_id"],
            created_at=created_at,
            updated_at=updated_at,
        )
