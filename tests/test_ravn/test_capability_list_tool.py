from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from niuu.domain.agent_directory import AgentDirectoryPage
from ravn.adapters.tools.capability_catalog import CapabilityListTool
from ravn.domain.capability_catalog import WorkflowCapability
from ravn.domain.models import Skill, ToolResult
from ravn.ports.capability import WorkflowLaunchRequest, WorkflowLaunchResult
from ravn.ports.tool import ToolPort


class FakeTool(ToolPort):
    @property
    def name(self) -> str:
        return "mimir_search"

    @property
    def description(self) -> str:
        return "Search Mimir."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    @property
    def required_permission(self) -> str:
        return "mimir:read"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content="ok")


class FakeSkillPort:
    async def list_skills(self, query: str | None = None) -> list[Skill]:
        return [
            Skill(
                skill_id="skill-1",
                name="Investigate pod crash",
                description="Gather pod failure context.",
                content="body",
                requires_tools=["kubernetes_inspect"],
                fallback_for_tools=[],
                source_episodes=[],
                created_at=datetime.now(UTC),
                success_count=2,
            )
        ]


class FakeWorkflowSource:
    async def list_workflows(self) -> list[WorkflowCapability]:
        return [
            WorkflowCapability(
                workflow_id="wf-build",
                name="Tool Builder",
                description="Build a missing tool.",
                tags=["tool-builder"],
            )
        ]

    async def launch_workflow(self, request: WorkflowLaunchRequest) -> WorkflowLaunchResult:
        raise AssertionError("capability_list must not launch workflows")


class FakeAgentDirectory:
    async def list_agents(self) -> AgentDirectoryPage:
        return AgentDirectoryPage.model_validate(
            {
                "items": [
                    {
                        "id": "agent-1",
                        "canonicalId": "source:observatory-a:agent-a",
                        "sourceAgentId": "agent-a",
                        "sourceInstanceId": "observatory-a",
                        "clusterId": "cluster-a",
                        "topologyNodeId": "runtime:agent-a",
                        "name": "Research peer",
                        "description": "Researches unfamiliar environments.",
                        "kind": "resident",
                        "cardUrl": "https://peer.example/card",
                        "cardVersion": "1.2",
                        "cardHash": "card-hash",
                        "skillIds": ["research"],
                        "skills": [
                            {
                                "id": "research",
                                "name": "Research environment",
                                "description": "Gather source-backed context.",
                                "tags": ["research"],
                            }
                        ],
                        "defaultInputModes": ["text/plain"],
                        "defaultOutputModes": ["application/json"],
                        "supportedInterfaces": [
                            {
                                "url": "https://peer.example/a2a",
                                "protocolBinding": "JSONRPC",
                                "protocolVersion": "1.0",
                            }
                        ],
                        "securityRequirements": [{"schemes": {"bearer": {}}}],
                        "observedStatus": "healthy",
                        "lastSeen": "2026-07-20T12:00:00Z",
                        "visibility": "tenant",
                    }
                ]
            }
        )

    async def get_agent(self, agent_id: str):
        page = await self.list_agents()
        return next((item for item in page.items if item.id == agent_id), None)


@pytest.mark.asyncio
async def test_capability_list_aggregates_tools_skills_and_workflows() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        skill_port=FakeSkillPort(),
        workflow_sources=[FakeWorkflowSource()],
    )

    result = await tool.execute({})

    assert not result.is_error
    payload = json.loads(result.content)
    assert [(item["kind"], item["name"]) for item in payload["capabilities"]] == [
        ("tool", "mimir_search"),
        ("skill", "Investigate pod crash"),
        ("workflow", "Tool Builder"),
    ]


@pytest.mark.asyncio
async def test_capability_list_filters_catalog_without_routing() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        skill_port=FakeSkillPort(),
        workflow_sources=[FakeWorkflowSource()],
    )

    result = await tool.execute({"kind": "workflow", "tags": ["tool-builder"]})

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["count"] == 1
    assert payload["capabilities"][0]["id"] == "workflow:wf-build"
    assert payload["catalog_total"] == 3
    assert payload["catalog_counts"] == {"tool": 1, "skill": 1, "workflow": 1}


@pytest.mark.asyncio
async def test_capability_list_returns_complete_catalog_without_text_filtering() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        agent_directory=FakeAgentDirectory(),
    )

    assert "query" not in tool.input_schema["properties"]

    result = await tool.execute({})

    payload = json.loads(result.content)
    assert [item["name"] for item in payload["capabilities"]] == [
        "mimir_search",
        "Research environment",
    ]
    assert payload["total"] == 2
    assert payload["catalog_total"] == 2
    assert payload["catalog_counts"] == {"tool": 1, "agent_skill": 1}
    assert payload["catalog_preview"] == []


@pytest.mark.asyncio
async def test_capability_list_projects_agent_card_skills_with_invocation_metadata() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        agent_directory=FakeAgentDirectory(),
    )

    result = await tool.execute({"kind": "agent_skill"})

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["count"] == 1
    capability = payload["capabilities"][0]
    assert capability["id"] == "agent:agent-1:research"
    assert capability["source"] == "agent-card"
    assert capability["metadata"]["invoke_via"] == "a2a_task"
    assert capability["metadata"]["agent_id"] == "agent-1"
    assert capability["metadata"]["skill_id"] == "research"
    assert capability["metadata"]["last_seen"] == "2026-07-20T12:00:00Z"


@pytest.mark.asyncio
async def test_capability_list_rejects_unknown_kind() -> None:
    tool = CapabilityListTool(tools_provider=lambda: [FakeTool()])

    result = await tool.execute({"kind": "policy"})

    assert result.is_error
    assert "Unsupported capability kind" in result.content


def _learned_artifact(name: str, description: str = "Read a metric window."):
    from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest

    return LearnedToolArtifact(
        artifact_id=f"learned-tool:{name}",
        manifest=LearnedToolManifest(
            name=name,
            description=description,
            input_schema={"type": "object"},
            required_permission="mimir:read",
            declared_reach=[],
        ),
        tool_code="def run(input):\n    return {'ok': True}\n",
    )


@pytest.mark.asyncio
async def test_capability_list_includes_learned_tools_without_loading_them() -> None:
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        learned_tools_provider=lambda: [_learned_artifact("metric_window")],
    )

    result = await tool.execute({"kind": "tool"})

    assert not result.is_error
    payload = json.loads(result.content)
    learned = [item for item in payload["capabilities"] if item["name"] == "metric_window"]
    assert len(learned) == 1
    assert learned[0]["tags"] == ["tool", "learned"]
    assert learned[0]["metadata"]["invoke_via"] == "learned_tool_run"
    assert learned[0]["metadata"] == {
        "invoke_via": "learned_tool_run",
        "artifact_id": "learned-tool:metric_window",
        "artifact_type": "agent_tool",
        "verification": "unknown",
        "has_tests": False,
        "requirements_count": 0,
    }


@pytest.mark.asyncio
async def test_capability_list_dedupes_learned_tools_against_native_names() -> None:
    # A learned tool already registered natively (legacy bulk mode, or freshly
    # built in-session) must not appear twice in the catalog.
    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        learned_tools_provider=lambda: [_learned_artifact("mimir_search")],
    )

    result = await tool.execute({"kind": "tool"})

    payload = json.loads(result.content)
    names = [item["name"] for item in payload["capabilities"]]
    assert names.count("mimir_search") == 1
    assert payload["capabilities"][0]["tags"] != ["tool", "learned"]


@pytest.mark.asyncio
async def test_capability_list_records_learned_source_errors() -> None:
    def _boom():
        raise RuntimeError("artifact dir unreadable")

    tool = CapabilityListTool(
        tools_provider=lambda: [FakeTool()],
        learned_tools_provider=_boom,
    )

    result = await tool.execute({})

    payload = json.loads(result.content)
    assert {"kind": "learned_tool", "error": "artifact dir unreadable"} in payload["errors"]
