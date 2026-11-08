"""The A2A gateway Ting's durable workflow ledger drives child tasks through."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from niuu.adapters.child_task_a2a import (
    ChildTaskA2AGateway,
    ConfiguredChildTaskA2AGateway,
    _tool_payload,
)
from niuu.adapters.outbound.http_json_client import HttpResponse
from niuu.domain.child_task import ChildLaunchRequest, ChildTaskHandle
from niuu.domain.tool import ToolResult


class _HttpClient:
    async def post(self, url: str, json_body: dict, *, headers=None) -> HttpResponse:
        del url, json_body, headers
        return HttpResponse(200, {"ok": True})


class _TaskTool:
    def __init__(self) -> None:
        self.starts: list[tuple[dict, str]] = []
        self.replies: list[tuple[dict, str]] = []
        self.auth_tokens: list[str] = []

    async def execute_persisted_start(
        self, value: dict, *, message_id: str, auth_token: str = ""
    ) -> ToolResult:
        self.auth_tokens.append(auth_token)
        self.starts.append((value, message_id))
        return ToolResult(
            tool_call_id="",
            content=json.dumps(
                {"task_id": "task-1", "context_id": "context-1", "state": "TASK_STATE_SUBMITTED"}
            ),
        )

    async def execute_persisted_reply(self, value: dict, *, message_id: str) -> ToolResult:
        self.replies.append((value, message_id))
        return self._observation()

    async def execute(self, value: dict) -> ToolResult:
        return self._observation()

    async def execute_persisted_task_operation(
        self, value: dict, *, message_id: str = "", auth_token: str = ""
    ) -> dict:
        self.auth_tokens.append(auth_token)
        if value["operation"] == "reply":
            self.replies.append((value, message_id))
        return json.loads(self._observation().content)

    @staticmethod
    def _observation() -> ToolResult:
        return ToolResult(
            tool_call_id="",
            content=json.dumps(
                {
                    "task_id": "task-1",
                    "context_id": "context-1",
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "status_message": "Need an interface decision",
                    "artifacts": [{"artifactId": "question-1"}],
                }
            ),
        )


class _HttpClient:
    async def post(self, url: str, json_body: dict, *, headers=None) -> HttpResponse:
        del url, json_body, headers
        return HttpResponse(200, {"ok": True})


@pytest.mark.asyncio
async def test_execution_gateway_preserves_terminal_failure_metadata() -> None:
    tool = SimpleNamespace(
        execute_persisted_task_operation=AsyncMock(
            return_value={
                "id": "task-1",
                "status": {"state": "TASK_STATE_FAILED"},
                "metadata": {"error": "Pinned workspace configuration changed"},
            }
        )
    )
    gateway = ChildTaskA2AGateway(task_tool=tool)
    observation = await gateway.get_child(
        ChildTaskHandle(agent_id="worker-agent", task_id="task-1", context_id="context-1")
    )
    assert observation.state == "failed"
    assert observation.error == "Pinned workspace configuration changed"


@pytest.mark.asyncio
async def test_execution_gateway_preserves_launch_and_reply_idempotency() -> None:
    task_tool = _TaskTool()
    gateway = ChildTaskA2AGateway(
        task_tool=task_tool,  # type: ignore[arg-type]
        workflow_execution_base_url="https://ting.example",
    )
    handle = await gateway.launch_child(
        ChildLaunchRequest(
            intent_id=str(uuid4()),
            message_id="launch-1",
            agent_id="worker-agent",
            skill_id="developer-workstream",
            work_order={
                "childKey": "api",
                "repository": "group/repo",
                "baseSha": "a" * 40,
                "workspace": {
                    "workspace_path": "/workspace/api",
                    "branch_name": "delivery/api",
                },
            },
            metadata={"executionId": "execution-1"},
        ),
        auth_token="owner-token",
    )
    first = await gateway.get_child(handle, auth_token="owner-token")
    second = await gateway.get_child(handle, auth_token="owner-token")
    reply = await gateway.reply_child(
        handle,
        answer="Use the existing interface.",
        metadata={"findingId": "question-1"},
        message_id="reply-1",
        auth_token="owner-token",
    )

    assert task_tool.starts[0][1] == "launch-1"
    assert json.loads(task_tool.starts[0][0]["prompt"])["childKey"] == "api"
    launch_metadata = task_tool.starts[0][0]["metadata"]
    assert launch_metadata["repo"] == "/workspace/api"
    assert launch_metadata["branch"] == "delivery/api"
    assert launch_metadata["baseSha"] == "a" * 40
    assert launch_metadata["workflowExecution"] == {"executionId": "execution-1"}
    assert first.state == "blocked"
    assert first.event_id == second.event_id == reply.event_id
    assert task_tool.replies[0][1] == "reply-1"
    assert task_tool.auth_tokens == ["owner-token"] * 4

    with pytest.raises(ValueError, match="non-empty answer"):
        await gateway.reply_child(handle, answer="", metadata={}, message_id="reply-2")
    with pytest.raises(ValueError, match="persisted message_id"):
        await gateway.reply_child(handle, answer="answer", metadata={}, message_id="")


@pytest.mark.asyncio
async def test_execution_gateway_observes_exact_metadata_delivery_result() -> None:
    class RawTaskTool(_TaskTool):
        async def execute_persisted_task_operation(
            self, value: dict, *, message_id: str = "", auth_token: str = ""
        ) -> dict:
            del value, message_id, auth_token
            return {
                "id": "task-1",
                "contextId": "context-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "metadata": {
                    "deliveryResult": {
                        "candidateSha": "a" * 40,
                        "reviews": [{"summary": "x" * 8_000}],
                    }
                },
            }

    gateway = ChildTaskA2AGateway(task_tool=RawTaskTool())  # type: ignore[arg-type]
    observation = await gateway.get_child(
        ChildTaskHandle(agent_id="worker-agent", task_id="task-1", context_id="context-1")
    )

    assert observation.state == "completed"
    assert observation.result is not None
    assert len(observation.result["reviews"][0]["summary"]) == 8_000


@pytest.mark.asyncio
async def test_execution_gateway_preserves_pending_question_and_gate_identity() -> None:
    class RawTaskTool(_TaskTool):
        request_id = "request-1"

        async def execute_persisted_task_operation(
            self, value: dict, *, message_id: str = "", auth_token: str = ""
        ) -> dict:
            del value, message_id, auth_token
            return {
                "id": "task-1",
                "contextId": "context-1",
                "status": {
                    "state": "TASK_STATE_INPUT_REQUIRED",
                    "message": "Input required",
                },
                "metadata": {
                    "pendingQuestions": [
                        {
                            "requestId": self.request_id,
                            "persona": "developer-coder",
                            "question": "Which branch?",
                            "reason": "Two targets exist",
                            "recommendation": "Use main",
                            "attempted": ["inspect refs"],
                        }
                    ],
                    "pendingGates": [
                        {
                            "gateId": "gate-1",
                            "nodeId": "review",
                            "label": "Approve review",
                            "condition": "review passed",
                            "instructions": "Inspect findings",
                            "summary": "One finding",
                        }
                    ],
                },
            }

    tool = RawTaskTool()
    gateway = ChildTaskA2AGateway(task_tool=tool)  # type: ignore[arg-type]
    handle = ChildTaskHandle(agent_id="worker-agent", task_id="task-1", context_id="context-1")
    first = await gateway.get_child(handle)
    tool.request_id = "request-2"
    changed = await gateway.get_child(handle)

    assert first.pending_questions[0].to_a2a_metadata() == {
        "requestId": "request-1",
        "persona": "developer-coder",
        "question": "Which branch?",
        "reason": "Two targets exist",
        "recommendation": "Use main",
        "attempted": ["inspect refs"],
    }
    assert first.pending_gates[0].to_a2a_metadata()["gateId"] == "gate-1"
    assert first.event_id != changed.event_id


def test_configured_gateway_composes_existing_a2a_stack(monkeypatch) -> None:
    http = _HttpClient()
    captured: dict = {}

    def _client_factory(**kwargs):
        captured.update(kwargs)
        return http

    monkeypatch.setattr(
        "niuu.adapters.child_task_a2a.client_from_workload_identity",
        _client_factory,
    )
    gateway = ConfiguredChildTaskA2AGateway(
        base_url="https://platform.example",
        trusted_origins=["https://peer.example"],
        agent_card_urls=["https://peer.example/card"],
        external_token="token",
    )

    assert gateway._workflow_execution_base_url == "https://platform.example"
    assert captured["external_token"] == "token"
    assert captured["allowed_origins"] == [
        "https://platform.example",
        "https://peer.example",
    ]
    assert gateway._task_tool._directory._agent_card_urls == [
        "https://peer.example/card",
        "https://platform.example/.well-known/agent-card.json",
    ]


def test_configured_gateway_anonymous_dev_mode_has_no_base_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "niuu.adapters.child_task_a2a.client_from_workload_identity",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("auth factory called")),
    )

    gateway = ConfiguredChildTaskA2AGateway(
        base_url="http://niuu:8180",
        agent_card_urls=["http://ravn-child:8080/.well-known/agent-card.json"],
        trusted_origins=["http://ravn-child:8080"],
        anonymous_dev_mode=True,
    )

    assert gateway._task_tool._client._auth is None


def test_a2a_tool_payload_rejects_transport_errors_and_invalid_json() -> None:
    with pytest.raises(RuntimeError, match="denied"):
        _tool_payload("denied", is_error=True)
    with pytest.raises(RuntimeError, match="invalid JSON"):
        _tool_payload("not-json", is_error=False)
    with pytest.raises(RuntimeError, match="non-object"):
        _tool_payload("[]", is_error=False)
