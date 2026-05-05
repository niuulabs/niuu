"""PostgreSQL implementation of SagaRepository."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from tyr.domain.models import Phase, PhaseStatus, Raid, RaidStatus, Saga, SagaStatus
from tyr.ports.saga_repository import SagaRepository


class PostgresSagaRepository(SagaRepository):
    """Saga reference persistence backed by asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[asyncpg.Connection]:
        """Acquire a connection and start a transaction."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def save_phase(self, phase: Phase, *, conn: Any | None = None) -> None:
        executor = conn or self._pool
        await executor.execute(
            """
            INSERT INTO phases (id, saga_id, tracker_id, number, name, status, confidence)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
            """,
            phase.id,
            phase.saga_id,
            phase.tracker_id,
            phase.number,
            phase.name,
            phase.status.value,
            phase.confidence,
        )

    async def save_raid(self, raid: Raid, *, conn: Any | None = None) -> None:
        executor = conn or self._pool
        await executor.execute(
            """
            INSERT INTO raids
                (id, phase_id, tracker_id, name, description, acceptance_criteria,
                 declared_files, estimate_hours, status, confidence, session_id,
                 branch, chronicle_summary, retry_count, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
            ON CONFLICT (id) DO NOTHING
            """,
            raid.id,
            raid.phase_id,
            raid.tracker_id,
            raid.name,
            raid.description,
            raid.acceptance_criteria,
            raid.declared_files,
            raid.estimate_hours,
            raid.status.value,
            raid.confidence,
            raid.session_id,
            raid.branch,
            raid.chronicle_summary,
            raid.retry_count,
            raid.created_at,
            raid.updated_at,
        )

    async def save_saga(self, saga: Saga, *, conn: Any | None = None) -> None:
        executor = conn or self._pool
        await executor.execute(
            """
            INSERT INTO sagas
                (id, tracker_id, tracker_type, slug, name,
                 repos, feature_branch, base_branch, status, confidence, created_at, owner_id,
                 workflow_id, workflow_version, workflow_snapshot)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                tracker_id = EXCLUDED.tracker_id,
                tracker_type = EXCLUDED.tracker_type,
                slug = EXCLUDED.slug,
                name = EXCLUDED.name,
                repos = EXCLUDED.repos,
                feature_branch = EXCLUDED.feature_branch,
                base_branch = EXCLUDED.base_branch,
                status = EXCLUDED.status,
                confidence = EXCLUDED.confidence,
                owner_id = EXCLUDED.owner_id,
                workflow_id = EXCLUDED.workflow_id,
                workflow_version = EXCLUDED.workflow_version,
                workflow_snapshot = EXCLUDED.workflow_snapshot
            """,
            saga.id,
            saga.tracker_id,
            saga.tracker_type,
            saga.slug,
            saga.name,
            saga.repos,
            saga.feature_branch,
            saga.base_branch,
            saga.status.value,
            saga.confidence,
            saga.created_at,
            saga.owner_id,
            saga.workflow_id,
            saga.workflow_version,
            json.dumps(saga.workflow_snapshot) if saga.workflow_snapshot is not None else None,
        )

    async def list_sagas(self, *, owner_id: str | None = None) -> list[Saga]:
        if owner_id is not None:
            rows = await self._pool.fetch(
                "SELECT * FROM sagas WHERE owner_id = $1 ORDER BY created_at DESC",
                owner_id,
            )
        else:
            rows = await self._pool.fetch(
                "SELECT * FROM sagas ORDER BY created_at DESC",
            )
        return [self._row_to_saga(r) for r in rows]

    async def get_saga(self, saga_id: UUID, *, owner_id: str | None = None) -> Saga | None:
        if owner_id is not None:
            row = await self._pool.fetchrow(
                "SELECT * FROM sagas WHERE id = $1 AND owner_id = $2",
                saga_id,
                owner_id,
            )
        else:
            row = await self._pool.fetchrow(
                "SELECT * FROM sagas WHERE id = $1",
                saga_id,
            )
        if row is None:
            return None
        return self._row_to_saga(row)

    async def get_saga_by_slug(self, slug: str) -> Saga | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM sagas WHERE slug = $1",
            slug,
        )
        if row is None:
            return None
        return self._row_to_saga(row)

    async def count_by_status(self) -> dict[str, int]:
        rows = await self._pool.fetch("SELECT status, COUNT(*) AS cnt FROM raids GROUP BY status")
        counts: dict[str, int] = {s.value: 0 for s in RaidStatus}
        for row in rows:
            counts[row["status"]] = row["cnt"]
        return counts

    async def delete_saga(self, saga_id: UUID, *, owner_id: str | None = None) -> bool:
        if owner_id is not None:
            result = await self._pool.execute(
                "DELETE FROM sagas WHERE id = $1 AND owner_id = $2",
                saga_id,
                owner_id,
            )
        else:
            result = await self._pool.execute(
                "DELETE FROM sagas WHERE id = $1",
                saga_id,
            )
        return result == "DELETE 1"

    async def update_saga_status(self, saga_id: UUID, status: SagaStatus) -> None:
        await self._pool.execute(
            "UPDATE sagas SET status = $1 WHERE id = $2",
            status.value,
            saga_id,
        )

    async def update_saga_workflow(
        self,
        saga_id: UUID,
        *,
        workflow_id: UUID | None,
        workflow_version: str | None,
        workflow_snapshot: dict[str, Any] | None,
        owner_id: str | None = None,
    ) -> None:
        snapshot = json.dumps(workflow_snapshot) if workflow_snapshot is not None else None

        if owner_id is not None:
            await self._pool.execute(
                """
                UPDATE sagas
                SET workflow_id = $1,
                    workflow_version = $2,
                    workflow_snapshot = $3::jsonb
                WHERE id = $4
                  AND owner_id = $5
                """,
                workflow_id,
                workflow_version,
                snapshot,
                saga_id,
                owner_id,
            )
            return

        await self._pool.execute(
            """
            UPDATE sagas
            SET workflow_id = $1,
                workflow_version = $2,
                workflow_snapshot = $3::jsonb
            WHERE id = $4
            """,
            workflow_id,
            workflow_version,
            snapshot,
            saga_id,
        )

    async def get_phase(self, phase_id: UUID) -> Phase | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM phases WHERE id = $1",
            phase_id,
        )
        if row is None:
            return None
        return self._row_to_phase(row)

    async def get_raid(self, raid_id: UUID) -> Raid | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM raids WHERE id = $1",
            raid_id,
        )
        if row is None:
            return None
        return self._row_to_raid(row)

    async def get_raids_by_phase(self, phase_id: UUID) -> list[Raid]:
        rows = await self._pool.fetch(
            "SELECT * FROM raids WHERE phase_id = $1 ORDER BY created_at ASC",
            phase_id,
        )
        return [self._row_to_raid(r) for r in rows]

    async def get_phases_by_saga(self, saga_id: UUID) -> list[Phase]:
        rows = await self._pool.fetch(
            "SELECT * FROM phases WHERE saga_id = $1 ORDER BY number ASC",
            saga_id,
        )
        return [self._row_to_phase(r) for r in rows]

    async def update_raid_outcome(
        self,
        raid_id: UUID,
        outcome: dict[str, Any],
        event_type: str,
        status: RaidStatus,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE raids
            SET structured_outcome = $1,
                outcome_event_type = $2,
                status = $3,
                updated_at = NOW()
            WHERE id = $4
            """,
            json.dumps(outcome),
            event_type,
            status.value,
            raid_id,
        )

    @staticmethod
    def _row_to_phase(row: asyncpg.Record) -> Phase:
        return Phase(
            id=row["id"],
            saga_id=row["saga_id"],
            tracker_id=row["tracker_id"],
            number=row["number"],
            name=row["name"],
            status=PhaseStatus(row.get("status", "PENDING") or "PENDING"),
            confidence=row["confidence"] or 0.0,
        )

    @staticmethod
    def _row_to_raid(row: asyncpg.Record) -> Raid:
        raw_outcome = row.get("structured_outcome")
        structured_outcome: dict | None = None
        if raw_outcome is not None:
            if isinstance(raw_outcome, str):
                structured_outcome = json.loads(raw_outcome)
            else:
                structured_outcome = dict(raw_outcome)
        return Raid(
            id=row["id"],
            phase_id=row["phase_id"],
            tracker_id=row["tracker_id"],
            name=row["name"],
            description=row.get("description") or "",
            acceptance_criteria=list(row.get("acceptance_criteria") or []),
            declared_files=list(row.get("declared_files") or []),
            estimate_hours=row.get("estimate_hours"),
            status=RaidStatus(row.get("status", "PENDING") or "PENDING"),
            confidence=row.get("confidence") or 0.0,
            session_id=row.get("session_id"),
            branch=row.get("branch"),
            chronicle_summary=row.get("chronicle_summary"),
            pr_url=row.get("pr_url"),
            pr_id=row.get("pr_id"),
            retry_count=row.get("retry_count") or 0,
            created_at=row.get("created_at") or datetime.now(UTC),
            updated_at=row.get("updated_at") or datetime.now(UTC),
            identifier=row.get("identifier") or "",
            url=row.get("url") or "",
            reviewer_session_id=row.get("reviewer_session_id"),
            review_round=row.get("review_round") or 0,
            structured_outcome=structured_outcome,
            outcome_event_type=row.get("outcome_event_type"),
        )

    @staticmethod
    def _row_to_saga(row: asyncpg.Record) -> Saga:
        slug = row["slug"]
        workflow_snapshot = PostgresSagaRepository._decode_jsonb(row.get("workflow_snapshot"))
        return Saga(
            id=row["id"],
            tracker_id=row["tracker_id"],
            tracker_type=row["tracker_type"],
            slug=slug,
            name=row["name"],
            repos=list(row["repos"]),
            feature_branch=row.get("feature_branch") or f"feat/{slug}",
            base_branch=row["base_branch"],
            status=SagaStatus(row.get("status", "ACTIVE") or "ACTIVE"),
            confidence=row["confidence"] or 0.0,
            created_at=row["created_at"] or datetime.now(UTC),
            owner_id=row.get("owner_id") or "",
            workflow_id=row.get("workflow_id"),
            workflow_version=row.get("workflow_version"),
            workflow_snapshot=workflow_snapshot,
        )

    @staticmethod
    def _decode_jsonb(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return dict(value)
