from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

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


@pytest.mark.asyncio
async def test_capability_list_rejects_unknown_kind() -> None:
    tool = CapabilityListTool(tools_provider=lambda: [FakeTool()])

    result = await tool.execute({"kind": "policy"})

    assert result.is_error
    assert "Unsupported capability kind" in result.content
