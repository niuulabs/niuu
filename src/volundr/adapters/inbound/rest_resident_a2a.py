"""A2A for resident runtimes: a resident is an A2A agent, its channels are contexts.

The protocol surface is the A2A SDK's JSON-RPC dispatcher over a per-request
:class:`ResidentTaskHandler`, the same shape Ting uses for workflows. Nothing
A2A-specific is stored:

* an A2A ``contextId`` is one of the resident's own sessions (its channels): a
  session id continues that channel, any other value names the channel titled
  ``A2A · <contextId>`` (created on first use), and no value opens a new channel
  whose id is returned as the context;
* an A2A task is one turn in that channel. Its id records the channel, the turn's
  position in the channel history and a digest of the prompt, so ``GetTask``
  reads the reply back from the channel history instead of a task table.

Chat goes through :class:`ResidentRuntimeService`, so the caller needs exactly the
access the resident sessions API already demands (owner or tenant admin).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
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
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol
from a2a.utils.errors import (
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.protobuf.json_format import MessageToDict

from niuu.version import package_version
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import (
    Principal,
    ResidentCapability,
    ResidentEndpoint,
    ResidentEngine,
    ResidentRuntime,
    ResidentSession,
    ResidentSessionStatus,
)
from volundr.domain.ports import ResidentChatConnection, SessionPersonaProvider
from volundr.domain.services.resident_runtime import (
    ResidentRuntimeAccessError,
    ResidentRuntimeDeploymentError,
    ResidentRuntimeNotFoundError,
    ResidentRuntimeService,
    ResidentRuntimeValidationError,
)

RESIDENT_RUNTIMES_PATH = "/api/v1/forge/resident-runtimes"
A2A_PATH_SUFFIX = "/a2a"
AGENT_CARD_PATH_SUFFIX = "/a2a/.well-known/agent-card.json"
A2A_CARD_ENDPOINT_KIND = "a2a-card"
A2A_ENDPOINT_KIND = "a2a"
BEARER_SECURITY_SCHEME = "platformBearer"
CONTEXT_TITLE_PREFIX = "A2A · "
_DEFAULT_RAVN_PERSONA = "product-steward"
_HISTORY_FRAME_TIMEOUT_SECONDS = 15.0
_TITLE_MAX_CHARS = 120
_SKILL_SUMMARY_MAX_CHARS = 600
_A2A_CAPABILITIES = frozenset(
    {
        ResidentCapability.CHAT,
        ResidentCapability.SESSION_LIST,
        ResidentCapability.SESSION_CREATE,
    }
)


def resident_supports_a2a(runtime: ResidentRuntime) -> bool:
    """A resident is an A2A agent when it can list, open and chat in channels."""
    return _A2A_CAPABILITIES.issubset(runtime.capabilities)


def resident_a2a_urls(runtime_id: UUID | str, base_url: str) -> tuple[str, str]:
    """The Agent Card URL and JSON-RPC endpoint of one resident under *base_url*."""
    base = f"{base_url.rstrip('/')}{RESIDENT_RUNTIMES_PATH}/{runtime_id}"
    return f"{base}{AGENT_CARD_PATH_SUFFIX}", f"{base}{A2A_PATH_SUFFIX}"


def with_a2a_endpoints(runtime: ResidentRuntime, base_url: str) -> ResidentRuntime:
    """Publish the resident's A2A card and endpoint beside its backend endpoints.

    Only for a configured public origin: the Agent Directory resolves these URLs
    from other hosts, so an internal or proxy-local base would be unreachable.
    """
    if not base_url or not resident_supports_a2a(runtime):
        return runtime
    card_url, endpoint_url = resident_a2a_urls(runtime.id, base_url)
    scheme = urlsplit(card_url).scheme or "https"
    kept = [
        endpoint
        for endpoint in runtime.endpoints
        if endpoint.kind not in {A2A_CARD_ENDPOINT_KIND, A2A_ENDPOINT_KIND}
    ]
    return runtime.model_copy(
        update={
            "endpoints": [
                *kept,
                ResidentEndpoint(kind=A2A_CARD_ENDPOINT_KIND, protocol=scheme, url=card_url),
                ResidentEndpoint(
                    kind=A2A_ENDPOINT_KIND,
                    protocol=TransportProtocol.JSONRPC.value.lower(),
                    url=endpoint_url,
                ),
            ]
        }
    )


def _persona_summary(system_prompt: str) -> str:
    """The first prose paragraph of a persona prompt: who the resident is."""
    for block in re.split(r"\n\s*\n", system_prompt or ""):
        lines = [line.strip() for line in block.strip().splitlines()]
        prose = " ".join(line for line in lines if line and not line.startswith("#"))
        if prose:
            if len(prose) <= _SKILL_SUMMARY_MAX_CHARS:
                return prose
            return f"{prose[: _SKILL_SUMMARY_MAX_CHARS - 1]}…"
    return ""


def build_resident_agent_card(
    runtime: ResidentRuntime,
    *,
    endpoint_url: str,
    persona_prompt: str = "",
) -> AgentCard:
    """Project one resident and its persona onto an A2A Agent Card."""
    persona = runtime.persona_name or (
        _DEFAULT_RAVN_PERSONA if runtime.engine == ResidentEngine.RAVN else ""
    )
    summary = _persona_summary(persona_prompt)
    description = summary or (
        f"{runtime.name} is a long-lived resident agent"
        + (f" with the {persona} persona." if persona else ".")
    )
    skill_id = persona or "chat"
    card = AgentCard(
        name=runtime.name,
        description=description,
        version=package_version(),
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(
                url=endpoint_url,
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
        skills=[
            AgentSkill(
                id=skill_id,
                name=persona.replace("-", " ").title() if persona else f"Chat with {runtime.name}",
                description=(
                    f"{description}\n\n"
                    "Each A2A context is one of this resident's channels: send a "
                    "contextId returned earlier (or any stable id of your own) to keep "
                    "talking in the same channel; omit it to open a new channel. Each "
                    "message is one task whose reply is the task artifact; when the "
                    "reply is not ready yet the task is WORKING, so poll GetTask."
                ),
                tags=[tag for tag in ("resident", runtime.engine.value, persona) if tag],
                examples=[f"Ask {runtime.name} for its view on a question in its domain."],
            )
        ],
    )
    bearer = card.security_schemes[BEARER_SECURITY_SCHEME].http_auth_security_scheme
    bearer.scheme = "bearer"
    bearer.description = (
        "Platform-issued JWT (PAT or workload-identity exchange) of a principal that "
        "may use this resident's sessions: its owner or a tenant admin."
    )
    card.security_requirements.add().schemes[BEARER_SECURITY_SCHEME]
    return card


def _task_id(session_id: UUID, turn_index: int, prompt: str) -> str:
    return f"{session_id}:{turn_index}:{_prompt_digest(prompt)}"


def _prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def _parse_task_id(task_id: str) -> tuple[UUID, int, str]:
    parts = str(task_id or "").strip().split(":")
    if len(parts) != 3:
        raise TaskNotFoundError(f"no task with id {task_id}")
    try:
        return UUID(parts[0]), int(parts[1]), parts[2]
    except ValueError as exc:
        raise TaskNotFoundError(f"no task with id {task_id}") from exc


def _prompt_from_message(message: Message) -> str:
    texts = [part.text for part in message.parts if part.text]
    return "\n\n".join(text for text in texts if text.strip()).strip()


def _turn_digest(turn: dict[str, Any]) -> str:
    return _prompt_digest(str(turn.get("content") or "").strip())


def _resident_task(
    *,
    task_id: str,
    context_id: str,
    state: int,
    reply: str = "",
    error: str = "",
    session: UUID,
    runtime: ResidentRuntime,
) -> Task:
    task = Task(id=task_id, context_id=context_id)
    task.status.state = state
    task.status.timestamp.FromDatetime(datetime.now(UTC))
    task.metadata.update(
        {
            "residentId": str(runtime.id),
            "residentName": runtime.name,
            "sessionId": str(session),
            **({"error": error} if error else {}),
        }
    )
    if reply:
        artifact = task.artifacts.add()
        artifact.artifact_id = "reply"
        artifact.name = "reply"
        part = artifact.parts.add()
        part.text = reply
        part.media_type = "text/plain"
    return task


class _TurnOutcome:
    """What one turn produced while the caller waited."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.completed = False
        self.error = ""


class ResidentTaskHandler(RequestHandler):
    """Per-request A2A handler for one resident, bound to the caller's identity."""

    def __init__(
        self,
        *,
        service: ResidentRuntimeService,
        principal: Principal,
        runtime: ResidentRuntime,
        reply_timeout_seconds: float,
    ) -> None:
        self._service = service
        self._principal = principal
        self._runtime = runtime
        self._reply_timeout_seconds = reply_timeout_seconds

    # -- Implemented methods ------------------------------------------- #

    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Task | Message:
        message = params.message
        prompt = _prompt_from_message(message)
        if not prompt:
            raise InvalidParamsError("message must include a non-empty text part")
        session_id, context_id = await self._resolve_channel(message, prompt)
        connection = await self._connect(session_id)
        try:
            turns = await _history_turns(connection)
            turn_index = len(turns)
            await connection.send(
                {
                    "type": "user",
                    "content": prompt,
                    # The engine treats this as an idempotency key, so a retried
                    # A2A message is not delivered to the channel twice.
                    "request_id": message.message_id or None,
                }
            )
            wait = 0.0 if params.configuration.return_immediately else self._reply_timeout_seconds
            outcome = await _await_turn(connection, wait)
        finally:
            with suppress(Exception):
                await connection.close()
        task_id = _task_id(session_id, turn_index, prompt)
        if outcome.error:
            state = TaskState.TASK_STATE_FAILED
        elif outcome.completed:
            state = TaskState.TASK_STATE_COMPLETED
        else:
            state = TaskState.TASK_STATE_WORKING
        return _resident_task(
            task_id=task_id,
            context_id=context_id,
            state=state,
            reply="".join(outcome.text).strip() if outcome.completed else "",
            error=outcome.error,
            session=session_id,
            runtime=self._runtime,
        )

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        return await self._turn_task(params.id)

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        task = await self._turn_task(params.id)
        if task.status.state != TaskState.TASK_STATE_WORKING:
            raise TaskNotCancelableError(f"task {params.id} is already in a terminal state")
        session_id, _, _ = _parse_task_id(params.id)
        connection = await self._connect(session_id)
        try:
            await connection.send({"type": "interrupt"})
        finally:
            with suppress(Exception):
                await connection.close()
        task.status.state = TaskState.TASK_STATE_CANCELED
        task.status.timestamp.FromDatetime(datetime.now(UTC))
        return task

    # -- Unsupported protocol surface ----------------------------------- #

    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        raise UnsupportedOperationError("task listing is not supported; use GetTask")

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
        raise UnsupportedOperationError("push notifications are not supported; poll GetTask")

    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError("push notifications are not supported; poll GetTask")

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        raise UnsupportedOperationError("push notifications are not supported; poll GetTask")

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        raise UnsupportedOperationError("push notifications are not supported; poll GetTask")

    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        raise UnsupportedOperationError("this agent publishes no extended card")

    # -- Internals ------------------------------------------------------ #

    async def _resolve_channel(self, message: Message, prompt: str) -> tuple[UUID, str]:
        """Map the message's task or context onto a resident channel."""
        referenced = message.task_id or next(iter(message.reference_task_ids), "")
        if referenced:
            # A follow-up to an earlier turn continues that turn's channel.
            session_id, _, _ = _parse_task_id(referenced)
            if await self._session_for_context(str(session_id)) is None:
                raise TaskNotFoundError(f"no task with id {referenced}")
            if message.context_id and message.context_id != str(session_id):
                session = await self._session_for_context(message.context_id)
                if session is None or session.id != session_id:
                    raise InvalidParamsError("taskId does not belong to the message contextId")
            return session_id, message.context_id or str(session_id)

        if message.context_id:
            session = await self._session_for_context(message.context_id)
            if session is None:
                session = await self._create_session(f"{CONTEXT_TITLE_PREFIX}{message.context_id}")
            return session.id, message.context_id

        title = " ".join(prompt.split())
        if len(title) > _TITLE_MAX_CHARS - len(CONTEXT_TITLE_PREFIX):
            title = f"{title[: _TITLE_MAX_CHARS - len(CONTEXT_TITLE_PREFIX) - 1]}…"
        session = await self._create_session(f"{CONTEXT_TITLE_PREFIX}{title}")
        return session.id, str(session.id)

    async def _session_for_context(self, context_id: str) -> ResidentSession | None:
        sessions = await self._call(self._service.list_sessions(self._principal, self._runtime.id))
        try:
            wanted_id: UUID | None = UUID(context_id)
        except ValueError:
            wanted_id = None
        wanted_title = f"{CONTEXT_TITLE_PREFIX}{context_id}"
        return next(
            (
                session
                for session in sessions
                if session.id == wanted_id or session.title == wanted_title
            ),
            None,
        )

    async def _create_session(self, title: str) -> ResidentSession:
        return await self._call(
            self._service.create_session(self._principal, self._runtime.id, title=title)
        )

    async def _connect(self, session_id: UUID) -> ResidentChatConnection:
        return await self._call(
            self._service.connect_chat(self._principal, self._runtime.id, session_id)
        )

    async def _turn_task(self, task_id: str) -> Task:
        session_id, turn_index, digest = _parse_task_id(task_id)
        session = await self._session_for_context(str(session_id))
        if session is None:
            raise TaskNotFoundError(f"no task with id {task_id}")
        connection = await self._connect(session_id)
        try:
            turns = await _history_turns(connection)
        finally:
            with suppress(Exception):
                await connection.close()
        start = _locate_user_turn(turns, turn_index, digest)
        context_id = str(session_id)
        if start is None:
            if turn_index < len(turns):
                raise TaskNotFoundError(f"no task with id {task_id}")
            return _resident_task(
                task_id=task_id,
                context_id=context_id,
                state=TaskState.TASK_STATE_WORKING,
                session=session_id,
                runtime=self._runtime,
            )
        replies: list[str] = []
        answered_later = False
        for turn in turns[start + 1 :]:
            if turn.get("role") == "user":
                answered_later = True
                break
            replies.append(str(turn.get("content") or ""))
        reply = "\n\n".join(text for text in replies if text.strip()).strip()
        status = session.status
        if status == ResidentSessionStatus.FAILED and not reply:
            state = TaskState.TASK_STATE_FAILED
        elif reply and (answered_later or status != ResidentSessionStatus.RUNNING):
            state = TaskState.TASK_STATE_COMPLETED
        else:
            state = TaskState.TASK_STATE_WORKING
        return _resident_task(
            task_id=task_id,
            context_id=context_id,
            state=state,
            reply=reply if state == TaskState.TASK_STATE_COMPLETED else "",
            session=session_id,
            runtime=self._runtime,
        )

    @staticmethod
    async def _call(awaitable: Any) -> Any:
        try:
            return await awaitable
        except Exception as exc:
            raise _resident_http_error(exc) from exc


def _locate_user_turn(turns: list[dict[str, Any]], index: int, digest: str) -> int | None:
    """The user turn that started a task: by position, re-found by its prompt digest."""
    candidates = [*range(index, len(turns)), *range(min(index, len(turns)) - 1, -1, -1)]
    for position in candidates:
        turn = turns[position]
        if turn.get("role") == "user" and _turn_digest(turn) == digest:
            return position
    # An engine that stores the prompt reworded still keeps it at its position.
    if index < len(turns) and turns[index].get("role") == "user":
        return index
    return None


async def _history_turns(connection: ResidentChatConnection) -> list[dict[str, Any]]:
    """Read frames up to the channel history every resident chat opens with."""
    async with asyncio.timeout(_HISTORY_FRAME_TIMEOUT_SECONDS):
        while True:
            frame = await connection.receive()
            if frame.get("type") == "conversation_history":
                turns = frame.get("turns")
                return [turn for turn in turns or [] if isinstance(turn, dict)]


async def _await_turn(connection: ResidentChatConnection, timeout: float) -> _TurnOutcome:
    """Collect the reply to the message just sent, until it ends or *timeout* passes."""
    outcome = _TurnOutcome()
    if timeout <= 0:
        return outcome
    with suppress(TimeoutError):
        async with asyncio.timeout(timeout):
            while True:
                frame = await connection.receive()
                kind = frame.get("type")
                if kind == "content_block_delta":
                    delta = frame.get("delta")
                    if isinstance(delta, dict) and delta.get("type") == "text_delta":
                        outcome.text.append(str(delta.get("text") or ""))
                elif kind == "result":
                    outcome.completed = True
                    return outcome
                elif kind == "error" and not frame.get("command"):
                    # An error naming a command answers an optional control frame;
                    # any other error ends the turn.
                    outcome.error = str(frame.get("error") or "resident turn failed")
                    return outcome
    return outcome


def _resident_http_error(exc: Exception) -> Exception:
    if isinstance(exc, (InvalidParamsError, TaskNotFoundError, HTTPException)):
        return exc
    if isinstance(exc, ResidentRuntimeAccessError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ResidentRuntimeNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ResidentRuntimeValidationError):
        return InvalidParamsError(str(exc))
    if isinstance(exc, ResidentRuntimeDeploymentError):
        return HTTPException(status_code=502, detail=str(exc))
    return exc


def _public_base_url(request: Request, configured: str) -> str:
    return configured.rstrip("/") or str(request.base_url).rstrip("/")


def create_resident_a2a_router(
    service: ResidentRuntimeService,
    *,
    persona_provider: SessionPersonaProvider | None = None,
    public_base_url: str = "",
    reply_timeout_seconds: float = 45.0,
) -> APIRouter:
    """A2A card and JSON-RPC endpoint for every resident that can chat in channels."""
    router = APIRouter(prefix=RESIDENT_RUNTIMES_PATH, tags=["Resident Runtimes", "A2A"])

    async def _resident(principal: Principal, runtime_id: UUID) -> ResidentRuntime:
        runtime = await ResidentTaskHandler._call(service.get(principal, runtime_id))
        if not resident_supports_a2a(runtime):
            raise HTTPException(status_code=404, detail="Resident is not an A2A agent")
        return runtime

    @router.get(f"/{{runtime_id}}{AGENT_CARD_PATH_SUFFIX}")
    async def get_resident_agent_card(
        request: Request,
        runtime_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        runtime = await _resident(principal, runtime_id)
        persona_prompt = ""
        persona_name = runtime.persona_name or (
            _DEFAULT_RAVN_PERSONA if runtime.engine == ResidentEngine.RAVN else ""
        )
        if persona_provider is not None and persona_name:
            with suppress(Exception):
                persona = await persona_provider.get(runtime.owner_id, persona_name)
                persona_prompt = persona.system_prompt if persona is not None else ""
        _, endpoint_url = resident_a2a_urls(runtime.id, _public_base_url(request, public_base_url))
        card = build_resident_agent_card(
            runtime,
            endpoint_url=endpoint_url,
            persona_prompt=persona_prompt,
        )
        return Response(
            content=_card_json(card),
            media_type="application/json",
            headers={"Cache-Control": "private, max-age=60"},
        )

    @router.post(f"/{{runtime_id}}{A2A_PATH_SUFFIX}")
    async def resident_a2a_jsonrpc(
        request: Request,
        runtime_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        runtime = await _resident(principal, runtime_id)
        handler = ResidentTaskHandler(
            service=service,
            principal=principal,
            runtime=runtime,
            reply_timeout_seconds=reply_timeout_seconds,
        )
        return await JsonRpcDispatcher(request_handler=handler).handle_requests(request)

    return router


def _card_json(card: AgentCard) -> str:
    return json.dumps(MessageToDict(card), sort_keys=True, separators=(",", ":"))
