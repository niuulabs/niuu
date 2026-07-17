"""A2A task endpoint — Ting workflows as A2A tasks over JSON-RPC.

Design constraint: no A2A-specific persistence. The A2A ``taskId`` IS the
workflow campaign slug; ``GetTask`` synthesizes the protocol Task from the
campaign record on read, and the campaign projector keeps that record fresh
from live session state. SendMessage creates the campaign row alongside the
launch, which is also what makes the run visible to the projector.
"""

from __future__ import annotations

import logging
import mimetypes
import posixpath
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a.utils.errors import (
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from google.protobuf.json_format import MessageToDict

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import token_has_scope
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.a2a_card import _endpoint_url as _card_endpoint_url
from ting.api.a2a_card import build_agent_card
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import (
    _campaign_status_from_session,
    _emit_campaign_event,
    _initial_stage_state,
    _resolve_campaign_mimir_port,
    resolve_workflow_campaign_repo,
)
from ting.api.workflows import (
    WorkflowLaunchBody,
    _can_view_workflow,
    launch_workflow_execution,
    resolve_workflow_repo,
)
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)

A2A_ENDPOINT_PREFIX = "/api/v1/ting/a2a"
A2A_SURFACE = "a2a"
LAUNCH_SCOPE = "ting:workflow:launch"
_CANCELED_KEY = "a2a_canceled"
_CONTEXT_ID_KEY = "a2a_context_id"

_STATUS_TO_STATE: dict[WorkflowCampaignStatus, int] = {
    WorkflowCampaignStatus.PENDING: TaskState.TASK_STATE_SUBMITTED,
    WorkflowCampaignStatus.RUNNING: TaskState.TASK_STATE_WORKING,
    WorkflowCampaignStatus.BLOCKED: TaskState.TASK_STATE_INPUT_REQUIRED,
    WorkflowCampaignStatus.COMPLETED: TaskState.TASK_STATE_COMPLETED,
    WorkflowCampaignStatus.FAILED: TaskState.TASK_STATE_FAILED,
}
_TERMINAL_STATUSES = frozenset({WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED})

# Reply convention for INPUT_REQUIRED tasks: metadata.gateDecision selects the
# outcome, the message text becomes the reviewer comment.
_GATE_DECISIONS: dict[str, str] = {
    "approve": "APPROVE",
    "request_changes": "CHANGES_REQUESTED",
}
_PENDING_GATE_STATUSES = frozenset({"", "pending", "open", "waiting", "help_needed", "blocked"})


def campaign_to_task(campaign: WorkflowCampaign) -> Task:
    """Synthesize the A2A Task view of a workflow campaign."""
    task = Task(
        id=campaign.slug,
        context_id=str(campaign.metadata.get(_CONTEXT_ID_KEY) or campaign.slug),
    )
    if campaign.metadata.get(_CANCELED_KEY):
        task.status.state = TaskState.TASK_STATE_CANCELED
    else:
        task.status.state = _STATUS_TO_STATE[campaign.status]
    task.status.timestamp.FromDatetime(campaign.updated_at.astimezone(UTC))
    task.metadata.update(
        {
            "sessionId": campaign.session_id,
            "workflowId": str(campaign.workflow_id),
            "workflowName": campaign.workflow_name,
            "campaignName": campaign.name,
            **({"repo": str(campaign.metadata["repo"])} if campaign.metadata.get("repo") else {}),
            **(
                {"branch": str(campaign.metadata["branch"])}
                if campaign.metadata.get("branch")
                else {}
            ),
        }
    )
    return task


class WorkflowTaskHandler(RequestHandler):
    """Per-request A2A handler bound to the caller's identity.

    Streaming, push notifications, and task listing are deliberately
    unsupported: the agent card advertises ``streaming=false`` /
    ``pushNotifications=false``, and polling ``GetTask`` is the supported
    follow mechanism. The extended agent card IS supported — it adds the
    caller's user-scope workflows to the public catalog.
    """

    def __init__(
        self,
        *,
        request: Request,
        principal: Principal,
        bearer_token: str | None,
        workflow_repo: WorkflowRepository,
        campaign_repo: WorkflowCampaignRepository,
        volundr_factory: VolundrFactory,
    ) -> None:
        self._request = request
        self._principal = principal
        self._bearer_token = bearer_token
        self._workflow_repo = workflow_repo
        self._campaign_repo = campaign_repo
        self._volundr_factory = volundr_factory

    # -- Implemented methods ------------------------------------------- #

    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Task | Message:
        message = params.message
        if message.task_id:
            return await self._continue_task(message)
        if not token_has_scope(self._bearer_token or "", LAUNCH_SCOPE):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Token lacks required build scope: {LAUNCH_SCOPE}",
            )

        metadata = _merged_metadata(params)
        workflow = await self._resolve_workflow(metadata)
        prompt = _prompt_from_message(message)
        if not prompt:
            raise InvalidParamsError("message must include a non-empty text part")

        launch = WorkflowLaunchBody(
            prompt=prompt,
            sessionName=_optional_str(metadata, "sessionName"),
            repo=str(metadata.get("repo") or ""),
            branch=str(metadata.get("branch") or ""),
            model=str(metadata.get("model") or ""),
            connectionId=_optional_str(metadata, "connectionId"),
            provenance={
                "surface": A2A_SURFACE,
                "a2a_message_id": message.message_id,
            },
        )
        execution = await launch_workflow_execution(
            request=self._request,
            workflow=workflow,
            launch=launch,
            volundr_factory=self._volundr_factory,
            principal=self._principal,
            bearer_token=self._bearer_token,
        )

        slug = await _reserve_task_slug(self._campaign_repo, execution.slug)
        now = datetime.now(UTC)
        stage_state = _initial_stage_state(execution.workflow_snapshot, now)
        campaign = WorkflowCampaign(
            id=uuid4(),
            slug=slug,
            name=execution.session.name,
            owner_id=self._principal.user_id,
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
                "surface": A2A_SURFACE,
                "prompt": prompt,
                "cluster_name": execution.session.cluster_name,
                _CONTEXT_ID_KEY: message.context_id or slug,
                # Code-output pointers: for code workflows the durable
                # artifact is the branch the session pushes, not a Mimir page.
                **({"repo": launch.repo} if launch.repo else {}),
                **({"branch": launch.branch} if launch.branch else {}),
            },
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        saved = await self._campaign_repo.save_campaign(campaign)
        await _emit_campaign_event(self._request, "workflow.campaign.created", saved)
        return campaign_to_task(saved)

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        campaign = await self._owned_campaign(params.id)
        task = campaign_to_task(campaign)
        if task.status.state == TaskState.TASK_STATE_COMPLETED:
            await self._attach_artifacts(task, campaign)
        if task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            await self._attach_pending_gates(task, campaign)
        return task

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        campaign = await self._owned_campaign(params.id)
        if campaign.status in _TERMINAL_STATUSES or campaign.metadata.get(_CANCELED_KEY):
            raise TaskNotCancelableError(f"task {params.id} is already in a terminal state")

        adapter = await self._volundr_factory.primary_for_owner(campaign.owner_id)
        if adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No Volundr connection is available for this user",
            )
        await adapter.stop_session(
            campaign.session_id,
            auth_token=self._bearer_token,
            principal=self._principal,
        )

        now = datetime.now(UTC)
        updated = WorkflowCampaign(
            **{
                **campaign.__dict__,
                "status": WorkflowCampaignStatus.FAILED,
                "metadata": {**campaign.metadata, _CANCELED_KEY: True},
                "updated_at": now,
                "last_activity_at": now,
                "completed_at": campaign.completed_at or now,
            }
        )
        saved = await self._campaign_repo.save_campaign(updated)
        await _emit_campaign_event(self._request, "workflow.campaign.updated", saved)
        return campaign_to_task(saved)

    # -- Unsupported protocol surface ----------------------------------- #

    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        raise UnsupportedOperationError("task listing is not supported")

    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ):
        raise UnsupportedOperationError("streaming is not supported; poll GetTask")
        yield  # pragma: no cover — makes this an async generator

    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ):
        raise UnsupportedOperationError("streaming is not supported; poll GetTask")
        yield  # pragma: no cover — makes this an async generator

    async def on_create_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError("push notifications are not supported")

    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError("push notifications are not supported")

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        raise UnsupportedOperationError("push notifications are not supported")

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        raise UnsupportedOperationError("push notifications are not supported")

    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        """Authenticated card: the caller's user-scope workflows join the catalog.

        The public well-known card only advertises system-scope workflows;
        this is how a principal discovers their own workflows as skills.
        """
        settings = self._request.app.state.settings
        workflows = await self._workflow_repo.list_workflows(
            owner_id=self._principal.user_id,
        )
        return build_agent_card(
            workflows=workflows,
            config=settings.a2a,
            endpoint_url=_card_endpoint_url(self._request, settings.a2a),
        )

    # -- Internals ------------------------------------------------------ #

    async def _continue_task(self, message: Message) -> Task:
        """Resolve a pending workflow gate from a reply on an INPUT_REQUIRED task."""
        campaign = await self._owned_campaign(message.task_id)
        task = campaign_to_task(campaign)
        if task.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
            raise InvalidParamsError(
                f"task {message.task_id} is not awaiting input; "
                "replies are only accepted in the INPUT_REQUIRED state"
            )

        metadata = MessageToDict(message.metadata)
        raw_decision = str(metadata.get("gateDecision") or "").strip().lower()
        decision = _GATE_DECISIONS.get(raw_decision)
        if decision is None:
            raise InvalidParamsError('metadata.gateDecision must be "approve" or "request_changes"')
        notes = _prompt_from_message(message)
        if decision == "CHANGES_REQUESTED" and not notes:
            raise InvalidParamsError(
                "a text part with review notes is required when requesting changes"
            )

        adapter = await self._volundr_factory.primary_for_owner(campaign.owner_id)
        if adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No Volundr connection is available for this user",
            )
        gates = await adapter.get_workflow_gates(
            campaign.session_id,
            auth_token=self._bearer_token,
            principal=self._principal,
        )
        gate_id = _select_pending_gate_id(
            gates,
            gate_id=str(metadata.get("gateId") or ""),
            node_id=str(metadata.get("nodeId") or ""),
        )
        if gate_id is None:
            raise InvalidParamsError(
                f"task {message.task_id} has no pending gate matching the reply"
            )
        await adapter.resolve_workflow_gate(
            campaign.session_id,
            gate_id,
            decision,
            notes=notes,
            source=A2A_SURFACE,
            auth_token=self._bearer_token,
            principal=self._principal,
        )

        # Optimistically report the task back in WORKING; the campaign
        # projector re-syncs the real session state on its next tick.
        now = datetime.now(UTC)
        updated = WorkflowCampaign(
            **{
                **campaign.__dict__,
                "status": WorkflowCampaignStatus.RUNNING,
                "updated_at": now,
                "last_activity_at": now,
            }
        )
        saved = await self._campaign_repo.save_campaign(updated)
        await _emit_campaign_event(self._request, "workflow.campaign.updated", saved)
        return campaign_to_task(saved)

    async def _attach_pending_gates(self, task: Task, campaign: WorkflowCampaign) -> None:
        """Attach the pending gate question(s) to an INPUT_REQUIRED task.

        The gate reply channel (SendMessage + ``metadata.gateDecision``) is
        useless to a remote agent that cannot see WHAT the workflow is asking,
        so expose each pending gate's label/condition/instructions in task
        metadata. Best-effort: the reply path re-fetches gates
        authoritatively, so a transient fetch failure only degrades context,
        never correctness.
        """
        adapter = await self._volundr_factory.primary_for_owner(campaign.owner_id)
        if adapter is None:
            return
        try:
            gates = await adapter.get_workflow_gates(
                campaign.session_id,
                auth_token=self._bearer_token,
                principal=self._principal,
            )
        except Exception as exc:
            logger.warning("a2a: failed to fetch pending gates for task %s: %s", task.id, exc)
            return
        pending = [
            _pending_gate_view(gate)
            for gate in gates
            if isinstance(gate, dict)
            and str(gate.get("status") or "").strip().lower() in _PENDING_GATE_STATUSES
        ]
        if pending:
            task.metadata.update({"pendingGates": pending})

    async def _attach_artifacts(self, task: Task, campaign: WorkflowCampaign) -> None:
        """Project the campaign's Mimir pages onto ``task.artifacts``.

        Only called for COMPLETED tasks: artifacts are outputs, and skipping
        the Mimir round-trips on every mid-run poll keeps GetTask cheap.
        Small text pages are inlined; anything over the configured limit
        becomes a url part pointing at the authenticated artifact route.
        """
        settings = self._request.app.state.settings
        adapter = _resolve_campaign_mimir_port(campaign, settings)
        if adapter is None:
            return
        prefix = f"research/campaigns/{campaign.slug}/"
        loaded: list[Any] = []
        for meta in await adapter.list_pages(prefix=prefix):
            try:
                loaded.append(await adapter.get_page(meta.path))
            except FileNotFoundError:
                continue
        listed_paths = {page.meta.path for page in loaded}
        for name in settings.a2a.extra_artifact_files:
            path = f"{prefix}{name}"
            if path in listed_paths:
                continue
            try:
                loaded.append(await adapter.get_page(path))
            except FileNotFoundError:
                continue

        max_inline = settings.a2a.inline_artifact_max_chars
        for page in sorted(loaded, key=lambda page: page.meta.path):
            artifact = task.artifacts.add()
            artifact.artifact_id = page.meta.path
            artifact.name = page.meta.title or posixpath.basename(page.meta.path)
            part = artifact.parts.add()
            part.filename = posixpath.basename(page.meta.path)
            part.media_type = _artifact_media_type(page.meta.path)
            if len(page.content) <= max_inline:
                part.text = page.content
            else:
                part.url = self._artifact_url(campaign, page.meta.path)

    def _artifact_url(self, campaign: WorkflowCampaign, path: str) -> str:
        settings = self._request.app.state.settings
        base = settings.a2a.public_base_url.rstrip("/") or str(self._request.base_url).rstrip("/")
        return (
            f"{base}/api/v1/ting/research/campaigns/{campaign.slug}"
            f"/artifact?path={quote(path, safe='')}"
        )

    async def _resolve_workflow(self, metadata: dict[str, Any]):
        raw_id = str(metadata.get("workflowId") or "").strip()
        if not raw_id:
            raise InvalidParamsError("message metadata must include workflowId")
        try:
            workflow_id = UUID(raw_id)
        except ValueError as exc:
            raise InvalidParamsError(f"workflowId is not a valid UUID: {raw_id}") from exc
        workflow = await self._workflow_repo.get_workflow(workflow_id)
        if workflow is None or not _can_view_workflow(workflow, self._principal):
            raise InvalidParamsError(f"unknown workflow: {raw_id}")
        return workflow

    async def _owned_campaign(self, task_id: str) -> WorkflowCampaign:
        slug = str(task_id or "").strip()
        if not slug:
            raise InvalidParamsError("task id must not be empty")
        campaign = await self._campaign_repo.get_campaign_by_slug(
            slug,
            owner_id=self._principal.user_id,
        )
        if campaign is None:
            raise TaskNotFoundError(f"no task with id {slug}")
        return campaign


def _merged_metadata(params: SendMessageRequest) -> dict[str, Any]:
    """Launch parameters: message metadata is canonical, request metadata fallback."""
    merged: dict[str, Any] = {}
    merged.update(MessageToDict(params.metadata))
    merged.update(MessageToDict(params.message.metadata))
    return merged


def _select_pending_gate_id(
    gates: list[dict],
    *,
    gate_id: str,
    node_id: str,
) -> str | None:
    wanted_gate = gate_id.strip()
    wanted_node = node_id.strip()
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        candidate = str(gate.get("id") or gate.get("gate_id") or gate.get("gateId") or "").strip()
        candidate_node = str(gate.get("node_id") or gate.get("nodeId") or "").strip()
        gate_status = str(gate.get("status") or "").strip().lower()
        if gate_status not in _PENDING_GATE_STATUSES:
            continue
        if wanted_gate and candidate != wanted_gate:
            continue
        if wanted_node and candidate_node != wanted_node:
            continue
        if candidate:
            return candidate
    return None


def _pending_gate_view(gate: dict) -> dict[str, str]:
    """Project a skuld WorkflowGateState dict to the A2A-facing gate context."""
    return {
        "gateId": str(gate.get("id") or ""),
        "nodeId": str(gate.get("node_id") or gate.get("nodeId") or ""),
        "label": str(gate.get("label") or ""),
        "condition": str(gate.get("condition") or ""),
        "instructions": str(gate.get("instructions") or ""),
        "summary": str(gate.get("summary") or ""),
    }


def _artifact_media_type(path: str) -> str:
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".md"):
        return "text/markdown"
    guessed, _encoding = mimetypes.guess_type(path)
    return guessed or "text/plain"


def _prompt_from_message(message: Message) -> str:
    texts = [part.text for part in message.parts if part.text]
    return "\n\n".join(text for text in texts if text.strip()).strip()


def _optional_str(metadata: dict[str, Any], key: str) -> str | None:
    value = str(metadata.get(key) or "").strip()
    return value or None


async def _reserve_task_slug(repo: WorkflowCampaignRepository, base_slug: str) -> str:
    base = base_slug or "workflow"
    slug = base
    suffix = 2
    while await repo.get_campaign_by_slug(slug) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def create_a2a_router() -> APIRouter:
    router = APIRouter(prefix=A2A_ENDPOINT_PREFIX, tags=["A2A"])

    @router.post("")
    async def a2a_jsonrpc(
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        workflow_repo: WorkflowRepository = Depends(resolve_workflow_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> Response:
        handler = WorkflowTaskHandler(
            request=request,
            principal=principal,
            bearer_token=bearer_token,
            workflow_repo=workflow_repo,
            campaign_repo=campaign_repo,
            volundr_factory=volundr_factory,
        )
        dispatcher = JsonRpcDispatcher(request_handler=handler)
        return await dispatcher.handle_requests(request)

    return router
