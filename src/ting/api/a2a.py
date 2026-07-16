"""A2A task endpoint — Ting workflows as A2A tasks over JSON-RPC.

Design constraint: no A2A-specific persistence. The A2A ``taskId`` IS the
workflow campaign slug; ``GetTask`` synthesizes the protocol Task from the
campaign record on read, and the campaign projector keeps that record fresh
from live session state. SendMessage creates the campaign row alongside the
launch, which is also what makes the run visible to the projector.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
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
from ting.api.dispatch import resolve_volundr_factory
from ting.api.research import (
    _campaign_status_from_session,
    _emit_campaign_event,
    _initial_stage_state,
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
        }
    )
    return task


class WorkflowTaskHandler(RequestHandler):
    """Per-request A2A handler bound to the caller's identity.

    Streaming, push notifications, task listing, and the extended agent
    card are deliberately unsupported: the agent card advertises
    ``streaming=false`` / ``pushNotifications=false``, and polling
    ``GetTask`` is the supported follow mechanism.
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
            raise UnsupportedOperationError(
                "task continuation is not supported yet; send a new message "
                "without taskId to launch a workflow"
            )
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
        return campaign_to_task(campaign)

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
        raise UnsupportedOperationError("the extended agent card is not supported")

    # -- Internals ------------------------------------------------------ #

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
