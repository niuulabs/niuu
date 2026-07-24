"""REST API for workflow-backed specification campaigns."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from niuu.domain.mimir import MimirPage
from niuu.domain.models import Principal
from niuu.ports.mimir import MimirPort
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import (
    CampaignArtifactDetailResponse,
    CampaignArtifactResponse,
    ResearchCampaignDetailResponse,
    ResearchCampaignResponse,
    _active_stage_id,
    _campaign_status_from_session,
    _emit_campaign_event,
    _initial_stage_state,
    _normalize_campaign_slug,
    _refresh_campaign_runtime,
    _resolve_campaign_mimir_port,
    _resolve_campaign_volundr_adapter,
    _title_from_path,
    _to_campaign_detail_response,
    _to_campaign_response,
    _workflow_has_tag,
    _workflow_stages,
    resolve_workflow_campaign_repo,
)
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
from ting.domain.utils import _session_name, _slugify
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)

_DEFAULT_SPEC_WORKFLOW_NAME = "Specification Stack"
_SPEC_SURFACE = "ting.specs"
_PENDING_GATES_METADATA_KEY = "pending_workflow_gates"
_SPEC_GATE_NODE_IDS = (
    "spec-prd-gate",
    "spec-srd-gate",
    "spec-sdd-gate",
    "spec-breakdown-gate",
)
_PENDING_GATE_STATUSES = {"", "pending", "open", "waiting", "help_needed", "blocked"}
_SPEC_MANIFEST_PATH_RE = re.compile(r"specifications/[A-Za-z0-9._/-]+\.md")


class SpecCampaignCreateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    name: str | None = Field(default=None, max_length=255)
    workflow_id: UUID | None = Field(default=None, alias="workflowId")
    repo: str = Field(default="", max_length=500)
    repos: list[str] = Field(default_factory=list)
    branch: str = Field(default="", max_length=255)
    context: str = Field(default="", max_length=25_000)
    connection_id: str | None = Field(default=None, alias="connectionId", max_length=255)

    model_config = {"populate_by_name": True}


class SpecReviewBody(BaseModel):
    decision: Literal["approve", "approved", "changes_requested", "change_requested"]
    notes: str = Field(default="", max_length=25_000)
    gate_id: str | None = Field(default=None, alias="gateId", max_length=255)
    node_id: str | None = Field(default=None, alias="nodeId", max_length=255)

    model_config = {"populate_by_name": True}


def create_specs_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/specs", tags=["Specs"])

    @router.get("/campaigns", response_model=list[ResearchCampaignResponse])
    async def list_campaigns(
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> list[ResearchCampaignResponse]:
        campaigns = [
            campaign
            for campaign in await repo.list_campaigns(owner_id=principal.user_id)
            if _is_spec_campaign(campaign)
        ]
        refreshed = []
        for campaign in campaigns:
            campaign = await _refresh_campaign_runtime(
                request=request,
                campaign=campaign,
                repo=repo,
                volundr_factory=volundr_factory,
                principal=principal,
            )
            campaign = await _sync_pending_spec_gates(
                campaign=campaign,
                repo=repo,
                volundr_factory=volundr_factory,
                principal=principal,
                bearer_token=bearer_token,
            )
            refreshed.append(campaign)
        return [_to_campaign_response(campaign) for campaign in refreshed]

    @router.post(
        "/campaigns",
        response_model=ResearchCampaignResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_campaign(
        body: SpecCampaignCreateBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        workflow_repo: WorkflowRepository = Depends(resolve_workflow_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> ResearchCampaignResponse:
        workflow = await _resolve_spec_workflow(
            repo=workflow_repo,
            owner_id=principal.user_id,
            workflow_id=body.workflow_id,
        )
        repos = _request_repos(body)
        campaign_name = _campaign_name(body)
        session_name = _session_name(campaign_name) or "specification-stack"
        launch = WorkflowLaunchBody(
            prompt=_build_spec_prompt(body, repos=repos),
            sessionName=session_name,
            repo=repos[0] if repos else "",
            branch=body.branch,
            connectionId=body.connection_id,
            provenance={
                "surface": _SPEC_SURFACE,
                "repos": repos,
                "branch": body.branch,
            },
        )
        execution = await launch_workflow_execution(
            request=request,
            workflow=workflow,
            launch=launch,
            volundr_factory=volundr_factory,
            principal=principal,
            bearer_token=bearer_token,
        )

        slug = await _reserve_spec_slug(campaign_repo, execution.slug or _slugify(campaign_name))
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
                "surface": _SPEC_SURFACE,
                "prompt": body.prompt,
                "context": body.context,
                "repo": repos[0] if repos else "",
                "repos": repos,
                "branch": body.branch,
                "connection_id": body.connection_id,
                "cluster_name": execution.session.cluster_name,
                "artifact_prefix": f"specifications/{slug}/",
            },
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            completed_at=now if execution.session.status == "stopped" else None,
            connection_id=execution.connection_id,
        )
        saved = await campaign_repo.save_campaign(campaign)
        await _emit_campaign_event(request, "workflow.campaign.created", saved)
        return _to_campaign_response(saved)

    @router.get("/campaigns/{slug}", response_model=ResearchCampaignDetailResponse)
    async def get_campaign(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> ResearchCampaignDetailResponse:
        campaign = await _get_spec_campaign(repo, slug, principal)
        refreshed = await _refresh_campaign_runtime(
            request=request,
            campaign=campaign,
            repo=repo,
            volundr_factory=volundr_factory,
            principal=principal,
        )
        refreshed = await _sync_pending_spec_gates(
            campaign=refreshed,
            repo=repo,
            volundr_factory=volundr_factory,
            principal=principal,
            bearer_token=bearer_token,
        )
        artifacts, canonical = await _load_spec_artifacts(
            refreshed,
            settings=request.app.state.settings,
        )
        stage_state = _derive_spec_stage_state(
            refreshed.workflow_snapshot,
            artifacts,
            refreshed.status,
            refreshed.stage_state,
            _stored_spec_gates(refreshed),
        )
        if stage_state != refreshed.stage_state or (
            stage_state and _active_stage_id(stage_state) != refreshed.active_stage_id
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

    @router.delete("/campaigns/{slug}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_campaign(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> None:
        campaign = await _get_spec_campaign(repo, slug, principal)
        deleted = await repo.delete_campaign(campaign.id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Campaign not found")
        await _emit_campaign_event(request, "workflow.campaign.deleted", campaign)

    @router.get("/campaigns/{slug}/artifacts", response_model=list[CampaignArtifactResponse])
    async def list_campaign_artifacts(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> list[CampaignArtifactResponse]:
        campaign = await _get_spec_campaign(repo, slug, principal)
        artifacts, _canonical = await _load_spec_artifacts(
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
        campaign = await _get_spec_campaign(repo, slug, principal)
        if not _spec_campaign_owns_path(campaign.slug, path):
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
        published_paths = await _spec_published_paths(adapter, campaign.slug)
        artifact = _spec_artifact_response(
            page,
            published_paths,
            manifest_known=bool(published_paths),
        )
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

    @router.post("/campaigns/{slug}/review", response_model=ResearchCampaignResponse)
    async def review_campaign(
        slug: str,
        body: SpecReviewBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> ResearchCampaignResponse:
        campaign = await _get_spec_campaign(repo, slug, principal)
        normalized_decision = body.decision.strip().lower()
        if (
            normalized_decision in {"changes_requested", "change_requested"}
            and not body.notes.strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="notes are required when requesting changes",
            )
        adapter = await _resolve_campaign_volundr_adapter(
            volundr_factory=volundr_factory,
            principal=principal,
            campaign=campaign,
        )
        if adapter is None:
            raise HTTPException(status_code=503, detail="No Volundr connection is available")

        gates = await _load_spec_gates(
            campaign=campaign,
            adapter=adapter,
            principal=principal,
            bearer_token=bearer_token,
        )
        gate = _select_review_gate(gates, gate_id=body.gate_id, node_id=body.node_id)
        if gate is None:
            raise HTTPException(status_code=404, detail="No pending specification gate found")

        gate_id = _gate_id(gate)
        if not gate_id:
            raise HTTPException(status_code=404, detail="No pending specification gate found")
        decision = "APPROVE"
        if normalized_decision in {"changes_requested", "change_requested"}:
            decision = "CHANGES_REQUESTED"
        try:
            await adapter.resolve_workflow_gate(
                campaign.session_id,
                gate_id,
                decision,
                notes=body.notes.strip(),
                source=_SPEC_SURFACE,
                auth_token=bearer_token,
                principal=principal,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to resolve specification review gate: {exc}",
            ) from exc

        next_gates = [
            existing
            for existing in _stored_spec_gates(campaign)
            if _gate_id(existing) != gate_id and _gate_node_id(existing) != _gate_node_id(gate)
        ]
        now = datetime.now(UTC)
        updated = WorkflowCampaign(
            **{
                **campaign.__dict__,
                "metadata": {
                    **campaign.metadata,
                    _PENDING_GATES_METADATA_KEY: next_gates,
                    "latest_spec_review": {
                        "decision": "changes_requested"
                        if decision == "CHANGES_REQUESTED"
                        else "approve",
                        "notes": body.notes.strip(),
                        "gate_id": gate_id,
                        "node_id": _gate_node_id(gate),
                        "reviewed_at": now.isoformat(),
                    },
                },
                "updated_at": now,
                "last_activity_at": now,
            }
        )
        saved = await repo.save_campaign(updated)
        await _emit_campaign_event(request, "workflow.campaign.updated", saved)
        return _to_campaign_response(saved)

    return router


async def _get_spec_campaign(
    repo: WorkflowCampaignRepository,
    slug: str,
    principal: Principal,
) -> WorkflowCampaign:
    campaign = await repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
    if campaign is None or not _is_spec_campaign(campaign):
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


async def _resolve_spec_workflow(
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
    tagged = [workflow for workflow in workflows if _workflow_has_tag(workflow, "specification")]
    for workflow in tagged:
        if workflow.name == _DEFAULT_SPEC_WORKFLOW_NAME:
            return workflow
    if tagged:
        return tagged[0]
    for workflow in workflows:
        if workflow.name == _DEFAULT_SPEC_WORKFLOW_NAME:
            return workflow
    raise HTTPException(status_code=404, detail="Specification Stack workflow not found")


def _is_spec_campaign(campaign: WorkflowCampaign) -> bool:
    surface = str(campaign.metadata.get("surface") or "").strip()
    if surface:
        return surface == _SPEC_SURFACE
    return campaign.workflow_name == _DEFAULT_SPEC_WORKFLOW_NAME


def _request_repos(body: SpecCampaignCreateBody) -> list[str]:
    repos: list[str] = []
    for repo in [body.repo, *body.repos]:
        normalized = " ".join(str(repo or "").strip().split())
        if normalized and normalized not in repos:
            repos.append(normalized)
    return repos


def _campaign_name(body: SpecCampaignCreateBody) -> str:
    explicit = " ".join((body.name or "").strip().split())
    if explicit:
        return explicit[:255]
    compact = " ".join(body.prompt.strip().split())
    return (compact[:80] or "Specification Stack")[:255]


def _build_spec_prompt(body: SpecCampaignCreateBody, *, repos: list[str]) -> str:
    lines = [
        body.prompt.strip(),
        "",
        "## Specification Request",
        "- Produce the PRD, SRD, SDD, and implementation breakdown using the "
        "Specification Stack workflow.",
        "- Pause at each review gate and wait for Ting feedback before continuing.",
    ]
    campaign_name = _campaign_name(body)
    if campaign_name:
        lines.append(f"- Title: {campaign_name}")
    if repos:
        lines.append("- Repositories:")
        lines.extend(f"  - {repo}" for repo in repos)
    else:
        lines.append("- Repository: none selected")
    if body.branch:
        lines.append(f"- Branch: {body.branch}")
    if body.context.strip():
        lines.extend(["", "## Additional Context", body.context.strip()])
    return "\n".join(lines).strip()


async def _reserve_spec_slug(repo: WorkflowCampaignRepository, base_slug: str) -> str:
    base = _normalize_campaign_slug(base_slug) or "specification"
    slug = base
    suffix = 2
    while await repo.get_campaign_by_slug(slug) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


async def _sync_pending_spec_gates(
    *,
    campaign: WorkflowCampaign,
    repo: WorkflowCampaignRepository,
    volundr_factory: VolundrFactory,
    principal: Principal,
    bearer_token: str | None,
) -> WorkflowCampaign:
    adapter = await _resolve_campaign_volundr_adapter(
        volundr_factory=volundr_factory,
        principal=principal,
        campaign=campaign,
    )
    if adapter is None:
        return campaign
    try:
        gates = await _load_spec_gates(
            campaign=campaign,
            adapter=adapter,
            principal=principal,
            bearer_token=bearer_token,
        )
    except Exception:
        logger.debug("Failed to load spec gates for campaign %s", campaign.slug, exc_info=True)
        return campaign
    stored = _stored_spec_gates(campaign)
    if gates == stored:
        return campaign
    return await repo.save_campaign(
        WorkflowCampaign(
            **{
                **campaign.__dict__,
                "metadata": {**campaign.metadata, _PENDING_GATES_METADATA_KEY: gates},
                "updated_at": datetime.now(UTC),
            }
        )
    )


async def _load_spec_gates(
    *,
    campaign: WorkflowCampaign,
    adapter: Any,
    principal: Principal,
    bearer_token: str | None,
) -> list[dict[str, Any]]:
    try:
        live_gates = await adapter.get_workflow_gates(
            campaign.session_id,
            auth_token=bearer_token,
            principal=principal,
        )
    except Exception:
        return _stored_spec_gates(campaign)
    pending = [
        gate
        for gate in live_gates
        if _gate_node_id(gate) in _SPEC_GATE_NODE_IDS and _is_pending_gate(gate)
    ]
    return pending


def _stored_spec_gates(campaign: WorkflowCampaign) -> list[dict[str, Any]]:
    raw = campaign.metadata.get(_PENDING_GATES_METADATA_KEY)
    if not isinstance(raw, list):
        return []
    return [
        gate
        for gate in raw
        if isinstance(gate, dict)
        and _gate_node_id(gate) in _SPEC_GATE_NODE_IDS
        and _is_pending_gate(gate)
    ]


def _gate_id(gate: dict[str, Any]) -> str:
    return str(gate.get("id") or gate.get("gate_id") or gate.get("gateId") or "").strip()


def _gate_node_id(gate: dict[str, Any]) -> str:
    return str(gate.get("node_id") or gate.get("nodeId") or "").strip()


def _is_pending_gate(gate: dict[str, Any]) -> bool:
    return str(gate.get("status") or "").strip().lower() in _PENDING_GATE_STATUSES


def _select_review_gate(
    gates: list[dict[str, Any]],
    *,
    gate_id: str | None,
    node_id: str | None,
) -> dict[str, Any] | None:
    normalized_gate_id = str(gate_id or "").strip()
    normalized_node_id = str(node_id or "").strip()
    for gate in gates:
        if normalized_gate_id and _gate_id(gate) != normalized_gate_id:
            continue
        if normalized_node_id and _gate_node_id(gate) != normalized_node_id:
            continue
        if _gate_node_id(gate) in _SPEC_GATE_NODE_IDS and _is_pending_gate(gate):
            return gate
    return gates[0] if gates else None


async def _load_spec_artifacts(
    campaign: WorkflowCampaign,
    *,
    settings: Any,
) -> tuple[list[CampaignArtifactResponse], dict[str, str]]:
    adapter = _resolve_campaign_mimir_port(campaign, settings)
    if adapter is None:
        return [], {}

    prefix = f"specifications/{campaign.slug}/"
    pages = await adapter.list_pages(prefix=prefix)
    full_pages: list[MimirPage] = []
    for meta in pages:
        try:
            full_pages.append(await adapter.get_page(meta.path))
        except FileNotFoundError:
            continue

    published_paths = await _spec_published_paths(adapter, campaign.slug)
    manifest_known = bool(published_paths)
    artifacts = [
        _spec_artifact_response(page, published_paths, manifest_known=manifest_known)
        for page in sorted(full_pages, key=lambda page: page.meta.path)
    ]
    canonical: dict[str, str] = {}
    for artifact in artifacts:
        if artifact.kind and artifact.kind not in canonical:
            canonical[artifact.kind] = artifact.path
    return artifacts, canonical


async def _spec_published_paths(adapter: MimirPort, slug: str) -> set[str]:
    manifest_path = f"specifications/{slug}/50-manifest.md"
    try:
        content = await adapter.read_page(manifest_path)
    except FileNotFoundError:
        return set()
    published = set(_SPEC_MANIFEST_PATH_RE.findall(content))
    published.add(manifest_path)
    return published


def _spec_artifact_response(
    page: MimirPage,
    published_paths: set[str],
    *,
    manifest_known: bool,
) -> CampaignArtifactResponse:
    kind = _classify_spec_artifact_kind(page.meta.path)
    publish_state = (
        "published"
        if page.meta.path in published_paths
        else ("unpublished" if manifest_known else "unknown")
    )
    return CampaignArtifactResponse(
        path=page.meta.path,
        title=page.meta.title or _spec_title_from_path(page.meta.path),
        updated_at=page.meta.updated_at,
        kind=kind,
        publish_state=publish_state,
        source_ids=list(page.meta.source_ids),
        summary=page.meta.summary or None,
    )


def _classify_spec_artifact_kind(path: str) -> str | None:
    name = Path(path).name
    by_name = {
        "00-brief.md": "brief",
        "10-prd.md": "prd",
        "11-prd-review.md": "prd_review",
        "12-prd-gate.md": "prd_gate",
        "20-srd.md": "srd",
        "21-srd-review.md": "srd_review",
        "22-srd-gate.md": "srd_gate",
        "30-sdd.md": "sdd",
        "31-sdd-review.md": "sdd_review",
        "32-sdd-gate.md": "sdd_gate",
        "40-breakdown.md": "breakdown",
        "41-breakdown-review.md": "breakdown_review",
        "42-breakdown-gate.md": "breakdown_gate",
        "50-manifest.md": "manifest",
    }
    return by_name.get(name)


def _spec_title_from_path(path: str) -> str:
    kind = _classify_spec_artifact_kind(path)
    titles = {
        "brief": "Brief",
        "prd": "PRD",
        "prd_review": "PRD Review",
        "prd_gate": "PRD Gate",
        "srd": "SRD",
        "srd_review": "SRD Review",
        "srd_gate": "SRD Gate",
        "sdd": "SDD",
        "sdd_review": "SDD Review",
        "sdd_gate": "SDD Gate",
        "breakdown": "Breakdown",
        "breakdown_review": "Breakdown Review",
        "breakdown_gate": "Breakdown Gate",
        "manifest": "Manifest",
    }
    return titles.get(kind or "", _title_from_path(path))


def _spec_campaign_owns_path(slug: str, path: str) -> bool:
    return path.startswith(f"specifications/{slug}/")


def _derive_spec_stage_state(
    snapshot: dict[str, Any],
    artifacts: list[CampaignArtifactResponse],
    status: WorkflowCampaignStatus,
    previous: list[CampaignStageState],
    gates: list[dict[str, Any]],
) -> list[CampaignStageState]:
    stages = _workflow_stages(snapshot)
    previous_map = {stage.stage_id: stage for stage in previous}
    available_kinds = {artifact.kind for artifact in artifacts if artifact.kind}
    pending_gate_stage = _stage_for_pending_gate(gates)
    now = datetime.now(UTC)
    derived: list[CampaignStageState] = []
    first_incomplete: int | None = None

    for index, stage in enumerate(stages):
        prior = previous_map.get(stage["id"])
        if _spec_stage_requirement_met(stage["id"], stage["label"], available_kinds, status):
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

    if pending_gate_stage:
        for index, stage in enumerate(derived):
            if stage.stage_id != pending_gate_stage:
                continue
            derived[index] = CampaignStageState(
                stage_id=stage.stage_id,
                label=stage.label,
                status="blocked",
                started_at=stage.started_at or now,
                completed_at=stage.completed_at,
                reason="Review required",
            )
            return derived

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
    return derived


def _stage_for_pending_gate(gates: list[dict[str, Any]]) -> str | None:
    gate_to_stage = {
        "spec-prd-gate": "spec-prd-review",
        "spec-srd-gate": "spec-srd-review",
        "spec-sdd-gate": "spec-sdd-review",
        "spec-breakdown-gate": "spec-breakdown-review",
    }
    for gate in gates:
        stage = gate_to_stage.get(_gate_node_id(gate))
        if stage:
            return stage
    return None


def _spec_stage_requirement_met(
    stage_id: str,
    label: str,
    kinds: set[str | None],
    status: WorkflowCampaignStatus,
) -> bool:
    if stage_id == "spec-frame" or "frame" in label.lower():
        return "brief" in kinds
    if stage_id == "spec-prd-review":
        return "prd_review" in kinds
    if stage_id == "spec-prd" or "prd" in label.lower():
        return "prd" in kinds
    if stage_id == "spec-srd-review":
        return "srd_review" in kinds
    if stage_id == "spec-srd" or "srd" in label.lower():
        return "srd" in kinds
    if stage_id == "spec-sdd-review":
        return "sdd_review" in kinds
    if stage_id == "spec-sdd" or "sdd" in label.lower():
        return "sdd" in kinds
    if stage_id == "spec-breakdown-review":
        return "breakdown_review" in kinds
    if stage_id == "spec-breakdown" or "breakdown" in label.lower():
        return "breakdown" in kinds
    if stage_id == "spec-publish" or "publish" in label.lower():
        return "manifest" in kinds
    if "complete" in label.lower():
        return status == WorkflowCampaignStatus.COMPLETED
    return False
