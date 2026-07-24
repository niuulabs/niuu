"""PostgreSQL implementation of WorkflowCampaignRepository."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from ting.domain.models import CampaignStageState, WorkflowCampaign, WorkflowCampaignStatus
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository


class PostgresWorkflowCampaignRepository(WorkflowCampaignRepository):
    """Workflow campaign persistence backed by asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_campaigns(self, *, owner_id: str) -> list[WorkflowCampaign]:
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM workflow_campaigns
            WHERE owner_id = $1
            ORDER BY updated_at DESC, created_at DESC
            """,
            owner_id,
        )
        return [self._row_to_campaign(row) for row in rows]

    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM workflow_campaigns
            WHERE status IN ('pending', 'running', 'blocked')
            ORDER BY updated_at DESC, created_at DESC
            """
        )
        return [self._row_to_campaign(row) for row in rows]

    async def get_campaign(self, campaign_id: UUID) -> WorkflowCampaign | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM workflow_campaigns WHERE id = $1",
            campaign_id,
        )
        if row is None:
            return None
        return self._row_to_campaign(row)

    async def get_campaign_by_slug(
        self,
        slug: str,
        *,
        owner_id: str | None = None,
    ) -> WorkflowCampaign | None:
        if owner_id is None:
            row = await self._pool.fetchrow(
                "SELECT * FROM workflow_campaigns WHERE slug = $1",
                slug,
            )
        else:
            row = await self._pool.fetchrow(
                """
                SELECT *
                FROM workflow_campaigns
                WHERE slug = $1
                  AND owner_id = $2
                """,
                slug,
                owner_id,
            )
        if row is None:
            return None
        return self._row_to_campaign(row)

    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        await self._pool.execute(
            """
            INSERT INTO workflow_campaigns (
                id,
                slug,
                name,
                owner_id,
                workflow_id,
                workflow_version,
                workflow_name,
                workflow_snapshot,
                session_id,
                session_name,
                status,
                active_stage_id,
                stage_state,
                metadata,
                created_at,
                updated_at,
                last_activity_at,
                completed_at,
                connection_id
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13::jsonb, $14::jsonb,
                $15, $16, $17, $18, $19
            )
            ON CONFLICT (id) DO UPDATE SET
                slug = EXCLUDED.slug,
                name = EXCLUDED.name,
                owner_id = EXCLUDED.owner_id,
                workflow_id = EXCLUDED.workflow_id,
                workflow_version = EXCLUDED.workflow_version,
                workflow_name = EXCLUDED.workflow_name,
                workflow_snapshot = EXCLUDED.workflow_snapshot,
                session_id = EXCLUDED.session_id,
                session_name = EXCLUDED.session_name,
                status = EXCLUDED.status,
                active_stage_id = EXCLUDED.active_stage_id,
                stage_state = EXCLUDED.stage_state,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at,
                last_activity_at = EXCLUDED.last_activity_at,
                completed_at = EXCLUDED.completed_at,
                connection_id = EXCLUDED.connection_id
            """,
            campaign.id,
            campaign.slug,
            campaign.name,
            campaign.owner_id,
            campaign.workflow_id,
            campaign.workflow_version,
            campaign.workflow_name,
            json.dumps(campaign.workflow_snapshot),
            campaign.session_id,
            campaign.session_name,
            campaign.status.value,
            campaign.active_stage_id,
            json.dumps([_stage_to_json(stage) for stage in campaign.stage_state]),
            json.dumps(campaign.metadata),
            campaign.created_at,
            campaign.updated_at,
            campaign.last_activity_at,
            campaign.completed_at,
            campaign.connection_id,
        )
        return campaign

    async def delete_campaign(self, campaign_id: UUID) -> bool:
        result = await self._pool.execute(
            "DELETE FROM workflow_campaigns WHERE id = $1",
            campaign_id,
        )
        return result == "DELETE 1"

    @staticmethod
    def _row_to_campaign(row: asyncpg.Record) -> WorkflowCampaign:
        raw_snapshot = row.get("workflow_snapshot") or {}
        raw_stage_state = row.get("stage_state") or []
        raw_metadata = row.get("metadata") or {}
        snapshot = json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else dict(raw_snapshot)
        stage_state_data = (
            json.loads(raw_stage_state)
            if isinstance(raw_stage_state, str)
            else list(raw_stage_state)
        )
        metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else dict(raw_metadata)
        status = WorkflowCampaignStatus(row.get("status") or WorkflowCampaignStatus.PENDING.value)
        stage_state = [
            _stage_from_json(item) for item in stage_state_data if isinstance(item, dict)
        ]
        return WorkflowCampaign(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            owner_id=row["owner_id"],
            workflow_id=row["workflow_id"],
            workflow_version=row.get("workflow_version") or "draft",
            workflow_name=row.get("workflow_name") or "",
            workflow_snapshot=snapshot,
            session_id=row["session_id"],
            session_name=row["session_name"],
            status=status,
            active_stage_id=row.get("active_stage_id"),
            stage_state=stage_state,
            metadata=metadata,
            created_at=row.get("created_at") or datetime.now(UTC),
            updated_at=row.get("updated_at") or datetime.now(UTC),
            last_activity_at=row.get("last_activity_at"),
            completed_at=row.get("completed_at"),
            connection_id=row.get("connection_id"),
        )


def _stage_to_json(stage: CampaignStageState) -> dict[str, str | None]:
    return {
        "stage_id": stage.stage_id,
        "label": stage.label,
        "status": stage.status,
        "started_at": stage.started_at.isoformat() if stage.started_at else None,
        "completed_at": stage.completed_at.isoformat() if stage.completed_at else None,
        "reason": stage.reason,
    }


def _stage_from_json(raw: dict[str, object]) -> CampaignStageState:
    started_at = raw.get("started_at")
    completed_at = raw.get("completed_at")
    return CampaignStageState(
        stage_id=str(raw.get("stage_id") or ""),
        label=str(raw.get("label") or ""),
        status=str(raw.get("status") or "pending"),
        started_at=datetime.fromisoformat(str(started_at)) if isinstance(started_at, str) else None,
        completed_at=(
            datetime.fromisoformat(str(completed_at)) if isinstance(completed_at, str) else None
        ),
        reason=str(raw.get("reason")) if raw.get("reason") is not None else None,
    )
