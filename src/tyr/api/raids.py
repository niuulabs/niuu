"""REST API for raid review actions.

Provides endpoints for reviewing, approving, rejecting, and retrying raids
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
from tyr.adapters.inbound.auth import extract_principal
from tyr.api.tracker import resolve_trackers
from tyr.config import ReviewConfig
from tyr.domain.exceptions import RaidNotFoundError
from tyr.domain.models import RaidStatus
from tyr.domain.services.raid_review import (
    InvalidRaidStateError,
    RaidReviewService,
)
from tyr.domain.services.session_message import (
    NoActiveSessionError,
    RaidNotRunningError,
    SessionMessageService,
)
from tyr.ports.git import GitPort
from tyr.ports.saga_repository import SagaRepository
from tyr.ports.tracker import TrackerPort
from tyr.ports.volundr import VolundrPort

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


async def _resolve_raid_identifier(tracker: TrackerPort, raid_id: str):
    """Resolve either an internal raid UUID or a tracker-owned raid identifier."""
    try:
        parsed = UUID(raid_id)
    except ValueError:
        parsed = None

    if parsed is not None:
        raid = await tracker.get_raid_by_id(parsed)
        if raid is not None:
            return raid

    return await tracker.get_raid(raid_id)


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
    raid_id: str
    name: str
    status: str
    chronicle_summary: str | None = None
    pr_url: str | None = None
    ci_passed: bool | None = None
    confidence: float
    confidence_events: list[ConfidenceEventResponse] = Field(default_factory=list)


class RaidResponse(BaseModel):
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
    raid_id: str
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
# Dependency stubs -- overridden by main.py lifespan
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


async def resolve_raid_repo() -> SagaRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Raid repository not configured",
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
) -> RaidReviewService:
    """Construct a RaidReviewService with config and event bus from app state."""
    review_cfg = _get_review_config(request)
    event_bus = getattr(request.app.state, "event_bus", None)
    return RaidReviewService(tracker, owner_id, review_cfg, event_bus=event_bus)


def _raid_response(raid, reason: str | None = None) -> RaidResponse:
    return RaidResponse(
        id=str(raid.id),
        name=raid.name,
        status=raid.status.value,
        confidence=raid.confidence,
        retry_count=raid.retry_count,
        branch=raid.branch,
        chronicle_summary=raid.chronicle_summary,
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


class ActiveRaidResponse(BaseModel):
    """A raid with progress data from the tracker."""

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


def create_raids_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/raids", tags=["Raids"])

    @router.get("/summary", response_model=dict[str, int])
    async def get_raids_summary(
        raid_repo: SagaRepository = Depends(resolve_raid_repo),
    ) -> dict[str, int]:
        """Return a count of raids grouped by status."""
        return await raid_repo.count_by_status()

    @router.get("/active", response_model=list[ActiveRaidResponse])
    async def list_active_raids(
        principal: Principal = Depends(extract_principal),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[ActiveRaidResponse]:
        """List all raids with progress data for the authenticated user."""
        results: list[ActiveRaidResponse] = []
        for tracker in adapters:
            for raid_status in RaidStatus:
                try:
                    raids = await tracker.list_raids_by_status(raid_status)
                except Exception:
                    continue
                for raid in raids:
                    results.append(
                        ActiveRaidResponse(
                            tracker_id=raid.tracker_id,
                            identifier=raid.identifier or raid.tracker_id,
                            title=raid.name,
                            url=raid.url or "",
                            status=raid.status.value,
                            session_id=raid.session_id,
                            reviewer_session_id=raid.reviewer_session_id,
                            review_round=raid.review_round,
                            confidence=raid.confidence,
                            pr_url=raid.pr_url,
                            last_updated=raid.updated_at.isoformat() if raid.updated_at else "",
                        )
                    )
        return results

    @router.get("/{raid_id}/review", response_model=ReviewResponse)
    async def get_review(
        raid_id: str,
        tracker: TrackerPort = Depends(resolve_tracker),
        volundr: VolundrPort = Depends(resolve_volundr),
    ) -> ReviewResponse:
        """Get review state for a raid: chronicle summary, CI status, confidence."""
        try:
            raid = await _resolve_raid_identifier(tracker, raid_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Raid not found: {raid_id}",
            )

        # Fetch PR/CI status from Volundr if a session exists
        pr_url: str | None = None
        ci_passed: bool | None = None
        if raid.session_id:
            try:
                pr_status = await volundr.get_pr_status(raid.session_id)
                pr_url = pr_status.url
                ci_passed = pr_status.ci_passed
            except Exception:
                logger.warning(
                    "Failed to fetch PR status for session %s",
                    raid.session_id,
                    exc_info=True,
                )

        events = await tracker.get_confidence_events(raid.tracker_id)

        return ReviewResponse(
            raid_id=str(raid.id),
            name=raid.name,
            status=raid.status.value,
            chronicle_summary=raid.chronicle_summary,
            pr_url=pr_url,
            ci_passed=ci_passed,
            confidence=raid.confidence,
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

    @router.post("/{raid_id}/approve", response_model=RaidResponse)
    async def approve_raid(
        raid_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        tracker: TrackerPort = Depends(resolve_tracker),
        volundr: VolundrPort = Depends(resolve_volundr),
        git: GitPort = Depends(resolve_git),
    ) -> RaidResponse:
        """Approve a raid: merge branch, update state, check phase gate.

        REST-specific pre/post steps (CI check, git merge, tracker update)
        wrap the shared RaidReviewService which handles confidence events,
        state transition, and phase gate checks.
        """
        svc = _build_review_service(request, tracker, principal.user_id)

        # Fetch raid for REST-specific pre-steps (CI check, git merge)
        try:
            raid = await _resolve_raid_identifier(tracker, raid_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Raid not found: {raid_id}",
            )

        # Pre-step: check CI status (warn but don't block)
        if raid.session_id:
            try:
                pr_status = await volundr.get_pr_status(raid.session_id)
                if pr_status.ci_passed is False:
                    logger.warning("Approving raid %s with failing CI", _sanitize_log(raid_id))
            except Exception:
                logger.warning(
                    "Could not verify CI status for raid %s",
                    _sanitize_log(raid_id),
                    exc_info=True,
                )

        # Pre-step: merge raid branch into feature branch before state transition
        saga = await tracker.get_saga_for_raid(raid.tracker_id)
        if saga is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent saga not found for raid",
            )
        if raid.branch and saga.repos:
            repo = saga.repos[0]
            try:
                await git.merge_branch(repo, raid.branch, saga.feature_branch)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Branch merge failed: {exc}",
                )

            try:
                await git.delete_branch(repo, raid.branch)
            except Exception:
                logger.warning("Failed to delete branch %s", raid.branch, exc_info=True)

        # Core review: confidence event, state → MERGED, phase gate check
        try:
            result = await svc.approve(raid.id)
        except RaidNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Raid not found: {raid_id}",
            )
        except InvalidRaidStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot approve raid in {exc.current} state",
            )

        # Post-step: update external tracker
        try:
            await tracker.update_raid_state(result.raid.tracker_id, RaidStatus.MERGED)
            await tracker.close_raid(result.raid.tracker_id)
        except Exception:
            logger.warning(
                "Failed to update tracker for raid %s",
                _sanitize_log(raid_id),
                exc_info=True,
            )

        return _raid_response(result.raid)

    @router.post("/{raid_id}/reject", response_model=RaidResponse)
    async def reject_raid(
        raid_id: str,
        request: Request,
        body: RejectRequest | None = None,
        principal: Principal = Depends(extract_principal),
        tracker: TrackerPort = Depends(resolve_tracker),
    ) -> RaidResponse:
        """Reject a raid: set FAILED, record reason, apply confidence penalty."""
        reason = body.reason if body else None
        svc = _build_review_service(request, tracker, principal.user_id)

        # Core review: confidence event, state → FAILED
        try:
            # Look up raid to get internal ID for the service
            raid_obj = await _resolve_raid_identifier(tracker, raid_id)
            result = await svc.reject(raid_obj.id, reason=reason)
        except RaidNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Raid not found: {raid_id}",
            )
        except InvalidRaidStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot reject raid in {exc.current} state",
            )

        # Post-step: update external tracker
        try:
            await tracker.update_raid_state(result.raid.tracker_id, result.raid.status)
        except Exception:
            logger.warning(
                "Failed to update tracker for raid %s",
                _sanitize_log(raid_id),
                exc_info=True,
            )

        return _raid_response(result.raid, reason=reason)

    @router.post("/{raid_id}/retry", response_model=RaidResponse)
    async def retry_raid(
        raid_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        tracker: TrackerPort = Depends(resolve_tracker),
    ) -> RaidResponse:
        """Retry a raid: re-queue with incremented retry_count."""
        svc = _build_review_service(request, tracker, principal.user_id)

        # Core review: confidence event, state → PENDING or QUEUED
        try:
            raid_obj = await _resolve_raid_identifier(tracker, raid_id)
            result = await svc.retry(raid_obj.id)
        except RaidNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Raid not found: {raid_id}",
            )
        except InvalidRaidStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot retry raid in {exc.current} state",
            )

        # Post-step: update external tracker with actual result status
        try:
            await tracker.update_raid_state(result.raid.tracker_id, result.raid.status)
        except Exception:
            logger.warning(
                "Failed to update tracker for raid %s",
                _sanitize_log(raid_id),
                exc_info=True,
            )

        return _raid_response(result.raid)

    @router.post("/{raid_id}/message", response_model=SendMessageResponse)
    async def send_message(
        raid_id: str,
        body: SendMessageRequest,
        request: Request,
        tracker: TrackerPort = Depends(resolve_tracker),
        volundr: VolundrPort = Depends(resolve_volundr),
    ) -> SendMessageResponse:
        """Send a message to the running session for a raid."""
        event_bus = getattr(request.app.state, "event_bus", None)
        svc = SessionMessageService(tracker, volundr, event_bus=event_bus)

        try:
            raid_obj = await _resolve_raid_identifier(tracker, raid_id)
            result = await svc.send_message(
                raid_obj.id,
                body.content,
                sender="user",
                target_peer_id=body.target_peer_id,
            )
        except RaidNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Raid not found: {raid_id}",
            )
        except RaidNotRunningError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Raid is in {exc.status} state, not running",
            )
        except NoActiveSessionError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Raid has no active session",
            )
        except Exception as exc:
            logger.warning(
                "Failed to send message to raid %s: %s",
                _sanitize_log(raid_id),
                _sanitize_log(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Session unavailable: {exc}",
            )

        return SendMessageResponse(
            message_id=str(result.message.id),
            raid_id=str(result.raid_id),
            session_id=result.session_id,
            content=result.message.content,
            sender=result.message.sender,
            created_at=result.message.created_at.isoformat(),
        )

    @router.get("/{raid_id}/messages", response_model=list[SessionMessageResponse])
    async def list_messages(
        raid_id: str,
        tracker: TrackerPort = Depends(resolve_tracker),
    ) -> list[SessionMessageResponse]:
        """List all messages sent to a raid's session (audit trail)."""
        try:
            raid = await _resolve_raid_identifier(tracker, raid_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Raid not found: {raid_id}",
            )

        messages = await tracker.get_session_messages(raid.tracker_id)
        return [_to_session_message_response(m) for m in messages]

    return router
