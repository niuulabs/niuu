"""REST API for saga management.

Saga references are stored in the DB. Display data (project name, status,
milestones, issues) is fetched live from the tracker at read time.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from inspect import isawaitable
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status

try:
    from sleipnir.domain.catalog import ting_saga_created as _catalog_saga_created
except ImportError:
    _catalog_saga_created = None  # type: ignore[assignment]
from pydantic import BaseModel, Field

from niuu.domain.models import InstanceKind, Principal
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.tracker import resolve_trackers
from ting.config import ReviewConfig
from ting.domain.models import (
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    TrackerIssue,
    TrackerProject,
    WorkflowScope,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot, workflow_name_from_snapshot
from ting.ports.git import GitPort
from ting.ports.llm import LLMPort
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import SpawnRequest, VolundrPort
from ting.ports.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _can_use_workflow(workflow, principal: Principal) -> bool:  # noqa: ANN001
    if workflow.scope == WorkflowScope.SYSTEM:
        return True
    return workflow.owner_id == principal.user_id


async def _resolve_instance_name(
    request: Request,
    principal: Principal,
    instance_id: str | None,
) -> str | None:
    if not instance_id:
        return None
    instance_service = getattr(request.app.state, "instance_service", None)
    if instance_service is None:
        return None
    instance = await instance_service.get_visible(principal, instance_id)
    return instance.name if instance is not None else None


async def _resolve_selected_workflow(
    *,
    request: Request,
    principal: Principal,
    workflow_id_value: str | None,
    use_default_when_missing: bool = False,
) -> tuple[UUID | None, str | None, dict | None]:
    workflow_repo: WorkflowRepository | None = getattr(request.app.state, "workflow_repo", None)
    if workflow_repo is None:
        if workflow_id_value is None:
            return None, None, None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workflow repository not configured",
        )

    if workflow_id_value is not None:
        try:
            workflow_id = UUID(workflow_id_value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid workflow_id: {workflow_id_value!r}",
            )

        workflow = await workflow_repo.get_workflow(workflow_id)
        if workflow is None or not _can_use_workflow(workflow, principal):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow not found: {workflow_id_value}",
            )
        return workflow.id, workflow.version, build_workflow_snapshot(workflow)

    if not use_default_when_missing:
        return None, None, None

    flock_settings = getattr(request.app.state.settings.dispatch, "flock", None)
    default_workflow_name = str(
        getattr(flock_settings, "default_workflow_name", ""),
    ).strip()
    if not default_workflow_name:
        return None, None, None

    workflows = await workflow_repo.list_workflows(
        owner_id=principal.user_id,
        scope=WorkflowScope.SYSTEM,
    )
    workflow = next(
        (candidate for candidate in workflows if candidate.name == default_workflow_name),
        None,
    )
    if workflow is None:
        return None, None, None
    return workflow.id, workflow.version, build_workflow_snapshot(workflow)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RunResponse(BaseModel):
    id: str
    identifier: str
    title: str
    status: str
    status_type: str = ""
    assignee: str | None = None
    labels: list[str] = Field(default_factory=list)
    priority: int = 0
    priority_label: str = ""
    estimate: float | None = None
    url: str = ""
    milestone_id: str | None = None


class PhaseResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    sort_order: int = 0
    progress: float = 0.0
    target_date: str | None = None
    runs: list[RunResponse] = Field(default_factory=list)


class PhaseSummaryResponse(BaseModel):
    total: int = 0
    completed: int = 0


class SagaListItem(BaseModel):
    id: str
    tracker_id: str
    tracker_type: str
    slug: str
    name: str
    repos: list[str]
    repo_branches: dict[str, str] = Field(default_factory=dict)
    repo_refs: list[dict[str, str]] = Field(default_factory=list)
    feature_branch: str
    status: str
    progress: float = 0.0
    milestone_count: int = 0
    issue_count: int = 0
    url: str = ""
    base_branch: str = "main"
    confidence: float = 0.0
    created_at: str = ""
    phase_summary: PhaseSummaryResponse = Field(default_factory=PhaseSummaryResponse)
    workflow_id: str | None = None
    workflow: str | None = None
    workflow_version: str | None = None
    instance_id: str | None = None
    instance_name: str | None = None
    target_tags: list[str] = Field(default_factory=list)
    target_match: str = "all"


class SagaDetailResponse(BaseModel):
    id: str
    tracker_id: str
    tracker_type: str
    slug: str
    name: str
    description: str = ""
    repos: list[str]
    repo_branches: dict[str, str] = Field(default_factory=dict)
    repo_refs: list[dict[str, str]] = Field(default_factory=list)
    feature_branch: str
    status: str
    progress: float = 0.0
    url: str = ""
    base_branch: str = "main"
    confidence: float = 0.0
    created_at: str = ""
    phase_summary: PhaseSummaryResponse = Field(default_factory=PhaseSummaryResponse)
    phases: list[PhaseResponse]
    workflow_id: str | None = None
    workflow: str | None = None
    workflow_version: str | None = None
    instance_id: str | None = None
    instance_name: str | None = None
    target_tags: list[str] = Field(default_factory=list)
    target_match: str = "all"


class DecomposeRequest(BaseModel):
    spec: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    model: str = Field(default="")


class UpdateSagaRequest(BaseModel):
    status: str


class SagaWorkflowAssignmentRequest(BaseModel):
    workflow_id: str | None = None


class SagaTargetAssignmentRequest(BaseModel):
    instance_id: str | None = None
    target_tags: list[str] = Field(default_factory=list)
    target_match: str = "all"


class RunSpecResponse(BaseModel):
    name: str
    description: str
    acceptance_criteria: list[str]
    declared_files: list[str]
    estimate_hours: float
    confidence: float


class PhaseSpecResponse(BaseModel):
    name: str
    runs: list[RunSpecResponse]


class SagaStructureResponse(BaseModel):
    name: str
    phases: list[PhaseSpecResponse]


# ---------------------------------------------------------------------------
# Commit request / response models
# ---------------------------------------------------------------------------


class RunSpecRequest(BaseModel):
    name: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    declared_files: list[str] = Field(default_factory=list)
    estimate_hours: float = 0.0


class PhaseSpecRequest(BaseModel):
    name: str
    runs: list[RunSpecRequest]


class PlanRequest(BaseModel):
    """Request to spawn an interactive planning session."""

    spec: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    base_branch: str = Field(default="main", description="Base branch for the planning session")
    model: str = Field(default="")


class PlanSessionResponse(BaseModel):
    """Response from spawning a planning session."""

    session_id: str
    chat_endpoint: str | None = None


class ExtractStructureRequest(BaseModel):
    """Request to extract a saga structure from freeform text."""

    text: str = Field(min_length=1)


class ExtractStructureResponse(BaseModel):
    """Extracted saga structure, or null if no valid structure found."""

    found: bool
    structure: SagaStructureResponse | None = None


class CommitRequest(BaseModel):
    name: str
    slug: str
    description: str = ""
    repos: list[str]
    base_branch: str
    phases: list[PhaseSpecRequest]
    transcript: str | None = None
    workflow_id: str | None = None


class CommittedRunResponse(BaseModel):
    id: str
    tracker_id: str
    name: str
    status: str


class CommittedPhaseResponse(BaseModel):
    id: str
    tracker_id: str
    number: int
    name: str
    status: str
    runs: list[CommittedRunResponse]


class CommittedSagaResponse(BaseModel):
    id: str
    tracker_id: str
    tracker_type: str
    slug: str
    name: str
    repos: list[str]
    feature_branch: str
    base_branch: str
    status: str
    confidence: float
    created_at: str
    phase_summary: PhaseSummaryResponse
    phases: list[CommittedPhaseResponse]
    warnings: list[str] = Field(default_factory=list)
    workflow_id: str | None = None
    workflow: str | None = None
    workflow_version: str | None = None


# ---------------------------------------------------------------------------
# Dependencies — overridden by main.py
# ---------------------------------------------------------------------------


async def resolve_saga_repo() -> SagaRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Saga repository not configured",
    )


async def resolve_llm() -> LLMPort:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="LLM adapter not configured",
    )


async def resolve_git() -> GitPort:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Git adapter not configured",
    )


async def resolve_volundr() -> VolundrPort:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Volundr adapter not configured",
    )


async def _resolve_git_for_request(request: Request) -> GitPort:
    """Resolve the git dependency while honoring test overrides safely.

    Some tests override ``resolve_git`` with ``AsyncMock`` directly. Letting
    FastAPI inspect that callable causes it to treat ``args``/``kwargs`` as
    required query params. We invoke the override manually so the endpoint
    contract stays stable in both production and tests.
    """

    provider = request.app.dependency_overrides.get(resolve_git, resolve_git)
    result = provider()
    if isawaitable(result):
        result = await result
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _find_project(
    tracker_id: str,
    adapters: list[TrackerPort],
) -> TrackerProject | None:
    """Find a project across all tracker adapters."""
    for adapter in adapters:
        try:
            return await adapter.get_project(tracker_id)
        except Exception:
            continue
    return None


async def _build_phase_summary(
    repo: SagaRepository,
    saga_id: UUID,
) -> PhaseSummaryResponse:
    """Summarize persisted phase progress for frontend saga summary routes."""
    try:
        phases = await repo.get_phases_by_saga(saga_id)
    except NotImplementedError:
        phases = []
    return PhaseSummaryResponse(
        total=len(phases),
        completed=sum(1 for phase in phases if phase.status == PhaseStatus.COMPLETE),
    )


def _build_phase_summary_from_hydrated_phases(
    phases: list[PhaseResponse],
) -> PhaseSummaryResponse:
    """Summarize hydrated tracker phases when Ting has no persisted phase rows."""
    total = len(phases)
    completed = sum(
        1
        for phase in phases
        if phase.runs and all(run.status_type == "completed" for run in phase.runs)
    )
    return PhaseSummaryResponse(total=total, completed=completed)


def _display_progress(
    saga: Saga,
    project: TrackerProject | None,
    phase_summary: PhaseSummaryResponse,
) -> float:
    """Return a truthful saga progress value for API responses.

    Tracker project progress is useful while work is in flight, but imported
    or synthetic-phase sagas can finish before the external project progress
    catches up. When Ting knows the saga is complete, prefer that truth and
    report full progress.
    """
    if saga.status == SagaStatus.COMPLETE:
        return 1.0
    if project is not None:
        return project.progress
    if phase_summary.total <= 0:
        return 0.0
    return phase_summary.completed / phase_summary.total


def _repo_refs(saga: Saga) -> list[dict[str, str]]:
    return [
        {"repo": repo, "branch": saga.repo_branches.get(repo, saga.base_branch)}
        for repo in saga.repos
    ]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_sagas_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/sagas", tags=["Sagas"])

    @router.get("", response_model=list[SagaListItem])
    async def list_sagas(
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: SagaRepository = Depends(resolve_saga_repo),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> list[SagaListItem]:
        """List all sagas, hydrating display data from the tracker."""
        sagas = await repo.list_sagas(owner_id=principal.user_id)

        # Fetch all projects once and index by ID
        all_projects: dict[str, TrackerProject] = {}
        for adapter in adapters:
            try:
                projects = await adapter.list_projects()
                for p in projects:
                    all_projects[p.id] = p
            except Exception:
                logger.warning("Failed to list projects from adapter", exc_info=True)

        items: list[SagaListItem] = []
        for saga in sagas:
            project = all_projects.get(saga.tracker_id)
            phase_summary = await _build_phase_summary(repo, saga.id)
            instance_name = await _resolve_instance_name(request, principal, saga.instance_id)
            items.append(
                SagaListItem(
                    id=str(saga.id),
                    tracker_id=saga.tracker_id,
                    tracker_type=saga.tracker_type,
                    slug=saga.slug,
                    name=project.name if project else saga.name,
                    repos=saga.repos,
                    repo_branches=saga.repo_branches,
                    repo_refs=_repo_refs(saga),
                    feature_branch=saga.feature_branch,
                    status=saga.status.value.lower(),
                    progress=_display_progress(saga, project, phase_summary),
                    milestone_count=project.milestone_count if project else 0,
                    issue_count=project.issue_count if project else 0,
                    url=project.url if project else "",
                    base_branch=saga.base_branch,
                    confidence=saga.confidence,
                    created_at=saga.created_at.isoformat(),
                    phase_summary=phase_summary,
                    workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
                    workflow=workflow_name_from_snapshot(saga.workflow_snapshot),
                    workflow_version=saga.workflow_version,
                    instance_id=saga.instance_id,
                    instance_name=instance_name,
                    target_tags=saga.target_tags,
                    target_match=saga.target_match,
                )
            )
        return items

    @router.get("/{saga_id}", response_model=SagaDetailResponse)
    async def get_saga(
        saga_id: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: SagaRepository = Depends(resolve_saga_repo),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> SagaDetailResponse:
        """Get saga detail, hydrating milestones and issues from the tracker."""
        saga = await repo.get_saga(UUID(saga_id), owner_id=principal.user_id)
        if saga is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saga not found: {saga_id}",
            )

        # Fetch project + milestones + issues from tracker (if linked)
        project = None
        milestones = []
        issues = []
        if saga.tracker_id:
            for adapter in adapters:
                try:
                    if hasattr(adapter, "get_project_full"):
                        project, milestones, issues = await adapter.get_project_full(
                            saga.tracker_id
                        )
                    else:
                        project = await adapter.get_project(saga.tracker_id)
                        milestones = await adapter.list_milestones(saga.tracker_id)
                        issues = await adapter.list_issues(saga.tracker_id)
                    break
                except Exception:
                    continue

        # Group issues by milestone
        issues_by_milestone: dict[str | None, list] = {}
        for issue in issues:
            key = issue.milestone_id
            issues_by_milestone.setdefault(key, []).append(issue)

        phase_responses: list[PhaseResponse] = []

        def _issue_to_run(i: TrackerIssue) -> RunResponse:
            return RunResponse(
                id=i.id,
                identifier=i.identifier,
                title=i.title,
                status=i.status,
                status_type=i.status_type,
                assignee=i.assignee,
                labels=i.labels or [],
                priority=i.priority,
                priority_label=i.priority_label,
                estimate=i.estimate,
                url=i.url,
                milestone_id=i.milestone_id,
            )

        for ms in milestones:
            ms_issues = issues_by_milestone.get(ms.id, [])
            phase_responses.append(
                PhaseResponse(
                    id=ms.id,
                    name=ms.name,
                    description=ms.description,
                    sort_order=ms.sort_order,
                    progress=ms.progress,
                    target_date=ms.target_date,
                    runs=[_issue_to_run(i) for i in ms_issues],
                )
            )

        # Unassigned issues
        unassigned = issues_by_milestone.get(None, [])
        if unassigned:
            phase_responses.append(
                PhaseResponse(
                    id="__unassigned__",
                    name="Unassigned",
                    sort_order=999999,
                    runs=[_issue_to_run(i) for i in unassigned],
                )
            )

        phase_summary = await _build_phase_summary(repo, saga.id)
        if phase_summary.total == 0 and phase_responses:
            phase_summary = _build_phase_summary_from_hydrated_phases(phase_responses)
        instance_name = await _resolve_instance_name(request, principal, saga.instance_id)

        return SagaDetailResponse(
            id=str(saga.id),
            tracker_id=saga.tracker_id,
            tracker_type=saga.tracker_type,
            slug=saga.slug,
            name=project.name if project else saga.name,
            description=project.description if project else "",
            repos=saga.repos,
            repo_branches=saga.repo_branches,
            repo_refs=_repo_refs(saga),
            feature_branch=saga.feature_branch,
            status=saga.status.value.lower(),
            progress=_display_progress(saga, project, phase_summary),
            url=project.url if project else "",
            base_branch=saga.base_branch,
            confidence=saga.confidence,
            created_at=saga.created_at.isoformat(),
            phase_summary=phase_summary,
            phases=phase_responses,
            workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
            workflow=workflow_name_from_snapshot(saga.workflow_snapshot),
            workflow_version=saga.workflow_version,
            instance_id=saga.instance_id,
            instance_name=instance_name,
            target_tags=saga.target_tags,
            target_match=saga.target_match,
        )

    @router.post("/decompose", response_model=SagaStructureResponse)
    async def decompose_spec(
        body: DecomposeRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        llm: LLMPort = Depends(resolve_llm),
    ) -> SagaStructureResponse:
        """Decompose a spec into a saga structure (stateless preview)."""
        model = body.model or request.app.state.settings.llm.default_model
        try:
            structure = await llm.decompose_spec(body.spec, body.repo, model=model)
        except Exception as exc:
            logger.error("Decomposition failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM decomposition failed: {exc}",
            )
        return SagaStructureResponse(
            name=structure.name,
            phases=[
                PhaseSpecResponse(
                    name=phase.name,
                    runs=[
                        RunSpecResponse(
                            name=run.name,
                            description=run.description,
                            acceptance_criteria=run.acceptance_criteria,
                            declared_files=run.declared_files,
                            estimate_hours=run.estimate_hours,
                            confidence=run.confidence,
                        )
                        for run in phase.runs
                    ],
                )
                for phase in structure.phases
            ],
        )

    @router.get("/plan/config")
    async def get_plan_config(request: Request) -> dict:
        """Return planner configuration including the finalize prompt."""
        settings = request.app.state.settings
        return {"finalize_prompt": settings.planner.finalize_prompt}

    @router.post("/plan", response_model=PlanSessionResponse, status_code=201)
    async def spawn_plan_session(
        body: PlanRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        volundr: VolundrPort = Depends(resolve_volundr),
    ) -> PlanSessionResponse:
        """Spawn an interactive planning session via Volundr.

        Creates a lightweight skuld-planner session that the user chats with
        to iteratively decompose a specification into a saga structure.
        """
        settings = request.app.state.settings
        model = body.model or settings.dispatch.default_model

        # Query Volundr for the user's integration IDs (includes PAT)
        auth_token = extract_bearer_token(request)
        integration_ids: list[str] = []
        try:
            integration_ids = await volundr.list_integration_ids(auth_token=auth_token)
        except Exception:
            logger.warning("Failed to fetch Volundr integrations for user %s", principal.user_id)

        planner_template = settings.planner.planner_system_prompt
        if planner_template:
            planner_prompt = planner_template.format(
                repo=body.repo,
                base_branch=body.base_branch,
                spec=body.spec,
            )
        else:
            planner_prompt = (
                f"Help decompose this specification into phases and runs.\n\n"
                f"Repository: {body.repo}\n"
                f"Base branch: {body.base_branch}\n"
                f"Specification:\n{body.spec}"
            )

        try:
            session = await volundr.spawn_session(
                SpawnRequest(
                    name=f"plan-{principal.user_id[:8]}",
                    repo=body.repo,
                    branch=body.base_branch,
                    base_branch=body.base_branch,
                    model=model,
                    tracker_issue_id="",
                    tracker_issue_url="",
                    system_prompt=settings.dispatch.default_system_prompt,
                    initial_prompt=planner_prompt,
                    workload_type="planner",
                    profile="planner",
                    integration_ids=integration_ids,
                ),
                auth_token=auth_token,
            )
        except Exception as exc:
            logger.error("Failed to spawn planning session: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to spawn planning session: {exc}",
            )

        return PlanSessionResponse(
            session_id=session.id,
            chat_endpoint=session.chat_endpoint,
        )

    @router.post("/extract-structure", response_model=ExtractStructureResponse)
    async def extract_structure(
        body: ExtractStructureRequest,
        _principal: Principal = Depends(extract_principal),
    ) -> ExtractStructureResponse:
        """Extract a saga structure from freeform assistant text.

        Scans the text for JSON code blocks (or raw JSON) matching the
        SagaStructure schema using ting.domain.validation.try_extract_structure.
        """
        from ting.domain.validation import try_extract_structure

        result = try_extract_structure(body.text)
        if result is None:
            return ExtractStructureResponse(found=False)

        return ExtractStructureResponse(
            found=True,
            structure=SagaStructureResponse(
                name=result.name,
                phases=[
                    PhaseSpecResponse(
                        name=phase.name,
                        runs=[
                            RunSpecResponse(
                                name=run.name,
                                description=run.description,
                                acceptance_criteria=run.acceptance_criteria,
                                declared_files=run.declared_files,
                                estimate_hours=run.estimate_hours,
                                confidence=run.confidence,
                            )
                            for run in phase.runs
                        ],
                    )
                    for phase in result.phases
                ],
            ),
        )

    @router.patch("/{saga_id}", response_model=SagaListItem)
    async def update_saga(
        saga_id: str,
        body: UpdateSagaRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: SagaRepository = Depends(resolve_saga_repo),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> SagaListItem:
        """Update a saga's status (e.g. archive a completed project)."""
        try:
            parsed_id = UUID(saga_id)
            new_status = SagaStatus(body.status.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid saga_id or status: {saga_id!r} / {body.status!r}",
            )

        saga = await repo.get_saga(parsed_id, owner_id=principal.user_id)
        if saga is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saga not found: {saga_id}",
            )

        await repo.update_saga_status(parsed_id, new_status)

        project = await _find_project(saga.tracker_id, adapters)
        return SagaListItem(
            id=str(saga.id),
            tracker_id=saga.tracker_id,
            tracker_type=saga.tracker_type,
            slug=saga.slug,
            name=project.name if project else saga.name,
            repos=saga.repos,
            repo_branches=saga.repo_branches,
            repo_refs=_repo_refs(saga),
            feature_branch=saga.feature_branch,
            status=new_status.value.lower(),
            progress=_display_progress(
                replace(saga, status=new_status),
                project,
                await _build_phase_summary(repo, saga.id),
            ),
            milestone_count=project.milestone_count if project else 0,
            issue_count=project.issue_count if project else 0,
            url=project.url if project else "",
            workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
            workflow=workflow_name_from_snapshot(saga.workflow_snapshot),
            workflow_version=saga.workflow_version,
            instance_id=saga.instance_id,
            instance_name=await _resolve_instance_name(request, principal, saga.instance_id),
            target_tags=saga.target_tags,
            target_match=saga.target_match,
        )

    @router.put("/{saga_id}/workflow", response_model=SagaListItem)
    async def assign_workflow(
        saga_id: str,
        body: SagaWorkflowAssignmentRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: SagaRepository = Depends(resolve_saga_repo),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> SagaListItem:
        try:
            parsed_id = UUID(saga_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saga not found: {saga_id}",
            )

        saga = await repo.get_saga(parsed_id, owner_id=principal.user_id)
        if saga is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saga not found: {saga_id}",
            )

        workflow_id, workflow_version, workflow_snapshot = await _resolve_selected_workflow(
            request=request,
            principal=principal,
            workflow_id_value=body.workflow_id,
            use_default_when_missing=False,
        )

        await repo.update_saga_workflow(
            parsed_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workflow_snapshot=workflow_snapshot,
            owner_id=principal.user_id,
        )
        updated = await repo.get_saga(parsed_id, owner_id=principal.user_id)
        assert updated is not None

        project = await _find_project(updated.tracker_id, adapters)
        phase_summary = await _build_phase_summary(repo, updated.id)
        instance_name = await _resolve_instance_name(request, principal, updated.instance_id)
        return SagaListItem(
            id=str(updated.id),
            tracker_id=updated.tracker_id,
            tracker_type=updated.tracker_type,
            slug=updated.slug,
            name=project.name if project else updated.name,
            repos=updated.repos,
            repo_branches=updated.repo_branches,
            repo_refs=_repo_refs(updated),
            feature_branch=updated.feature_branch,
            status=updated.status.value.lower(),
            progress=_display_progress(updated, project, phase_summary),
            milestone_count=project.milestone_count if project else 0,
            issue_count=project.issue_count if project else 0,
            url=project.url if project else "",
            base_branch=updated.base_branch,
            confidence=updated.confidence,
            created_at=updated.created_at.isoformat(),
            phase_summary=phase_summary,
            workflow_id=str(updated.workflow_id) if updated.workflow_id else None,
            workflow=workflow_name_from_snapshot(updated.workflow_snapshot),
            workflow_version=updated.workflow_version,
            instance_id=updated.instance_id,
            instance_name=instance_name,
            target_tags=updated.target_tags,
            target_match=updated.target_match,
        )

    @router.put("/{saga_id}/target", response_model=SagaListItem)
    async def assign_target(
        saga_id: str,
        body: SagaTargetAssignmentRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: SagaRepository = Depends(resolve_saga_repo),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
    ) -> SagaListItem:
        try:
            parsed_id = UUID(saga_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saga not found: {saga_id}",
            )

        saga = await repo.get_saga(parsed_id, owner_id=principal.user_id)
        if saga is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saga not found: {saga_id}",
            )

        target_tags = [tag.strip() for tag in body.target_tags if tag.strip()]
        target_match = body.target_match if body.target_match in {"all", "any"} else "all"
        instance_id = None if target_tags else body.instance_id

        instance_name: str | None = None
        if instance_id:
            instance_service = getattr(request.app.state, "instance_service", None)
            if instance_service is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Instance registry not configured",
                )
            instance = await instance_service.get_visible(principal, instance_id)
            if instance is None or instance.kind != InstanceKind.VOLUNDR or not instance.enabled:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Target not found: {instance_id}",
                )
            instance_name = instance.name

        await repo.update_saga_target(
            parsed_id,
            instance_id=instance_id,
            target_tags=target_tags,
            target_match=target_match,
            owner_id=principal.user_id,
        )
        updated = await repo.get_saga(parsed_id, owner_id=principal.user_id)
        assert updated is not None

        project = await _find_project(updated.tracker_id, adapters)
        phase_summary = await _build_phase_summary(repo, updated.id)
        return SagaListItem(
            id=str(updated.id),
            tracker_id=updated.tracker_id,
            tracker_type=updated.tracker_type,
            slug=updated.slug,
            name=project.name if project else updated.name,
            repos=updated.repos,
            repo_branches=updated.repo_branches,
            repo_refs=_repo_refs(updated),
            feature_branch=updated.feature_branch,
            status=updated.status.value.lower(),
            progress=_display_progress(updated, project, phase_summary),
            milestone_count=project.milestone_count if project else 0,
            issue_count=project.issue_count if project else 0,
            url=project.url if project else "",
            base_branch=updated.base_branch,
            confidence=updated.confidence,
            created_at=updated.created_at.isoformat(),
            phase_summary=phase_summary,
            workflow_id=str(updated.workflow_id) if updated.workflow_id else None,
            workflow=workflow_name_from_snapshot(updated.workflow_snapshot),
            workflow_version=updated.workflow_version,
            instance_id=updated.instance_id,
            instance_name=instance_name,
            target_tags=updated.target_tags,
            target_match=updated.target_match,
        )

    @router.delete("/{saga_id}", status_code=204)
    async def delete_saga(
        saga_id: str,
        principal: Principal = Depends(extract_principal),
        repo: SagaRepository = Depends(resolve_saga_repo),
    ) -> None:
        """Delete a saga reference (scoped to the current user)."""
        try:
            parsed_id = UUID(saga_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saga not found: {saga_id}",
            )
        deleted = await repo.delete_saga(parsed_id, owner_id=principal.user_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saga not found: {saga_id}",
            )

    @router.post("/commit", response_model=CommittedSagaResponse, status_code=201)
    async def commit_saga(
        body: CommitRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        saga_repo: SagaRepository = Depends(resolve_saga_repo),
        adapters: list[TrackerPort] = Depends(resolve_trackers),
        git: GitPort = Depends(_resolve_git_for_request),
    ) -> CommittedSagaResponse:
        """Commit a previewed saga structure.

        Persists the saga, phases, and runs to PostgreSQL inside a single
        transaction, then creates tracker entities and the feature branch.

        Tracker and git calls are best-effort — if they fail after the DB
        transaction commits, the operator must retry.  The DB writes are
        atomic: a failure in any save rolls back the entire transaction.

        Returns 409 if the slug already exists.
        """
        # Idempotency: reject duplicate slugs
        existing = await saga_repo.get_saga_by_slug(body.slug)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Saga with slug '{body.slug}' already exists",
            )

        if not body.phases:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="At least one phase is required",
            )

        tracker = adapters[0] if adapters else None
        if tracker is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No tracker configured",
            )

        review_cfg: ReviewConfig = getattr(
            getattr(request.app.state, "settings", None),
            "review",
            ReviewConfig(),
        )
        initial_confidence = review_cfg.initial_confidence

        now = datetime.now(UTC)
        saga_id = uuid4()
        feature_branch = f"feat/{body.slug}"
        workflow_id, workflow_version, workflow_snapshot = await _resolve_selected_workflow(
            request=request,
            principal=principal,
            workflow_id_value=body.workflow_id,
            use_default_when_missing=True,
        )

        # Build saga domain object (tracker_id filled after tracker call)
        saga = Saga(
            id=saga_id,
            tracker_id="",
            tracker_type="",
            slug=body.slug,
            name=body.name,
            repos=body.repos,
            feature_branch=feature_branch,
            base_branch=body.base_branch,
            status=SagaStatus.ACTIVE,
            confidence=initial_confidence,
            created_at=now,
            owner_id=principal.user_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workflow_snapshot=workflow_snapshot,
        )

        # 1. Create saga in tracker — this MUST succeed or we abort
        tracker_type = type(tracker).__name__
        try:
            tracker_saga_id = await tracker.create_saga(saga, description=body.description)
        except Exception as exc:
            logger.error(
                "Tracker create_saga failed for slug=%s",
                _sanitize_log(body.slug),
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to create project in tracker: {exc}",
            )
        saga = replace(saga, tracker_id=tracker_saga_id, tracker_type=tracker_type)

        # 2. Build all phases and runs, creating tracker entities along the way
        phases: list[Phase] = []
        runs: list[Run] = []
        phase_responses: list[CommittedPhaseResponse] = []

        for phase_num, phase_spec in enumerate(body.phases, start=1):
            is_first_phase = phase_num == 1
            phase_status = PhaseStatus.ACTIVE if is_first_phase else PhaseStatus.GATED

            phase = Phase(
                id=uuid4(),
                saga_id=saga_id,
                tracker_id="",
                number=phase_num,
                name=phase_spec.name,
                status=phase_status,
                confidence=initial_confidence,
            )

            try:
                tracker_phase_id = await tracker.create_phase(phase, project_id=saga.tracker_id)
            except Exception as exc:
                logger.error(
                    "Tracker create_phase failed for phase=%s", phase_spec.name, exc_info=True
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to create phase '{phase_spec.name}' in tracker: {exc}",
                )
            phase = replace(phase, tracker_id=tracker_phase_id)
            phases.append(phase)

            run_responses: list[CommittedRunResponse] = []

            for run_spec in phase_spec.runs:
                run = Run(
                    id=uuid4(),
                    phase_id=phase.id,
                    tracker_id="",
                    name=run_spec.name,
                    description=run_spec.description,
                    acceptance_criteria=run_spec.acceptance_criteria,
                    declared_files=run_spec.declared_files,
                    estimate_hours=run_spec.estimate_hours,
                    status=RunStatus.PENDING,
                    confidence=initial_confidence,
                    session_id=None,
                    branch=None,
                    chronicle_summary=None,
                    pr_url=None,
                    pr_id=None,
                    retry_count=0,
                    created_at=now,
                    updated_at=now,
                )

                try:
                    tracker_run_id = await tracker.create_run(
                        run,
                        project_id=saga.tracker_id,
                        milestone_id=phase.tracker_id,
                    )
                except Exception as exc:
                    logger.error(
                        "Tracker create_run failed for run=%s", run_spec.name, exc_info=True
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Failed to create run '{run_spec.name}' in tracker: {exc}",
                    )
                run = replace(run, tracker_id=tracker_run_id)
                runs.append(run)

                run_responses.append(
                    CommittedRunResponse(
                        id=str(run.id),
                        tracker_id=run.tracker_id,
                        name=run.name,
                        status=run.status.value,
                    )
                )

            phase_responses.append(
                CommittedPhaseResponse(
                    id=str(phase.id),
                    tracker_id=phase.tracker_id,
                    number=phase.number,
                    name=phase.name,
                    status=phase.status.value,
                    runs=run_responses,
                )
            )

        # 3. Persist all DB rows in a single transaction
        async with saga_repo.begin() as conn:
            await saga_repo.save_saga(saga, conn=conn)
            for phase in phases:
                await saga_repo.save_phase(phase, conn=conn)
            for run in runs:
                await saga_repo.save_run(run, conn=conn)

        # 4. Create feature branch for each repo (best-effort — logged on failure)
        warnings: list[str] = []
        for repo in body.repos:
            try:
                await git.create_branch(repo, feature_branch, base=body.base_branch)
            except Exception:
                msg = f"Failed to create branch '{feature_branch}' in {_sanitize_log(repo)}"
                logger.warning(
                    "Failed to create branch %s in %s",
                    _sanitize_log(feature_branch),
                    _sanitize_log(repo),
                    exc_info=True,
                )
                warnings.append(msg)

        # 5. Attach planning transcript as a document (best-effort)
        if body.transcript and saga.tracker_id:
            try:
                await tracker.attach_document(
                    saga.tracker_id,
                    f"Planning Transcript — {saga.name}",
                    body.transcript,
                )
            except Exception:
                logger.warning("Failed to attach transcript for saga %s", saga.slug, exc_info=True)

        # NIU-582: emit ting.saga.created (best-effort, non-blocking)
        publisher = getattr(request.app.state, "sleipnir_publisher", None)
        if publisher is not None and _catalog_saga_created is not None:
            try:
                event = _catalog_saga_created(
                    saga_id=str(saga.id),
                    template=body.slug,
                    trigger_event="api.commit_saga",
                    source="ting",
                    correlation_id=str(saga.id),
                )
                await publisher.publish(event)
            except Exception:
                logger.warning("Failed to emit ting.saga.created; continuing.", exc_info=True)

        dispatch_service = getattr(request.app.state, "dispatch_service", None)
        if dispatch_service is not None:
            try:
                await dispatch_service.try_auto_continue(principal.user_id, saga.tracker_id)
            except Exception:
                msg = f"Failed to kick off initial dispatch for saga '{_sanitize_log(body.slug)}'"
                logger.warning(
                    "Failed to kick off initial dispatch for saga %s",
                    _sanitize_log(body.slug),
                    exc_info=True,
                )
                warnings.append(msg)

        return CommittedSagaResponse(
            id=str(saga.id),
            tracker_id=saga.tracker_id,
            tracker_type=saga.tracker_type,
            slug=saga.slug,
            name=saga.name,
            repos=saga.repos,
            feature_branch=saga.feature_branch,
            base_branch=saga.base_branch,
            status=saga.status.value,
            confidence=saga.confidence,
            created_at=saga.created_at.isoformat(),
            phase_summary=PhaseSummaryResponse(
                total=len(phases),
                completed=sum(1 for phase in phases if phase.status == PhaseStatus.COMPLETE),
            ),
            phases=phase_responses,
            warnings=warnings,
            workflow_id=str(saga.workflow_id) if saga.workflow_id else None,
            workflow=workflow_name_from_snapshot(saga.workflow_snapshot),
            workflow_version=saga.workflow_version,
            instance_id=saga.instance_id,
            instance_name=None,
        )

    return router
