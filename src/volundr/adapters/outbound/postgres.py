"""PostgreSQL adapter for session repository."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from volundr.domain.models import (
    GitSource,
    LocalMountSource,
    Session,
    SessionActivityState,
    SessionStatus,
)
from volundr.domain.ports import SessionRepository
from volundr.domain.projects import SessionCoordination
from volundr.domain.session_changes import SessionChanges
from volundr.domain.session_read_state import (
    SessionReadState,
    SessionReadStateChange,
    SessionReadStateConflictError,
)


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
                 launch_spec_id, archived_at, owner_id, tenant_id, workload_type,
                 origin, external_session_id, cli_session_id, session_definition,
                 activity_state, activity_metadata, activity_state_since,
                 workload_config, turn_started_at,
                 short_description, description,
                 short_description_updated_at, description_updated_at, session_usage,
                 coordination, actor_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
                    $22, $23, $24, $25, $26, $27, $28, $29, $30, $31, $32,
                    $33, $34, $35, $36, $37)
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
            session.launch_spec_id,
            session.archived_at,
            session.owner_id,
            session.tenant_id,
            session.workload_type,
            session.origin,
            session.external_session_id,
            session.cli_session_id,
            session.session_definition,
            session.activity_state.value if session.activity_state else None,
            json.dumps(session.activity_metadata or {}),
            session.activity_state_since,
            json.dumps(session.workload_config or {}),
            session.turn_started_at,
            session.short_description,
            session.description,
            session.short_description_updated_at,
            session.description_updated_at,
            json.dumps(session.session_usage) if session.session_usage is not None else None,
            session.coordination.model_dump_json() if session.coordination else None,
            session.actor_id,
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
        row = await self._pool.fetchrow(
            """
            UPDATE sessions
            SET name = $2, model = $3, source = $4, status = $5,
                chat_endpoint = $6, code_endpoint = $7, updated_at = $8,
                last_active = GREATEST(last_active, $9), message_count = $10, tokens_used = $11,
                pod_name = $12, error = $13, tracker_issue_id = $14,
                issue_tracker_url = $15, launch_spec_id = $16, archived_at = $17,
                owner_id = $18, tenant_id = $19, workload_type = $20,
                origin = $21, external_session_id = $22, cli_session_id = $23,
                session_definition = $24,
                activity_state = CASE
                    WHEN activity_state_since IS NULL OR $27 >= activity_state_since
                    THEN $25 ELSE activity_state END,
                activity_metadata = CASE
                    WHEN activity_state_since IS NULL OR $27 >= activity_state_since
                    THEN $26::jsonb ELSE activity_metadata END,
                activity_state_since = CASE
                    WHEN activity_state_since IS NULL OR $27 >= activity_state_since
                    THEN $27 ELSE activity_state_since END,
                workload_config = CASE WHEN coordination_revision = $30
                    THEN $28::jsonb ELSE ($28::jsonb - 'project_context') END,
                turn_started_at = CASE
                    WHEN activity_state_since IS NULL OR $27 >= activity_state_since
                    THEN $29 ELSE turn_started_at END
            WHERE id = $1
            RETURNING coordination, coordination_revision, workload_config,
                      activity_state, activity_metadata, activity_state_since,
                      turn_started_at, last_active,
                      short_description, description,
                      short_description_updated_at, description_updated_at,
                      last_work_at, open_tool_count, session_usage
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
            session.launch_spec_id,
            session.archived_at,
            session.owner_id,
            session.tenant_id,
            session.workload_type,
            session.origin,
            session.external_session_id,
            session.cli_session_id,
            session.session_definition,
            session.activity_state.value if session.activity_state else None,
            json.dumps(session.activity_metadata or {}),
            session.activity_state_since,
            json.dumps(session.workload_config or {}),
            session.turn_started_at,
            session.coordination_revision,
        )
        if row is None:
            raise LookupError(f"Session no longer exists: {session.id}")
        return Session.model_validate(
            {
                **session.model_dump(),
                "coordination": self._parse_json_dict(row["coordination"]) or None,
                "coordination_revision": row["coordination_revision"],
                "workload_config": self._parse_json_dict(row["workload_config"]) or {},
                "activity_state": self._parse_activity_state(row["activity_state"]),
                "activity_metadata": self._parse_activity_metadata(row["activity_metadata"]),
                "activity_state_since": row["activity_state_since"],
                "turn_started_at": row["turn_started_at"],
                "last_active": row["last_active"],
                # Never written here: the stored description wins over a stale copy.
                **self._description_fields(row),
                # Folded at log ingest (F12d), never by this update: the stored facts win.
                **self._work_fact_fields(row),
                # Projection-owned: an unrelated stale update must preserve newer usage.
                "session_usage": self._parse_json_dict(row.get("session_usage")) or None,
            }
        )

    async def update_coordination(
        self, session: Session, coordination: SessionCoordination | None
    ) -> Session | None:
        row = await self._pool.fetchrow(
            """UPDATE sessions SET coordination = $2::jsonb,
                   coordination_revision = coordination_revision + 1,
                   workload_config = COALESCE(workload_config, '{}'::jsonb) - 'project_context',
                   updated_at = $3
               WHERE id = $1 AND coordination_revision = $4
                 AND owner_id IS NOT DISTINCT FROM $5
                 AND tenant_id IS NOT DISTINCT FROM $6
               RETURNING *""",
            session.id,
            coordination.model_dump_json() if coordination is not None else None,
            datetime.now(UTC),
            session.coordination_revision,
            session.owner_id,
            session.tenant_id,
        )
        return self._row_to_session(row) if row is not None else None

    async def update_description(
        self, session_id: UUID, changes: dict[str, str | None], now: datetime
    ) -> Session | None:
        # $2/$4 say whether a key was sent. SET expressions read the pre-update row,
        # so a stamp only moves when the sent value differs from the stored one.
        row = await self._pool.fetchrow(
            """UPDATE sessions SET
                   short_description = CASE WHEN $2 THEN $3 ELSE short_description END,
                   short_description_updated_at = CASE
                       WHEN $2 AND $3::text IS DISTINCT FROM short_description
                       THEN $6 ELSE short_description_updated_at END,
                   description = CASE WHEN $4 THEN $5 ELSE description END,
                   description_updated_at = CASE
                       WHEN $4 AND $5::text IS DISTINCT FROM description
                       THEN $6 ELSE description_updated_at END,
                   updated_at = CASE
                       WHEN ($2 AND $3::text IS DISTINCT FROM short_description)
                         OR ($4 AND $5::text IS DISTINCT FROM description)
                       THEN $6 ELSE updated_at END
               WHERE id = $1
               RETURNING *""",
            session_id,
            "short_description" in changes,
            changes.get("short_description"),
            "description" in changes,
            changes.get("description"),
            now,
        )
        return self._row_to_session(row) if row is not None else None

    async def update_forge_mcp_grants(
        self, session_id: UUID, grants: list[str], now: datetime
    ) -> Session | None:
        # One statement, so a concurrent start's launch_id write is never lost:
        # only the grants key of the forge_mcp block is replaced.
        row = await self._pool.fetchrow(
            """UPDATE sessions SET
                   workload_config = jsonb_set(
                       COALESCE(workload_config, '{}'::jsonb),
                       '{forge_mcp}',
                       CASE WHEN jsonb_typeof(workload_config->'forge_mcp') = 'object'
                            THEN workload_config->'forge_mcp' ELSE '{}'::jsonb END
                       || jsonb_build_object('grants', $2::jsonb)
                   ),
                   updated_at = $3
               WHERE id = $1
               RETURNING *""",
            session_id,
            json.dumps(grants),
            now,
        )
        return self._row_to_session(row) if row is not None else None

    async def list_stale_running(self, older_than: datetime) -> list[Session]:
        """Return RUNNING sessions whose last_active is at/before older_than."""
        rows = await self._pool.fetch(
            """SELECT * FROM sessions
               WHERE status = $1
                 AND COALESCE(last_active, created_at) <= $2
               ORDER BY last_active ASC""",
            SessionStatus.RUNNING.value,
            older_than,
        )
        return [self._row_to_session(row) for row in rows]

    async def get_read_states(
        self, session_ids: list[UUID], user_id: str
    ) -> dict[UUID, SessionReadState]:
        if not session_ids:
            return {}
        rows = await self._pool.fetch(
            """SELECT s.id, s.latest_final_seq AS latest_output_seq,
                s.latest_final_turn_id AS latest_output_turn_id,
                s.latest_final_at AS latest_output_at,
                COALESCE(r.read_through_seq, 0) AS read_through_seq,
                COALESCE(r.manually_unread, FALSE) AS manually_unread,
                COALESCE(r.revision, 0) AS revision
            FROM sessions s LEFT JOIN session_read_states r
                ON r.session_id = s.id AND r.user_id = $2
            WHERE s.id = ANY($1::uuid[])""",
            session_ids,
            user_id,
        )
        return {row["id"]: SessionReadState.model_validate(dict(row)) for row in rows}

    async def changes_since(self, cursor: int, reader_id: str | None) -> SessionChanges:
        # One statement, so the cursor and the changed ids come from one snapshot.
        # change_xid is set by trigger on every write (migration 000102). A cursor
        # past this snapshot's xmax was issued by another database (one restored
        # from a logical dump starts its transaction ids over): everything changed.
        row = await self._pool.fetchrow(
            """WITH snap AS (SELECT pg_current_snapshot() AS s),
               since AS (
                   SELECT CASE WHEN $1::text::xid8 > pg_snapshot_xmax(s)
                               THEN '0'::xid8 ELSE $1::text::xid8 END AS xid
                   FROM snap
               )
               SELECT pg_snapshot_xmin((SELECT s FROM snap))::text::bigint AS cursor,
                   ARRAY(
                       SELECT id FROM sessions
                       WHERE change_xid >= (SELECT xid FROM since)
                       UNION
                       SELECT session_id FROM session_read_states
                       WHERE user_id = $2 AND change_xid >= (SELECT xid FROM since)
                   ) AS ids""",
            str(cursor),
            reader_id,
        )
        return SessionChanges(cursor=row["cursor"], ids=frozenset(row["ids"]))

    async def change_read_state(
        self,
        session_id: UUID,
        user_id: str,
        change: SessionReadStateChange,
    ) -> SessionReadState:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Serializes both completion projection and reader mutations. Generic lifecycle
                # updates deliberately do not write these columns or this separate reader table.
                session = await conn.fetchrow(
                    "SELECT latest_final_seq, latest_final_turn_id, latest_final_at "
                    "FROM sessions WHERE id=$1 FOR UPDATE",
                    session_id,
                )
                if session is None:
                    raise ValueError("Session no longer exists")
                current = await conn.fetchrow(
                    "SELECT * FROM session_read_states WHERE session_id=$1 AND user_id=$2",
                    session_id,
                    user_id,
                )
                revision = current["revision"] if current else 0
                if change.expected_revision != revision:
                    raise SessionReadStateConflictError(
                        "Read state changed; refresh before changing it"
                    )
                if change.through_seq > session["latest_final_seq"]:
                    raise ValueError("Cannot mark unseen future output as read")
                through = current["read_through_seq"] if current else 0
                if change.state == "read":
                    through = max(through, change.through_seq)
                forced = change.state == "unread"
                await conn.execute(
                    """INSERT INTO session_read_states
                        (session_id, user_id, read_through_seq, manually_unread, revision)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (session_id, user_id) DO UPDATE
                    SET read_through_seq=$3, manually_unread=$4, revision=$5""",
                    session_id,
                    user_id,
                    through,
                    forced,
                    revision + 1,
                )
                return SessionReadState(
                    latest_output_seq=session["latest_final_seq"],
                    latest_output_turn_id=session["latest_final_turn_id"],
                    latest_output_at=session["latest_final_at"],
                    read_through_seq=through,
                    manually_unread=forced,
                    revision=revision + 1,
                )

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
        activity_state_since = row.get("activity_state_since")
        turn_started_at = row.get("turn_started_at")

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if last_active is not None and last_active.tzinfo is None:
            last_active = last_active.replace(tzinfo=UTC)
        if archived_at is not None and archived_at.tzinfo is None:
            archived_at = archived_at.replace(tzinfo=UTC)
        if activity_state_since is not None and activity_state_since.tzinfo is None:
            activity_state_since = activity_state_since.replace(tzinfo=UTC)
        if turn_started_at is not None and turn_started_at.tzinfo is None:
            turn_started_at = turn_started_at.replace(tzinfo=UTC)

        source = self._parse_source(row.get("source"))
        activity_state = self._parse_activity_state(row.get("activity_state"))
        activity_metadata = self._parse_activity_metadata(row.get("activity_metadata"))

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
            launch_spec_id=row.get("launch_spec_id"),
            archived_at=archived_at,
            owner_id=row.get("owner_id"),
            actor_id=row.get("actor_id"),
            tenant_id=row.get("tenant_id"),
            workload_type=row.get("workload_type") or "session",
            workload_config=self._parse_json_dict(row.get("workload_config")),
            coordination=self._parse_json_dict(row.get("coordination")) or None,
            coordination_revision=row.get("coordination_revision", 0),
            origin=row.get("origin") or "volundr",
            external_session_id=row.get("external_session_id"),
            cli_session_id=row.get("cli_session_id"),
            session_definition=row.get("session_definition"),
            activity_state=activity_state,
            activity_state_since=activity_state_since,
            turn_started_at=turn_started_at,
            activity_metadata=activity_metadata,
            session_usage=self._parse_json_dict(row.get("session_usage")) or None,
            **self._description_fields(row),
            **self._work_fact_fields(row),
        )

    @staticmethod
    def _work_fact_fields(row: asyncpg.Record) -> dict:
        """Work-fact columns (G10, F12d); tolerant of rows predating migration 000094."""
        last_work_at = row.get("last_work_at")
        if last_work_at is not None and last_work_at.tzinfo is None:
            last_work_at = last_work_at.replace(tzinfo=UTC)
        return {
            "last_work_at": last_work_at,
            "open_tool_count": max(int(row.get("open_tool_count") or 0), 0),
        }

    @staticmethod
    def _description_fields(row: asyncpg.Record) -> dict:
        """Description columns from a row; tolerant of rows predating migration 000092."""
        stamps = {}
        for name in ("short_description_updated_at", "description_updated_at"):
            stamp = row.get(name)
            if stamp is not None and stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            stamps[name] = stamp
        return {
            "short_description": row.get("short_description"),
            "description": row.get("description"),
            **stamps,
        }

    @staticmethod
    def _parse_activity_state(raw: str | None) -> SessionActivityState | None:
        """Parse the activity_state column, tolerating unknown/legacy values."""
        if not raw:
            return None
        try:
            return SessionActivityState(raw)
        except ValueError:
            return None

    @staticmethod
    def _parse_activity_metadata(raw: str | dict | None) -> dict:
        """Parse the activity_metadata JSONB column (asyncpg may return str or dict)."""
        return PostgresSessionRepository._parse_json_dict(raw)

    @staticmethod
    def _parse_json_dict(raw: str | dict | None) -> dict:
        """Parse a JSONB dict column (asyncpg may return str or dict)."""
        if raw is None:
            return {}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        if isinstance(raw, dict):
            return raw
        return {}

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
