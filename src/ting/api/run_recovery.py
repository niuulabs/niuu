"""Operator recovery actions for workflow runs: retry, reassign and abort.

A step that failed or got stuck (see ``ting.domain.step_stuck``) is recovered by a
person. These routes are the same engine calls the sweep never makes on its own:

* ``retry`` makes a failed or flagged step a new attempt of the same node and member,
  on the member's session (a fresh one if that session failed);
* ``reassign`` moves a step to a fresh session of the member and stops the old one, for
  when the session itself is broken;
* ``abort`` cancels the run.

Each needs a reason, is authorized like the run's gate decision (the caller's own run,
looked up by owner) and is recorded in the run's event log with who, when and why. A
step in a state where the action makes no sense is refused with 409 and the reason.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from ting.adapters.forge_step_sessions import StepSessionError
from ting.adapters.inbound.auth import extract_principal
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.workflow_execution_auth import require_coordinate_scope
from ting.domain.models import WorkflowCampaign
from ting.domain.services.workflow_runs import WorkflowRuns
from ting.domain.workflow_steps import (
    ActorKind,
    GateActor,
    RecoveryRefusedError,
    RunStateError,
    StepState,
)
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository

logger = logging.getLogger(__name__)


class RecoveryRequest(BaseModel):
    reason: str = Field(
        min_length=1,
        description="Why the action is taken; recorded in the run's event log.",
    )


class StepRecoveryResponse(BaseModel):
    status: str
    activation_id: str = Field(description="The new attempt's activation id.")
    attempt: int
    state: str = Field(description="The new attempt's state: active, or pending when queued.")


class RunAbortResponse(BaseModel):
    status: str
    run_status: str


def _workflow_runs(request: Request) -> WorkflowRuns:
    runs = getattr(request.app.state, "workflow_runs", None)
    if runs is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The workflow step engine is not configured",
        )
    return runs


async def _owned_run(
    campaign_repo: WorkflowCampaignRepository, slug: str, principal: Principal
) -> WorkflowCampaign:
    campaign = await campaign_repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {slug}")
    return campaign


def _reason(body: RecoveryRequest) -> str:
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Say why the action is taken")
    return reason


def _refused(exc: RecoveryRefusedError | RunStateError) -> HTTPException:
    missing = isinstance(exc, RecoveryRefusedError) and exc.missing
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND if missing else status.HTTP_409_CONFLICT,
        detail=exc.reason if isinstance(exc, RecoveryRefusedError) else str(exc),
    )


def _forge_failed(action: str, exc: Exception) -> HTTPException:
    logger.error("Run recovery %s could not reach the run's sessions: %s", action, exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"The {action} could not reach the run's sessions on Forge: {exc}",
    )


def create_run_recovery_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/work/runs/{slug}", tags=["Run recovery"])

    @router.post("/steps/{activation_id}/retry", response_model=StepRecoveryResponse)
    async def retry_step(
        slug: str,
        activation_id: UUID,
        body: RecoveryRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        _scope: None = Depends(require_coordinate_scope),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> StepRecoveryResponse:
        """Make a failed or stuck step a new attempt on the member's session."""
        campaign = await _owned_run(campaign_repo, slug, principal)
        try:
            recovered = await _workflow_runs(request).retry_step(
                campaign,
                activation_id,
                by=GateActor(ActorKind.PERSON, principal.user_id, principal.email),
                reason=_reason(body),
            )
        except (RecoveryRefusedError, RunStateError) as exc:
            raise _refused(exc) from exc
        except (StepSessionError, httpx.HTTPError) as exc:
            raise _forge_failed("retry", exc) from exc
        return _recovered("retried", recovered.step)

    @router.post("/steps/{activation_id}/reassign", response_model=StepRecoveryResponse)
    async def reassign_step(
        slug: str,
        activation_id: UUID,
        body: RecoveryRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        _scope: None = Depends(require_coordinate_scope),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> StepRecoveryResponse:
        """Move an open or failed step to a fresh session of the same member."""
        campaign = await _owned_run(campaign_repo, slug, principal)
        try:
            recovered = await _workflow_runs(request).reassign_step(
                campaign,
                activation_id,
                by=GateActor(ActorKind.PERSON, principal.user_id, principal.email),
                reason=_reason(body),
            )
        except (RecoveryRefusedError, RunStateError) as exc:
            raise _refused(exc) from exc
        except (StepSessionError, httpx.HTTPError) as exc:
            raise _forge_failed("reassign", exc) from exc
        return _recovered("reassigned", recovered.step)

    @router.post("/abort", response_model=RunAbortResponse)
    async def abort_run(
        slug: str,
        body: RecoveryRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        _scope: None = Depends(require_coordinate_scope),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> RunAbortResponse:
        """Cancel the run with a reason: its open steps are canceled and sessions stopped."""
        campaign = await _owned_run(campaign_repo, slug, principal)
        try:
            await _workflow_runs(request).cancel(
                campaign,
                _reason(body),
                by=GateActor(ActorKind.PERSON, principal.user_id, principal.email),
            )
        except RunStateError as exc:
            raise _refused(exc) from exc
        except (StepSessionError, httpx.HTTPError) as exc:
            raise _forge_failed("abort", exc) from exc
        aborted = await campaign_repo.get_campaign(campaign.id) or campaign
        return RunAbortResponse(status="aborted", run_status=aborted.status.value)

    return router


def _recovered(outcome: str, step) -> StepRecoveryResponse:  # noqa: ANN001
    """The response for a new attempt; an attempt that could not start is an error."""
    if step.state is StepState.FAILED:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"The new attempt {step.attempt} was created but could not be started: {step.note}"
            ),
        )
    return StepRecoveryResponse(
        status=outcome,
        activation_id=str(step.id),
        attempt=step.attempt,
        state=step.state.value,
    )


__all__ = ["create_run_recovery_router"]
