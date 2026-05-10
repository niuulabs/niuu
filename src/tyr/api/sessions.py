"""Compatibility session endpoints for Tyr's web-next HTTP adapter.

Ownership decision:
- Tyr owns review and approval semantics for raid-linked sessions.
- Forge/Volundr remains the provider of session runtime primitives such as
  session lookup, chronicle summaries, and PR status.

These endpoints intentionally compose those concerns instead of moving
approval flows back under Forge.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from niuu.domain.models import Principal
from tyr.adapters.inbound.auth import extract_bearer_token, extract_principal
from tyr.api.raids import _build_review_service, resolve_git, resolve_volundr
from tyr.api.tracker import resolve_trackers
from tyr.domain.exceptions import RaidNotFoundError
from tyr.domain.models import Raid, RaidStatus, Saga
from tyr.domain.services.raid_review import InvalidRaidStateError
from tyr.ports.git import GitPort
from tyr.ports.tracker import TrackerPort
from tyr.ports.volundr import VolundrPort, VolundrSession

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


class SessionInfoResponse(BaseModel):
    session_id: str
    status: str
    chronicle_lines: list[str]
    branch: str | None = None
    confidence: float
    raid_name: str
    saga_name: str
    cluster_name: str = ""


def _normalise_confidence(value: float) -> float:
    if value <= 1.0:
        return round(value * 100, 2)
    return value


def _session_status_for_raid(raid: Raid | None, session: VolundrSession | None) -> str:
    if raid is None:
        return session.status if session is not None else "running"

    if raid.status in {RaidStatus.REVIEW, RaidStatus.ESCALATED}:
        return "awaiting_approval"
    if raid.status is RaidStatus.MERGED:
        return "approved"
    if raid.status is RaidStatus.FAILED:
        return "failed"
    return "running"


async def _resolve_session_context(
    session_id: str,
    *,
    trackers: list[TrackerPort],
) -> tuple[TrackerPort | None, Raid | None, Saga | None]:
    for tracker in trackers:
        raid = await tracker.get_raid_by_session(session_id)
        if raid is None:
            continue
        saga = await tracker.get_saga_for_raid(raid.tracker_id)
        return tracker, raid, saga
    return None, None, None


async def _build_session_info(
    session: VolundrSession,
    *,
    volundr: VolundrPort,
    trackers: list[TrackerPort],
) -> SessionInfoResponse:
    tracker, raid, saga = await _resolve_session_context(session.id, trackers=trackers)
    del tracker

    chronicle_lines: list[str] = []
    try:
        summary = await volundr.get_chronicle_summary(session.id)
    except Exception:
        summary = ""
    if summary:
        chronicle_lines = summary.splitlines()

    return SessionInfoResponse(
        session_id=session.id,
        status=_session_status_for_raid(raid, session),
        chronicle_lines=chronicle_lines,
        branch=session.branch or (raid.branch if raid else None),
        confidence=_normalise_confidence(raid.confidence if raid else 0.0),
        raid_name=raid.name if raid else session.name,
        saga_name=saga.name if saga else "",
        cluster_name=session.cluster_name or volundr.name,
    )


async def _resolve_volundr_adapters(
    request: Request,
    *,
    owner_id: str,
    fallback: VolundrPort,
) -> list[VolundrPort]:
    factory = getattr(request.app.state, "volundr_factory", None)
    if factory is None:
        return [fallback]

    try:
        adapters = await factory.for_owner(owner_id)
    except Exception:
        logger.warning(
            "Failed to resolve Volundr adapters for owner %s",
            _sanitize_log(owner_id),
            exc_info=True,
        )
        return [fallback]

    return adapters or [fallback]


async def _list_sessions_across_adapters(
    adapters: list[VolundrPort], *, auth_token: str | None = None
) -> list[tuple[VolundrPort, VolundrSession]]:
    responses = await asyncio.gather(
        *(adapter.list_sessions(auth_token=auth_token) for adapter in adapters),
        return_exceptions=True,
    )

    seen_session_ids: set[str] = set()
    sessions: list[tuple[VolundrPort, VolundrSession]] = []
    for adapter, response in zip(adapters, responses, strict=False):
        if isinstance(response, Exception):
            logger.warning(
                "Failed to list sessions from Volundr adapter %s: %s",
                _sanitize_log(adapter.name or "<unnamed>"),
                _sanitize_log(response),
            )
            continue

        for session in response:
            if session.id in seen_session_ids:
                continue
            seen_session_ids.add(session.id)
            sessions.append((adapter, session))
    return sessions


async def _find_session_across_adapters(
    session_id: str,
    adapters: list[VolundrPort],
    *,
    auth_token: str | None = None,
) -> tuple[VolundrPort | None, VolundrSession | None]:
    for adapter in adapters:
        try:
            session = await adapter.get_session(session_id, auth_token=auth_token)
        except Exception:
            logger.warning(
                "Failed to load session %s from Volundr adapter %s",
                _sanitize_log(session_id),
                _sanitize_log(adapter.name or "<unnamed>"),
                exc_info=True,
            )
            continue
        if session is not None:
            return adapter, session
    return None, None


def create_sessions_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/sessions", tags=["Tyr Sessions"])

    @router.get("", response_model=list[SessionInfoResponse])
    async def list_sessions(
        request: Request,
        principal: Principal = Depends(extract_principal),
        trackers: list[TrackerPort] = Depends(resolve_trackers),
        volundr: VolundrPort = Depends(resolve_volundr),
    ) -> list[SessionInfoResponse]:
        auth_token = extract_bearer_token(request)
        adapters = await _resolve_volundr_adapters(
            request, owner_id=principal.user_id, fallback=volundr
        )
        sessions = await _list_sessions_across_adapters(adapters, auth_token=auth_token)
        items: list[SessionInfoResponse] = []
        for adapter, session in sessions:
            items.append(await _build_session_info(session, volundr=adapter, trackers=trackers))
        return items

    @router.get("/{session_id}", response_model=SessionInfoResponse)
    async def get_session(
        session_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        trackers: list[TrackerPort] = Depends(resolve_trackers),
        volundr: VolundrPort = Depends(resolve_volundr),
    ) -> SessionInfoResponse:
        auth_token = extract_bearer_token(request)
        adapters = await _resolve_volundr_adapters(
            request, owner_id=principal.user_id, fallback=volundr
        )
        adapter, session = await _find_session_across_adapters(
            session_id, adapters, auth_token=auth_token
        )
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        assert adapter is not None
        return await _build_session_info(session, volundr=adapter, trackers=trackers)

    @router.post("/{session_id}/approve", status_code=status.HTTP_202_ACCEPTED)
    async def approve_session(
        session_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        trackers: list[TrackerPort] = Depends(resolve_trackers),
        volundr: VolundrPort = Depends(resolve_volundr),
        git: GitPort = Depends(resolve_git),
    ) -> Response:
        tracker, raid, saga = await _resolve_session_context(session_id, trackers=trackers)
        if tracker is None or raid is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )

        svc = _build_review_service(request, tracker, principal.user_id)

        if raid.session_id:
            adapters = await _resolve_volundr_adapters(
                request, owner_id=principal.user_id, fallback=volundr
            )
            session_volundr, _session = await _find_session_across_adapters(
                raid.session_id, adapters
            )
            active_volundr = session_volundr or volundr
            try:
                pr_status = await active_volundr.get_pr_status(raid.session_id)
                if pr_status.ci_passed is False:
                    logger.warning(
                        "Approving session %s with failing CI",
                        _sanitize_log(session_id),
                    )
            except Exception:
                logger.warning(
                    "Could not verify CI status for session %s",
                    _sanitize_log(session_id),
                    exc_info=True,
                )

        if saga is not None and raid.branch and saga.repos:
            repo = saga.repos[0]
            try:
                await git.merge_branch(repo, raid.branch, saga.feature_branch)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Branch merge failed: {exc}",
                ) from exc

            try:
                await git.delete_branch(repo, raid.branch)
            except Exception:
                logger.warning(
                    "Failed to delete branch %s",
                    _sanitize_log(raid.branch),
                    exc_info=True,
                )

        try:
            result = await svc.approve(raid.id)
        except RaidNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            ) from exc
        except InvalidRaidStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot approve session in {exc.current} state",
            ) from exc

        try:
            await tracker.update_raid_state(result.raid.tracker_id, RaidStatus.MERGED)
            await tracker.close_raid(result.raid.tracker_id)
        except Exception:
            logger.warning(
                "Failed to update tracker after approving session %s",
                _sanitize_log(session_id),
                exc_info=True,
            )

        return Response(status_code=status.HTTP_202_ACCEPTED)

    return router
