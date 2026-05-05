"""PostgreSQL adapter for session repository."""

import json
from datetime import UTC
from uuid import UUID

import asyncpg

from volundr.domain.models import GitSource, LocalMountSource, Session, SessionStatus
from volundr.domain.ports import SessionRepository


class PostgresSessionRepository(SessionRepository):
    """PostgreSQL implementation of SessionRepository using raw SQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create(self, session: Session) -> Session:
        """Persist a new session."""
        source_json = json.dumps(session.source.model_dump())
        await self._pool.execute(
            """
            INSERT INTO sessions
                (id, name, model, source, status, chat_endpoint, code_endpoint,
                 created_at, updated_at, last_active, message_count, tokens_used,
                 pod_name, error, tracker_issue_id, issue_tracker_url,
                 preset_id, archived_at, owner_id, tenant_id, workload_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21)
            """,
            session.id,
            session.name,
            session.model,
            source_json,
            session.status.value,
            session.chat_endpoint,
            session.code_endpoint,
            session.created_at,
            session.updated_at,
            session.last_active,
            session.message_count,
            session.tokens_used,
            session.pod_name,
            session.error,
            session.tracker_issue_id,
            session.issue_tracker_url,
            session.preset_id,
            session.archived_at,
            session.owner_id,
            session.tenant_id,
            session.workload_type,
        )
        return session

    async def get(self, session_id: UUID) -> Session | None:
        """Retrieve a session by ID."""
        row = await self._pool.fetchrow(
            "SELECT * FROM sessions WHERE id = $1",
            session_id,
        )
        if row is None:
            return None
        return self._row_to_session(row)

    async def get_many(self, session_ids: list[UUID]) -> dict[UUID, Session]:
        """Retrieve multiple sessions by ID."""
        if not session_ids:
            return {}
        rows = await self._pool.fetch(
            "SELECT * FROM sessions WHERE id = ANY($1::uuid[])",
            session_ids,
        )
        return {row["id"]: self._row_to_session(row) for row in rows}

    async def list(
        self,
        status: SessionStatus | None = None,
        tenant_id: str | None = None,
        owner_id: str | None = None,
    ) -> list[Session]:
        """Retrieve sessions ordered by creation time, with optional filters."""
        conditions: list[str] = []
        params: list[object] = []
        param_index = 1

        if status is not None:
            conditions.append(f"status = ${param_index}")
            params.append(status.value)
            param_index += 1

        if tenant_id is not None:
            conditions.append(f"tenant_id = ${param_index}")
            params.append(tenant_id)
            param_index += 1

        if owner_id is not None:
            conditions.append(f"owner_id = ${param_index}")
            params.append(owner_id)
            param_index += 1

        query = "SELECT * FROM sessions"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"

        rows = await self._pool.fetch(query, *params)
        return [self._row_to_session(row) for row in rows]

    async def update(self, session: Session) -> Session:
        """Update an existing session."""
        source_json = json.dumps(session.source.model_dump())
        await self._pool.execute(
            """
            UPDATE sessions
            SET name = $2, model = $3, source = $4, status = $5,
                chat_endpoint = $6, code_endpoint = $7, updated_at = $8,
                last_active = $9, message_count = $10, tokens_used = $11,
                pod_name = $12, error = $13, tracker_issue_id = $14,
                issue_tracker_url = $15, preset_id = $16, archived_at = $17,
                owner_id = $18, tenant_id = $19, workload_type = $20
            WHERE id = $1
            """,
            session.id,
            session.name,
            session.model,
            source_json,
            session.status.value,
            session.chat_endpoint,
            session.code_endpoint,
            session.updated_at,
            session.last_active,
            session.message_count,
            session.tokens_used,
            session.pod_name,
            session.error,
            session.tracker_issue_id,
            session.issue_tracker_url,
            session.preset_id,
            session.archived_at,
            session.owner_id,
            session.tenant_id,
            session.workload_type,
        )
        return session

    async def delete(self, session_id: UUID) -> bool:
        """Delete a session by ID."""
        result = await self._pool.execute(
            "DELETE FROM sessions WHERE id = $1",
            session_id,
        )
        return result == "DELETE 1"

    def _row_to_session(self, row: asyncpg.Record) -> Session:
        """Convert a database row to a Session domain model."""
        created_at = row["created_at"]
        updated_at = row["updated_at"]
        last_active = row["last_active"]
        archived_at = row.get("archived_at")

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if last_active is not None and last_active.tzinfo is None:
            last_active = last_active.replace(tzinfo=UTC)
        if archived_at is not None and archived_at.tzinfo is None:
            archived_at = archived_at.replace(tzinfo=UTC)

        source = self._parse_source(row.get("source"))

        return Session(
            id=row["id"],
            name=row["name"],
            model=row["model"],
            source=source,
            status=SessionStatus(row["status"]),
            chat_endpoint=row["chat_endpoint"],
            code_endpoint=row["code_endpoint"],
            created_at=created_at,
            updated_at=updated_at,
            last_active=last_active,
            message_count=row["message_count"],
            tokens_used=row["tokens_used"],
            pod_name=row["pod_name"],
            error=row["error"],
            tracker_issue_id=row.get("tracker_issue_id"),
            issue_tracker_url=row.get("issue_tracker_url"),
            preset_id=row.get("preset_id"),
            archived_at=archived_at,
            owner_id=row.get("owner_id"),
            tenant_id=row.get("tenant_id"),
            workload_type=row.get("workload_type") or "session",
        )

    @staticmethod
    def _parse_source(raw: str | dict | None) -> GitSource | LocalMountSource:
        """Parse source JSONB from DB, with backward compat for old repo/branch columns."""
        if raw is None:
            return GitSource()
        if isinstance(raw, str):
            raw = json.loads(raw)
        source_type = raw.get("type", "git")
        if source_type == "local_mount":
            return LocalMountSource.model_validate(raw)
        return GitSource.model_validate(raw)
