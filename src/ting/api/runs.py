"""REST API for run review actions.

Provides endpoints for reviewing, approving, rejecting, and retrying runs
that are in the REVIEW state.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_principal
from ting.api.tracker import resolve_trackers
from ting.config import ReviewConfig
from ting.domain.exceptions import RunNotFoundError
from ting.domain.models import RunStatus
from ting.domain.services.run_review import (
    InvalidRunStateError,
    RunReviewService,
)
from ting.domain.services.session_message import (
    NoActiveSessionError,
    RunNotRunningError,
    SessionMessageService,
)
from ting.ports.git import GitPort
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import VolundrPort

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


async def _resolve_run_identifier(tracker: TrackerPort, run_id: str):
    """Resolve either an internal run UUID or a tracker-owned run identifier."""
    try:
        parsed = UUID(run_id)
    except ValueError:
        parsed = None

    if parsed is not None:
        run = await tracker.get_run_by_id(parsed)
        if run is not None:
            return run

    return await tracker.get_run(run_id)


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class ConfidenceEventResponse(BaseModel):
    id: str
    event_type: str
    delta: float
    score_after: float
    created_at: str


class ReviewResponse(BaseModel):
    run_id: str
    name: str
    status: str
    chronicle_summary: str | None = None
    pr_url: str | None = None
    ci_passed: bool | None = None
    confidence: float
    confidence_events: list[ConfidenceEventResponse] = Field(default_factory=list)


class RunResponse(BaseModel):
    id: str
    name: str
    status: str
    confidence: float
    retry_count: int
    branch: str | None = None
    chronicle_summary: str | None = None
    reason: str | None = None


class RejectRequest(BaseModel):
    reason: str | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=32768)
    target_peer_id: str | None = None


class SendMessageResponse(BaseModel):
    message_id: str
    run_id: str
    session_id: str
    content: str
    sender: str
    created_at: str


class HelpRequestResponse(BaseModel):
    summary: str
    reason: str
    attempted: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    target_peer_id: str | None = None
    persona: str | None = None


class SessionMessageResponse(BaseModel):
    id: str
    session_id: str
    content: str
    sender: str
    created_at: str
    kind: str = "message"
    help_request: HelpRequestResponse | None = None


# ---------------------------------------------------------------------------
# Explicit unavailable dependency defaults -- overridden by main.py lifespan
# ---------------------------------------------------------------------------


async def resolve_tracker() -> TrackerPort:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Tracker port not configured",
    )


async def resolve_volundr() -> VolundrPort:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Volundr port not configured",
    )


async def resolve_git() -> GitPort:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Git port not configured",
    )


async def resolve_run_repo() -> SagaRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Run repository not configured",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_review_config(request: Request) -> ReviewConfig:
    """Extract ReviewConfig from app settings, with fallback defaults."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return ReviewConfig()
    return settings.review


def _build_review_service(
    request: Request, tracker: TrackerPort, owner_id: str
) -> RunReviewService:
    """Construct a RunReviewService with config and event bus from app state."""
    review_cfg = _get_review_config(request)
    event_bus = getattr(request.app.state, "event_bus", None)
    return RunReviewService(tracker, owner_id, review_cfg, event_bus=event_bus)


def _run_response(run, reason: str | None = None) -> RunResponse:
    return RunResponse(
        id=str(run.id),
        name=run.name,
        status=run.status.value,
        confidence=run.confidence,
        retry_count=run.retry_count,
        branch=run.branch,
        chronicle_summary=run.chronicle_summary,
        reason=reason,
    )


def _to_session_message_response(message) -> SessionMessageResponse:
    help_request = (
        _parse_help_request_payload(message.content) if message.sender == "help_needed" else None
    )
    return SessionMessageResponse(
        id=str(message.id),
        session_id=message.session_id,
        content=message.content,
        sender=message.sender,
        created_at=message.created_at.isoformat(),
        kind="help_request" if help_request is not None else "message",
        help_request=help_request,
    )


def _parse_help_request_payload(raw: str) -> HelpRequestResponse | None:
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    attempted = data.get("attempted")
    if not isinstance(attempted, list):
        attempted = []
    context = data.get("context")
    if not isinstance(context, dict):
        context = {}
    return HelpRequestResponse(
        summary=str(data.get("summary") or "Agent needs help"),
        reason=str(data.get("reason") or "needs_context"),
        attempted=[str(item) for item in attempted if str(item).strip()],
        recommendation=str(data.get("recommendation") or "") or None,
        context=context,
        target_peer_id=str(data.get("target_peer_id") or "") or None,
        persona=str(data.get("persona") or "") or None,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class ActiveRunResponse(BaseModel):
    """A run with progress data from the tracker."""

    tracker_id: str
    identifier: str = ""
    title: str = ""
    url: str = ""
    status: str
    session_id: str | None = None
    reviewer_session_id: str | None = None
    review_round: int = 0
    confidence: float = 0.0
    pr_url: str | None = None
    last_updated: str = ""


def create_runs_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/runs", tags=["Runs"])

    @router.get("/summary", response_model=dict[str, int])
    async def get_runs_summary(
        run_repo: SagaRepository = Depends(resolve_run_repo),
    ) -> dict[str, int]:
        """Return a count of runs grouped by status."""
        return await run_repo.count_by_status()

    @router.get("/active", response_model=list[ActiveRunResponse])
    async def list_active_runs(
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[ActiveRunResponse]:
        """List all runs with progress data for the authenticated user."""
        results: list[ActiveRunResponse] = []
        for tracker in adapters:
            for run_status in RunStatus:
                try:
                    runs = await tracker.list_runs_by_status(run_status)
                except Exception:
                    continue
                for run in runs:
                    results.append(
                        ActiveRunResponse(
                            tracker_id=run.tracker_id,
                            identifier=run.identifier or run.tracker_id,
                            title=run.name,
                            url=run.url or "",
                            status=run.status.value,
                            session_id=run.session_id,
                            reviewer_session_id=run.reviewer_session_id,
                            review_round=run.review_round,
                            confidence=run.confidence,
                            pr_url=run.pr_url,
                            last_updated=run.updated_at.isoformat() if run.updated_at else "",
                        )
                    )
        return results

    @router.get("/{run_id}/review", response_model=ReviewResponse)
    async def get_review(
        run_id: str,
        tracker: TrackerPort = Depends(resolve_tracker),
        volundr: VolundrPort = Depends(resolve_volundr),
    ) -> ReviewResponse:
        """Get review state for a run: chronicle summary, CI status, confidence."""
        try:
            run = await _resolve_run_identifier(tracker, run_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        # Fetch PR/CI status from Volundr if a session exists
        pr_url: str | None = None
        ci_passed: bool | None = None
        if run.session_id:
            try:
                pr_status = await volundr.get_pr_status(run.session_id)
                pr_url = pr_status.url
                ci_passed = pr_status.ci_passed
            except Exception:
                logger.warning(
                    "Failed to fetch PR status for session %s",
                    run.session_id,
                    exc_info=True,
                )

        events = await tracker.get_confidence_events(run.tracker_id)

        return ReviewResponse(
            run_id=str(run.id),
            name=run.name,
            status=run.status.value,
            chronicle_summary=run.chronicle_summary,
            pr_url=pr_url,
            ci_passed=ci_passed,
            confidence=run.confidence,
            confidence_events=[
                ConfidenceEventResponse(
                    id=str(e.id),
                    event_type=e.event_type.value,
                    delta=e.delta,
                    score_after=e.score_after,
                    created_at=e.created_at.isoformat(),
                )
                for e in events
            ],
        )

    @router.post("/{run_id}/approve", response_model=RunResponse)
    async def approve_run(
        run_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        tracker: TrackerPort = Depends(resolve_tracker),
        volundr: VolundrPort = Depends(resolve_volundr),
        git: GitPort = Depends(resolve_git),
    ) -> RunResponse:
        """Approve a run: merge branch, update state, check phase gate.

        REST-specific pre/post steps (CI check, git merge, tracker update)
        wrap the shared RunReviewService which handles confidence events,
        state transition, and phase gate checks.
        """
        svc = _build_review_service(request, tracker, principal.user_id)

        # Fetch run for REST-specific pre-steps (CI check, git merge)
        try:
            run = await _resolve_run_identifier(tracker, run_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        # Pre-step: check CI status (warn but don't block)
        if run.session_id:
            try:
                pr_status = await volundr.get_pr_status(run.session_id)
                if pr_status.ci_passed is False:
                    logger.warning("Approving run %s with failing CI", _sanitize_log(run_id))
            except Exception:
                logger.warning(
                    "Could not verify CI status for run %s",
                    _sanitize_log(run_id),
                    exc_info=True,
                )

        # Pre-step: merge run branch into feature branch before state transition
        saga = await tracker.get_saga_for_run(run.tracker_id)
        if saga is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent saga not found for run",
            )
        if run.branch and saga.repos:
            repo = saga.repos[0]
            try:
                await git.merge_branch(repo, run.branch, saga.feature_branch)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Branch merge failed: {exc}",
                )

            try:
                await git.delete_branch(repo, run.branch)
            except Exception:
                logger.warning("Failed to delete branch %s", run.branch, exc_info=True)

        # Core review: confidence event, state → MERGED, phase gate check
        try:
            result = await svc.approve(run.id)
        except RunNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )
        except InvalidRunStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot approve run in {exc.current} state",
            )

        # Post-step: update external tracker
        try:
            await tracker.update_run_state(result.run.tracker_id, RunStatus.MERGED)
            await tracker.close_run(result.run.tracker_id)
        except Exception:
            logger.warning(
                "Failed to update tracker for run %s",
                _sanitize_log(run_id),
                exc_info=True,
            )

        return _run_response(result.run)

    @router.post("/{run_id}/reject", response_model=RunResponse)
    async def reject_run(
        run_id: str,
        request: Request,
        body: RejectRequest | None = None,
        principal: Principal = Depends(extract_principal),
        tracker: TrackerPort = Depends(resolve_tracker),
    ) -> RunResponse:
        """Reject a run: set FAILED, record reason, apply confidence penalty."""
        reason = body.reason if body else None
        svc = _build_review_service(request, tracker, principal.user_id)

        # Core review: confidence event, state → FAILED
        try:
            # Look up run to get internal ID for the service
            run_obj = await _resolve_run_identifier(tracker, run_id)
            result = await svc.reject(run_obj.id, reason=reason)
        except RunNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )
        except InvalidRunStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot reject run in {exc.current} state",
            )

        # Post-step: update external tracker
        try:
            await tracker.update_run_state(result.run.tracker_id, result.run.status)
        except Exception:
            logger.warning(
                "Failed to update tracker for run %s",
                _sanitize_log(run_id),
                exc_info=True,
            )

        return _run_response(result.run, reason=reason)

    @router.post("/{run_id}/retry", response_model=RunResponse)
    async def retry_run(
        run_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        tracker: TrackerPort = Depends(resolve_tracker),
    ) -> RunResponse:
        """Retry a run: re-queue with incremented retry_count."""
        svc = _build_review_service(request, tracker, principal.user_id)

        # Core review: confidence event, state → PENDING or QUEUED
        try:
            run_obj = await _resolve_run_identifier(tracker, run_id)
            result = await svc.retry(run_obj.id)
        except RunNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )
        except InvalidRunStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot retry run in {exc.current} state",
            )

        # Post-step: update external tracker with actual result status
        try:
            await tracker.update_run_state(result.run.tracker_id, result.run.status)
        except Exception:
            logger.warning(
                "Failed to update tracker for run %s",
                _sanitize_log(run_id),
                exc_info=True,
            )

        return _run_response(result.run)

    @router.post("/{run_id}/message", response_model=SendMessageResponse)
    async def send_message(
        run_id: str,
        body: SendMessageRequest,
        request: Request,
        tracker: TrackerPort = Depends(resolve_tracker),
        volundr: VolundrPort = Depends(resolve_volundr),
    ) -> SendMessageResponse:
        """Send a message to the running session for a run."""
        event_bus = getattr(request.app.state, "event_bus", None)
        svc = SessionMessageService(tracker, volundr, event_bus=event_bus)

        try:
            run_obj = await _resolve_run_identifier(tracker, run_id)
            result = await svc.send_message(
                run_obj.id,
                body.content,
                sender="user",
                target_peer_id=body.target_peer_id,
            )
        except RunNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )
        except RunNotRunningError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run is in {exc.status} state, not running",
            )
        except NoActiveSessionError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run has no active session",
            )
        except Exception as exc:
            logger.warning(
                "Failed to send message to run %s: %s",
                _sanitize_log(run_id),
                _sanitize_log(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Session unavailable: {exc}",
            )

        return SendMessageResponse(
            message_id=str(result.message.id),
            run_id=str(result.run_id),
            session_id=result.session_id,
            content=result.message.content,
            sender=result.message.sender,
            created_at=result.message.created_at.isoformat(),
        )

    @router.get("/{run_id}/messages", response_model=list[SessionMessageResponse])
    async def list_messages(
        run_id: str,
        tracker: TrackerPort = Depends(resolve_tracker),
    ) -> list[SessionMessageResponse]:
        """List all messages sent to a run's session (audit trail)."""
        try:
            run = await _resolve_run_identifier(tracker, run_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {run_id}",
            )

        messages = await tracker.get_session_messages(run.tracker_id)
        return [_to_session_message_response(m) for m in messages]

    return router
