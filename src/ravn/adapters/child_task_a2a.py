"""Ravn A2A gateway for Ting's durable developer child ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

from ravn.adapters.agent_directory import GuildAgentDirectoryAdapter
from ravn.adapters.tool_build.http import HttpxJsonClient, client_from_workload_identity
from ravn.adapters.tools.a2a_task import A2ATaskTool
from ravn.domain.delivery import (
    ChildLaunchRequest,
    ChildPendingGate,
    ChildPendingQuestion,
    ChildTaskHandle,
    ChildTaskObservation,
)

_A2A_STATE_MAP = {
    "TASK_STATE_SUBMITTED": "submitted",
    "TASK_STATE_WORKING": "running",
    "TASK_STATE_INPUT_REQUIRED": "blocked",
    "TASK_STATE_AUTH_REQUIRED": "blocked",
    "TASK_STATE_COMPLETED": "completed",
    "TASK_STATE_FAILED": "failed",
    "TASK_STATE_CANCELED": "canceled",
    "TASK_STATE_REJECTED": "failed",
}


class RavnChildTaskA2AGateway:
    """Bridge persisted child intents to the existing authenticated A2A client."""

    def __init__(
        self,
        *,
        task_tool: A2ATaskTool,
        workflow_execution_base_url: str = "",
    ) -> None:
        self._task_tool = task_tool
        self._workflow_execution_base_url = workflow_execution_base_url.rstrip("/")

    async def launch_child(
        self, request: ChildLaunchRequest, *, auth_token: str = ""
    ) -> ChildTaskHandle:
        metadata = {
            **request.metadata,
            "intentId": str(request.intent_id),
            "messageId": request.message_id,
            "deliveryContract": "niuu.workflow-child.v1",
        }
        workspace = request.work_order.get("workspace")
        workspace = workspace if isinstance(workspace, dict) else {}
        repository = str(
            workspace.get("workspace_path")
            or workspace.get("repository_path")
            or request.work_order.get("repository")
            or ""
        ).strip()
        base_sha = str(request.work_order.get("baseSha") or "").strip()
        branch = str(workspace.get("branch_name") or base_sha).strip()
        if repository:
            metadata["repo"] = repository
        if branch:
            metadata["branch"] = branch
        if base_sha:
            metadata["baseSha"] = base_sha
        if self._workflow_execution_base_url:
            metadata["workflowExecution"] = {
                "executionId": str(request.metadata.get("executionId") or ""),
            }
        result = await self._task_tool.execute_persisted_start(
            {
                "operation": "start",
                "agent_id": request.agent_id,
                "skill_id": request.skill_id,
                "prompt": json.dumps(request.work_order, sort_keys=True, ensure_ascii=False),
                "metadata": metadata,
            },
            message_id=request.message_id,
            auth_token=auth_token,
        )
        payload = _tool_payload(result.content, is_error=result.is_error)
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise RuntimeError("A2A child launch returned no task_id")
        return ChildTaskHandle(
            agent_id=request.agent_id,
            task_id=task_id,
            context_id=str(payload.get("context_id") or ""),
        )

    async def get_child(
        self, handle: ChildTaskHandle, *, auth_token: str = ""
    ) -> ChildTaskObservation:
        return await self._observe("get", handle, auth_token=auth_token)

    async def reply_child(
        self,
        handle: ChildTaskHandle,
        *,
        answer: str,
        metadata: dict[str, Any],
        message_id: str,
        auth_token: str = "",
    ) -> ChildTaskObservation:
        if not answer.strip():
            raise ValueError("A2A child reply requires a non-empty answer")
        if not message_id.strip():
            raise ValueError("A2A child reply requires a persisted message_id")
        return await self._observe(
            "reply",
            handle,
            answer=answer,
            metadata=metadata,
            message_id=message_id,
            auth_token=auth_token,
        )

    async def cancel_child(
        self, handle: ChildTaskHandle, *, auth_token: str = ""
    ) -> ChildTaskObservation:
        return await self._observe("cancel", handle, auth_token=auth_token)

    async def _observe(
        self,
        operation: str,
        handle: ChildTaskHandle,
        *,
        answer: str = "",
        metadata: dict[str, Any] | None = None,
        message_id: str = "",
        auth_token: str = "",
    ) -> ChildTaskObservation:
        tool_input: dict[str, Any] = {
            "operation": operation,
            "agent_id": handle.agent_id,
            "task_id": handle.task_id,
        }
        if operation == "reply":
            tool_input.update({"answer": answer.strip(), "metadata": dict(metadata or {})})
        payload = await self._task_tool.execute_persisted_task_operation(
            tool_input,
            message_id=message_id,
            auth_token=auth_token,
        )
        raw_artifacts = payload.get("artifacts")
        artifacts = (
            tuple(item for item in raw_artifacts if isinstance(item, dict))
            if isinstance(raw_artifacts, list)
            else ()
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            metadata_payload = payload.get("metadata")
            if isinstance(metadata_payload, dict):
                result = metadata_payload.get("deliveryResult")
        status_payload = payload.get("status")
        status_payload = status_payload if isinstance(status_payload, dict) else {}
        metadata_payload = payload.get("metadata")
        metadata_payload = metadata_payload if isinstance(metadata_payload, dict) else {}
        pending_questions = _pending_questions(metadata_payload, payload)
        pending_gates = _pending_gates(metadata_payload, payload)
        raw_state = str(
            status_payload.get("state") or payload.get("state") or "TASK_STATE_UNSPECIFIED"
        )
        state = _A2A_STATE_MAP.get(raw_state, raw_state.casefold())
        error = str(
            payload.get("error")
            or payload.get("status_message")
            or metadata_payload.get("error")
            or status_payload.get("message")
            or status_payload.get("update")
            or ""
        )
        event_payload = {
            "agent_id": handle.agent_id,
            "task_id": str(payload.get("id") or payload.get("task_id") or handle.task_id),
            "context_id": str(
                payload.get("contextId") or payload.get("context_id") or handle.context_id
            ),
            "state": state,
            "result": result if isinstance(result, dict) else None,
            "artifacts": artifacts,
            "error": error,
            "pending_questions": [item.to_a2a_metadata() for item in pending_questions],
            "pending_gates": [item.to_a2a_metadata() for item in pending_gates],
        }
        event_id = (
            "sha256:"
            + sha256(
                json.dumps(event_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )
        return ChildTaskObservation(
            handle=ChildTaskHandle(
                agent_id=handle.agent_id,
                task_id=str(payload.get("id") or payload.get("task_id") or handle.task_id),
                context_id=str(
                    payload.get("contextId") or payload.get("context_id") or handle.context_id
                ),
            ),
            state=state,
            result=result if isinstance(result, dict) else None,
            artifacts=artifacts,
            observed_at=datetime.now(UTC),
            event_id=event_id,
            failure_kind="remote_failed" if state == "failed" else None,
            error=error if state in {"failed", "blocked"} else "",
            pending_questions=pending_questions,
            pending_gates=pending_gates,
        )


class ConfiguredRavnChildTaskA2AGateway(RavnChildTaskA2AGateway):
    """Production gateway composed from deployment config at Ting's boundary."""

    def __init__(
        self,
        *,
        base_url: str,
        trusted_origins: list[str] | None = None,
        agent_card_urls: list[str] | None = None,
        external_token: str = "",
        external_token_env: str = "",
        workload_token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token",
        workload_exchange_url: str = "",
        workload_audiences: list[str] | None = None,
        timeout_seconds: float = 30.0,
        message_max_chars: int = 12_000,
        result_max_chars: int = 12_000,
        default_connection_id: str = "",
        default_metadata: dict[str, Any] | None = None,
        push_callback_url: str = "",
        anonymous_dev_mode: bool = False,
    ) -> None:
        origins = list(dict.fromkeys([base_url, *(trusted_origins or [])]))
        local_card_url = f"{base_url.rstrip('/')}{AGENT_CARD_WELL_KNOWN_PATH}"
        configured_cards = list(dict.fromkeys([*(agent_card_urls or []), local_card_url]))
        client = (
            HttpxJsonClient(
                auth=None,
                timeout_seconds=timeout_seconds,
                allowed_origins=origins,
            )
            if anonymous_dev_mode
            else client_from_workload_identity(
                base_url=base_url,
                external_token=external_token,
                external_token_env=external_token_env,
                workload_token_file=workload_token_file,
                workload_exchange_url=workload_exchange_url,
                workload_audiences=workload_audiences,
                timeout_seconds=timeout_seconds,
                allowed_origins=origins,
            )
        )
        directory = GuildAgentDirectoryAdapter(
            base_url=base_url,
            client=client,
            agent_card_urls=configured_cards,
        )
        super().__init__(
            task_tool=A2ATaskTool(
                agent_directory=directory,
                client=client,
                trusted_origins=origins,
                message_max_chars=message_max_chars,
                result_max_chars=result_max_chars,
                default_connection_id=default_connection_id,
                default_metadata=default_metadata,
                push_callback_url=push_callback_url,
            ),
            workflow_execution_base_url=base_url,
        )


def _tool_payload(content: str, *, is_error: bool) -> dict[str, Any]:
    if is_error:
        raise RuntimeError(content)
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("A2A task tool returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("A2A task tool returned a non-object result")
    return value


def _pending_questions(
    metadata: dict[str, Any], payload: dict[str, Any]
) -> tuple[ChildPendingQuestion, ...]:
    raw = metadata.get("pendingQuestions", payload.get("pending_questions"))
    if not isinstance(raw, list):
        return ()
    return tuple(
        ChildPendingQuestion(
            request_id=str(item.get("requestId") or item.get("request_id") or ""),
            persona=str(item.get("persona") or ""),
            question=str(item.get("question") or item.get("summary") or ""),
            reason=str(item.get("reason") or ""),
            recommendation=str(item.get("recommendation") or ""),
            attempted=tuple(str(value) for value in item.get("attempted") or ()),
        )
        for item in raw
        if isinstance(item, dict)
    )


def _pending_gates(
    metadata: dict[str, Any], payload: dict[str, Any]
) -> tuple[ChildPendingGate, ...]:
    raw = metadata.get("pendingGates", payload.get("pending_gates"))
    if not isinstance(raw, list):
        return ()
    return tuple(
        ChildPendingGate(
            gate_id=str(item.get("gateId") or item.get("gate_id") or item.get("id") or ""),
            node_id=str(item.get("nodeId") or item.get("node_id") or ""),
            label=str(item.get("label") or ""),
            condition=str(item.get("condition") or ""),
            instructions=str(item.get("instructions") or ""),
            summary=str(item.get("summary") or ""),
        )
        for item in raw
        if isinstance(item, dict)
    )
