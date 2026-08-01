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
from niuu.observability import get_observability
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
from ting.domain.workflow_snapshot import workflow_artifact_paths_from_snapshot
from ting.ports.a2a_push import A2APushDispatcherPort
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)

A2A_ENDPOINT_PREFIX = "/api/v1/ting/a2a"
A2A_SURFACE = "a2a"
LAUNCH_SCOPE = "ting:workflow:launch"
_CANCELED_KEY = "a2a_canceled"
_CONTEXT_ID_KEY = "a2a_context_id"
_MESSAGE_ID_KEY = "a2a_message_id"
_WORKFLOW_SLUG_KEY = "a2a_workflow_slug"

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
            "skillId": str(campaign.workflow_id),
            "workflowName": campaign.workflow_name,
            "campaignName": campaign.name,
            **({"repo": str(campaign.metadata["repo"])} if campaign.metadata.get("repo") else {}),
            **(
                {"branch": str(campaign.metadata["branch"])}
                if campaign.metadata.get("branch")
                else {}
            ),
            **(
                {"error": str(campaign.metadata["failure_error"])}
                if campaign.metadata.get("failure_error")
                else {}
            ),
        }
    )
    return task


class WorkflowTaskHandler(RequestHandler):
    """Per-request A2A handler bound to the caller's identity.

    Streaming remains deliberately unsupported. Task listing and durable push
    notifications are supported; the agent card only advertises push when its
    encrypted callback outbox is configured.
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
        prompt = _prompt_from_message(message)
        if not prompt:
            raise InvalidParamsError("message must include a non-empty text part")

        existing = await self._campaign_for_message(message.message_id)
        if existing is not None:
            requested_skill_id = str(metadata.get("skillId") or "")
            if requested_skill_id != str(existing.workflow_id):
                raise InvalidParamsError(
                    f"messageId {message.message_id!r} already belongs to another skill"
                )
            get_observability().event(
                "ting.a2a.workflow.launch.reused",
                attributes={
                    "a2a.message.id": message.message_id,
                    "a2a.task.id": existing.slug,
                    "a2a.skill.id": str(existing.workflow_id),
                },
            )
            return campaign_to_task(existing)

        workflow = await self._resolve_workflow(metadata)
        telemetry = get_observability()
        trace_context = _trace_context(metadata)
        attributes = {
            "a2a.message.id": message.message_id,
            "a2a.skill.id": str(workflow.id),
            "ting.workflow.name": workflow.name,
        }
        with telemetry.span(
            "ting.a2a.workflow.launch",
            attributes=attributes,
            carrier=trace_context,
        ) as span:
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
                    **(
                        {"trace_context": outbound_trace_context}
                        if (outbound_trace_context := telemetry.inject() or trace_context)
                        else {}
                    ),
                },
            )
            try:
                execution = await launch_workflow_execution(
                    request=self._request,
                    workflow=workflow,
                    launch=launch,
                    volundr_factory=self._volundr_factory,
                    principal=self._principal,
                    bearer_token=self._bearer_token,
                )
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                telemetry.event(
                    "ting.a2a.workflow.launch.failed",
                    attributes={**attributes, "error.type": type(exc).__name__},
                    content={"error": str(exc)},
                )
                raise
            span.set_attribute("ting.session.id", str(execution.session.id))
            telemetry.event(
                "ting.a2a.workflow.launched",
                attributes={
                    **attributes,
                    "ting.session.id": str(execution.session.id),
                },
            )

        campaign_id = uuid4()
        slug = f"{execution.slug or 'workflow'}-{campaign_id.hex[:12]}"
        now = datetime.now(UTC)
        stage_state = _initial_stage_state(execution.workflow_snapshot, now)
        campaign = WorkflowCampaign(
            id=campaign_id,
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
                _MESSAGE_ID_KEY: message.message_id,
                _WORKFLOW_SLUG_KEY: execution.slug,
                # Code-output pointers: for code workflows the durable
                # artifact is the branch the session pushes, not a Mimir page.
                **({"repo": launch.repo} if launch.repo else {}),
                **({"branch": launch.branch} if launch.branch else {}),
            },
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            connection_id=execution.connection_id,
        )
        try:
            saved = await self._campaign_repo.save_campaign(campaign)
        except Exception:
            try:
                await execution.adapter.stop_session(
                    execution.session.id,
                    auth_token=self._bearer_token,
                    principal=self._principal,
                )
            except Exception:
                logger.exception(
                    "Failed to stop orphaned session %s after campaign persistence failed",
                    execution.session.id,
                )
            raise
        await _emit_campaign_event(self._request, "workflow.campaign.created", saved)
        await self._queue_push(saved)
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
            await self._attach_pending_questions(task, campaign)
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

        adapter = await self._campaign_adapter(campaign)
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
        await self._queue_push(saved)
        return campaign_to_task(saved)

    # -- Additional protocol surface ------------------------------------ #

    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        campaigns = await self._campaign_repo.list_campaigns(
            owner_id=self._principal.user_id,
        )
        tasks: list[Task] = []
        requested_state = int(params.status)
        after = (
            params.status_timestamp_after.ToDatetime(tzinfo=UTC)
            if params.HasField("status_timestamp_after")
            else None
        )
        for campaign in campaigns:
            task = campaign_to_task(campaign)
            if params.context_id and task.context_id != params.context_id:
                continue
            if requested_state and int(task.status.state) != requested_state:
                continue
            if after is not None and campaign.updated_at.astimezone(UTC) <= after:
                continue
            tasks.append(task)

        tasks.sort(key=lambda item: item.status.timestamp.ToDatetime(), reverse=True)
        try:
            offset = int(params.page_token or 0)
        except ValueError as exc:
            raise InvalidParamsError("pageToken must be a non-negative integer") from exc
        if offset < 0:
            raise InvalidParamsError("pageToken must be a non-negative integer")

        page_size = int(params.page_size) if params.page_size > 0 else len(tasks)
        selected = tasks[offset : offset + page_size]
        campaigns_by_slug = {campaign.slug: campaign for campaign in campaigns}
        for task in selected:
            campaign = campaigns_by_slug[task.id]
            if params.include_artifacts and task.status.state == TaskState.TASK_STATE_COMPLETED:
                await self._attach_artifacts(task, campaign)
            if task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
                await self._attach_pending_questions(task, campaign)
                await self._attach_pending_gates(task, campaign)

        next_offset = offset + len(selected)
        return ListTasksResponse(
            tasks=selected,
            next_page_token=str(next_offset) if next_offset < len(tasks) else "",
            page_size=page_size,
            total_size=len(tasks),
        )

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
        task_id = str(params.task_id or "").strip()
        if not task_id:
            raise InvalidParamsError("taskId is required")
        campaign = await self._owned_campaign(task_id)
        dispatcher = self._push_dispatcher()
        self._validate_push_config(dispatcher, params)
        saved = await dispatcher.save_config(
            task_id=task_id,
            owner_id=self._principal.user_id,
            config=params,
        )
        await dispatcher.queue_campaign(campaign)
        return saved

    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        task_id = str(params.task_id or "").strip()
        config_id = str(params.id or "").strip()
        if not task_id or not config_id:
            raise InvalidParamsError("taskId and id are required")
        await self._owned_campaign(task_id)
        config = await self._push_dispatcher().get_config(
            task_id=task_id,
            owner_id=self._principal.user_id,
            config_id=config_id,
        )
        if config is None:
            raise InvalidParamsError("push notification config was not found")
        return config

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        task_id = str(params.task_id or "").strip()
        if not task_id:
            raise InvalidParamsError("taskId is required")
        await self._owned_campaign(task_id)
        dispatcher = self._push_dispatcher()
        configs = await dispatcher.list_configs(
            task_id=task_id,
            owner_id=self._principal.user_id,
        )
        try:
            offset = int(params.page_token or 0)
        except ValueError as exc:
            raise InvalidParamsError("pageToken must be a non-negative integer") from exc
        if offset < 0:
            raise InvalidParamsError("pageToken must be a non-negative integer")
        page_size = min(
            int(params.page_size) if params.page_size > 0 else dispatcher.max_configs_page_size,
            dispatcher.max_configs_page_size,
        )
        selected = configs[offset : offset + page_size]
        next_offset = offset + len(selected)
        return ListTaskPushNotificationConfigsResponse(
            configs=selected,
            next_page_token=str(next_offset) if next_offset < len(configs) else "",
        )

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        task_id = str(params.task_id or "").strip()
        config_id = str(params.id or "").strip()
        if not task_id or not config_id:
            raise InvalidParamsError("taskId and id are required")
        await self._owned_campaign(task_id)
        deleted = await self._push_dispatcher().delete_config(
            task_id=task_id,
            owner_id=self._principal.user_id,
            config_id=config_id,
        )
        if not deleted:
            raise InvalidParamsError("push notification config was not found")

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

    async def _campaign_for_message(self, message_id: str) -> WorkflowCampaign | None:
        campaigns = await self._campaign_repo.list_campaigns(
            owner_id=self._principal.user_id,
        )
        return next(
            (
                campaign
                for campaign in campaigns
                if str(campaign.metadata.get(_MESSAGE_ID_KEY) or "") == message_id
            ),
            None,
        )

    async def _continue_task(self, message: Message) -> Task:
        """Handle a reply on an INPUT_REQUIRED task.

        Two reply kinds, routed by ``metadata.gateDecision``:
        - present: resolve a pending workflow gate (approve/request_changes)
        - absent: the message text answers a pending peer question
          (``help_needed``) and is delivered to the asking peer
        """
        campaign = await self._owned_campaign(message.task_id)
        task = campaign_to_task(campaign)
        if task.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
            raise InvalidParamsError(
                f"task {message.task_id} is not awaiting input; "
                "replies are only accepted in the INPUT_REQUIRED state"
            )

        metadata = MessageToDict(message.metadata)
        raw_decision = str(metadata.get("gateDecision") or "").strip().lower()
        if not raw_decision:
            return await self._answer_question(message, campaign, metadata)
        decision = _GATE_DECISIONS.get(raw_decision)
        if decision is None:
            raise InvalidParamsError('metadata.gateDecision must be "approve" or "request_changes"')
        notes = _prompt_from_message(message)
        if decision == "CHANGES_REQUESTED" and not notes:
            raise InvalidParamsError(
                "a text part with review notes is required when requesting changes"
            )

        adapter = await self._campaign_adapter(campaign)
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
        await self._queue_push(saved)
        return campaign_to_task(saved)

    async def _answer_question(
        self,
        message: Message,
        campaign: WorkflowCampaign,
        metadata: dict[str, Any],
    ) -> Task:
        """Deliver a plain-text reply to the peer whose question blocked the task."""
        answer = _prompt_from_message(message)
        if not answer:
            raise InvalidParamsError(
                "a reply without gateDecision answers a pending question and "
                "must include a non-empty text part"
            )
        adapter = await self._campaign_adapter(campaign)
        if adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No Volundr connection is available for this user",
            )
        requests = await adapter.get_help_requests(
            campaign.session_id,
            auth_token=self._bearer_token,
            principal=self._principal,
        )
        request_id = _select_pending_question_id(
            requests,
            request_id=str(metadata.get("requestId") or ""),
        )
        if request_id is None:
            raise InvalidParamsError(
                f"task {message.task_id} has no pending question matching the reply"
            )
        await adapter.answer_help_request(
            campaign.session_id,
            request_id,
            answer,
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
        await self._queue_push(saved)
        return campaign_to_task(saved)

    def _push_dispatcher(self) -> A2APushDispatcherPort:
        dispatcher = getattr(self._request.app.state, "a2a_push_dispatcher", None)
        if dispatcher is None or not dispatcher.enabled:
            raise UnsupportedOperationError("push notifications are not configured")
        return dispatcher

    async def _queue_push(self, campaign: WorkflowCampaign) -> None:
        dispatcher = getattr(self._request.app.state, "a2a_push_dispatcher", None)
        if dispatcher is not None and dispatcher.enabled:
            await dispatcher.queue_campaign(campaign)

    @staticmethod
    def _validate_push_config(
        dispatcher: A2APushDispatcherPort,
        config: TaskPushNotificationConfig,
    ) -> None:
        try:
            dispatcher.validate_config(config)
        except ValueError as exc:
            raise InvalidParamsError(str(exc)) from exc

    async def _attach_pending_questions(self, task: Task, campaign: WorkflowCampaign) -> None:
        """Attach pending peer questions to an INPUT_REQUIRED task.

        A flock peer that emitted ``help_needed`` is genuinely blocked on
        information only the commissioning agent has. Expose the question so
        the remote agent can answer it with a plain reply message (no
        gateDecision) — the answer routes back to the asking peer.
        """
        adapter = await self._campaign_adapter(campaign)
        if adapter is None:
            return
        try:
            requests = await adapter.get_help_requests(
                campaign.session_id,
                auth_token=self._bearer_token,
                principal=self._principal,
            )
        except Exception as exc:
            logger.warning(
                "a2a: failed to fetch pending help requests for task %s: %s", task.id, exc
            )
            return
        pending = [
            _pending_question_view(request)
            for request in requests
            if isinstance(request, dict)
            and str(request.get("status") or "").strip().lower() == "pending"
        ]
        if pending:
            task.metadata.update({"pendingQuestions": pending})

    async def _attach_pending_gates(self, task: Task, campaign: WorkflowCampaign) -> None:
        """Attach the pending gate question(s) to an INPUT_REQUIRED task.

        The gate reply channel (SendMessage + ``metadata.gateDecision``) is
        useless to a remote agent that cannot see WHAT the workflow is asking,
        so expose each pending gate's label/condition/instructions in task
        metadata. Best-effort: the reply path re-fetches gates
        authoritatively, so a transient fetch failure only degrades context,
        never correctness.
        """
        adapter = await self._campaign_adapter(campaign)
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
        """Project configured durable Mimir outputs onto ``task.artifacts``.

        Only called for COMPLETED tasks: artifacts are outputs, and skipping
        the Mimir round-trips on every mid-run poll keeps GetTask cheap.
        Small text pages are inlined; anything over the configured limit
        becomes a url part pointing at the authenticated artifact route.

        Workflows can declare exact ``artifactPaths`` in their graph. Paths may
        contain ``{slug}``, resolved from the immutable launch slug. Legacy
        research campaigns retain their campaign-prefix discovery behavior.
        """
        settings = self._request.app.state.settings
        adapter = _resolve_campaign_mimir_port(campaign, settings)
        if adapter is None:
            return
        loaded: list[Any] = []
        artifact_slug = str(campaign.metadata.get(_WORKFLOW_SLUG_KEY) or campaign.slug).strip()
        configured_paths = workflow_artifact_paths_from_snapshot(
            campaign.workflow_snapshot,
            slug=artifact_slug,
        )
        for path in configured_paths:
            try:
                loaded.append(await adapter.get_page(path))
            except FileNotFoundError:
                continue
        if not configured_paths and not _declares_artifact_paths(campaign):
            prefix = f"research/campaigns/{campaign.slug}/"
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
        raw_id = str(metadata.get("skillId") or "").strip()
        if not raw_id:
            raise InvalidParamsError("message metadata must include skillId")
        try:
            workflow_id = UUID(raw_id)
        except ValueError as exc:
            raise InvalidParamsError(f"skillId is not a valid UUID: {raw_id}") from exc
        workflow = await self._workflow_repo.get_workflow(workflow_id)
        if workflow is None or not _can_view_workflow(workflow, self._principal):
            raise InvalidParamsError(f"unknown skill: {raw_id}")
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

    async def _campaign_adapter(self, campaign: WorkflowCampaign):
        """Resolve the Volundr adapter for the connection this campaign's
        session was launched on.

        The owner's primary connection is only a fallback for campaigns
        persisted before connection affinity existed — a session launched on
        a non-default cluster does not exist on the primary.
        """
        if campaign.connection_id:
            adapter = await self._volundr_factory.for_connection(
                campaign.owner_id, campaign.connection_id
            )
            if adapter is not None:
                return adapter
            logger.warning(
                "Campaign %s connection %s no longer resolves; refusing to retarget",
                campaign.slug,
                campaign.connection_id,
            )
            return None
        return await self._volundr_factory.primary_for_owner(campaign.owner_id)


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


def _pending_question_view(request: dict) -> dict[str, Any]:
    """Project a skuld PendingHelpRequest dict to the A2A-facing question."""
    attempted = request.get("attempted")
    return {
        "requestId": str(request.get("id") or ""),
        "persona": str(request.get("persona") or ""),
        "question": str(request.get("summary") or ""),
        "reason": str(request.get("reason") or ""),
        "recommendation": str(request.get("recommendation") or ""),
        "attempted": [str(item) for item in attempted] if isinstance(attempted, list) else [],
    }


def _select_pending_question_id(
    requests: list[dict],
    *,
    request_id: str,
) -> str | None:
    wanted = request_id.strip()
    for request in requests:
        if not isinstance(request, dict):
            continue
        candidate = str(request.get("id") or "").strip()
        if str(request.get("status") or "").strip().lower() != "pending":
            continue
        if wanted and candidate != wanted:
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


def _declares_artifact_paths(campaign: WorkflowCampaign) -> bool:
    graph = campaign.workflow_snapshot.get("graph")
    return isinstance(graph, dict) and ("artifactPaths" in graph or "artifact_paths" in graph)


def _prompt_from_message(message: Message) -> str:
    texts = [part.text for part in message.parts if part.text]
    return "\n\n".join(text for text in texts if text.strip()).strip()


def _optional_str(metadata: dict[str, Any], key: str) -> str | None:
    value = str(metadata.get(key) or "").strip()
    return value or None


def _trace_context(metadata: dict[str, Any]) -> dict[str, str]:
    """Accept only W3C propagation fields from A2A message metadata."""
    raw = metadata.get("traceContext") or metadata.get("trace_context")
    if not isinstance(raw, dict):
        return {}
    return {key: str(raw[key]) for key in ("traceparent", "tracestate", "baggage") if raw.get(key)}


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
