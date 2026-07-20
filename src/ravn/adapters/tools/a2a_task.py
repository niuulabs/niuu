"""One general, model-directed tool for durable A2A task interaction."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from niuu.domain.agent_directory import AgentDirectoryEntry
from ravn.adapters.tool_build.http import AsyncJsonHttpClient
from ravn.domain.models import ToolResult
from ravn.ports.agent_directory import PeerAgentDirectoryPort
from ravn.ports.tool import ToolPort

_A2A_HEADERS = {"A2A-Version": "1.0"}
_JSONRPC_BINDING = "jsonrpc"
_INPUT_REQUIRED_STATE = "TASK_STATE_INPUT_REQUIRED"


class A2ATaskTool(ToolPort):
    """Start, inspect, answer, or cancel a task on a discovered peer agent."""

    def __init__(
        self,
        *,
        agent_directory: PeerAgentDirectoryPort,
        client: AsyncJsonHttpClient,
    ) -> None:
        self._directory = agent_directory
        self._client = client

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
                    "description": "Optional A2A message metadata.",
                },
            },
            "required": ["operation", "agent_id"],
        }

    @property
    def required_permission(self) -> str:
        return "a2a:task"

    async def execute(self, input: dict) -> ToolResult:
        operation = str(input.get("operation") or "").strip().lower()
        if operation not in {"start", "get", "reply", "cancel"}:
            return _error("operation must be start, get, reply, or cancel")

        agent_id = str(input.get("agent_id") or "").strip()
        if not agent_id:
            return _error("agent_id is required")
        try:
            agent = await self._directory.get_agent(agent_id)
        except Exception as exc:
            return _error(f"Agent Directory lookup failed: {exc}")
        if agent is None:
            return _error(f"Agent {agent_id!r} is not visible in the Guild directory")

        endpoint = _jsonrpc_endpoint(agent)
        if not endpoint:
            return _error(f"Agent {agent_id!r} declares no JSONRPC interface")

        try:
            result = await self._execute_operation(operation, input, agent, endpoint)
        except _A2ATaskError as exc:
            return _error(str(exc))
        return ToolResult(
            tool_call_id="",
            content=json.dumps(
                _response_payload(
                    operation=operation,
                    agent=agent,
                    result=result,
                    requested_task_id=str(input.get("task_id") or "").strip(),
                ),
                indent=2,
                default=str,
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
            if skill_id not in agent.skill_ids:
                raise _A2ATaskError(
                    f"Agent {agent.id!r} does not publish skill {skill_id!r}"
                )
            supplied_metadata = input.get("metadata")
            metadata = dict(supplied_metadata) if isinstance(supplied_metadata, dict) else {}
            metadata.update({"skillId": skill_id, "workflowId": skill_id})
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
        if operation == "get":
            return await self._rpc(endpoint, "GetTask", {"id": task_id})
        if operation == "cancel":
            return await self._rpc(endpoint, "CancelTask", {"id": task_id})

        answer = str(input.get("answer") or "").strip()
        if not answer:
            raise _A2ATaskError("reply requires answer")
        supplied_metadata = input.get("metadata")
        metadata = dict(supplied_metadata) if isinstance(supplied_metadata, dict) else {}
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

    async def _rpc(
        self,
        endpoint: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
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
        if response.status_code != 200 or not isinstance(response.body, dict):
            raise _A2ATaskError(f"A2A {method} returned HTTP {response.status_code}")
        error = response.body.get("error")
        if error:
            message = error.get("message", error) if isinstance(error, dict) else error
            raise _A2ATaskError(f"A2A {method} failed: {message}")
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


def _response_payload(
    *,
    operation: str,
    agent: AgentDirectoryEntry,
    result: dict[str, Any],
    requested_task_id: str,
) -> dict[str, Any]:
    embedded = result.get("task")
    task = embedded if isinstance(embedded, dict) else result
    status = task.get("status") if isinstance(task, dict) else {}
    status = status if isinstance(status, dict) else {}
    state = str(status.get("state") or "")
    task_id = (
        str(task.get("id") or requested_task_id)
        if isinstance(task, dict)
        else requested_task_id
    )
    metadata = task.get("metadata") if isinstance(task, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    artifacts = task.get("artifacts") if isinstance(task, dict) else []
    artifacts = artifacts if isinstance(artifacts, list) else []
    pending_questions = metadata.get("pendingQuestions")
    pending_questions = pending_questions if isinstance(pending_questions, list) else []
    pending_gates = metadata.get("pendingGates")
    pending_gates = pending_gates if isinstance(pending_gates, list) else []
    return {
        "operation": operation,
        "agent_id": agent.id,
        "task_id": task_id,
        "state": state,
        "input_required": state == _INPUT_REQUIRED_STATE,
        "pending_questions": pending_questions,
        "pending_gates": pending_gates,
        "artifacts": artifacts,
        "result": result,
        "provenance": {
            "source": "guild-agent-directory",
            "source_agent_id": agent.source_agent_id,
            "card_url": agent.card_url,
            "card_hash": agent.card_hash,
            "signature_verified": agent.signature_verified,
            "directory": [item.model_dump(by_alias=True) for item in agent.provenance],
        },
    }


def _error(message: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=message, is_error=True)
