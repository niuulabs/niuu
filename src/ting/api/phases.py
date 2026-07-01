"""Dedicated saga phase endpoints for Ting."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_principal
from ting.api.sagas import resolve_saga_repo
from ting.api.tracker import resolve_trackers
from ting.domain.models import PhaseStatus, RunStatus, TrackerIssue, TrackerMilestone
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort


class RunPhaseItemResponse(BaseModel):
    id: str
    phase_id: str
    tracker_id: str
    identifier: str = ""
    url: str = ""
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
    runs: list[RunPhaseItemResponse]


def _coerce_issue_status(issue: TrackerIssue) -> str:
    status_type = issue.status_type.lower()
    if status_type in {"completed", "done"}:
        return RunStatus.MERGED.value.lower()
    if status_type in {"started", "in_progress"}:
        return RunStatus.RUNNING.value.lower()
    if status_type in {"review", "in_review"}:
        return RunStatus.REVIEW.value.lower()
    if status_type in {"canceled", "cancelled"}:
        return RunStatus.FAILED.value.lower()
    return RunStatus.PENDING.value.lower()


def _coerce_phase_status(
    runs: list[RunPhaseItemResponse], milestone: TrackerMilestone | None
) -> str:
    if runs:
        statuses = {run.status for run in runs}
        if statuses and statuses.issubset({RunStatus.MERGED.value.lower()}):
            return PhaseStatus.COMPLETE.value.lower()
        if statuses & {
            RunStatus.RUNNING.value.lower(),
            RunStatus.REVIEW.value.lower(),
            RunStatus.QUEUED.value.lower(),
        }:
            return PhaseStatus.ACTIVE.value.lower()
        if statuses & {RunStatus.MERGED.value.lower(), RunStatus.FAILED.value.lower()}:
            return PhaseStatus.ACTIVE.value.lower()
    if milestone is not None and milestone.progress >= 1.0:
        return PhaseStatus.COMPLETE.value.lower()
    if milestone is not None and milestone.progress > 0:
        return PhaseStatus.ACTIVE.value.lower()
    return PhaseStatus.PENDING.value.lower()


def _fallback_run(issue: TrackerIssue, *, phase_id: str) -> RunPhaseItemResponse:
    now = datetime.now(UTC)
    return RunPhaseItemResponse(
        id=issue.id,
        phase_id=phase_id,
        tracker_id=issue.identifier,
        identifier=issue.identifier,
        url=issue.url,
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


async def _tracker_run_metadata(
    trackers: list[TrackerPort],
    tracker_id: str,
) -> tuple[str, str]:
    for tracker in trackers:
        try:
            run = await tracker.get_run(tracker_id)
        except Exception:
            continue
        return run.identifier, run.url
    return "", ""


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
        run_items: list[RunPhaseItemResponse] = []
        for issue in issues_by_milestone.get(milestone.id, []):
            try:
                run = await tracker.get_run(issue.id)
                run_items.append(
                    RunPhaseItemResponse(
                        id=str(run.id),
                        phase_id=str(run.phase_id),
                        tracker_id=run.tracker_id,
                        identifier=run.identifier,
                        url=run.url,
                        name=run.name,
                        description=run.description,
                        acceptance_criteria=run.acceptance_criteria,
                        declared_files=run.declared_files,
                        estimate_hours=run.estimate_hours,
                        status=run.status.value.lower(),
                        confidence=run.confidence,
                        session_id=run.session_id,
                        reviewer_session_id=run.reviewer_session_id,
                        review_round=run.review_round,
                        branch=run.branch,
                        chronicle_summary=run.chronicle_summary,
                        retry_count=run.retry_count,
                        created_at=run.created_at,
                        updated_at=run.updated_at,
                    )
                )
            except Exception:
                run_items.append(_fallback_run(issue, phase_id=milestone.id))

        phase_confidence = (
            sum(run.confidence for run in run_items) / len(run_items)
            if run_items
            else milestone.progress * 100.0
        )
        responses.append(
            SagaPhaseItemResponse(
                id=milestone.id,
                saga_id=saga_id,
                tracker_id=milestone.id,
                number=index,
                name=milestone.name,
                status=_coerce_phase_status(run_items, milestone),
                confidence=phase_confidence,
                runs=run_items,
            )
        )

    unassigned = issues_by_milestone.get(None, [])
    if unassigned:
        phase_id = "__unassigned__"
        run_items = []
        for issue in unassigned:
            try:
                run = await tracker.get_run(issue.id)
                run_items.append(
                    RunPhaseItemResponse(
                        id=str(run.id),
                        phase_id=str(run.phase_id),
                        tracker_id=run.tracker_id,
                        identifier=run.identifier,
                        url=run.url,
                        name=run.name,
                        description=run.description,
                        acceptance_criteria=run.acceptance_criteria,
                        declared_files=run.declared_files,
                        estimate_hours=run.estimate_hours,
                        status=run.status.value.lower(),
                        confidence=run.confidence,
                        session_id=run.session_id,
                        reviewer_session_id=run.reviewer_session_id,
                        review_round=run.review_round,
                        branch=run.branch,
                        chronicle_summary=run.chronicle_summary,
                        retry_count=run.retry_count,
                        created_at=run.created_at,
                        updated_at=run.updated_at,
                    )
                )
            except Exception:
                run_items.append(_fallback_run(issue, phase_id=phase_id))

        phase_confidence = (
            sum(run.confidence for run in run_items) / len(run_items) if run_items else 0.0
        )
        responses.append(
            SagaPhaseItemResponse(
                id=phase_id,
                saga_id=saga_id,
                tracker_id=phase_id,
                number=len(ordered_milestones) + 1,
                name="Unassigned",
                status=_coerce_phase_status(run_items, None),
                confidence=phase_confidence,
                runs=run_items,
            )
        )

    return responses


def create_saga_phases_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/sagas", tags=["Sagas"])

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
            runs = await repo.get_runs_by_phase(phase.id)
            run_items: list[RunPhaseItemResponse] = []
            for run in runs:
                identifier = run.identifier
                url = run.url
                if run.tracker_id and (not identifier or not url):
                    tracker_identifier, tracker_url = await _tracker_run_metadata(
                        trackers,
                        run.tracker_id,
                    )
                    identifier = identifier or tracker_identifier
                    url = url or tracker_url
                run_items.append(
                    RunPhaseItemResponse(
                        id=str(run.id),
                        phase_id=str(run.phase_id),
                        tracker_id=run.tracker_id,
                        identifier=identifier,
                        url=url,
                        name=run.name,
                        description=run.description,
                        acceptance_criteria=run.acceptance_criteria,
                        declared_files=run.declared_files,
                        estimate_hours=run.estimate_hours,
                        status=run.status.value.lower(),
                        confidence=run.confidence,
                        session_id=run.session_id,
                        reviewer_session_id=run.reviewer_session_id,
                        review_round=run.review_round,
                        branch=run.branch,
                        chronicle_summary=run.chronicle_summary,
                        retry_count=run.retry_count,
                        created_at=run.created_at,
                        updated_at=run.updated_at,
                    )
                )
            responses.append(
                SagaPhaseItemResponse(
                    id=str(phase.id),
                    saga_id=str(phase.saga_id),
                    tracker_id=phase.tracker_id,
                    number=phase.number,
                    name=phase.name,
                    status=phase.status.value.lower(),
                    confidence=phase.confidence,
                    runs=run_items,
                )
            )
        return responses

    return router
