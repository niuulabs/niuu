"""REST API for research campaigns launched through Ting workflows."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from mimir.connections import resolve_mimir_workload
from niuu.domain.mimir import MimirPage, MimirPageMeta
from niuu.domain.models import Principal
from niuu.ports.mimir import MimirPort
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflow_execution_launch import (
    durable_parent_node_id,
    launch_reserved_parent,
    new_parent_execution,
    parent_launch_body,
)
from ting.api.workflow_executions import resolve_optional_workflow_execution_repo
from ting.api.workflows import (
    WorkflowLaunchBody,
    WorkflowLaunchExecution,
    launch_workflow_execution,
    resolve_workflow_repo,
)
from ting.domain.campaign_stages import (
    FAILURE_ERROR_KEY,
    FAILURE_STAGE_KEY,
    WorkflowStage,
    record_runtime_failure,
    render_stage_state,
    workflow_stages,
)
from ting.domain.models import (
    CampaignStageState,
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDefinition,
)
from ting.domain.utils import _session_name, _slugify
from ting.domain.workflow_execution import (
    CHILDREN_JOINED_SUSPENSION_REASON,
    ExecutionConflictError,
    ExecutionState,
    WorkflowExecution,
)
from ting.domain.workflow_snapshot import (
    build_workflow_snapshot,
    workflow_artifact_paths_from_snapshot,
    workflow_mimir_from_snapshot,
)
from ting.ports.event_bus import TingEvent
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_execution import WorkflowExecutionRepository
from ting.ports.workflow_repository import WorkflowRepository

_DEFAULT_RESEARCH_WORKFLOW_NAME = "Research Campaign"
_RESEARCH_SURFACE = "ting.research"
_A2A_SURFACE = "a2a"
#: Campaign metadata key linking a campaign to the durable execution that
#: coordinates its subworkflow fan-out (``/workflow-executions/{id}``).
_WORKFLOW_EXECUTION_ID_KEY = "workflow_execution_id"
# How a workflow declares it is research work. The graph is the authority on
# kind; metadata.surface only records where a launch came from.
_RESEARCH_TAG = "research"
logger = logging.getLogger(__name__)
_MANIFEST_PATH_RE = re.compile(
    r"(research/campaigns/[A-Za-z0-9._/-]+\.md|learnings/research/[A-Za-z0-9._-]+\.md|followups/research/[A-Za-z0-9._-]+\.md)"
)


async def resolve_workflow_campaign_repo() -> WorkflowCampaignRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Workflow campaign repository not configured",
    )


class ResearchCampaignCreateBody(BaseModel):
    question: str = Field(min_length=1, max_length=100_000)
    name: str | None = Field(default=None, max_length=255)
    workflow_id: UUID | None = Field(default=None, alias="workflowId")
    workflow_version: str | None = Field(default=None, alias="workflowVersion", max_length=64)
    repo: str = Field(default="", max_length=500)
    branch: str = Field(default="", max_length=255)
    model: str = Field(default="", max_length=255)
    definition: str | None = Field(default=None, max_length=255)
    mode: str = Field(default="exploratory", max_length=64)
    audience: str = Field(default="", max_length=255)
    deliverable: str = Field(default="", max_length=255)
    success: str = Field(default="", max_length=500)
    constraints: list[str] = Field(default_factory=list)
    monitoring_cadence: str | None = Field(default=None, alias="monitoringCadence", max_length=255)
    connection_id: str | None = Field(default=None, alias="connectionId", max_length=255)
    gate_auto_forward_after: str | None = Field(
        default=None,
        max_length=32,
        alias="gateAutoForwardAfter",
    )

    model_config = {"populate_by_name": True}


class ResearchCampaignPatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] | None = None


class CampaignStageStateResponse(BaseModel):
    stage_id: str = Field(serialization_alias="stageId")
    label: str
    status: str
    started_at: datetime | None = Field(default=None, serialization_alias="startedAt")
    completed_at: datetime | None = Field(default=None, serialization_alias="completedAt")
    reason: str | None = None

    model_config = {"populate_by_name": True}


class CampaignArtifactResponse(BaseModel):
    path: str
    title: str
    updated_at: datetime = Field(serialization_alias="updatedAt")
    kind: str | None = None
    publish_state: str = Field(serialization_alias="publishState")
    source_ids: list[str] = Field(default_factory=list, serialization_alias="sourceIds")
    summary: str | None = None

    model_config = {"populate_by_name": True}


class CampaignArtifactSummaryResponse(BaseModel):
    """Per-campaign artifact tallies for the research list cards.

    The cards need counts and a published flag, not artifacts. Fetching the
    full detail per row to derive them meant a request per campaign plus a
    ``get_page`` per artifact; a listing of 18 campaigns did that on mount and
    again every 15s. Every field here comes from page metadata plus one
    manifest read, so no artifact content crosses the wire and a remote mount
    is read in a single listing rather than once per artifact.
    """

    artifact_count: int = Field(default=0, serialization_alias="artifactCount")
    source_count: int = Field(default=0, serialization_alias="sourceCount")
    critique_count: int = Field(default=0, serialization_alias="critiqueCount")
    learning_count: int = Field(default=0, serialization_alias="learningCount")
    follow_up_count: int = Field(default=0, serialization_alias="followUpCount")
    published: bool = False
    # False when Mímir could not be read, so the counts are unknown rather than
    # zero — the cards must not report an unreachable campaign as empty.
    known: bool = True

    model_config = {"populate_by_name": True}


class ResearchCampaignResponse(BaseModel):
    id: str
    slug: str
    name: str
    owner_id: str = Field(serialization_alias="ownerId")
    workflow_id: str = Field(serialization_alias="workflowId")
    workflow_version: str = Field(serialization_alias="workflowVersion")
    workflow_name: str = Field(serialization_alias="workflowName")
    session_id: str = Field(serialization_alias="sessionId")
    session_name: str = Field(serialization_alias="sessionName")
    chat_endpoint: str | None = Field(default=None, serialization_alias="chatEndpoint")
    status: str
    active_stage_id: str | None = Field(default=None, serialization_alias="activeStageId")
    stage_state: list[CampaignStageStateResponse] = Field(
        default_factory=list,
        serialization_alias="stageState",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
    last_activity_at: datetime | None = Field(default=None, serialization_alias="lastActivityAt")
    completed_at: datetime | None = Field(default=None, serialization_alias="completedAt")
    artifact_summary: CampaignArtifactSummaryResponse | None = Field(
        default=None,
        serialization_alias="artifactSummary",
    )

    model_config = {"populate_by_name": True}


class ResearchCampaignDetailResponse(ResearchCampaignResponse):
    artifacts: list[CampaignArtifactResponse] = Field(default_factory=list)
    canonical_artifacts: dict[str, str] = Field(
        default_factory=dict,
        serialization_alias="canonicalArtifacts",
    )
    #: True when Mímir could not be read in time, so ``artifacts`` is empty
    #: because the listing failed — not because the campaign produced nothing.
    #: Without this the two are indistinguishable to a caller, and an empty
    #: list would quietly assert a finished campaign wrote nothing.
    artifacts_unavailable: bool = Field(
        default=False,
        serialization_alias="artifactsUnavailable",
    )


class CampaignArtifactDetailResponse(BaseModel):
    path: str
    title: str
    updated_at: datetime = Field(serialization_alias="updatedAt")
    summary: str | None = None
    kind: str | None = None
    publish_state: str = Field(serialization_alias="publishState")
    source_ids: list[str] = Field(default_factory=list, serialization_alias="sourceIds")
    content: str

    model_config = {"populate_by_name": True}


def create_research_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/research", tags=["Research"])

    @router.get("/campaigns", response_model=list[ResearchCampaignResponse])
    async def list_campaigns(
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> list[ResearchCampaignResponse]:
        campaigns = [
            campaign
            for campaign in await repo.list_campaigns(owner_id=principal.user_id)
            if _is_research_campaign(campaign)
        ]
        refreshed = [
            await _refresh_campaign_runtime(
                request=request,
                campaign=campaign,
                repo=repo,
                volundr_factory=volundr_factory,
                principal=principal,
            )
            for campaign in campaigns
        ]
        # Summarised here so the cards need no per-campaign follow-up request,
        # and in one batch so the list is not one round trip per row.
        summaries = await _campaign_artifact_summaries(
            refreshed,
            settings=request.app.state.settings,
            bearer_token=extract_bearer_token(request),
        )
        return [
            _to_campaign_response(campaign, artifact_summary=summary)
            for campaign, summary in zip(refreshed, summaries, strict=True)
        ]

    @router.post(
        "/campaigns",
        response_model=ResearchCampaignResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_campaign(
        body: ResearchCampaignCreateBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        workflow_repo: WorkflowRepository = Depends(resolve_workflow_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
        execution_repo: WorkflowExecutionRepository | None = Depends(
            resolve_optional_workflow_execution_repo
        ),
    ) -> ResearchCampaignResponse:
        workflow = await _resolve_research_workflow(
            repo=workflow_repo,
            owner_id=principal.user_id,
            workflow_id=body.workflow_id,
            workflow_version=body.workflow_version,
        )
        initiative_prompt = _build_campaign_prompt(body)
        campaign_name = _campaign_name(body)
        session_name = _session_name(campaign_name) or "research-campaign"
        campaign_id = uuid4()
        try:
            workflow_snapshot = build_workflow_snapshot(
                workflow, persona_source=getattr(request.app.state, "persona_source", None)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        workflow_execution_id: str | None = None
        durable = await _durable_research_launch(
            request=request,
            body=body,
            workflow=workflow,
            workflow_snapshot=workflow_snapshot,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            session_name=session_name,
            prompt=initiative_prompt,
            principal=principal,
            bearer_token=bearer_token,
            execution_repo=execution_repo,
            volundr_factory=volundr_factory,
        )
        if durable is not None:
            execution, workflow_execution_id = durable
        else:
            launch = WorkflowLaunchBody(
                prompt=initiative_prompt,
                sessionName=session_name,
                repo=body.repo,
                branch=body.branch,
                connectionId=body.connection_id,
                model=body.model,
                definition=body.definition,
                workflowVersion=workflow.version,
                gateAutoForwardAfter=body.gate_auto_forward_after,
            )
            execution = await launch_workflow_execution(
                request=request,
                workflow=workflow,
                launch=launch,
                volundr_factory=volundr_factory,
                principal=principal,
                bearer_token=bearer_token,
                pinned_workflow_snapshot=workflow_snapshot,
            )

        slug = await _reserve_slug(campaign_repo, execution.slug)
        now = datetime.now(UTC)
        stage_state = _initial_stage_state(execution.workflow_snapshot, now)
        campaign = WorkflowCampaign(
            tenant_id=principal.tenant_id,
            id=campaign_id,
            slug=slug,
            name=campaign_name,
            owner_id=principal.user_id,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workflow_name=workflow.name,
            workflow_snapshot=execution.workflow_snapshot,
            session_id=execution.session.id,
            session_name=execution.session.name,
            status=_campaign_status_from_session(execution.session.status),
            active_stage_id=stage_state[0].stage_id if stage_state else None,
            stage_state=stage_state,
            metadata={
                "question": body.question,
                "surface": _RESEARCH_SURFACE,
                "mode": body.mode,
                "audience": body.audience,
                "deliverable": body.deliverable,
                "success": body.success,
                "constraints": body.constraints,
                "monitoring_cadence": body.monitoring_cadence,
                "repo": body.repo,
                "branch": body.branch,
                "connection_id": body.connection_id,
                "cluster_name": execution.session.cluster_name,
                **(
                    {_WORKFLOW_EXECUTION_ID_KEY: workflow_execution_id}
                    if workflow_execution_id
                    else {}
                ),
            },
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            completed_at=now if execution.session.status == "stopped" else None,
            connection_id=execution.connection_id,
        )
        saved = await campaign_repo.save_campaign(campaign)
        await _emit_campaign_event(request, "workflow.campaign.created", saved)
        return _to_campaign_response(saved, chat_endpoint=execution.session.chat_endpoint)

    @router.get("/campaigns/{slug}", response_model=ResearchCampaignDetailResponse)
    async def get_campaign(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
        execution_repo: WorkflowExecutionRepository | None = Depends(
            resolve_optional_workflow_execution_repo
        ),
    ) -> ResearchCampaignDetailResponse:
        campaign = await repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None or not _is_research_campaign(campaign):
            raise HTTPException(status_code=404, detail="Campaign not found")
        refreshed = await _refresh_campaign_runtime(
            request=request,
            campaign=campaign,
            repo=repo,
            volundr_factory=volundr_factory,
            principal=principal,
        )
        artifacts_unavailable = False
        try:
            artifacts, canonical = await _load_campaign_artifacts(
                refreshed,
                settings=request.app.state.settings,
                bearer_token=extract_bearer_token(request),
            )
        except _CampaignArtifactsUnavailableError:
            artifacts, canonical = [], {}
            artifacts_unavailable = True
        # Stage state is derived FROM the artifacts: _derive_stage_state builds
        # `available_kinds` out of the list and marks every stage it cannot
        # satisfy as pending. So an empty list does not mean "nothing is done",
        # it means "nothing is known" — and persisting it rewrites a finished
        # campaign as though it had never started. 41 of 49 completed campaigns
        # were sitting at 0/N stages for exactly this reason.
        #
        # A timeout is one way to get an empty list; Mimir answering 200 with
        # nothing is another, and that one reaches here. Guard the condition
        # rather than the cause: never derive from an empty listing when what
        # is already stored records real progress.
        stage_progress_known = any(stage.status == "complete" for stage in refreshed.stage_state)
        if not artifacts_unavailable and not (not artifacts and stage_progress_known):
            stage_state = _derive_stage_state(
                refreshed.workflow_snapshot,
                artifacts,
                refreshed.status,
                refreshed.stage_state,
                slug=_artifact_slug(refreshed),
                execution=await _campaign_execution(refreshed, execution_repo, principal),
                metadata=refreshed.metadata,
            )
            if stage_state != refreshed.stage_state or (
                stage_state and stage_state[0].stage_id != refreshed.active_stage_id
            ):
                refreshed = await repo.save_campaign(
                    WorkflowCampaign(
                        **{
                            **refreshed.__dict__,
                            "stage_state": stage_state,
                            "active_stage_id": _active_stage_id(stage_state),
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
        return _to_campaign_detail_response(
            refreshed,
            artifacts,
            canonical,
            artifacts_unavailable=artifacts_unavailable,
        )

    @router.patch("/campaigns/{slug}", response_model=ResearchCampaignResponse)
    async def patch_campaign(
        slug: str,
        body: ResearchCampaignPatchBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> ResearchCampaignResponse:
        campaign = await repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None or not _is_research_campaign(campaign):
            raise HTTPException(status_code=404, detail="Campaign not found")

        next_slug = campaign.slug
        if body.slug is not None:
            next_slug = _normalize_campaign_slug(body.slug)
            if not next_slug:
                raise HTTPException(status_code=400, detail="Invalid campaign slug")
            if next_slug != campaign.slug:
                existing = await repo.get_campaign_by_slug(next_slug)
                if existing is not None and existing.id != campaign.id:
                    raise HTTPException(status_code=409, detail="Campaign slug already exists")

        next_status = campaign.status
        if body.status:
            try:
                next_status = WorkflowCampaignStatus(body.status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid status") from exc
        now = datetime.now(UTC)
        updated = WorkflowCampaign(
            **{
                **campaign.__dict__,
                "slug": next_slug,
                "name": body.name or campaign.name,
                "status": next_status,
                "metadata": {**campaign.metadata, **(body.metadata or {})},
                "updated_at": now,
                "completed_at": now
                if next_status == WorkflowCampaignStatus.COMPLETED and campaign.completed_at is None
                else campaign.completed_at,
            }
        )
        saved = await repo.save_campaign(updated)
        await _emit_campaign_event(request, "workflow.campaign.updated", saved)
        return _to_campaign_response(saved)

    @router.delete("/campaigns/{slug}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_campaign(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> None:
        campaign = await repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None or not _is_research_campaign(campaign):
            raise HTTPException(status_code=404, detail="Campaign not found")
        deleted = await repo.delete_campaign(campaign.id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Campaign not found")
        await request.app.state.event_bus.emit(
            TingEvent(
                event="workflow.campaign.deleted",
                owner_id=campaign.owner_id,
                data={
                    "campaign_id": str(campaign.id),
                    "slug": campaign.slug,
                    "name": campaign.name,
                    "session_id": campaign.session_id,
                    "workflow_id": str(campaign.workflow_id),
                },
            )
        )

    @router.get("/campaigns/{slug}/artifacts", response_model=list[CampaignArtifactResponse])
    async def list_campaign_artifacts(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> list[CampaignArtifactResponse]:
        campaign = await repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None or not _campaign_artifacts_accessible(campaign):
            raise HTTPException(status_code=404, detail="Campaign not found")
        try:
            artifacts, _canonical = await _load_campaign_artifacts(
                campaign,
                settings=request.app.state.settings,
                bearer_token=extract_bearer_token(request),
            )
        except _CampaignArtifactsUnavailableError as exc:
            # This route's whole payload IS the artifact list, so an empty 200
            # would be a lie. Say the store is unreachable and let the caller
            # retry.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Artifact store is not responding; artifacts are unknown",
            ) from exc
        return artifacts

    @router.get("/campaigns/{slug}/artifact", response_model=CampaignArtifactDetailResponse)
    async def get_campaign_artifact(
        slug: str,
        request: Request,
        path: str = Query(),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> CampaignArtifactDetailResponse:
        campaign = await repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None or not _campaign_artifacts_accessible(campaign):
            raise HTTPException(status_code=404, detail="Campaign not found")
        if not _campaign_owns_path(campaign, path):
            raise HTTPException(status_code=404, detail="Artifact not found")
        adapter = _campaign_knowledge(
            campaign, request.app.state.settings, bearer_token=extract_bearer_token(request)
        )
        if adapter is None:
            raise HTTPException(
                status_code=503,
                detail="No Mimir mount is configured for this campaign",
            )
        try:
            page = await adapter.get_page(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Artifact not found") from exc
        published_paths = await _published_paths(adapter, _artifact_slug(campaign))
        artifact = _artifact_response(page, published_paths, manifest_known=bool(published_paths))
        return CampaignArtifactDetailResponse(
            path=artifact.path,
            title=artifact.title,
            updated_at=artifact.updated_at,
            summary=artifact.summary,
            kind=artifact.kind,
            publish_state=artifact.publish_state,
            source_ids=artifact.source_ids,
            content=page.content,
        )

    return router


async def _durable_research_launch(
    *,
    request: Request,
    body: ResearchCampaignCreateBody,
    workflow: WorkflowDefinition,
    workflow_snapshot: dict[str, Any],
    campaign_id: UUID,
    campaign_name: str,
    session_name: str,
    prompt: str,
    principal: Principal,
    bearer_token: str | None,
    execution_repo: WorkflowExecutionRepository | None,
    volundr_factory: VolundrFactory,
) -> tuple[WorkflowLaunchExecution, str] | None:
    """Launch a research workflow that fans out as a durable parent execution.

    Returns ``None`` when the workflow needs no durable execution, so the
    caller keeps the plain launch. Otherwise reserves a ``WorkflowExecution``
    and spawns the parent through the same trusted path
    ``POST /workflow-executions`` uses — without it the coordinator persona's
    ``workflow_execution_*`` tools have no owner-bound runtime context and its
    Ravn crashes the moment the frame stage hands over.

    The parent keeps the campaign's own session name, so the launch slug —
    and with it the ``research/campaigns/<slug>/`` Mímir prefix the workflow
    writes to and the campaign slug callers address — is unchanged.
    """
    parent_node_id = durable_parent_node_id(workflow, workflow_snapshot)
    if parent_node_id is None:
        return None
    if execution_repo is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "This research workflow fans out through durable workflow executions, "
                "which are not enabled here (Ting workflow_execution.enabled)"
            ),
        )
    execution = new_parent_execution(
        request=request,
        principal=principal,
        workflow=workflow,
        execution_id=uuid4(),
        workflow_snapshot=workflow_snapshot,
        parent_node_id=parent_node_id,
        prompt=prompt,
        name=campaign_name,
        # One execution per campaign: the campaign id is fresh per request, so
        # this key never replays an earlier launch.
        launch_key=f"{_RESEARCH_SURFACE}:{campaign_id}",
        launch_request=body.model_dump(mode="json", by_alias=True),
    )
    try:
        reserved, _ = await execution_repo.reserve_parent_launch(execution)
    except ExecutionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    launch = parent_launch_body(
        reserved,
        request=request,
        prompt=prompt,
        session_name=session_name,
        model=body.model,
        connection_id=body.connection_id,
        repo=body.repo,
        branch=body.branch,
        definition=body.definition,
        workflow_version=workflow.version,
        gate_auto_forward_after=body.gate_auto_forward_after,
    )
    launched, _ = await launch_reserved_parent(
        request=request,
        workflow=workflow,
        reserved=reserved,
        launch=launch,
        execution_repo=execution_repo,
        volundr_factory=volundr_factory,
        principal=principal,
        bearer_token=bearer_token,
    )
    return launched, str(reserved.id)


async def _campaign_execution(
    campaign: WorkflowCampaign,
    execution_repo: WorkflowExecutionRepository | None,
    principal: Principal,
) -> WorkflowExecution | None:
    """The durable execution coordinating a campaign's fan-out, when it has one."""
    raw_id = str(campaign.metadata.get(_WORKFLOW_EXECUTION_ID_KEY) or "").strip()
    if execution_repo is None or not raw_id:
        return None
    try:
        return await execution_repo.get(
            UUID(raw_id),
            owner_id=principal.user_id,
            tenant_id=principal.tenant_id,
        )
    except Exception:
        # Stage evidence only; the campaign detail must not fail without it.
        logger.warning(
            "Could not read workflow execution %s for research campaign %s",
            raw_id,
            campaign.slug,
            exc_info=True,
        )
        return None


async def _resolve_research_workflow(
    *,
    repo: WorkflowRepository,
    owner_id: str,
    workflow_id: UUID | None,
    workflow_version: str | None = None,
) -> WorkflowDefinition:
    if workflow_id is not None:
        workflow = (
            await repo.get_workflow_version(workflow_id, version=workflow_version)
            if workflow_version
            else await repo.get_workflow(workflow_id)
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return workflow

    workflows = await repo.list_workflows(owner_id=owner_id)
    tagged = [workflow for workflow in workflows if _workflow_has_tag(workflow, "research")]
    for workflow in tagged:
        if workflow.name == _DEFAULT_RESEARCH_WORKFLOW_NAME:
            return await _selected_version(repo, workflow, workflow_version)
    if tagged:
        return await _selected_version(repo, tagged[0], workflow_version)
    for workflow in workflows:
        if workflow.name == _DEFAULT_RESEARCH_WORKFLOW_NAME:
            return await _selected_version(repo, workflow, workflow_version)
    raise HTTPException(status_code=404, detail="Research Campaign workflow not found")


async def _selected_version(
    repo: WorkflowRepository,
    workflow: WorkflowDefinition,
    version: str | None,
) -> WorkflowDefinition:
    if not version:
        return workflow
    selected = await repo.get_workflow_version(workflow.id, version=version)
    if selected is None:
        raise HTTPException(status_code=404, detail="Workflow version not found")
    return selected


def _graph_tags(graph: object) -> set[str]:
    """Normalized tags declared by a workflow graph.

    One reader for both shapes the graph arrives in: a live
    :class:`WorkflowDefinition` on the launch path, and the frozen
    ``workflow_snapshot`` a campaign keeps of the graph it launched.
    """
    if not isinstance(graph, dict):
        return set()
    raw_tags = graph.get("tags") or []
    return {str(item).strip().lower() for item in raw_tags if str(item).strip()}


def _workflow_has_tag(workflow: WorkflowDefinition, tag: str) -> bool:
    return tag.strip().lower() in _graph_tags(workflow.graph)


def _is_research_campaign(campaign: WorkflowCampaign) -> bool:
    """Whether this campaign is research work, regardless of who launched it.

    A workflow declares its own kind (``graph.tags``), which is the same
    question :func:`_resolve_research_workflow` asks when choosing what to
    launch — so creating and listing now agree on one definition.

    ``metadata.surface`` records the ENTRY POINT, not the kind. Reading kind
    off it hid every campaign started anywhere but the research page: 38 real
    Research Campaign runs launched over A2A were filtered out of the list
    while their artifacts stayed reachable by URL. The old name comparison had
    the matching flaw a name comparison always has — "Research Campaign Mimir"
    is tagged research and was excluded for spelling.

    The surface check survives only as an OR: a user who picks some untagged
    workflow on the research page still owns a research campaign by intent.
    """
    snapshot = campaign.workflow_snapshot if isinstance(campaign.workflow_snapshot, dict) else {}
    if _RESEARCH_TAG in _graph_tags(snapshot.get("graph")):
        return True
    return str(campaign.metadata.get("surface") or "").strip() == _RESEARCH_SURFACE


def _campaign_artifacts_accessible(campaign: WorkflowCampaign) -> bool:
    """Artifact routes serve research campaigns AND A2A-launched ones.

    A2A tasks expose their outputs through these routes as url parts, so an
    ``a2a``-surface campaign must be able to serve artifacts even though it
    is not a research campaign.
    """
    surface = str(campaign.metadata.get("surface") or "").strip()
    if surface == _A2A_SURFACE:
        return True
    return _is_research_campaign(campaign)


async def _reserve_slug(repo: WorkflowCampaignRepository, base_slug: str) -> str:
    slug = base_slug or "research"
    suffix = 2
    while await repo.get_campaign_by_slug(slug) is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _normalize_campaign_slug(value: str) -> str:
    return _slugify(value)[:96]


def _default_campaign_name(question: str) -> str:
    compact = " ".join(question.strip().split())
    if not compact:
        return "Research Campaign"
    return compact[:80]


def _campaign_name(body: ResearchCampaignCreateBody) -> str:
    explicit = " ".join((body.name or "").strip().split())
    if explicit:
        return explicit[:255]
    return _default_campaign_name(body.question)[:255]


def _build_campaign_prompt(body: ResearchCampaignCreateBody) -> str:
    lines = [
        body.question.strip(),
        "",
        "## Research Brief",
        f"- Mode: {body.mode}",
    ]
    campaign_name = _campaign_name(body)
    if campaign_name:
        lines.append(f"- Title: {campaign_name}")
    if body.audience:
        lines.append(f"- Audience: {body.audience}")
    if body.deliverable:
        lines.append(f"- Deliverable: {body.deliverable}")
    if body.success:
        lines.append(f"- Success criteria: {body.success}")
    if body.constraints:
        lines.append("- Constraints:")
        lines.extend(f"  - {constraint}" for constraint in body.constraints if constraint.strip())
    if body.monitoring_cadence:
        lines.append(f"- Monitoring cadence: {body.monitoring_cadence}")
    return "\n".join(lines).strip()


def _initial_stage_state(snapshot: dict[str, Any], now: datetime) -> list[CampaignStageState]:
    stages = workflow_stages(snapshot)
    result: list[CampaignStageState] = []
    for index, stage in enumerate(stages):
        result.append(
            CampaignStageState(
                stage_id=stage.id,
                label=stage.label,
                status="active" if index == 0 else "pending",
                started_at=now if index == 0 else None,
            )
        )
    return result


async def _refresh_campaign_runtime(
    *,
    request: Request,
    campaign: WorkflowCampaign,
    repo: WorkflowCampaignRepository,
    volundr_factory: VolundrFactory,
    principal: Principal,
) -> WorkflowCampaign:
    adapter = await _resolve_campaign_volundr_adapter(
        volundr_factory=volundr_factory,
        principal=principal,
        campaign=campaign,
    )
    if adapter is None:
        return campaign
    try:
        session = await adapter.get_session(campaign.session_id, principal=principal)
    except Exception:
        logger.warning(
            "Skipping runtime refresh for research campaign %s",
            campaign.slug,
            exc_info=True,
        )
        return campaign
    if session is None:
        return campaign
    now = datetime.now(UTC)
    metadata = campaign.metadata
    stage_state = campaign.stage_state
    active_stage_id = campaign.active_stage_id
    activity_state = str(getattr(session, "activity_state", "") or "").strip().lower()
    if activity_state == "error" and campaign.status not in _TERMINAL_CAMPAIGN_STATUSES:
        # A stage's runtime failed (Skuld reports a peer error as activity
        # state "error") while the pod itself keeps running. The session
        # status alone would keep this campaign "running" forever.
        next_status = WorkflowCampaignStatus.FAILED
        metadata, stage_state, active_stage_id = record_runtime_failure(
            campaign, getattr(session, "activity_metadata", {}) or {}, now=now
        )
    elif campaign.status in _TERMINAL_CAMPAIGN_STATUSES:
        # A finished or failed campaign keeps its outcome while its pod winds
        # down or lingers: a still-"running" session is not new progress, and
        # the "stopped" session a failed campaign's cleanup leaves behind is
        # not a completion.
        next_status = campaign.status
    else:
        next_status = _campaign_status_from_session(session.status, fallback=campaign.status)
    next_completed_at = campaign.completed_at
    if next_status == WorkflowCampaignStatus.COMPLETED and campaign.completed_at is None:
        next_completed_at = now
    if next_status == campaign.status and session.name == campaign.session_name:
        return campaign
    updated = WorkflowCampaign(
        **{
            **campaign.__dict__,
            "session_name": session.name,
            "status": next_status,
            "metadata": metadata,
            "stage_state": stage_state,
            "active_stage_id": active_stage_id,
            "updated_at": now,
            "last_activity_at": now,
            "completed_at": next_completed_at,
        }
    )
    saved = await repo.save_campaign(updated)
    event_name = "workflow.campaign.updated"
    if next_status == WorkflowCampaignStatus.COMPLETED:
        event_name = "workflow.campaign.completed"
    elif next_status == WorkflowCampaignStatus.FAILED:
        event_name = "workflow.campaign.failed"
    await _emit_campaign_event(request, event_name, saved)
    return saved


_TERMINAL_CAMPAIGN_STATUSES = frozenset(
    {WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED}
)


async def _resolve_campaign_volundr_adapter(
    *,
    volundr_factory: VolundrFactory,
    principal: Principal,
    campaign: WorkflowCampaign,
):
    connection_id = str(campaign.metadata.get("connection_id") or "").strip()
    if not connection_id:
        return await volundr_factory.primary_for_principal(principal)

    try:
        adapters = await volundr_factory.for_principal(principal)
    except Exception:
        logger.warning(
            "Skipping runtime refresh for research campaign %s; failed to load Volundr targets",
            campaign.slug,
            exc_info=True,
        )
        return None
    for adapter in adapters:
        if connection_id in {adapter.target_id, adapter.name}:
            return adapter
    logger.warning(
        "Skipping runtime refresh for research campaign %s; Volundr target %s is not visible",
        campaign.slug,
        connection_id,
    )
    return None


def _campaign_status_from_session(
    session_status: str,
    *,
    fallback: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
) -> WorkflowCampaignStatus:
    normalized = session_status.strip().lower()
    if normalized in {"creating", "starting", "queued"}:
        return WorkflowCampaignStatus.PENDING
    if normalized in {"running", "active", "busy"}:
        return WorkflowCampaignStatus.RUNNING
    if normalized in {"blocked", "waiting", "paused"}:
        return WorkflowCampaignStatus.BLOCKED
    if normalized in {"stopped", "completed", "complete", "succeeded", "success"}:
        return WorkflowCampaignStatus.COMPLETED
    if normalized in {"failed", "error", "cancelled", "canceled"}:
        return WorkflowCampaignStatus.FAILED
    return fallback


#: Where an A2A launch stores the slug the workflow actually wrote under.
#: Mirrors ``ting.api.a2a._WORKFLOW_SLUG_KEY``.
_A2A_WORKFLOW_SLUG_KEY = "a2a_workflow_slug"


def _artifact_slug(campaign: WorkflowCampaign) -> str:
    """The slug a campaign's Mímir pages actually live under.

    An A2A launch uniquifies the campaign slug with the campaign id
    (``<launch-slug>-<id.hex[:12]>``) so two runs of the same workflow do not
    collide. The workflow itself never sees that suffix — it writes to
    ``research/campaigns/<launch-slug>/``. Addressing artifacts by
    ``campaign.slug`` therefore looks in a directory that has never existed,
    and every A2A-launched research campaign reported zero artifacts. Since
    stage state is derived from that listing, they also displayed as unstarted.

    ``a2a.py`` already resolves paths this way; this is the same rule.
    """
    return str(campaign.metadata.get(_A2A_WORKFLOW_SLUG_KEY) or campaign.slug).strip()


class _CampaignArtifactsUnavailableError(Exception):
    """Mímir could not be read, so this campaign's artifacts are unknown.

    Distinct from "the campaign has no artifacts", which an empty list would
    otherwise assert. Callers that can still render something useful catch this
    and mark the response; callers that cannot let it surface.
    """

    def __init__(self, slug: str) -> None:
        super().__init__(f"artifact listing unavailable for campaign {slug!r}")
        self.slug = slug


async def _load_campaign_artifacts(
    campaign: WorkflowCampaign,
    *,
    settings: Any,
    bearer_token: str | None = None,
) -> tuple[list[CampaignArtifactResponse], dict[str, str]]:
    adapter = _campaign_knowledge(campaign, settings, bearer_token=bearer_token)
    if adapter is None:
        return [], {}

    slug = _artifact_slug(campaign)
    prefix = f"research/campaigns/{slug}/"
    try:
        pages = await adapter.list_pages(prefix=prefix)
    except httpx.TimeoutException:
        # Mímir lists pages by walking the corpus even when given a prefix, so
        # this call scales with the whole wiki rather than with one campaign's
        # handful of files. Past 30s it times out, and the exception used to
        # escape and 500 the entire detail route — which deleted the campaign
        # from the research page altogether. Four finished campaigns with every
        # artifact safely written were invisible for that reason alone.
        #
        # Degrade to "artifacts could not be read" rather than vanishing. The
        # caller is told which it is; nothing here claims the campaign is empty.
        logger.error(
            "Mimir timed out listing artifacts for research campaign %s "
            "(prefix=%s); returning the campaign without its artifact list",
            campaign.slug,
            prefix,
            exc_info=True,
        )
        raise _CampaignArtifactsUnavailableError(campaign.slug) from None
    page_paths = {page.path for page in pages}
    extra_paths = [
        f"learnings/research/{slug}.md",
        f"followups/research/{slug}.md",
    ]
    extra_pages: list[MimirPage] = []
    for path in extra_paths:
        if path in page_paths:
            continue
        try:
            extra_pages.append(await adapter.get_page(path))
        except FileNotFoundError:
            continue

    full_pages: list[MimirPage] = []
    for meta in pages:
        try:
            full_pages.append(await adapter.get_page(meta.path))
        except FileNotFoundError:
            continue
    full_pages.extend(extra_pages)

    published_paths = await _published_paths(adapter, slug)
    manifest_known = bool(published_paths)
    artifacts = [
        _artifact_response(page, published_paths, manifest_known=manifest_known)
        for page in sorted(full_pages, key=lambda page: page.meta.path)
    ]
    canonical: dict[str, str] = {}
    for artifact in artifacts:
        if artifact.kind and artifact.kind not in canonical:
            canonical[artifact.kind] = artifact.path
    return artifacts, canonical


async def _campaign_artifact_summaries(
    campaigns: list[WorkflowCampaign],
    *,
    settings: Any,
    bearer_token: str | None = None,
) -> list[CampaignArtifactSummaryResponse]:
    """Summarise a whole list of campaigns in three reads per mount.

    Everything the research cards show is derivable from page metadata: the
    kind comes from the path, the citations from ``source_ids``, and
    "published" from whether the campaign wrote a manifest. None of it needs a
    page body, and none of it needs a request per campaign.

    That last part is the point. Summarising each campaign on its own still
    cost four calls apiece, and Mímir serves on a single worker — eighteen
    campaigns meant seventy-odd requests queued behind each other, which took
    about as long as doing it serially. Listing the three prefixes once per
    mount and grouping by slug turns the whole list into three reads.
    """
    if not campaigns:
        return []

    # Campaigns can resolve to different mounts, so group by the adapter each
    # one lands on and read each mount once.
    adapters: list[tuple[MimirPort, list[int]]] = []
    for index, campaign in enumerate(campaigns):
        adapter = _campaign_knowledge(campaign, settings, bearer_token=bearer_token)
        if adapter is None:
            continue
        for known_adapter, indexes in adapters:
            if known_adapter is adapter or _same_mimir_mount(known_adapter, adapter):
                indexes.append(index)
                break
        else:
            adapters.append((adapter, [index]))

    summaries = [CampaignArtifactSummaryResponse(known=False) for _ in campaigns]
    for adapter, indexes in adapters:
        try:
            pages = [
                page
                for prefix in ("research/campaigns/", "learnings/research/", "followups/research/")
                for page in await adapter.list_pages(prefix=prefix)
            ]
        except Exception:
            # Same rule as the artifact listing: unknown is not empty.
            logger.warning(
                "Mimir could not be read while summarising %d research campaign(s)",
                len(indexes),
                exc_info=True,
            )
            continue
        for index in indexes:
            summaries[index] = _summarize_campaign_pages(campaigns[index], pages)
    return summaries


def _same_mimir_mount(left: MimirPort, right: MimirPort) -> bool:
    """True when two adapters read the same store, so one listing serves both."""
    left_url = getattr(left, "_base_url", None)
    right_url = getattr(right, "_base_url", None)
    if left_url is not None or right_url is not None:
        return left_url == right_url and getattr(left, "_mount", None) == getattr(
            right, "_mount", None
        )
    left_root = left.filesystem_root()
    right_root = right.filesystem_root()
    return left_root is not None and left_root == right_root


def _summarize_campaign_pages(
    campaign: WorkflowCampaign,
    pages: list[MimirPageMeta],
) -> CampaignArtifactSummaryResponse:
    """Tally one campaign's artifacts out of a mount-wide page listing."""
    slug = _artifact_slug(campaign)
    prefix = f"research/campaigns/{slug}/"
    owned = {f"learnings/research/{slug}.md", f"followups/research/{slug}.md"}
    mine = [page for page in pages if page.path.startswith(prefix) or page.path in owned]

    kinds = [(page.path, _classify_artifact_kind(page.path)) for page in mine]
    source_ids: set[str] = set()
    for page in mine:
        source_ids.update(page.source_ids or [])

    return CampaignArtifactSummaryResponse(
        artifact_count=len(mine),
        source_count=len(source_ids),
        critique_count=sum(1 for _, kind in kinds if kind in {"critique", "challenge", "skeptic"}),
        learning_count=sum(
            1 for path, kind in kinds if kind == "learning" or path.startswith("learnings/")
        ),
        follow_up_count=sum(
            1 for path, kind in kinds if kind == "followup" or path.startswith("followups/")
        ),
        # A campaign is published once it has written its manifest; that is
        # exactly what _published_paths reports, without reading the file.
        published=any(page.path == f"{prefix}manifest.md" for page in mine),
        known=True,
    )


async def _published_paths(adapter: MimirPort, slug: str) -> set[str]:
    manifest_path = f"research/campaigns/{slug}/manifest.md"
    try:
        content = await adapter.read_page(manifest_path)
    except FileNotFoundError:
        return set()
    published = set(_MANIFEST_PATH_RE.findall(content))
    published.add(manifest_path)
    return published


def _artifact_response(
    page: MimirPage,
    published_paths: set[str],
    *,
    manifest_known: bool,
) -> CampaignArtifactResponse:
    kind = _classify_artifact_kind(page.meta.path)
    publish_state = (
        "published"
        if page.meta.path in published_paths
        else ("unpublished" if manifest_known else "unknown")
    )
    return CampaignArtifactResponse(
        path=page.meta.path,
        title=page.meta.title or _title_from_path(page.meta.path),
        updated_at=page.meta.updated_at,
        kind=kind,
        publish_state=publish_state,
        source_ids=list(page.meta.source_ids),
        summary=page.meta.summary or None,
    )


def _classify_artifact_kind(path: str) -> str | None:
    if path.endswith("/brief.md"):
        return "brief"
    if path.endswith("/plan.md"):
        return "plan"
    if "/notes/" in path:
        return "note"
    if path.endswith("/analysis.md"):
        return "analysis"
    if path.endswith("/sources.md"):
        return "sources"
    if path.endswith("/critique.md"):
        return "critique"
    if path.endswith("/final.md"):
        return "final"
    if path.endswith("/manifest.md"):
        return "manifest"
    if path.startswith("learnings/research/"):
        return "learnings"
    if path.startswith("followups/research/"):
        return "followups"
    return None


def _title_from_path(path: str) -> str:
    name = Path(path).stem.replace("-", " ").replace("_", " ")
    return name.title() or path


def _workflow_stages(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Stage ids and labels in graph order (spec campaigns derive by stage id)."""
    return [{"id": stage.id, "label": stage.label} for stage in workflow_stages(snapshot)]


#: Artifact kinds each kind of research stage writes, keyed by a fragment of
#: the stage's stable identifiers (node id, persona ids, produced events) or —
#: only as a fallback — of its display label. Order matters twice: a stage
#: takes the first rule it matches, and a kind is credited to the first stage
#: (in graph order) that claims it, so an analysis stage's ``analysis.md`` is
#: never read as proof that a later synthesis stage finished.
_STAGE_ARTIFACT_RULES: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (("frame",), frozenset({"brief", "plan"})),
    (("analy",), frozenset({"analysis", "sources"})),
    (("explor", "evidence"), frozenset({"note", "sources"})),
    (("challeng", "critique", "skeptic"), frozenset({"critique"})),
    (("synth",), frozenset({"analysis", "final"})),
    (("curat",), frozenset({"learnings", "followups"})),
    (("publish",), frozenset({"manifest"})),
)


def _artifact_rule(texts: tuple[str, ...]) -> frozenset[str] | None:
    lowered = [text.lower() for text in texts if text]
    for fragments, kinds in _STAGE_ARTIFACT_RULES:
        if any(fragment in text for fragment in fragments for text in lowered):
            return kinds
    return None


def _stage_artifact_claims(stages: list[WorkflowStage]) -> dict[str, frozenset[str]]:
    """Credit each artifact kind to exactly one stage.

    Stages that declare their own evidence (artifact paths, a dispatched
    subworkflow) claim no kinds. The rest match on stable identifiers first;
    a stage none of whose identifiers match falls back to its label.
    """
    claimed: set[str] = set()
    claims: dict[str, frozenset[str]] = {}
    unmatched: list[WorkflowStage] = []
    for stage in stages:
        if stage.artifact_paths or stage.dispatches_subworkflows:
            continue
        kinds = _artifact_rule(stage.tokens)
        if kinds is None:
            unmatched.append(stage)
            continue
        claims[stage.id] = kinds - claimed
        claimed |= kinds
    for stage in unmatched:
        kinds = _artifact_rule((stage.label,))
        if kinds is None:
            continue
        claims[stage.id] = kinds - claimed
        claimed |= kinds
    return claims


def _subworkflow_joined(
    stage: WorkflowStage,
    execution: WorkflowExecution | None,
) -> bool:
    """True once the durable fan-out a stage dispatched has joined.

    The parent execution records the join as it resumes the parent session
    with the subworkflow's joined event (``research.threads.joined`` for the
    research graphs); a completed execution has necessarily joined too.
    """
    if execution is None or execution.parent_node_id not in stage.dispatches_subworkflows:
        return False
    return (
        execution.suspension_reason == CHILDREN_JOINED_SUSPENSION_REASON
        or execution.state == ExecutionState.COMPLETED
    )


def _derive_stage_state(
    snapshot: dict[str, Any],
    artifacts: list[CampaignArtifactResponse],
    status: WorkflowCampaignStatus,
    previous: list[CampaignStageState],
    *,
    slug: str = "",
    execution: WorkflowExecution | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[CampaignStageState]:
    """Derive stage progress from the evidence each stage leaves behind.

    Per stage, strongest evidence first:

    1. ``artifactPaths`` declared on the stage node — all must exist;
    2. a stage that dispatches a subworkflow — its children have joined;
    3. the artifact kinds the stage is credited with (see
       :func:`_stage_artifact_claims`) — any one exists.

    A stage that finished proves its upstream stages finished (closed in
    :func:`render_stage_state`), and a failed campaign marks the stage the
    runtime attributed the failure to.
    """
    stages = workflow_stages(snapshot)
    available_kinds = {artifact.kind for artifact in artifacts if artifact.kind}
    available_paths = {artifact.path for artifact in artifacts}
    claims = _stage_artifact_claims(stages)
    completed: set[str] = set()
    for stage in stages:
        if stage.artifact_paths:
            required = {path.replace("{slug}", slug) for path in stage.artifact_paths}
            met = bool(slug) and required <= available_paths
        elif stage.dispatches_subworkflows:
            met = _subworkflow_joined(stage, execution)
        else:
            met = bool(claims.get(stage.id, frozenset()) & available_kinds)
            if not met and status == WorkflowCampaignStatus.COMPLETED:
                met = "complete" in stage.label.lower()
        if met:
            completed.add(stage.id)
    campaign_metadata = metadata or {}
    return render_stage_state(
        stages,
        completed,
        status=status,
        previous=previous,
        now=datetime.now(UTC),
        failed_stage_id=str(campaign_metadata.get(FAILURE_STAGE_KEY) or "") or None,
        failure_reason=str(campaign_metadata.get(FAILURE_ERROR_KEY) or "") or None,
    )


def _active_stage_id(stage_state: list[CampaignStageState]) -> str | None:
    for stage in stage_state:
        if stage.status in {"active", "blocked", "failed"}:
            return stage.stage_id
    return stage_state[-1].stage_id if stage_state else None


def _campaign_knowledge(
    campaign: WorkflowCampaign, settings: Any, *, bearer_token: str | None = None
) -> MimirPort | None:
    return resolve_mimir_workload(
        workflow_mimir_from_snapshot(campaign.workflow_snapshot),
        hosted_url=settings.dispatch.flock.mimir_hosted_url,
        registry_path=settings.dispatch.flock.mimir_registry_path,
        bearer_token=bearer_token,
    )


def _campaign_owns_path(campaign: WorkflowCampaign, path: str) -> bool:
    # Same slug the pages were written under, not the uniquified campaign
    # slug — otherwise every real artifact path fails this check.
    slug = _artifact_slug(campaign)

    if str(campaign.metadata.get("surface") or "").strip() == _A2A_SURFACE:
        workflow_slug = str(campaign.metadata.get("a2a_workflow_slug") or campaign.slug).strip()
        declared = workflow_artifact_paths_from_snapshot(
            campaign.workflow_snapshot,
            slug=workflow_slug,
        )
        # A graph that declares paths is authoritative — honour it exactly.
        # Most graphs declare none: `artifactPaths` is optional and absent from
        # every seeded research workflow, which made `path in declared` false
        # for every path and 404'd every artifact of every A2A campaign, even
        # though the listing route found them by prefix and showed them. Fall
        # back to the same slug scoping that listing uses, so the two agree.
        if declared:
            return path in declared

    if path.startswith(f"research/campaigns/{slug}/"):
        return True
    return path in {
        f"learnings/research/{slug}.md",
        f"followups/research/{slug}.md",
    }


async def _emit_campaign_event(request: Request, event: str, campaign: WorkflowCampaign) -> None:
    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus is None:
        return
    await event_bus.emit(
        TingEvent(
            event=event,
            owner_id=campaign.owner_id,
            data={
                "campaign_id": str(campaign.id),
                "slug": campaign.slug,
                "name": campaign.name,
                "status": campaign.status.value,
                "session_id": campaign.session_id,
                "workflow_id": str(campaign.workflow_id),
                "active_stage_id": campaign.active_stage_id,
            },
        )
    )


def _to_stage_response(stage: CampaignStageState) -> CampaignStageStateResponse:
    return CampaignStageStateResponse(
        stage_id=stage.stage_id,
        label=stage.label,
        status=stage.status,
        started_at=stage.started_at,
        completed_at=stage.completed_at,
        reason=stage.reason,
    )


def _to_campaign_response(
    campaign: WorkflowCampaign,
    *,
    chat_endpoint: str | None = None,
    artifact_summary: CampaignArtifactSummaryResponse | None = None,
) -> ResearchCampaignResponse:
    return ResearchCampaignResponse(
        id=str(campaign.id),
        slug=campaign.slug,
        name=campaign.name,
        owner_id=campaign.owner_id,
        workflow_id=str(campaign.workflow_id),
        workflow_version=campaign.workflow_version,
        workflow_name=campaign.workflow_name,
        session_id=campaign.session_id,
        session_name=campaign.session_name,
        chat_endpoint=chat_endpoint,
        status=campaign.status.value,
        active_stage_id=campaign.active_stage_id,
        stage_state=[_to_stage_response(stage) for stage in campaign.stage_state],
        metadata=campaign.metadata,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        last_activity_at=campaign.last_activity_at,
        completed_at=campaign.completed_at,
        artifact_summary=artifact_summary,
    )


def _to_campaign_detail_response(
    campaign: WorkflowCampaign,
    artifacts: list[CampaignArtifactResponse],
    canonical: dict[str, str],
    *,
    artifacts_unavailable: bool = False,
) -> ResearchCampaignDetailResponse:
    base = _to_campaign_response(campaign)
    return ResearchCampaignDetailResponse(
        **base.model_dump(),
        artifacts=artifacts,
        canonical_artifacts=canonical,
        artifacts_unavailable=artifacts_unavailable,
    )
