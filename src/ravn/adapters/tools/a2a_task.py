"""One general, model-directed tool for durable A2A task interaction."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from niuu.domain.agent_directory import AgentDirectoryEntry
from niuu.observability import get_observability
from ravn.adapters.tool_build.http import AsyncJsonHttpClient, normalize_http_origin
from ravn.domain.models import ToolResult
from ravn.ports.agent_directory import PeerAgentDirectoryPort
from ravn.ports.tool import ToolPort

_A2A_HEADERS = {"A2A-Version": "1.0"}
_JSONRPC_BINDING = "jsonrpc"
_INPUT_REQUIRED_STATE = "TASK_STATE_INPUT_REQUIRED"
_DEFAULT_RESULT_MAX_CHARS = 12_000
_DEFAULT_MESSAGE_MAX_CHARS = 12_000


class A2ATaskTool(ToolPort):
    """Start, inspect, answer, or cancel a task on a discovered peer agent."""

    def __init__(
        self,
        *,
        agent_directory: PeerAgentDirectoryPort,
        client: AsyncJsonHttpClient,
        trusted_origins: list[str] | None = None,
        result_max_chars: int = _DEFAULT_RESULT_MAX_CHARS,
        message_max_chars: int = _DEFAULT_MESSAGE_MAX_CHARS,
    ) -> None:
        self._directory = agent_directory
        self._client = client
        self._trusted_origins = frozenset(
            normalize_http_origin(origin) for origin in (trusted_origins or [])
        )
        self._result_max_chars = max(1_000, result_max_chars)
        self._message_max_chars = max(1_000, message_max_chars)

    @property
    def name(self) -> str:
        return "a2a_task"

    @property
    def description(self) -> str:
        return (
            "Interact with a peer skill discovered through capability_list. "
            "Operations: start a task, get its current state/artifacts, reply when "
            "it requests input, or cancel it. Preserve agent_id and task_id from "
            "the response for later turns. The peer and skill are your choice."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["start", "get", "reply", "cancel"],
                },
                "agent_id": {
                    "type": "string",
                    "description": "Guild agent id from an agent_skill catalog entry.",
                },
                "skill_id": {
                    "type": "string",
                    "description": "Agent Card skill id; required for start.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Task request; required for start.",
                },
                "task_id": {
                    "type": "string",
                    "description": "A2A task id; required for get, reply, and cancel.",
                },
                "answer": {
                    "type": "string",
                    "description": "Answer or requested revision; required for reply.",
                },
                "metadata": {
                    "type": "object",
                    "description": (
                        "Optional continuation metadata returned by the peer. For a "
                        "pending question, preserve requestId. For a pending gate, send "
                        "gateId plus gateDecision=approve or request_changes; include "
                        "review notes in answer when requesting changes."
                    ),
                },
            },
            "required": ["operation", "agent_id"],
        }

    @property
    def required_permission(self) -> str:
        return "a2a:task"

    async def execute(self, input: dict) -> ToolResult:
        telemetry = get_observability()
        operation = str(input.get("operation") or "").strip().lower()
        attributes = {
            "a2a.operation": operation or "unknown",
            "a2a.agent.id": str(input.get("agent_id") or "").strip(),
            "a2a.task.id": str(input.get("task_id") or "").strip(),
        }
        with telemetry.span("ravn.a2a.task", attributes=attributes) as span:
            try:
                result = await self._execute_observed(input)
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                telemetry.event(
                    "ravn.a2a.operation.failed",
                    attributes={**attributes, "error.type": type(exc).__name__},
                    content={"error": str(exc)},
                )
                raise
            if result.is_error:
                telemetry.mark_error(span, "a2a_operation_failed", result.content)
                telemetry.event(
                    "ravn.a2a.operation.failed",
                    attributes={**attributes, "error.type": "a2a_operation_failed"},
                    content={"error": result.content},
                )
            return result

    async def _execute_observed(self, input: dict) -> ToolResult:
        telemetry = get_observability()
        operation = str(input.get("operation") or "").strip().lower()
        if operation not in {"start", "get", "reply", "cancel"}:
            return _error("operation must be start, get, reply, or cancel")

        agent_id = str(input.get("agent_id") or "").strip()
        if not agent_id:
            return _error("agent_id is required")
        if len(agent_id) > self._message_max_chars:
            return _error(
                f"agent_id exceeds a2a message limit of {self._message_max_chars} characters"
            )
        try:
            agent = await self._directory.get_agent(agent_id)
        except Exception as exc:
            return _error(f"Agent Directory lookup failed: {exc}")
        if agent is None:
            return _error(f"Agent {agent_id!r} is not visible in the Guild directory")
        telemetry.event(
            "ravn.a2a.agent.resolved",
            attributes={
                "a2a.agent.id": agent.id,
                "a2a.operation": operation,
                "a2a.agent.signature_verified": agent.signature_verified,
            },
            content=agent.model_dump(by_alias=True),
        )

        endpoint = _jsonrpc_endpoint(agent)
        if not endpoint:
            return _error(f"Agent {agent_id!r} declares no JSONRPC interface")
        try:
            card_origin = normalize_http_origin(agent.card_url)
            endpoint_origin = normalize_http_origin(endpoint)
        except ValueError as exc:
            return _error(f"Agent {agent_id!r} declares an invalid A2A URL: {exc}")
        if endpoint_origin != card_origin:
            return _error(f"Agent {agent_id!r} JSONRPC interface must share its Agent Card origin")
        if self._trusted_origins and endpoint_origin not in self._trusted_origins:
            return _error(f"Agent {agent_id!r} uses untrusted origin {endpoint_origin}")

        try:
            result = await self._execute_operation(operation, input, agent, endpoint)
        except _A2ATaskError as exc:
            return _error(str(exc))
        embedded = result.get("task")
        task = embedded if isinstance(embedded, dict) else result
        status = task.get("status") if isinstance(task, dict) else {}
        status = status if isinstance(status, dict) else {}
        task_id = str(
            (task.get("id") if isinstance(task, dict) else "") or input.get("task_id") or ""
        )
        state = str(status.get("state") or "")
        result_attributes = {
            "a2a.agent.id": agent.id,
            "a2a.operation": operation,
            "a2a.task.id": task_id,
            "a2a.task.state": state,
        }
        telemetry.set_attributes(result_attributes)
        telemetry.event("ravn.a2a.operation.result", attributes=result_attributes, content=result)
        telemetry.count(
            "ravn.a2a.operations",
            attributes={
                "a2a.operation": operation,
                "a2a.task.state": state or "unknown",
            },
        )
        return ToolResult(
            tool_call_id="",
            content=_render_response_payload(
                operation=operation,
                agent=agent,
                result=result,
                requested_task_id=str(input.get("task_id") or "").strip(),
                max_chars=self._result_max_chars,
            ),
        )

    async def _execute_operation(
        self,
        operation: str,
        input: dict,
        agent: AgentDirectoryEntry,
        endpoint: str,
    ) -> dict[str, Any]:
        task_id = str(input.get("task_id") or "").strip()
        if operation == "start":
            skill_id = str(input.get("skill_id") or "").strip()
            prompt = str(input.get("prompt") or "").strip()
            if not skill_id or not prompt:
                raise _A2ATaskError("start requires skill_id and prompt")
            self._validate_message(skill_id, "skill_id")
            self._validate_message(prompt, "prompt")
            if skill_id not in agent.skill_ids:
                raise _A2ATaskError(f"Agent {agent.id!r} does not publish skill {skill_id!r}")
            supplied_metadata = input.get("metadata")
            metadata = dict(supplied_metadata) if isinstance(supplied_metadata, dict) else {}
            self._validate_metadata(metadata)
            metadata.update({"skillId": skill_id, "workflowId": skill_id})
            trace_context = get_observability().inject()
            if trace_context:
                metadata["traceContext"] = trace_context
            return await self._rpc(
                endpoint,
                "SendMessage",
                {
                    "message": {
                        "messageId": str(uuid4()),
                        "role": "ROLE_USER",
                        "parts": [{"text": prompt}],
                        "metadata": metadata,
                    }
                },
            )

        if not task_id:
            raise _A2ATaskError(f"{operation} requires task_id")
        self._validate_message(task_id, "task_id")
        if operation == "get":
            return await self._rpc(endpoint, "GetTask", {"id": task_id})
        if operation == "cancel":
            return await self._rpc(endpoint, "CancelTask", {"id": task_id})

        answer = str(input.get("answer") or "").strip()
        if not answer:
            raise _A2ATaskError("reply requires answer")
        self._validate_message(answer, "answer")
        supplied_metadata = input.get("metadata")
        metadata = dict(supplied_metadata) if isinstance(supplied_metadata, dict) else {}
        self._validate_metadata(metadata)
        trace_context = get_observability().inject()
        if trace_context:
            metadata["traceContext"] = trace_context
        return await self._rpc(
            endpoint,
            "SendMessage",
            {
                "message": {
                    "messageId": str(uuid4()),
                    "taskId": task_id,
                    "role": "ROLE_USER",
                    "parts": [{"text": answer}],
                    "metadata": metadata,
                }
            },
        )

    def _validate_message(self, value: str, field: str) -> None:
        if len(value) > self._message_max_chars:
            raise _A2ATaskError(
                f"{field} exceeds a2a message limit of {self._message_max_chars} characters"
            )

    def _validate_metadata(self, metadata: dict[str, Any]) -> None:
        rendered = json.dumps(metadata, sort_keys=True, default=str)
        if len(rendered) > self._message_max_chars:
            raise _A2ATaskError(
                f"metadata exceeds a2a message limit of {self._message_max_chars} characters"
            )

    async def _rpc(
        self,
        endpoint: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        telemetry = get_observability()
        attributes = {
            "rpc.system": "jsonrpc",
            "rpc.method": method,
            "server.address": endpoint,
        }
        with telemetry.span("ravn.a2a.rpc", attributes=attributes) as span:
            try:
                response = await self._client.post(
                    endpoint,
                    {
                        "jsonrpc": "2.0",
                        "id": str(uuid4()),
                        "method": method,
                        "params": params,
                    },
                    headers=_A2A_HEADERS,
                )
            except Exception as exc:
                error = _A2ATaskError(f"A2A {method} transport failed: {exc}")
                telemetry.mark_error(span, type(error).__name__, str(error))
                raise error from exc
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code != 200 or not isinstance(response.body, dict):
                error = _A2ATaskError(f"A2A {method} returned HTTP {response.status_code}")
                telemetry.mark_error(span, type(error).__name__, str(error))
                raise error
            rpc_error = response.body.get("error")
            if rpc_error:
                message = (
                    rpc_error.get("message", rpc_error)
                    if isinstance(rpc_error, dict)
                    else rpc_error
                )
                if isinstance(rpc_error, dict) and rpc_error.get("code") is not None:
                    span.set_attribute(
                        "rpc.jsonrpc.error_code",
                        str(rpc_error["code"]),
                    )
                error = _A2ATaskError(f"A2A {method} failed: {message}")
                telemetry.mark_error(span, type(error).__name__, str(error))
                raise error
            telemetry.event(
                "ravn.a2a.rpc.completed",
                attributes={**attributes, "http.response.status_code": response.status_code},
            )
            result = response.body.get("result")
            return result if isinstance(result, dict) else {}


class _A2ATaskError(RuntimeError):
    pass


def _jsonrpc_endpoint(agent: AgentDirectoryEntry) -> str:
    for interface in agent.supported_interfaces:
        binding = interface.protocol_binding.strip().casefold()
        if binding and binding != _JSONRPC_BINDING:
            continue
        if interface.url:
            return interface.url
    return ""


def _render_response_payload(
    *,
    operation: str,
    agent: AgentDirectoryEntry,
    result: dict[str, Any],
    requested_task_id: str,
    max_chars: int,
) -> str:
    embedded = result.get("task")
    task = embedded if isinstance(embedded, dict) else result
    status = task.get("status") if isinstance(task, dict) else {}
    status = status if isinstance(status, dict) else {}
    state = str(status.get("state") or "")
    task_id = (
        str(task.get("id") or requested_task_id) if isinstance(task, dict) else requested_task_id
    )
    metadata = task.get("metadata") if isinstance(task, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    artifacts = task.get("artifacts") if isinstance(task, dict) else []
    artifacts = artifacts if isinstance(artifacts, list) else []
    pending_questions = metadata.get("pendingQuestions")
    pending_questions = pending_questions if isinstance(pending_questions, list) else []
    pending_gates = metadata.get("pendingGates")
    pending_gates = pending_gates if isinstance(pending_gates, list) else []
    payload: dict[str, Any] = {
        "operation": operation,
        "agent_id": agent.id,
        "task_id": task_id,
        "state": state,
        "input_required": state == _INPUT_REQUIRED_STATE,
        "pending_questions": [],
        "pending_gates": [],
        "artifacts": [],
        "provenance": {
            "source": "guild-agent-directory",
            "source_agent_id": agent.source_agent_id,
            "card_url": agent.card_url,
            "card_hash": agent.card_hash,
            "signature_verified": agent.signature_verified,
            "directory": [item.model_dump(by_alias=True) for item in agent.provenance[:8]],
        },
    }
    message = status.get("message") or status.get("update")
    if message:
        payload["status_message"] = _truncate(str(message), 2_000)

    _append_while_fits(payload, "pending_questions", pending_questions, max_chars)
    _append_while_fits(payload, "pending_gates", pending_gates, max_chars)
    _append_while_fits(payload, "artifacts", artifacts, max_chars)
    rendered = json.dumps(payload, indent=2, default=str)
    if len(rendered) <= max_chars:
        return rendered
    # The fixed envelope can only exceed the configured limit through unusual
    # directory metadata. Keep the continuation identifiers and state intact.
    payload["provenance"] = {
        "source": "guild-agent-directory",
        "card_hash": agent.card_hash,
        "signature_verified": agent.signature_verified,
    }
    rendered = json.dumps(payload, indent=2, default=str)
    if len(rendered) <= max_chars:
        return rendered
    # A malformed directory entry or peer task can make even the fixed
    # envelope enormous. Return a final valid, bounded envelope rather than
    # violating the advertised context limit.
    return json.dumps(
        {
            "operation": operation,
            "agent_id": _truncate(str(agent.id), 128),
            "task_id": _truncate(task_id, 512),
            "state": _truncate(state, 64),
            "input_required": state == _INPUT_REQUIRED_STATE,
            "truncated": "peer identifiers or provenance exceeded the result limit",
        },
        indent=2,
    )


def _append_while_fits(
    payload: dict[str, Any],
    key: str,
    values: list[Any],
    max_chars: int,
) -> None:
    target = payload[key]
    assert isinstance(target, list)
    for value in values[:16]:
        bounded = _bounded_item(value, max_chars=max(256, max_chars // 3))
        target.append(bounded)
        if len(json.dumps(payload, indent=2, default=str)) > max_chars:
            target.pop()
            target.append({"truncated": f"additional {key} omitted"})
            if len(json.dumps(payload, indent=2, default=str)) > max_chars:
                target.pop()
            break


def _bounded_item(value: Any, *, max_chars: int) -> Any:
    rendered = json.dumps(value, sort_keys=True, default=str)
    if len(rendered) <= max_chars:
        return value
    if isinstance(value, dict):
        identity = {
            key: value[key]
            for key in ("artifactId", "artifact_id", "requestId", "gateId", "id", "name")
            if key in value
        }
        identity["content_excerpt"] = _truncate(rendered, max(64, max_chars - 160))
        return identity
    return _truncate(rendered, max_chars)


def _truncate(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else f"{value[: max_chars - 1]}…"


def _error(message: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=message, is_error=True)
