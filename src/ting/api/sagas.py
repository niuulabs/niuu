"""REST API for saga management.

Saga references are stored in the DB. Display data (project name, status,
milestones, issues) is fetched live from the tracker at read time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

from niuu.domain.outcome import parse_outcome_block

try:
    from sleipnir.domain.catalog import ting_saga_created as _catalog_saga_created
except ImportError:
    _catalog_saga_created = None  # type: ignore[assignment]
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import resolve_workflow_campaign_repo
from ting.api.tracker import resolve_trackers
from ting.api.workflows import WorkflowLaunchBody, launch_workflow_execution, resolve_workflow_repo
from ting.config import ReviewConfig
from ting.domain.models import (
    CampaignStageState,
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    SagaStructure,
    TrackerIssue,
    TrackerProject,
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDefinition,
    WorkflowScope,
)
from ting.domain.utils import _session_name, _slugify
from ting.domain.workflow_snapshot import build_workflow_snapshot, workflow_name_from_snapshot
from ting.ports.git import GitPort
from ting.ports.llm import LLMPort
from ting.ports.saga_repository import SagaRepository
from ting.ports.tracker import TrackerPort
from ting.ports.volundr import VolundrFactory, VolundrPort
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)

_PLANNING_WORKFLOW_NAME = "Saga Planning"
_PLAN_BRIEF_GATE_NODE_ID = "plan-brief-gate"
_PLAN_REVIEW_GATE_NODE_ID = "plan-review-gate"
_PLAN_GATE_NODE_IDS = (_PLAN_BRIEF_GATE_NODE_ID, _PLAN_REVIEW_GATE_NODE_ID)
_PLAN_GATE_DECISION_APPROVE = "APPROVE"
_PLAN_GATE_DECISION_CHANGES_REQUESTED = "CHANGES_REQUESTED"
_PLAN_GATE_POLL_SECONDS = 2.0
_PLAN_GATE_POLL_ATTEMPTS = 6
_PLAN_FEEDBACK_POLL_SECONDS = 2.0
_PLAN_FEEDBACK_POLL_ATTEMPTS = 20
_PLAN_FEEDBACK_METADATA_KEY = "planning_feedback_notes"
_PLAN_FEEDBACK_DECISION_METADATA_KEY = "planning_feedback_decision"
_PLAN_BRIEF_GATE_RESOLVED_METADATA_KEY = "planning_brief_gate_resolved"
_PLAN_PENDING_GATES_METADATA_KEY = "pending_workflow_gates"
_TRANSIENT_PLAN_DRAFT_STATUS_CODES = {404, 409, 425, 502, 503, 504}


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _can_use_workflow(workflow, principal: Principal) -> bool:  # noqa: ANN001
    if workflow.scope == WorkflowScope.SYSTEM:
        return True
    return workflow.owner_id == principal.user_id


def _pending_gate(gates: list[dict], node_id: str) -> dict | None:
    for gate in gates:
        if gate.get("node_id") != node_id and gate.get("nodeId") != node_id:
            continue
        status_value = str(gate.get("status", "")).lower()
        if status_value not in {"", "pending", "open", "waiting"}:
            continue
        if gate.get("id") or gate.get("gate_id") or gate.get("gateId"):
            return gate
    return None


def _pending_gate_id(gates: list[dict], node_id: str) -> str | None:
    gate = _pending_gate(gates, node_id)
    if gate is None:
        return None
    gate_id = gate.get("id") or gate.get("gate_id") or gate.get("gateId")
    if gate_id:
        return str(gate_id)
    return None


def _pending_plan_gate_node_id(gates: list[dict]) -> str | None:
    for node_id in _PLAN_GATE_NODE_IDS:
        if _pending_gate(gates, node_id) is not None:
            return node_id
    return None


def _is_transient_plan_http_error(exc: httpx.HTTPStatusError) -> bool:
    return exc.response.status_code in _TRANSIENT_PLAN_DRAFT_STATUS_CODES


async def _resolve_pending_plan_gate(
    *,
    adapter: VolundrPort,
    campaign: WorkflowCampaign,
    node_id: str | None = None,
    node_ids: Sequence[str] | None = None,
    decision: str,
    notes: str,
    auth_token: str | None,
    principal: Principal,
    attempts: int = _PLAN_GATE_POLL_ATTEMPTS,
) -> bool:
    """Resolve a workflow gate if it is already waiting for Ting's next step."""
    candidate_node_ids = tuple(node_ids or ([node_id] if node_id else []))
    for attempt in range(max(1, attempts)):
        try:
            gates = await adapter.get_workflow_gates(
                campaign.session_id,
                auth_token=auth_token,
                principal=principal,
            )
        except httpx.HTTPStatusError as exc:
            if not _is_transient_plan_http_error(exc) or attempt >= attempts - 1:
                raise
            await asyncio.sleep(_PLAN_GATE_POLL_SECONDS)
            continue
        for candidate_node_id in candidate_node_ids:
            gate_id = _pending_gate_id(gates, candidate_node_id)
            if gate_id is not None:
                await adapter.resolve_workflow_gate(
                    campaign.session_id,
                    gate_id,
                    decision,
                    notes=notes,
                    source="ting.plan",
                    auth_token=auth_token,
                    principal=principal,
                )
                return True
        if attempt < attempts - 1:
            await asyncio.sleep(_PLAN_GATE_POLL_SECONDS)
    return False


async def _approve_pending_plan_gate(
    *,
    adapter: VolundrPort,
    campaign: WorkflowCampaign,
    node_id: str | None = None,
    node_ids: Sequence[str] | None = None,
    notes: str,
    auth_token: str | None,
    principal: Principal,
    attempts: int = _PLAN_GATE_POLL_ATTEMPTS,
) -> bool:
    """Approve a workflow gate if it is already waiting for Ting's next step."""
    return await _resolve_pending_plan_gate(
        adapter=adapter,
        campaign=campaign,
        node_id=node_id,
        node_ids=node_ids,
        decision=_PLAN_GATE_DECISION_APPROVE,
        notes=notes,
        auth_token=auth_token,
        principal=principal,
        attempts=attempts,
    )


async def _send_plan_message_with_retry(
    *,
    adapter: VolundrPort,
    session_id: str,
    content: str,
    auth_token: str | None,
    principal: Principal,
    attempts: int = _PLAN_FEEDBACK_POLL_ATTEMPTS,
) -> None:
    for attempt in range(max(1, attempts)):
        try:
            await adapter.send_message(
                session_id,
                content,
                auth_token=auth_token,
                principal=principal,
            )
            return
        except httpx.HTTPStatusError as exc:
            if not _is_transient_plan_http_error(exc) or attempt >= attempts - 1:
                raise
            await asyncio.sleep(_PLAN_FEEDBACK_POLL_SECONDS)


async def _record_plan_feedback(
    *,
    campaign_repo: WorkflowCampaignRepository,
    campaign: WorkflowCampaign,
    notes: str,
    decision: str,
) -> WorkflowCampaign:
    metadata = dict(campaign.metadata)
    metadata[_PLAN_FEEDBACK_METADATA_KEY] = notes
    metadata[_PLAN_FEEDBACK_DECISION_METADATA_KEY] = decision
    metadata.pop(_PLAN_BRIEF_GATE_RESOLVED_METADATA_KEY, None)
    if decision == "changes_requested":
        metadata.pop(_PLAN_PENDING_GATES_METADATA_KEY, None)
    return await campaign_repo.save_campaign(
        replace(campaign, metadata=metadata, updated_at=datetime.now(UTC))
    )


async def _mark_plan_brief_gate_resolved(
    *,
    campaign_repo: WorkflowCampaignRepository,
    campaign: WorkflowCampaign,
) -> WorkflowCampaign:
    metadata = dict(campaign.metadata)
    metadata[_PLAN_BRIEF_GATE_RESOLVED_METADATA_KEY] = True
    metadata.pop(_PLAN_PENDING_GATES_METADATA_KEY, None)
    return await campaign_repo.save_campaign(
        replace(campaign, metadata=metadata, updated_at=datetime.now(UTC))
    )


async def _retry_recorded_plan_gate_resolution(
    *,
    adapter: VolundrPort,
    campaign_repo: WorkflowCampaignRepository,
    campaign: WorkflowCampaign,
    auth_token: str | None,
    principal: Principal,
) -> WorkflowCampaign:
    if campaign.metadata.get(_PLAN_BRIEF_GATE_RESOLVED_METADATA_KEY) is True:
        return campaign
    notes = campaign.metadata.get(_PLAN_FEEDBACK_METADATA_KEY)
    decision = campaign.metadata.get(_PLAN_FEEDBACK_DECISION_METADATA_KEY)
    if decision not in {None, "approve", "approved"}:
        return campaign
    if not isinstance(notes, str) or not notes.strip():
        return campaign
    try:
        resolved = await _approve_pending_plan_gate(
            adapter=adapter,
            campaign=campaign,
            node_id=_PLAN_BRIEF_GATE_NODE_ID,
            notes=notes,
            auth_token=auth_token,
            principal=principal,
            attempts=1,
        )
    except Exception:
        logger.warning(
            "Failed to retry planning workflow gate resolution for campaign %s",
            _sanitize_log(campaign.id),
            exc_info=True,
        )
        return campaign
    if not resolved:
        return campaign
    return await _mark_plan_brief_gate_resolved(
        campaign_repo=campaign_repo,
        campaign=campaign,
    )


async def _resolve_instance_name(
    request: Request,
    principal: Principal,
    instance_id: str | None,
) -> str | None:
    if not instance_id:
        return None
    instance_registry = getattr(request.app.state, "instance_registry", None)
    if instance_registry is None:
        return None
    instance = await instance_registry.get_volundr_target(principal, instance_id)
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


async def _resolve_planning_workflow(
    repo: WorkflowRepository,
    principal: Principal,
    workflow_id: UUID | None = None,
) -> WorkflowDefinition:
    if workflow_id is not None:
        workflow = await repo.get_workflow(workflow_id)
        if workflow is None or not _can_use_workflow(workflow, principal):
            raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow_id}")
        return workflow

    workflows = await repo.list_workflows(
        owner_id=principal.user_id,
        scope=WorkflowScope.SYSTEM,
    )
    workflow = next(
        (candidate for candidate in workflows if candidate.name == _PLANNING_WORKFLOW_NAME),
        None,
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"{_PLANNING_WORKFLOW_NAME} workflow not found")
    return workflow


def _plan_name(spec: str) -> str:
    compact = " ".join(spec.strip().split())
    if not compact:
        return "Saga Planning"
    return compact[:80]


async def _reserve_plan_slug(repo: WorkflowCampaignRepository, base_slug: str) -> str:
    slug = base_slug or "plan"
    suffix = 2
    while await repo.get_campaign_by_slug(slug) is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


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


def _initial_plan_stage_state(
    snapshot: dict[str, Any],
    now: datetime,
) -> list[CampaignStageState]:
    result: list[CampaignStageState] = []
    for index, stage in enumerate(_workflow_stages(snapshot)):
        result.append(
            CampaignStageState(
                stage_id=stage["id"],
                label=stage["label"],
                status="active" if index == 0 else "pending",
                started_at=now if index == 0 else None,
            )
        )
    return result


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


def _to_plan_stage_response(stage: CampaignStageState) -> PlanStageStateResponse:
    return PlanStageStateResponse(
        stage_id=stage.stage_id,
        label=stage.label,
        status=stage.status,
        started_at=stage.started_at,
        completed_at=stage.completed_at,
        reason=stage.reason,
    )


def _gate_hint(gate: dict | None, fallback: str) -> str:
    if gate:
        for key in ("summary", "instructions", "condition", "description"):
            value = gate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


def _stored_plan_gates(campaign: WorkflowCampaign) -> list[dict]:
    raw = campaign.metadata.get(_PLAN_PENDING_GATES_METADATA_KEY)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _plan_questions_for_campaign(
    campaign: WorkflowCampaign,
    gates: list[dict] | None = None,
) -> list[PlanQuestionResponse]:
    gates = gates or []
    review_gate = _pending_gate(gates, _PLAN_REVIEW_GATE_NODE_ID)
    if review_gate is not None:
        return [
            PlanQuestionResponse(
                id="draft-feedback",
                question=(
                    "The workflow is waiting on draft plan review. What focused changes should "
                    "it make before Ting shows the final draft?"
                ),
                hint=_gate_hint(
                    review_gate,
                    "Type a focused change request, or type approved if the draft is ready.",
                ),
            )
        ]

    brief_gate = _pending_gate(gates, _PLAN_BRIEF_GATE_NODE_ID)
    active_stage = _pending_plan_gate_node_id(gates) or campaign.active_stage_id or ""
    if active_stage in {"plan-clarify", "plan-brief-gate"}:
        return [
            PlanQuestionResponse(
                id="planning-feedback",
                question=(
                    "What constraints, scope boundaries, or acceptance expectations should this "
                    "planning workflow account for?"
                ),
                hint=_gate_hint(
                    brief_gate,
                    (
                        "Keep this focused; the answer is sent to the active workflow run before "
                        "drafting."
                    ),
                ),
            )
        ]
    if active_stage in {"plan-review", "plan-review-gate"}:
        return [
            PlanQuestionResponse(
                id="draft-feedback",
                question="What should change before this draft is approved?",
                hint=(
                    "Request focused changes; nothing is committed until you approve the final "
                    "draft."
                ),
            )
        ]
    return []


def _to_saga_structure_response(structure: SagaStructure) -> SagaStructureResponse:
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
        risks=[PlanRiskResponse(kind=risk.kind, message=risk.message) for risk in structure.risks],
    )


def _extract_plan_structure(text: str) -> SagaStructure | None:
    from ting.domain.validation import parse_and_validate, try_extract_structure

    outcome = parse_outcome_block(text)
    if outcome is not None:
        raw_structure = outcome.fields.get("structure")
        if isinstance(raw_structure, str):
            with suppress(Exception):
                return parse_and_validate(raw_structure)
        if isinstance(raw_structure, dict):
            with suppress(Exception):
                return parse_and_validate(json.dumps(raw_structure))
    return try_extract_structure(text)


async def _read_plan_draft(
    adapter: VolundrPort,
    session_id: str,
    *,
    auth_token: str | None,
    principal: Principal,
) -> SagaStructure | None:
    conversation = await adapter.get_conversation(
        session_id,
        auth_token=auth_token,
        principal=principal,
    )
    turns = conversation.get("turns", []) if isinstance(conversation, dict) else []
    for turn in reversed(turns):
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        content = turn.get("content", "")
        if not isinstance(content, str):
            continue
        structure = _extract_plan_structure(content)
        if structure is not None:
            return structure

    text = await adapter.get_last_assistant_message(
        session_id,
        auth_token=auth_token,
        principal=principal,
    )
    return _extract_plan_structure(text)


def _to_plan_session_response(
    campaign: WorkflowCampaign,
    chat_endpoint: str | None,
    gates: list[dict] | None = None,
) -> PlanSessionResponse:
    active_gate_node_id = _pending_plan_gate_node_id(gates or [])
    return PlanSessionResponse(
        session_id=campaign.session_id,
        chat_endpoint=chat_endpoint,
        name=campaign.name,
        prompt=str(campaign.metadata.get("spec") or ""),
        repo=str(campaign.metadata.get("repo") or ""),
        campaign_slug=campaign.slug,
        workflow_name=campaign.workflow_name,
        status=campaign.status.value,
        active_stage_id=active_gate_node_id or campaign.active_stage_id,
        updated_at=campaign.updated_at,
        stage_state=[_to_plan_stage_response(stage) for stage in campaign.stage_state],
        questions=_plan_questions_for_campaign(campaign, gates),
    )


async def _resolve_plan_volundr_adapter(
    *,
    volundr_factory: VolundrFactory,
    principal: Principal,
):
    return await volundr_factory.primary_for_principal(principal)


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


class SagaRepoRefRequest(BaseModel):
    repo: str
    branch: str = "main"


class SagaReposAssignmentRequest(BaseModel):
    repos: list[str] = Field(default_factory=list)
    repo_refs: list[SagaRepoRefRequest] = Field(default_factory=list)
    base_branch: str = "main"


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


class PlanRiskResponse(BaseModel):
    kind: str
    message: str


class SagaStructureResponse(BaseModel):
    name: str
    phases: list[PhaseSpecResponse]
    risks: list[PlanRiskResponse] = Field(default_factory=list)


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
    workflow_id: UUID | None = Field(default=None, alias="workflowId")
    repo: str = ""
    base_branch: str = Field(default="main", description="Base branch for the planning session")
    model: str = Field(default="")
    connection_id: str | None = Field(default=None, alias="connectionId")

    model_config = {"populate_by_name": True}


class PlanFeedbackRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    decision: str = Field(
        default="approve",
        description="Gate decision: approve or changes_requested.",
    )


class PlanStageStateResponse(BaseModel):
    stage_id: str
    label: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    reason: str | None = None


class PlanQuestionResponse(BaseModel):
    id: str
    question: str
    hint: str | None = None
    kind: str = "text"


class PlanSessionResponse(BaseModel):
    """Response from spawning a planning session."""

    session_id: str
    chat_endpoint: str | None = None
    name: str | None = None
    prompt: str | None = None
    repo: str | None = None
    campaign_slug: str | None = None
    workflow_name: str | None = None
    status: str | None = None
    active_stage_id: str | None = None
    updated_at: datetime | None = None
    stage_state: list[PlanStageStateResponse] = Field(default_factory=list)
    questions: list[PlanQuestionResponse] = Field(default_factory=list)


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

    @router.get("/plan", response_model=list[PlanSessionResponse])
    async def list_plan_sessions(
        principal: Principal = Depends(extract_principal),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> list[PlanSessionResponse]:
        campaigns = await campaign_repo.list_campaigns(owner_id=principal.user_id)
        # Every planning campaign, not only the resumable ones. Filtering to
        # PENDING/RUNNING/BLOCKED meant a plan vanished from the surface the
        # moment it finished: the campaign, its approved plan and its slug all
        # still existed, but nothing linked to them, so a completed plan could
        # only be reached by knowing its URL. The status travels on each item
        # for the caller to group by.
        plan_campaigns = [
            campaign
            for campaign in campaigns
            if campaign.metadata.get("surface") == "ting.plan"
            or campaign.workflow_name == _PLANNING_WORKFLOW_NAME
        ]
        return [
            _to_plan_session_response(
                campaign,
                chat_endpoint=None,
                gates=_stored_plan_gates(campaign),
            )
            for campaign in plan_campaigns
        ]

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
        return _to_saga_structure_response(structure)

    @router.get("/plan/config")
    async def get_plan_config(request: Request) -> dict:
        """Return planner configuration including the finalize prompt."""
        settings = request.app.state.settings
        return {"finalize_prompt": settings.planner.finalize_prompt}

    @router.get("/plan/{slug}", response_model=PlanSessionResponse)
    async def get_plan_session(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> PlanSessionResponse:
        campaign = await campaign_repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Plan run not found")
        gates: list[dict] = []
        try:
            adapter = await _resolve_plan_volundr_adapter(
                volundr_factory=volundr_factory,
                principal=principal,
            )
            if adapter is not None:
                gates = await adapter.get_workflow_gates(
                    campaign.session_id,
                    auth_token=extract_bearer_token(request),
                    principal=principal,
                )
        except Exception:
            logger.warning(
                "Failed to load planning workflow gates for campaign %s",
                _sanitize_log(campaign.id),
                exc_info=True,
            )
        if not gates:
            gates = _stored_plan_gates(campaign)
        return _to_plan_session_response(campaign, chat_endpoint=None, gates=gates)

    @router.delete("/plan/{slug}", status_code=204)
    async def cancel_plan_session(
        slug: str,
        principal: Principal = Depends(extract_principal),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
    ) -> None:
        campaign = await campaign_repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Plan run not found")

        now = datetime.now(UTC)
        await campaign_repo.save_campaign(
            replace(
                campaign,
                status=WorkflowCampaignStatus.FAILED,
                active_stage_id=None,
                metadata={**campaign.metadata, "cancelled_by": "ting.plan"},
                updated_at=now,
                completed_at=campaign.completed_at or now,
            )
        )

    @router.get("/plan/{slug}/draft", response_model=ExtractStructureResponse)
    async def get_plan_draft(
        slug: str,
        request: Request,
        principal: Principal = Depends(extract_principal),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> ExtractStructureResponse:
        campaign = await campaign_repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Plan run not found")

        adapter = await _resolve_plan_volundr_adapter(
            volundr_factory=volundr_factory,
            principal=principal,
        )
        if adapter is None:
            raise HTTPException(status_code=503, detail="No Volundr connection is available")

        auth_token = extract_bearer_token(request)
        campaign = await _retry_recorded_plan_gate_resolution(
            adapter=adapter,
            campaign_repo=campaign_repo,
            campaign=campaign,
            auth_token=auth_token,
            principal=principal,
        )
        gates: list[dict] = []
        try:
            gates = await adapter.get_workflow_gates(
                campaign.session_id,
                auth_token=auth_token,
                principal=principal,
            )
        except Exception:
            logger.debug(
                "Failed to load planning workflow gates before draft read for campaign %s",
                _sanitize_log(campaign.id),
                exc_info=True,
            )
        visible_gates = gates or _stored_plan_gates(campaign)
        if (
            campaign.metadata.get(_PLAN_FEEDBACK_DECISION_METADATA_KEY) == "changes_requested"
            and _pending_gate(visible_gates, _PLAN_REVIEW_GATE_NODE_ID) is None
        ):
            return ExtractStructureResponse(found=False)
        try:
            result = await _read_plan_draft(
                adapter,
                campaign.session_id,
                auth_token=auth_token,
                principal=principal,
            )
        except ValueError:
            return ExtractStructureResponse(found=False)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _TRANSIENT_PLAN_DRAFT_STATUS_CODES:
                logger.info(
                    "Planning draft not ready for campaign %s: session API returned %s",
                    _sanitize_log(campaign.id),
                    exc.response.status_code,
                )
                return ExtractStructureResponse(found=False)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to load plan draft: {exc}",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to load plan draft: {exc}",
            ) from exc

        if result is None:
            return ExtractStructureResponse(found=False)
        return ExtractStructureResponse(found=True, structure=_to_saga_structure_response(result))

    @router.post("/plan/{slug}/feedback")
    async def send_plan_feedback(
        slug: str,
        body: PlanFeedbackRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> dict[str, str]:
        campaign = await campaign_repo.get_campaign_by_slug(slug, owner_id=principal.user_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Plan run not found")

        adapter = await _resolve_plan_volundr_adapter(
            volundr_factory=volundr_factory,
            principal=principal,
        )
        if adapter is None:
            raise HTTPException(status_code=503, detail="No Volundr connection is available")

        auth_token = extract_bearer_token(request)
        try:
            normalized_decision = body.decision.strip().lower().replace("-", "_")
            final_approval = (
                normalized_decision in {"approve", "approved"}
                and body.content.strip() == "Approved in Ting Plan."
            )
            await _send_plan_message_with_retry(
                adapter=adapter,
                session_id=campaign.session_id,
                content=body.content,
                auth_token=auth_token,
                principal=principal,
            )
            message_sent = True
        except httpx.HTTPStatusError as exc:
            if not final_approval or not _is_transient_plan_http_error(exc):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to send plan feedback: {exc}",
                ) from exc
            logger.info(
                "Skipping stale final plan approval feedback for campaign %s: session returned %s",
                _sanitize_log(campaign.id),
                exc.response.status_code,
            )
            message_sent = False
        try:
            if normalized_decision in {"changes_requested", "change_requested"}:
                normalized_decision = "changes_requested"
                gate_decision = _PLAN_GATE_DECISION_CHANGES_REQUESTED
                target_node_ids = (_PLAN_REVIEW_GATE_NODE_ID, _PLAN_BRIEF_GATE_NODE_ID)
            elif normalized_decision in {"approve", "approved"}:
                normalized_decision = "approve"
                gate_decision = _PLAN_GATE_DECISION_APPROVE
                target_node_ids = _PLAN_GATE_NODE_IDS
            else:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="decision must be approve or changes_requested",
                )
            campaign = await _record_plan_feedback(
                campaign_repo=campaign_repo,
                campaign=campaign,
                notes=body.content,
                decision=normalized_decision,
            )
            if final_approval and not message_sent:
                return {"status": "sent", "session_id": campaign.session_id}
            gate_resolved = await _resolve_pending_plan_gate(
                adapter=adapter,
                campaign=campaign,
                node_ids=target_node_ids,
                decision=gate_decision,
                notes=body.content,
                auth_token=auth_token,
                principal=principal,
            )
            if gate_resolved and gate_decision == _PLAN_GATE_DECISION_APPROVE:
                campaign = await _mark_plan_brief_gate_resolved(
                    campaign_repo=campaign_repo,
                    campaign=campaign,
                )
                logger.info(
                    "Resolved planning workflow gate %s for campaign %s",
                    _sanitize_log(_PLAN_BRIEF_GATE_NODE_ID),
                    _sanitize_log(campaign.id),
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to send plan feedback: {exc}",
            ) from exc
        return {"status": "sent", "session_id": campaign.session_id}

    @router.post("/plan", response_model=PlanSessionResponse, status_code=201)
    async def spawn_plan_session(
        body: PlanRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        workflow_repo: WorkflowRepository = Depends(resolve_workflow_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> PlanSessionResponse:
        """Spawn a workflow-backed planning session via Volundr."""
        settings = request.app.state.settings
        model = body.model or settings.dispatch.default_model
        repo = body.repo.strip()

        auth_token = extract_bearer_token(request)

        planner_template = settings.planner.planner_system_prompt
        if planner_template:
            planner_prompt = planner_template.format(
                repo=repo or "No repository selected",
                base_branch=body.base_branch,
                spec=body.spec,
            )
        else:
            planner_prompt = (
                f"Help decompose this specification into phases and runs.\n\n"
                f"Repository: {repo or 'No repository selected'}\n"
                f"Base branch: {body.base_branch}\n"
                f"Specification:\n{body.spec}"
            )

        try:
            workflow = await _resolve_planning_workflow(
                workflow_repo,
                principal,
                body.workflow_id,
            )
            plan_name = _plan_name(body.spec)
            provenance = {
                "surface": "ting.plan",
                "repo": repo,
                "base_branch": body.base_branch,
            }
            metadata = {
                "surface": "ting.plan",
                "spec": body.spec,
                "repo": repo,
                "base_branch": body.base_branch,
            }
            if body.connection_id:
                provenance["connection_id"] = body.connection_id
                metadata["connection_id"] = body.connection_id
            execution = await launch_workflow_execution(
                request=request,
                workflow=workflow,
                launch=WorkflowLaunchBody(
                    prompt=planner_prompt,
                    sessionName=_session_name(f"plan-{_slugify(plan_name)}"),
                    repo=repo,
                    branch=body.base_branch,
                    model=model,
                    connectionId=body.connection_id,
                    provenance=provenance,
                ),
                volundr_factory=volundr_factory,
                principal=principal,
                bearer_token=auth_token,
            )
            slug = await _reserve_plan_slug(campaign_repo, execution.slug)
            now = datetime.now(UTC)
            stage_state = _initial_plan_stage_state(execution.workflow_snapshot, now)
            campaign_status = _campaign_status_from_session(execution.session.status)
            campaign = WorkflowCampaign(
                id=uuid4(),
                slug=slug,
                name=plan_name,
                owner_id=principal.user_id,
                workflow_id=workflow.id,
                workflow_version=workflow.version,
                workflow_name=workflow.name,
                workflow_snapshot=execution.workflow_snapshot,
                session_id=execution.session.id,
                session_name=execution.session.name,
                status=campaign_status,
                active_stage_id=stage_state[0].stage_id if stage_state else None,
                stage_state=stage_state,
                metadata={**metadata, "cluster_name": execution.session.cluster_name},
                created_at=now,
                updated_at=now,
                last_activity_at=now,
                completed_at=now if campaign_status == WorkflowCampaignStatus.COMPLETED else None,
            )
            saved = await campaign_repo.save_campaign(campaign)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Failed to spawn planning session: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to spawn planning session: {exc}",
            )

        return _to_plan_session_response(saved, execution.session.chat_endpoint)

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

        return ExtractStructureResponse(found=True, structure=_to_saga_structure_response(result))

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

    @router.put("/{saga_id}/repos", response_model=SagaListItem)
    async def assign_repos(
        saga_id: str,
        body: SagaReposAssignmentRequest,
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

        default_branch = body.base_branch.strip() or saga.base_branch or "main"
        raw_refs = (
            [(entry.repo, entry.branch) for entry in body.repo_refs]
            if body.repo_refs
            else [(repo_name, default_branch) for repo_name in body.repos]
        )
        repo_branches: dict[str, str] = {}
        for repo_name, branch_name in raw_refs:
            repo_ref = repo_name.strip()
            if not repo_ref or repo_ref in repo_branches:
                continue
            repo_branches[repo_ref] = branch_name.strip() or default_branch
        if not repo_branches:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="At least one repository is required",
            )

        updated_saga = replace(
            saga,
            repos=list(repo_branches),
            repo_branches=repo_branches,
            base_branch=next(iter(repo_branches.values())),
        )
        await repo.save_saga(updated_saga)

        project = await _find_project(updated_saga.tracker_id, adapters)
        phase_summary = await _build_phase_summary(repo, updated_saga.id)
        instance_name = await _resolve_instance_name(request, principal, updated_saga.instance_id)
        return SagaListItem(
            id=str(updated_saga.id),
            tracker_id=updated_saga.tracker_id,
            tracker_type=updated_saga.tracker_type,
            slug=updated_saga.slug,
            name=project.name if project else updated_saga.name,
            repos=updated_saga.repos,
            repo_branches=updated_saga.repo_branches,
            repo_refs=_repo_refs(updated_saga),
            feature_branch=updated_saga.feature_branch,
            status=updated_saga.status.value.lower(),
            progress=_display_progress(updated_saga, project, phase_summary),
            milestone_count=project.milestone_count if project else 0,
            issue_count=project.issue_count if project else 0,
            url=project.url if project else "",
            base_branch=updated_saga.base_branch,
            confidence=updated_saga.confidence,
            created_at=updated_saga.created_at.isoformat(),
            phase_summary=phase_summary,
            workflow_id=str(updated_saga.workflow_id) if updated_saga.workflow_id else None,
            workflow=workflow_name_from_snapshot(updated_saga.workflow_snapshot),
            workflow_version=updated_saga.workflow_version,
            instance_id=updated_saga.instance_id,
            instance_name=instance_name,
            target_tags=updated_saga.target_tags,
            target_match=updated_saga.target_match,
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
            instance_registry = getattr(request.app.state, "instance_registry", None)
            if instance_registry is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Instance registry not configured",
                )
            instance = await instance_registry.get_volundr_target(principal, instance_id)
            if instance is None:
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
                identifier = ""
                url = ""
                try:
                    tracker_run = await tracker.get_run(tracker_run_id)
                    identifier = tracker_run.identifier
                    url = tracker_run.url
                except Exception:
                    logger.debug(
                        "Tracker run metadata unavailable for %s",
                        _sanitize_log(tracker_run_id),
                        exc_info=True,
                    )
                run = replace(
                    run,
                    tracker_id=tracker_run_id,
                    identifier=identifier,
                    url=url,
                )
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
