from __future__ import annotations

import json

import pytest

from ravn.adapters.tools.workflow_tools import (
    WorkflowDescribeTool,
    WorkflowLaunchTool,
    WorkflowListTool,
)
from ravn.domain.capability_catalog import WorkflowCapability
from ravn.ports.capability import WorkflowLaunchRequest, WorkflowLaunchResult


class FakeWorkflowCapabilitySource:
    def __init__(self, *, fail_list: bool = False, fail_launch: bool = False) -> None:
        self.fail_list = fail_list
        self.fail_launch = fail_launch
        self.launch_requests: list[WorkflowLaunchRequest] = []
        self.workflows = [
            WorkflowCapability(
                workflow_id="wf-build",
                name="Build Missing Tool",
                description="Build a missing resident tool.",
                version="1",
                tags=["build", "tool"],
                metadata={"scope": "tenant"},
            ),
            WorkflowCapability(
                workflow_id="wf-research",
                name="Research Campaign",
                description="Run a research workflow.",
                version="2",
                tags=["research"],
            ),
        ]

    async def list_workflows(self) -> list[WorkflowCapability]:
        if self.fail_list:
            raise RuntimeError("catalog unavailable")
        return list(self.workflows)

    async def launch_workflow(self, request: WorkflowLaunchRequest) -> WorkflowLaunchResult:
        if self.fail_launch:
            raise RuntimeError("launch unavailable")
        self.launch_requests.append(request)
        return WorkflowLaunchResult(
            workflow_id=request.workflow_id,
            workflow_name="Build Missing Tool",
            session_id="session-1",
            session_name=request.session_name,
            status="started",
            slug="build-missing-tool",
            cluster_name="ymir",
            owner_id="owner-1",
            tenant_id="tenant-1",
            workload_subject="system:serviceaccount:ravn:valkyrie",
            workload_name="valkyrie",
            raw={"sessionId": "session-1", "status": "started"},
        )


@pytest.mark.asyncio
async def test_workflow_list_filters_configured_sources() -> None:
    tool = WorkflowListTool([FakeWorkflowCapabilitySource()])

    result = await tool.execute({"tags": ["build"], "query": "missing"})

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["count"] == 1
    assert payload["workflows"][0]["id"] == "wf-build"
    assert payload["workflows"][0]["source_index"] == 0
    assert payload["workflows"][0]["capability"]["kind"] == "workflow"
    assert payload["workflows"][0]["capability"]["required_permission"] == "workflow:launch"


@pytest.mark.asyncio
async def test_workflow_describe_returns_named_workflow() -> None:
    tool = WorkflowDescribeTool([FakeWorkflowCapabilitySource()])

    result = await tool.execute({"name": "Research Campaign"})

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["workflow"]["id"] == "wf-research"


@pytest.mark.asyncio
async def test_workflow_launch_uses_discovered_source_and_records_provenance() -> None:
    first = FakeWorkflowCapabilitySource(fail_list=True, fail_launch=True)
    second = FakeWorkflowCapabilitySource()
    tool = WorkflowLaunchTool([first, second])

    result = await tool.execute(
        {
            "workflow_id": "wf-build",
            "prompt": "Build the printer tool",
            "session_name": "Printer tool builder",
            "provenance": {"signal_id": "sig-1"},
        }
    )

    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["launch"]["session_id"] == "session-1"
    assert payload["source_index"] == 1
    assert second.launch_requests[0].provenance == {
        "signal_id": "sig-1",
        "launched_by": "workflow_launch",
    }


@pytest.mark.asyncio
async def test_workflow_launch_requires_prompt() -> None:
    tool = WorkflowLaunchTool([FakeWorkflowCapabilitySource()])

    result = await tool.execute({"workflow_id": "wf-build"})

    assert result.is_error
    assert result.content == "prompt is required"
