"""REST API for research campaigns launched through Ting workflows."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from mimir.adapters.markdown import MarkdownMimirAdapter
from niuu.domain.mimir import MimirPage
from niuu.domain.models import Principal
from niuu.ports.mimir import MimirPort
from niuu.utils import import_class, resolve_secret_kwargs
from ravn.adapters.mimir.http import HttpMimirAdapter
from ravn.domain.mimir import MimirAuth
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflows import (
    WorkflowLaunchBody,
    launch_workflow_execution,
    resolve_workflow_repo,
)
from ting.domain.models import (
    CampaignStageState,
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDefinition,
)
from ting.domain.services.dispatch_service import (
    _normalize_mimir_workload_config,
    _resolve_mimir_registry_refs,
)
from ting.domain.utils import _session_name, _slugify
from ting.domain.workflow_snapshot import (
    workflow_artifact_paths_from_snapshot,
    workflow_mimir_from_snapshot,
)
from ting.ports.event_bus import TingEvent
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository

_DEFAULT_RESEARCH_WORKFLOW_NAME = "Research Campaign"
_RESEARCH_SURFACE = "ting.research"
_A2A_SURFACE = "a2a"
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

    model_config = {"populate_by_name": True}


class ResearchCampaignDetailResponse(ResearchCampaignResponse):
    artifacts: list[CampaignArtifactResponse] = Field(default_factory=list)
    canonical_artifacts: dict[str, str] = Field(
        default_factory=dict,
        serialization_alias="canonicalArtifacts",
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
        return [_to_campaign_response(campaign) for campaign in refreshed]

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
    ) -> ResearchCampaignResponse:
        workflow = await _resolve_research_workflow(
            repo=workflow_repo,
            owner_id=principal.user_id,
            workflow_id=body.workflow_id,
        )
        initiative_prompt = _build_campaign_prompt(body)
        campaign_name = _campaign_name(body)
        session_name = _session_name(campaign_name) or "research-campaign"
        launch = WorkflowLaunchBody(
            prompt=initiative_prompt,
            sessionName=session_name,
            repo=body.repo,
            branch=body.branch,
            connectionId=body.connection_id,
            model=body.model,
            definition=body.definition,
            gateAutoForwardAfter=body.gate_auto_forward_after,
        )
        execution = await launch_workflow_execution(
            request=request,
            workflow=workflow,
            launch=launch,
            volundr_factory=volundr_factory,
            principal=principal,
            bearer_token=bearer_token,
        )

        slug = await _reserve_slug(campaign_repo, execution.slug)
        now = datetime.now(UTC)
        stage_state = _initial_stage_state(execution.workflow_snapshot, now)
        campaign = WorkflowCampaign(
            id=uuid4(),
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
        artifacts, canonical = await _load_campaign_artifacts(
            refreshed,
            settings=request.app.state.settings,
        )
        stage_state = _derive_stage_state(
            refreshed.workflow_snapshot,
            artifacts,
            refreshed.status,
            refreshed.stage_state,
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
        return _to_campaign_detail_response(refreshed, artifacts, canonical)

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
        artifacts, _canonical = await _load_campaign_artifacts(
            campaign,
            settings=request.app.state.settings,
        )
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
        adapter = _resolve_campaign_mimir_port(campaign, request.app.state.settings)
        if adapter is None:
            raise HTTPException(
                status_code=503,
                detail="No Mimir mount is configured for this campaign",
            )
        try:
            page = await adapter.get_page(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Artifact not found") from exc
        published_paths = await _published_paths(adapter, campaign.slug)
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


async def _resolve_research_workflow(
    *,
    repo: WorkflowRepository,
    owner_id: str,
    workflow_id: UUID | None,
) -> WorkflowDefinition:
    if workflow_id is not None:
        workflow = await repo.get_workflow(workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return workflow

    workflows = await repo.list_workflows(owner_id=owner_id)
    tagged = [workflow for workflow in workflows if _workflow_has_tag(workflow, "research")]
    for workflow in tagged:
        if workflow.name == _DEFAULT_RESEARCH_WORKFLOW_NAME:
            return workflow
    if tagged:
        return tagged[0]
    for workflow in workflows:
        if workflow.name == _DEFAULT_RESEARCH_WORKFLOW_NAME:
            return workflow
    raise HTTPException(status_code=404, detail="Research Campaign workflow not found")


def _workflow_has_tag(workflow: WorkflowDefinition, tag: str) -> bool:
    graph = workflow.graph if isinstance(workflow.graph, dict) else {}
    raw_tags = graph.get("tags") or []
    normalized = {str(item).strip().lower() for item in raw_tags if str(item).strip()}
    return tag.strip().lower() in normalized


def _is_research_campaign(campaign: WorkflowCampaign) -> bool:
    surface = str(campaign.metadata.get("surface") or "").strip()
    if surface:
        return surface == _RESEARCH_SURFACE
    return (
        campaign.workflow_name == _DEFAULT_RESEARCH_WORKFLOW_NAME
        or campaign.metadata.get("question") is not None
    )


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
    stages = _workflow_stages(snapshot)
    result: list[CampaignStageState] = []
    for index, stage in enumerate(stages):
        result.append(
            CampaignStageState(
                stage_id=stage["id"],
                label=stage["label"],
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
    next_status = _campaign_status_from_session(session.status, fallback=campaign.status)
    next_completed_at = campaign.completed_at
    now = datetime.now(UTC)
    if next_status == WorkflowCampaignStatus.COMPLETED and campaign.completed_at is None:
        next_completed_at = now
    if next_status == campaign.status and session.name == campaign.session_name:
        return campaign
    updated = WorkflowCampaign(
        **{
            **campaign.__dict__,
            "session_name": session.name,
            "status": next_status,
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


async def _load_campaign_artifacts(
    campaign: WorkflowCampaign,
    *,
    settings: Any,
) -> tuple[list[CampaignArtifactResponse], dict[str, str]]:
    adapter = _resolve_campaign_mimir_port(campaign, settings)
    if adapter is None:
        return [], {}

    prefix = f"research/campaigns/{campaign.slug}/"
    pages = await adapter.list_pages(prefix=prefix)
    page_paths = {page.path for page in pages}
    extra_paths = [
        f"learnings/research/{campaign.slug}.md",
        f"followups/research/{campaign.slug}.md",
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

    published_paths = await _published_paths(adapter, campaign.slug)
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
    graph = snapshot.get("graph") if isinstance(snapshot, dict) else None
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    stages: list[dict[str, str]] = []
    for node in nodes or []:
        if not isinstance(node, dict) or node.get("kind") != "stage":
            continue
        stages.append(
            {
                "id": str(node.get("id") or ""),
                "label": str(node.get("label") or node.get("id") or "Stage"),
            }
        )
    return stages


def _derive_stage_state(
    snapshot: dict[str, Any],
    artifacts: list[CampaignArtifactResponse],
    status: WorkflowCampaignStatus,
    previous: list[CampaignStageState],
) -> list[CampaignStageState]:
    stages = _workflow_stages(snapshot)
    previous_map = {stage.stage_id: stage for stage in previous}
    available_kinds = {artifact.kind for artifact in artifacts if artifact.kind}
    now = datetime.now(UTC)
    derived: list[CampaignStageState] = []
    first_incomplete: int | None = None

    for index, stage in enumerate(stages):
        requirement_met = _stage_requirement_met(stage["label"], available_kinds, status)
        prior = previous_map.get(stage["id"])
        if requirement_met:
            derived.append(
                CampaignStageState(
                    stage_id=stage["id"],
                    label=stage["label"],
                    status="complete",
                    started_at=prior.started_at if prior else None,
                    completed_at=(prior.completed_at if prior and prior.completed_at else now),
                    reason=prior.reason if prior else None,
                )
            )
            continue
        if first_incomplete is None:
            first_incomplete = index
        derived.append(
            CampaignStageState(
                stage_id=stage["id"],
                label=stage["label"],
                status="pending",
                started_at=prior.started_at if prior else None,
                completed_at=prior.completed_at if prior else None,
                reason=prior.reason if prior else None,
            )
        )

    if first_incomplete is not None:
        current = derived[first_incomplete]
        current_status = "active"
        if status == WorkflowCampaignStatus.BLOCKED:
            current_status = "blocked"
        elif status == WorkflowCampaignStatus.FAILED:
            current_status = "failed"
        derived[first_incomplete] = CampaignStageState(
            stage_id=current.stage_id,
            label=current.label,
            status=current_status,
            started_at=current.started_at or now,
            completed_at=current.completed_at,
            reason=current.reason,
        )
    elif derived and status == WorkflowCampaignStatus.COMPLETED:
        last = derived[-1]
        derived[-1] = CampaignStageState(
            stage_id=last.stage_id,
            label=last.label,
            status="complete",
            started_at=last.started_at,
            completed_at=last.completed_at or now,
            reason=last.reason,
        )
    return derived


def _stage_requirement_met(
    label: str,
    kinds: set[str | None],
    status: WorkflowCampaignStatus,
) -> bool:
    lowered = label.lower()
    if "frame" in lowered:
        return "brief" in kinds or "plan" in kinds
    if "explore" in lowered or "evidence" in lowered:
        return "note" in kinds or "sources" in kinds
    if "challenge" in lowered or "critique" in lowered:
        return "critique" in kinds
    if "synth" in lowered:
        return "analysis" in kinds or "final" in kinds
    if "curate" in lowered:
        return "learnings" in kinds or "followups" in kinds
    if "publish" in lowered:
        return "manifest" in kinds
    if "complete" in lowered:
        return status == WorkflowCampaignStatus.COMPLETED
    return False


def _active_stage_id(stage_state: list[CampaignStageState]) -> str | None:
    for stage in stage_state:
        if stage.status in {"active", "blocked", "failed"}:
            return stage.stage_id
    return stage_state[-1].stage_id if stage_state else None


def _resolve_campaign_mimir_port(campaign: WorkflowCampaign, settings: Any) -> MimirPort | None:
    normalized = _resolve_mimir_registry_refs(
        _normalize_mimir_workload_config(
            workflow_mimir_from_snapshot(campaign.workflow_snapshot),
            hosted_url=settings.dispatch.flock.mimir_hosted_url,
        ),
        registry_path=settings.dispatch.flock.mimir_registry_path,
    )
    default_mounts = list(normalized.get("default_mounts") or [])
    registry_refs = list(normalized.get("registry_refs") or [])
    ephemeral_locals = list(normalized.get("ephemeral_locals") or [])

    for collection in (ephemeral_locals, registry_refs):
        for ref in collection:
            if not isinstance(ref, dict):
                continue
            mount_name = str(ref.get("mount_name") or "")
            if default_mounts and mount_name not in default_mounts:
                continue
            url = str(ref.get("url") or "").strip()
            path = str(ref.get("path") or "").strip()
            if url:
                return HttpMimirAdapter(base_url=url, auth=_mimir_http_auth(settings))
            if path:
                return MarkdownMimirAdapter(root=path)

    hosted_url = str(settings.dispatch.flock.mimir_hosted_url or "").strip()
    if hosted_url:
        return HttpMimirAdapter(base_url=hosted_url, auth=_mimir_http_auth(settings))
    return None


def _mimir_http_auth(settings: Any) -> MimirAuth | None:
    config = getattr(getattr(settings, "volundr", None), "auth", None)
    if config is None:
        return None

    try:
        cls = import_class(config.adapter)
        kwargs = resolve_secret_kwargs(config.kwargs, config.secret_kwargs_env)
        adapter = cls(**kwargs)
        header = str(adapter.headers().get("Authorization") or "")
    except Exception as exc:
        logger.warning("Unable to build Mimir HTTP auth from Ting outbound auth: %s", exc)
        return None

    prefix = "Bearer "
    if not header.startswith(prefix):
        return None
    token = header[len(prefix) :].strip()
    if not token:
        return None
    return MimirAuth(type="bearer", token=token)


def _campaign_owns_path(campaign: WorkflowCampaign, path: str) -> bool:
    if str(campaign.metadata.get("surface") or "").strip() == _A2A_SURFACE:
        workflow_slug = str(campaign.metadata.get("a2a_workflow_slug") or campaign.slug).strip()
        declared = workflow_artifact_paths_from_snapshot(
            campaign.workflow_snapshot,
            slug=workflow_slug,
        )
        return path in declared

    slug = campaign.slug
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
    )


def _to_campaign_detail_response(
    campaign: WorkflowCampaign,
    artifacts: list[CampaignArtifactResponse],
    canonical: dict[str, str],
) -> ResearchCampaignDetailResponse:
    base = _to_campaign_response(campaign)
    return ResearchCampaignDetailResponse(
        **base.model_dump(),
        artifacts=artifacts,
        canonical_artifacts=canonical,
    )
