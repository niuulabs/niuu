"""Dedicated saga phase endpoints for Tyr."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from niuu.domain.models import Principal
from tyr.adapters.inbound.auth import extract_principal
from tyr.api.sagas import resolve_saga_repo
from tyr.api.tracker import resolve_trackers
from tyr.domain.models import PhaseStatus, RaidStatus, TrackerIssue, TrackerMilestone
from tyr.ports.saga_repository import SagaRepository
from tyr.ports.tracker import TrackerPort


class RaidPhaseItemResponse(BaseModel):
    id: str
    phase_id: str
    tracker_id: str
    name: str
    description: str
    acceptance_criteria: list[str]
    declared_files: list[str]
    estimate_hours: float | None
    status: str
    confidence: float
    session_id: str | None = None
    reviewer_session_id: str | None = None
    review_round: int = 0
    branch: str | None = None
    chronicle_summary: str | None = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime


class SagaPhaseItemResponse(BaseModel):
    id: str
    saga_id: str
    tracker_id: str
    number: int
    name: str
    status: str
    confidence: float
    raids: list[RaidPhaseItemResponse]


def _coerce_issue_status(issue: TrackerIssue) -> str:
    status_type = issue.status_type.lower()
    if status_type in {"completed", "done"}:
        return RaidStatus.MERGED.value.lower()
    if status_type in {"started", "in_progress"}:
        return RaidStatus.RUNNING.value.lower()
    if status_type in {"review", "in_review"}:
        return RaidStatus.REVIEW.value.lower()
    if status_type in {"canceled", "cancelled"}:
        return RaidStatus.FAILED.value.lower()
    return RaidStatus.PENDING.value.lower()


def _coerce_phase_status(
    raids: list[RaidPhaseItemResponse], milestone: TrackerMilestone | None
) -> str:
    if raids:
        statuses = {raid.status for raid in raids}
        if statuses and statuses.issubset({RaidStatus.MERGED.value.lower()}):
            return PhaseStatus.COMPLETE.value.lower()
        if statuses & {
            RaidStatus.RUNNING.value.lower(),
            RaidStatus.REVIEW.value.lower(),
            RaidStatus.QUEUED.value.lower(),
        }:
            return PhaseStatus.ACTIVE.value.lower()
        if statuses & {RaidStatus.MERGED.value.lower(), RaidStatus.FAILED.value.lower()}:
            return PhaseStatus.ACTIVE.value.lower()
    if milestone is not None and milestone.progress >= 1.0:
        return PhaseStatus.COMPLETE.value.lower()
    if milestone is not None and milestone.progress > 0:
        return PhaseStatus.ACTIVE.value.lower()
    return PhaseStatus.PENDING.value.lower()


def _fallback_raid(issue: TrackerIssue, *, phase_id: str) -> RaidPhaseItemResponse:
    now = datetime.now(UTC)
    return RaidPhaseItemResponse(
        id=issue.id,
        phase_id=phase_id,
        tracker_id=issue.identifier,
        name=issue.title,
        description=issue.description,
        acceptance_criteria=[],
        declared_files=[],
        estimate_hours=issue.estimate,
        status=_coerce_issue_status(issue),
        confidence=100.0 if issue.status_type.lower() == "completed" else 0.0,
        session_id=None,
        reviewer_session_id=None,
        review_round=0,
        branch=None,
        chronicle_summary=None,
        retry_count=0,
        created_at=now,
        updated_at=now,
    )


async def _hydrate_tracker_backed_phases(
    tracker: TrackerPort,
    *,
    saga_id: str,
    tracker_project_id: str,
) -> list[SagaPhaseItemResponse]:
    if hasattr(tracker, "get_project_full"):
        _, milestones, issues = await tracker.get_project_full(tracker_project_id)
    else:
        milestones = await tracker.list_milestones(tracker_project_id)
        issues = await tracker.list_issues(tracker_project_id)

    issues_by_milestone: dict[str | None, list[TrackerIssue]] = {}
    for issue in issues:
        issues_by_milestone.setdefault(issue.milestone_id, []).append(issue)

    responses: list[SagaPhaseItemResponse] = []
    ordered_milestones = sorted(milestones, key=lambda milestone: milestone.sort_order)
    for index, milestone in enumerate(ordered_milestones, start=1):
        raid_items: list[RaidPhaseItemResponse] = []
        for issue in issues_by_milestone.get(milestone.id, []):
            try:
                raid = await tracker.get_raid(issue.id)
                raid_items.append(
                    RaidPhaseItemResponse(
                        id=str(raid.id),
                        phase_id=str(raid.phase_id),
                        tracker_id=raid.tracker_id,
                        name=raid.name,
                        description=raid.description,
                        acceptance_criteria=raid.acceptance_criteria,
                        declared_files=raid.declared_files,
                        estimate_hours=raid.estimate_hours,
                        status=raid.status.value.lower(),
                        confidence=raid.confidence,
                        session_id=raid.session_id,
                        reviewer_session_id=raid.reviewer_session_id,
                        review_round=raid.review_round,
                        branch=raid.branch,
                        chronicle_summary=raid.chronicle_summary,
                        retry_count=raid.retry_count,
                        created_at=raid.created_at,
                        updated_at=raid.updated_at,
                    )
                )
            except Exception:
                raid_items.append(_fallback_raid(issue, phase_id=milestone.id))

        phase_confidence = (
            sum(raid.confidence for raid in raid_items) / len(raid_items)
            if raid_items
            else milestone.progress * 100.0
        )
        responses.append(
            SagaPhaseItemResponse(
                id=milestone.id,
                saga_id=saga_id,
                tracker_id=milestone.id,
                number=index,
                name=milestone.name,
                status=_coerce_phase_status(raid_items, milestone),
                confidence=phase_confidence,
                raids=raid_items,
            )
        )

    unassigned = issues_by_milestone.get(None, [])
    if unassigned:
        phase_id = "__unassigned__"
        raid_items = []
        for issue in unassigned:
            try:
                raid = await tracker.get_raid(issue.id)
                raid_items.append(
                    RaidPhaseItemResponse(
                        id=str(raid.id),
                        phase_id=str(raid.phase_id),
                        tracker_id=raid.tracker_id,
                        name=raid.name,
                        description=raid.description,
                        acceptance_criteria=raid.acceptance_criteria,
                        declared_files=raid.declared_files,
                        estimate_hours=raid.estimate_hours,
                        status=raid.status.value.lower(),
                        confidence=raid.confidence,
                        session_id=raid.session_id,
                        reviewer_session_id=raid.reviewer_session_id,
                        review_round=raid.review_round,
                        branch=raid.branch,
                        chronicle_summary=raid.chronicle_summary,
                        retry_count=raid.retry_count,
                        created_at=raid.created_at,
                        updated_at=raid.updated_at,
                    )
                )
            except Exception:
                raid_items.append(_fallback_raid(issue, phase_id=phase_id))

        phase_confidence = (
            sum(raid.confidence for raid in raid_items) / len(raid_items) if raid_items else 0.0
        )
        responses.append(
            SagaPhaseItemResponse(
                id=phase_id,
                saga_id=saga_id,
                tracker_id=phase_id,
                number=len(ordered_milestones) + 1,
                name="Unassigned",
                status=_coerce_phase_status(raid_items, None),
                confidence=phase_confidence,
                raids=raid_items,
            )
        )

    return responses


def create_saga_phases_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/sagas", tags=["Sagas"])

    @router.get("/{saga_id}/phases", response_model=list[SagaPhaseItemResponse])
    async def get_saga_phases(
        saga_id: str,
        principal: Principal = Depends(extract_principal),
        repo: SagaRepository = Depends(resolve_saga_repo),
        trackers: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[SagaPhaseItemResponse]:
        """Return saga phases in the shape expected by web-next.

        Imported tracker-backed sagas may not have persisted phases yet. In that
        case, synthesize them live from tracker milestones and issues so the
        dashboard and dispatch views can still operate on imported work.
        """
        try:
            parsed_saga_id = UUID(saga_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Saga not found: {saga_id}",
            ) from exc

        saga = await repo.get_saga(parsed_saga_id, owner_id=principal.user_id)
        if saga is None:
            raise HTTPException(
                status_code=404,
                detail=f"Saga not found: {saga_id}",
            )

        phases = await repo.get_phases_by_saga(parsed_saga_id)
        if not phases and saga.tracker_id:
            for tracker in trackers:
                try:
                    return await _hydrate_tracker_backed_phases(
                        tracker,
                        saga_id=str(saga.id),
                        tracker_project_id=saga.tracker_id,
                    )
                except Exception:
                    continue

        responses: list[SagaPhaseItemResponse] = []
        for phase in phases:
            raids = await repo.get_raids_by_phase(phase.id)
            responses.append(
                SagaPhaseItemResponse(
                    id=str(phase.id),
                    saga_id=str(phase.saga_id),
                    tracker_id=phase.tracker_id,
                    number=phase.number,
                    name=phase.name,
                    status=phase.status.value.lower(),
                    confidence=phase.confidence,
                    raids=[
                        RaidPhaseItemResponse(
                            id=str(raid.id),
                            phase_id=str(raid.phase_id),
                            tracker_id=raid.tracker_id,
                            name=raid.name,
                            description=raid.description,
                            acceptance_criteria=raid.acceptance_criteria,
                            declared_files=raid.declared_files,
                            estimate_hours=raid.estimate_hours,
                            status=raid.status.value.lower(),
                            confidence=raid.confidence,
                            session_id=raid.session_id,
                            reviewer_session_id=raid.reviewer_session_id,
                            review_round=raid.review_round,
                            branch=raid.branch,
                            chronicle_summary=raid.chronicle_summary,
                            retry_count=raid.retry_count,
                            created_at=raid.created_at,
                            updated_at=raid.updated_at,
                        )
                        for raid in raids
                    ],
                )
            )
        return responses

    return router
