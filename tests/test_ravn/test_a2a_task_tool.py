from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock

import pytest

from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryPage,
    AgentInterface,
    AgentProvenance,
    AgentSkill,
)
from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.adapters.tool_build.http import HttpResponse
from ravn.adapters.tools.a2a_task import A2ATaskTool
from ravn.adapters.tools.capability_catalog import CapabilityListTool
from ravn.agent import RavnAgent
from ravn.domain.events import RavnEventType
from ravn.domain.models import StreamEvent, StreamEventType, TokenUsage, ToolCall
from tests.ravn.fixtures.fakes import InMemoryChannel


def _agent() -> AgentDirectoryEntry:
    return AgentDirectoryEntry(
        id="agent-aggregate-1",
        canonicalId="signed:card-hash:keys:key",
        sourceAgentId="builder-1",
        sourceInstanceId="observatory-a",
        clusterId="cluster-a",
        topologyNodeId="runtime:builder-1",
        name="Builder",
        description="Builds and reviews changes.",
        kind="workflow-session",
        cardUrl="https://peer.example/card",
        cardVersion="1.0",
        cardHash="card-hash",
        signatureVerified=True,
        skillIds=["review"],
        skills=[
            AgentSkill(
                id="review",
                name="Review change",
                description="Review a proposed change.",
                tags=["review"],
            )
        ],
        supportedInterfaces=[
            AgentInterface(
                url="https://peer.example/a2a",
                protocolBinding="JSONRPC",
                protocolVersion="1.0",
            )
        ],
        observedStatus="healthy",
        lastSeen="2026-07-20T12:00:00Z",
        visibility="tenant",
        provenance=[
            AgentProvenance(
                sourceAgentId="builder-1",
                sourceInstanceId="observatory-a",
                clusterId="cluster-a",
                topologyNodeId="runtime:builder-1",
            )
        ],
    )


class _Directory:
    def __init__(self, agent: AgentDirectoryEntry | None = None) -> None:
        self.agent = agent
        self.lookups: list[str] = []

    async def list_agents(self) -> AgentDirectoryPage:
        return AgentDirectoryPage(items=[self.agent] if self.agent else [])

    async def get_agent(self, agent_id: str) -> AgentDirectoryEntry | None:
        self.lookups.append(agent_id)
        return self.agent if self.agent and self.agent.id == agent_id else None


class _Client:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = list(results)
        self.posts: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        raise AssertionError("A2ATaskTool must use the validated directory, not refetch cards")

    async def post(
        self,
        url: str,
        json_body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.posts.append((url, json_body, dict(headers or {})))
        return HttpResponse(200, {"jsonrpc": "2.0", "result": self.results.pop(0)})


@pytest.mark.asyncio
async def test_a2a_task_preserves_task_state_questions_artifacts_and_provenance() -> None:
    client = _Client(
        [
            {"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}},
            {
                "id": "task-1",
                "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                "metadata": {"pendingQuestions": [{"question": "Which branch?"}]},
                "artifacts": [{"artifactId": "draft", "parts": [{"text": "draft"}]}],
            },
            {"task": {"id": "task-1", "status": {"state": "TASK_STATE_WORKING"}}},
            {"id": "task-1", "status": {"state": "TASK_STATE_CANCELED"}},
        ]
    )
    tool = A2ATaskTool(agent_directory=_Directory(_agent()), client=client)

    started = json.loads(
        (
            await tool.execute(
                {
                    "operation": "start",
                    "agent_id": "agent-aggregate-1",
                    "skill_id": "review",
                    "prompt": "Review the change.",
                }
            )
        ).content
    )
    inspected = json.loads(
        (
            await tool.execute(
                {"operation": "get", "agent_id": started["agent_id"], "task_id": "task-1"}
            )
        ).content
    )
    replied = json.loads(
        (
            await tool.execute(
                {
                    "operation": "reply",
                    "agent_id": started["agent_id"],
                    "task_id": "task-1",
                    "answer": "Use codex/resident-judgment-loop.",
                    "metadata": {"requestId": "question-1"},
                }
            )
        ).content
    )
    canceled = json.loads(
        (
            await tool.execute(
                {"operation": "cancel", "agent_id": started["agent_id"], "task_id": "task-1"}
            )
        ).content
    )

    assert started["task_id"] == "task-1"
    assert started["provenance"]["card_hash"] == "card-hash"
    assert inspected["input_required"] is True
    assert inspected["pending_questions"] == [{"question": "Which branch?"}]
    assert inspected["artifacts"][0]["artifactId"] == "draft"
    assert replied["state"] == "TASK_STATE_WORKING"
    assert canceled["state"] == "TASK_STATE_CANCELED"
    assert [body["method"] for _, body, _ in client.posts] == [
        "SendMessage",
        "GetTask",
        "SendMessage",
        "CancelTask",
    ]
    start_message = client.posts[0][1]["params"]["message"]
    assert start_message["metadata"] == {"skillId": "review", "workflowId": "review"}
    reply_message = client.posts[2][1]["params"]["message"]
    assert reply_message["taskId"] == "task-1"
    assert reply_message["metadata"] == {"requestId": "question-1"}
    assert all(url == "https://peer.example/a2a" for url, _, _ in client.posts)


@pytest.mark.asyncio
async def test_a2a_task_propagates_active_trace_in_message_metadata(monkeypatch) -> None:
    client = _Client([{"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}}])
    telemetry = MagicMock()
    telemetry.inject.return_value = {
        "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    }
    telemetry.span.side_effect = lambda *_args, **_kwargs: nullcontext(MagicMock())
    monkeypatch.setattr(
        "ravn.adapters.tools.a2a_task.get_observability",
        lambda: telemetry,
    )
    tool = A2ATaskTool(agent_directory=_Directory(_agent()), client=client)

    result = await tool.execute(
        {
            "operation": "start",
            "agent_id": _agent().id,
            "skill_id": "review",
            "prompt": "Review the change.",
        }
    )

    assert not result.is_error
    metadata = client.posts[0][1]["params"]["message"]["metadata"]
    assert metadata["traceContext"] == telemetry.inject.return_value


@pytest.mark.asyncio
async def test_a2a_task_rejects_unknown_agent_and_unpublished_skill() -> None:
    unknown = A2ATaskTool(agent_directory=_Directory(), client=_Client([]))
    missing_agent = await unknown.execute(
        {"operation": "get", "agent_id": "hidden", "task_id": "task-1"}
    )
    assert missing_agent.is_error
    assert "not visible" in missing_agent.content

    client = _Client([])
    tool = A2ATaskTool(agent_directory=_Directory(_agent()), client=client)
    missing_skill = await tool.execute(
        {
            "operation": "start",
            "agent_id": "agent-aggregate-1",
            "skill_id": "deploy",
            "prompt": "Deploy it.",
        }
    )
    assert missing_skill.is_error
    assert "does not publish skill" in missing_skill.content
    assert client.posts == []


@pytest.mark.asyncio
async def test_a2a_task_rejects_cross_origin_and_untrusted_peer_endpoints() -> None:
    cross_origin = _agent().model_copy(deep=True)
    cross_origin.supported_interfaces[0].url = "https://attacker.example/a2a"
    client = _Client([])
    tool = A2ATaskTool(
        agent_directory=_Directory(cross_origin),
        client=client,
        trusted_origins=["https://peer.example"],
    )

    result = await tool.execute(
        {"operation": "get", "agent_id": cross_origin.id, "task_id": "task-1"}
    )

    assert result.is_error
    assert "must share its Agent Card origin" in result.content
    assert client.posts == []

    untrusted = A2ATaskTool(
        agent_directory=_Directory(_agent()),
        client=client,
        trusted_origins=["https://platform.example"],
    )
    result = await untrusted.execute(
        {"operation": "get", "agent_id": _agent().id, "task_id": "task-1"}
    )
    assert result.is_error
    assert "uses untrusted origin https://peer.example" in result.content
    assert client.posts == []


@pytest.mark.asyncio
async def test_a2a_task_bounds_outbound_messages_and_model_facing_results() -> None:
    client = _Client(
        [
            {
                "id": "task-large",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [
                    {
                        "artifactId": "large-artifact",
                        "parts": [{"text": "result " * 10_000}],
                    }
                ],
            }
        ]
    )
    tool = A2ATaskTool(
        agent_directory=_Directory(_agent()),
        client=client,
        trusted_origins=["https://peer.example"],
        message_max_chars=1_000,
        result_max_chars=1_000,
    )

    oversized = await tool.execute(
        {
            "operation": "start",
            "agent_id": _agent().id,
            "skill_id": "review",
            "prompt": "x" * 1_001,
        }
    )
    assert oversized.is_error
    assert "prompt exceeds a2a message limit" in oversized.content
    assert client.posts == []

    bounded = await tool.execute(
        {"operation": "get", "agent_id": _agent().id, "task_id": "task-large"}
    )
    payload = json.loads(bounded.content)
    assert len(bounded.content) <= 1_000
    assert payload["task_id"] == "task-large"
    assert payload["state"] == "TASK_STATE_COMPLETED"
    assert payload["artifacts"][0]["artifactId"] == "large-artifact"

    client.results.append(
        {
            "id": "peer-task-" + "x" * 10_000,
            "status": {"state": "TASK_STATE_WORKING"},
        }
    )
    malformed = await tool.execute(
        {"operation": "get", "agent_id": _agent().id, "task_id": "requested-task"}
    )
    malformed_payload = json.loads(malformed.content)
    assert len(malformed.content) <= 1_000
    assert malformed_payload["truncated"].startswith("peer identifiers")


class _ScriptedLLM:
    def __init__(self, calls: list[ToolCall], final: str) -> None:
        self._calls = iter(calls)
        self._remaining = len(calls)
        self._final = final
        self.messages: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        self.messages.append(messages)
        if self._remaining:
            self._remaining -= 1
            yield StreamEvent(type=StreamEventType.TOOL_CALL, tool_call=next(self._calls))
        else:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=self._final)
        yield StreamEvent(
            type=StreamEventType.MESSAGE_DONE,
            usage=TokenUsage(input_tokens=20, output_tokens=10),
        )


@pytest.mark.asyncio
async def test_agent_discovers_peer_handles_question_and_uses_returned_artifact() -> None:
    directory = _Directory(_agent())
    client = _Client(
        [
            {"task": {"id": "task-1", "status": {"state": "TASK_STATE_SUBMITTED"}}},
            {
                "id": "task-1",
                "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                "metadata": {
                    "pendingQuestions": [
                        {"requestId": "q-1", "question": "Which branch should I review?"}
                    ]
                },
            },
            {"task": {"id": "task-1", "status": {"state": "TASK_STATE_WORKING"}}},
            {
                "id": "task-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [
                    {
                        "artifactId": "review-1",
                        "parts": [{"text": "No blocking findings; tests cover the change."}],
                    }
                ],
            },
        ]
    )
    a2a = A2ATaskTool(agent_directory=directory, client=client)
    tools: list[Any] = []
    catalog = CapabilityListTool(
        tools_provider=lambda: tools,
        agent_directory=directory,
    )
    tools.extend([catalog, a2a])
    llm = _ScriptedLLM(
        [
            ToolCall(
                id="catalog",
                name="capability_list",
                input={"kind": "agent_skill", "query": "review"},
            ),
            ToolCall(
                id="start",
                name="a2a_task",
                input={
                    "operation": "start",
                    "agent_id": "agent-aggregate-1",
                    "skill_id": "review",
                    "prompt": "Review the resident-loop change.",
                },
            ),
            ToolCall(
                id="inspect",
                name="a2a_task",
                input={
                    "operation": "get",
                    "agent_id": "agent-aggregate-1",
                    "task_id": "task-1",
                },
            ),
            ToolCall(
                id="reply",
                name="a2a_task",
                input={
                    "operation": "reply",
                    "agent_id": "agent-aggregate-1",
                    "task_id": "task-1",
                    "answer": "Review codex/resident-judgment-loop.",
                    "metadata": {"requestId": "q-1"},
                },
            ),
            ToolCall(
                id="complete",
                name="a2a_task",
                input={
                    "operation": "get",
                    "agent_id": "agent-aggregate-1",
                    "task_id": "task-1",
                },
            ),
        ],
        final=(
            "Peer review completed. My judgment uses artifact review-1: "
            "no blocking findings, with tests covering the change."
        ),
    )
    channel = InMemoryChannel()
    agent = RavnAgent(
        llm=llm,
        tools=tools,
        channel=channel,
        permission=AllowAllPermission(),
        system_prompt="Choose capabilities from evidence and retain peer provenance.",
        model="test-model",
        max_tokens=1024,
        max_iterations=10,
    )

    result = await agent.run_turn("Get the best available review and judge the result.")

    assert "artifact review-1" in result.response
    tool_names = [
        event.payload["tool_name"]
        for event in channel.events
        if event.type == RavnEventType.TOOL_START
    ]
    assert tool_names == [
        "capability_list",
        "a2a_task",
        "a2a_task",
        "a2a_task",
        "a2a_task",
    ]
    assert [body["method"] for _, body, _ in client.posts] == [
        "SendMessage",
        "GetTask",
        "SendMessage",
        "GetTask",
    ]
    assert any(
        "No blocking findings; tests cover the change." in str(message)
        for message in llm.messages[-1]
    )
