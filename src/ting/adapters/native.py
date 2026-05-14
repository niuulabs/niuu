"""Native PostgreSQL tracker adapter.

Pure-database fallback for local dev, air-gapped environments,
or before an external tracker (Linear, Jira, etc.) is configured.
Stores sagas, phases, and runs entirely in Ting's PostgreSQL schema.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from ting.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from ting.ports.tracker import TrackerPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State mapping: RunStatus <-> DB status text
# ---------------------------------------------------------------------------

_RUN_STATUS_DISPLAY: dict[RunStatus, str] = {
    RunStatus.PENDING: "Pending",
    RunStatus.QUEUED: "Queued",
    RunStatus.RUNNING: "In Progress",
    RunStatus.REVIEW: "In Review",
    RunStatus.ESCALATED: "Escalated",
    RunStatus.MERGED: "Done",
    RunStatus.FAILED: "Failed",
}

_DISPLAY_TO_RUN: dict[str, RunStatus] = {v: k for k, v in _RUN_STATUS_DISPLAY.items()}


class NativeTrackerAdapter(TrackerPort):
    """PostgreSQL-backed tracker: sagas=projects, phases=milestones, runs=issues."""

    def __init__(self, pool: asyncpg.Pool, **_extra: object) -> None:
        self._pool = pool

    # -- CRUD: create --

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        tracker_id = str(saga.id)
        await self._pool.execute(
            """
            INSERT INTO sagas
                (id, tracker_id, tracker_type, slug, name,
                 repos, feature_branch, base_branch, status, confidence, created_at,
                 owner_id, workflow_id, workflow_version, workflow_snapshot)
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
            tracker_id,
            "native",
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
        return tracker_id

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        tracker_id = str(phase.id)
        await self._pool.execute(
            """
            INSERT INTO phases (id, saga_id, tracker_id, number, name, status, confidence)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO NOTHING
            """,
            phase.id,
            phase.saga_id,
            tracker_id,
            phase.number,
            phase.name,
            phase.status.value,
            phase.confidence,
        )
        return tracker_id

    async def create_run(self, run: Run, *, project_id: str = "", milestone_id: str = "") -> str:
        tracker_id = str(run.id)
        await self._pool.execute(
            """
            INSERT INTO runs
                (id, phase_id, tracker_id, name, description,
                 acceptance_criteria, declared_files, estimate_hours,
                 status, confidence, session_id, branch,
                 chronicle_summary, retry_count, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
            ON CONFLICT (id) DO NOTHING
            """,
            run.id,
            run.phase_id,
            tracker_id,
            run.name,
            run.description,
            run.acceptance_criteria,
            run.declared_files,
            run.estimate_hours,
            run.status.value,
            run.confidence,
            run.session_id,
            run.branch,
            run.chronicle_summary,
            run.retry_count,
            run.created_at,
            run.updated_at,
        )
        return tracker_id

    # -- CRUD: update / close --

    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        result = await self._pool.execute(
            "UPDATE runs SET status = $1, updated_at = $2 WHERE tracker_id = $3",
            state.value,
            datetime.now(UTC),
            run_id,
        )
        if result == "UPDATE 0":
            raise LookupError(f"Run not found: {run_id}")

    async def close_run(self, run_id: str) -> None:
        await self.update_run_state(run_id, RunStatus.MERGED)

    # -- Read: domain entities --

    async def get_saga(self, saga_id: str) -> Saga:
        row = await self._pool.fetchrow("SELECT * FROM sagas WHERE tracker_id = $1", saga_id)
        if row is None:
            raise LookupError(f"Saga not found: {saga_id}")
        return self._row_to_saga(row)

    async def get_phase(self, tracker_id: str) -> Phase:
        row = await self._pool.fetchrow("SELECT * FROM phases WHERE tracker_id = $1", tracker_id)
        if row is None:
            raise LookupError(f"Phase not found: {tracker_id}")
        return self._row_to_phase(row)

    async def get_run(self, tracker_id: str) -> Run:
        row = await self._pool.fetchrow("SELECT * FROM runs WHERE tracker_id = $1", tracker_id)
        if row is None:
            raise LookupError(f"Run not found: {tracker_id}")
        return self._row_to_run(row)

    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM runs
            WHERE phase_id = (SELECT id FROM phases WHERE tracker_id = $1)
              AND status IN ($2, $3)
            ORDER BY created_at
            """,
            phase_id,
            RunStatus.PENDING.value,
            RunStatus.QUEUED.value,
        )
        return [self._row_to_run(r) for r in rows]

    # -- Browsing --

    async def list_projects(self) -> list[TrackerProject]:
        rows = await self._pool.fetch("SELECT * FROM sagas ORDER BY created_at DESC")
        return [await self._saga_row_to_project(r) for r in rows]

    async def get_project(self, project_id: str) -> TrackerProject:
        row = await self._pool.fetchrow("SELECT * FROM sagas WHERE tracker_id = $1", project_id)
        if row is None:
            raise LookupError(f"Project not found: {project_id}")
        return await self._saga_row_to_project(row)

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        rows = await self._pool.fetch(
            """
            SELECT p.* FROM phases p
            JOIN sagas s ON s.id = p.saga_id
            WHERE s.tracker_id = $1
            ORDER BY p.number
            """,
            project_id,
        )
        return [self._phase_row_to_milestone(r, project_id) for r in rows]

    async def list_issues(
        self,
        project_id: str,
        milestone_id: str | None = None,
    ) -> list[TrackerIssue]:
        if milestone_id:
            rows = await self._pool.fetch(
                """
                SELECT r.*, p.tracker_id AS milestone_tracker_id
                FROM runs r
                JOIN phases p ON p.id = r.phase_id
                JOIN sagas s ON s.id = p.saga_id
                WHERE s.tracker_id = $1 AND p.tracker_id = $2
                ORDER BY r.created_at
                """,
                project_id,
                milestone_id,
            )
        else:
            rows = await self._pool.fetch(
                """
                SELECT r.*, p.tracker_id AS milestone_tracker_id
                FROM runs r
                JOIN phases p ON p.id = r.phase_id
                JOIN sagas s ON s.id = p.saga_id
                WHERE s.tracker_id = $1
                ORDER BY r.created_at
                """,
                project_id,
            )
        return [self._run_row_to_issue(r) for r in rows]

    # -- Run progress --

    async def update_run_progress(
        self,
        tracker_id: str,
        *,
        status: RunStatus | None = None,
        session_id: str | None = None,
        confidence: float | None = None,
        pr_url: str | None = None,
        pr_id: str | None = None,
        retry_count: int | None = None,
        reason: str | None = None,
        owner_id: str | None = None,
        phase_tracker_id: str | None = None,
        saga_tracker_id: str | None = None,
        chronicle_summary: str | None = None,
        reviewer_session_id: str | None = None,
        review_round: int | None = None,
    ) -> Run:
        row = await self._pool.fetchrow(
            """
            UPDATE runs SET
                status              = COALESCE($2, status),
                session_id          = COALESCE($3, session_id),
                confidence          = COALESCE($4, confidence),
                pr_url              = COALESCE($5, pr_url),
                pr_id               = COALESCE($6, pr_id),
                retry_count         = COALESCE($7, retry_count),
                reason              = COALESCE($8, reason),
                chronicle_summary   = COALESCE($9, chronicle_summary),
                reviewer_session_id = COALESCE($10, reviewer_session_id),
                review_round        = COALESCE($11, review_round),
                updated_at          = NOW()
            WHERE tracker_id = $1
            RETURNING *
            """,
            tracker_id,
            status.value if status is not None else None,
            session_id,
            confidence,
            pr_url,
            pr_id,
            retry_count,
            reason,
            chronicle_summary,
            reviewer_session_id,
            review_round,
        )
        if row is None:
            raise LookupError(f"Run not found: {tracker_id}")
        return self._row_to_run(row)

    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        rows = await self._pool.fetch(
            """
            SELECT r.* FROM runs r
            JOIN phases p ON p.id = r.phase_id
            JOIN sagas s ON s.id = p.saga_id
            WHERE s.tracker_id = $1
            ORDER BY r.created_at
            """,
            saga_tracker_id,
        )
        return [self._row_to_run(r) for r in rows]

    async def get_run_by_session(self, session_id: str) -> Run | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM runs WHERE session_id = $1",
            session_id,
        )
        if row is None:
            return None
        return self._row_to_run(row)

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        rows = await self._pool.fetch(
            "SELECT * FROM runs WHERE status = $1 ORDER BY updated_at",
            status.value,
        )
        return [self._row_to_run(r) for r in rows]

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        row = await self._pool.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
        if row is None:
            return None
        return self._row_to_run(row)

    # -- Confidence events --

    async def add_confidence_event(self, tracker_id: str, event: ConfidenceEvent) -> None:
        await self._pool.execute(
            """
            INSERT INTO confidence_events (id, run_id, event_type, delta, score_after, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            event.id,
            event.run_id,
            event.event_type.value,
            event.delta,
            event.score_after,
            event.created_at,
        )
        await self._pool.execute(
            "UPDATE runs SET confidence = $2, updated_at = $3 WHERE tracker_id = $1",
            tracker_id,
            event.score_after,
            event.created_at,
        )

    async def get_confidence_events(self, tracker_id: str) -> list[ConfidenceEvent]:
        rows = await self._pool.fetch(
            """
            SELECT ce.* FROM confidence_events ce
            JOIN runs r ON r.id = ce.run_id
            WHERE r.tracker_id = $1
            ORDER BY ce.created_at
            """,
            tracker_id,
        )
        return [
            ConfidenceEvent(
                id=r["id"],
                run_id=r["run_id"],
                event_type=ConfidenceEventType(r["event_type"]),
                delta=r["delta"],
                score_after=r["score_after"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- Phase gate management --

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        row = await self._pool.fetchrow(
            """
            SELECT count(*) FILTER (WHERE r.status != 'MERGED') AS remaining
            FROM runs r
            JOIN phases p ON p.id = r.phase_id
            WHERE p.tracker_id = $1
            """,
            phase_tracker_id,
        )
        return row is not None and row["remaining"] == 0

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        rows = await self._pool.fetch(
            """
            SELECT p.* FROM phases p
            JOIN sagas s ON s.id = p.saga_id
            WHERE s.tracker_id = $1
            ORDER BY p.number
            """,
            saga_tracker_id,
        )
        return [self._row_to_phase(r) for r in rows]

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        row = await self._pool.fetchrow(
            """
            UPDATE phases SET status = $2 WHERE tracker_id = $1
            RETURNING *
            """,
            phase_tracker_id,
            status.value,
        )
        if row is None:
            return None
        return self._row_to_phase(row)

    # -- Cross-entity navigation --

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        row = await self._pool.fetchrow(
            """
            SELECT s.* FROM sagas s
            JOIN phases p ON p.saga_id = s.id
            JOIN runs r ON r.phase_id = p.id
            WHERE r.tracker_id = $1
            """,
            tracker_id,
        )
        if row is None:
            return None
        return self._row_to_saga(row)

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        row = await self._pool.fetchrow(
            """
            SELECT p.* FROM phases p
            JOIN runs r ON r.phase_id = p.id
            WHERE r.tracker_id = $1
            """,
            tracker_id,
        )
        if row is None:
            return None
        return self._row_to_phase(row)

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        row = await self._pool.fetchrow(
            """
            SELECT s.owner_id FROM sagas s
            JOIN phases p ON p.saga_id = s.id
            JOIN runs r ON r.phase_id = p.id
            WHERE r.tracker_id = $1
            """,
            tracker_id,
        )
        if row is None:
            return None
        return row["owner_id"] or None

    # -- Session messages --

    async def save_session_message(self, message: SessionMessage) -> None:
        await self._pool.execute(
            """
            INSERT INTO session_messages (id, run_id, session_id, content, sender, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            message.id,
            message.run_id,
            message.session_id,
            message.content,
            message.sender,
            message.created_at,
        )

    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        rows = await self._pool.fetch(
            """
            SELECT sm.* FROM session_messages sm
            JOIN runs r ON r.id = sm.run_id
            WHERE r.tracker_id = $1
            ORDER BY sm.created_at
            """,
            tracker_id,
        )
        return [
            SessionMessage(
                id=r["id"],
                run_id=r["run_id"],
                session_id=r["session_id"],
                content=r["content"],
                sender=r["sender"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def close(self) -> None:
        """No-op — pool lifecycle is managed externally."""

    # -- Row conversion helpers --

    @staticmethod
    def _row_to_saga(row: asyncpg.Record) -> Saga:
        slug = row["slug"]
        return Saga(
            id=row["id"],
            tracker_id=row["tracker_id"],
            tracker_type=row.get("tracker_type", "native") or "native",
            slug=slug,
            name=row["name"],
            repos=list(row["repos"]),
            feature_branch=row.get("feature_branch") or f"feat/{slug}",
            status=SagaStatus(row.get("status", "ACTIVE") or "ACTIVE"),
            confidence=row["confidence"] or 0.0,
            created_at=row["created_at"] or datetime.now(UTC),
            base_branch=row["base_branch"],
        )

    @staticmethod
    def _row_to_phase(row: asyncpg.Record) -> Phase:
        return Phase(
            id=row["id"],
            saga_id=row["saga_id"],
            tracker_id=row["tracker_id"],
            number=row["number"],
            name=row["name"],
            status=PhaseStatus(row.get("status", "GATED") or "GATED"),
            confidence=row["confidence"] or 0.0,
        )

    @staticmethod
    def _row_to_run(row: asyncpg.Record) -> Run:
        return Run(
            id=row["id"],
            phase_id=row["phase_id"],
            tracker_id=row["tracker_id"],
            name=row["name"],
            description=row.get("description", "") or "",
            acceptance_criteria=list(row.get("acceptance_criteria") or []),
            declared_files=list(row.get("declared_files") or []),
            estimate_hours=row.get("estimate_hours"),
            status=RunStatus(row["status"]),
            confidence=row["confidence"] or 0.0,
            session_id=row.get("session_id"),
            branch=row.get("branch"),
            chronicle_summary=row.get("chronicle_summary"),
            pr_url=row.get("pr_url"),
            pr_id=row.get("pr_id"),
            retry_count=row.get("retry_count", 0) or 0,
            created_at=row["created_at"] or datetime.now(UTC),
            updated_at=row["updated_at"] or datetime.now(UTC),
            reviewer_session_id=row.get("reviewer_session_id"),
            review_round=row.get("review_round", 0) or 0,
        )

    async def _saga_row_to_project(self, row: asyncpg.Record) -> TrackerProject:
        saga_id = row["id"]
        milestone_count = await self._pool.fetchval(
            "SELECT COUNT(*) FROM phases WHERE saga_id = $1", saga_id
        )
        issue_count = await self._pool.fetchval(
            """
            SELECT COUNT(*) FROM runs r
            JOIN phases p ON p.id = r.phase_id
            WHERE p.saga_id = $1
            """,
            saga_id,
        )
        return TrackerProject(
            id=row["tracker_id"],
            name=row["name"],
            description=f"Saga: {row['slug']}",
            status=row.get("status", "ACTIVE") or "ACTIVE",
            url="",
            milestone_count=milestone_count or 0,
            issue_count=issue_count or 0,
            slug=row["slug"],
        )

    @staticmethod
    def _phase_row_to_milestone(row: asyncpg.Record, project_id: str) -> TrackerMilestone:
        return TrackerMilestone(
            id=row["tracker_id"],
            project_id=project_id,
            name=row["name"],
            description="",
            sort_order=row["number"],
            progress=0.0,
        )

    @staticmethod
    def _run_row_to_issue(row: asyncpg.Record) -> TrackerIssue:
        status = RunStatus(row["status"])
        return TrackerIssue(
            id=row["tracker_id"],
            identifier=f"NAT-{row['tracker_id'][:8]}",
            title=row["name"],
            description=row.get("description", "") or "",
            status=_RUN_STATUS_DISPLAY.get(status, status.value),
            status_type="unstarted" if status == RunStatus.PENDING else "",
            assignee=None,
            labels=[],
            priority=0,
            url="",
            milestone_id=row.get("milestone_tracker_id") or row.get("milestone_id"),
        )
