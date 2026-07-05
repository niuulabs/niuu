"""Tests for TingPlanTool / TingSpecTool and the launch gate override passthrough."""

from __future__ import annotations

import json

import httpx
import respx

from ravn.adapters.tools.platform_tools import TingPlanTool, TingSpecTool, TingWorkflowTool

_BASE = "http://platform.test"


def _plan_tool() -> TingPlanTool:
    return TingPlanTool(base_url=_BASE, workload_token_file="/nonexistent")


def _spec_tool() -> TingSpecTool:
    return TingSpecTool(base_url=_BASE, workload_token_file="/nonexistent")


class TestTingPlanTool:
    @respx.mock
    async def test_spawn(self):
        route = respx.post(f"{_BASE}/api/v1/ting/sagas/plan").mock(
            return_value=httpx.Response(201, json={"slug": "plan-x"})
        )
        result = await _plan_tool().execute(
            {"action": "spawn", "spec": "Build the thing", "repo": "org/repo"}
        )
        assert not result.is_error
        assert json.loads(result.content)["slug"] == "plan-x"
        body = json.loads(route.calls.last.request.content)
        assert body["spec"] == "Build the thing"
        assert body["base_branch"] == "main"

    async def test_spawn_requires_spec(self):
        result = await _plan_tool().execute({"action": "spawn"})
        assert result.is_error

    @respx.mock
    async def test_draft(self):
        respx.get(f"{_BASE}/api/v1/ting/sagas/plan/plan-x/draft").mock(
            return_value=httpx.Response(200, json={"phases": [{"name": "p1"}]})
        )
        result = await _plan_tool().execute({"action": "draft", "slug": "plan-x"})
        assert json.loads(result.content)["phases"] == [{"name": "p1"}]

    @respx.mock
    async def test_feedback_defaults_to_approve(self):
        route = respx.post(f"{_BASE}/api/v1/ting/sagas/plan/plan-x/feedback").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = await _plan_tool().execute(
            {"action": "feedback", "slug": "plan-x", "content": "ship it"}
        )
        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["decision"] == "approve"

    async def test_feedback_requires_content(self):
        result = await _plan_tool().execute({"action": "feedback", "slug": "plan-x"})
        assert result.is_error

    @respx.mock
    async def test_upstream_error_is_tool_error(self):
        respx.get(f"{_BASE}/api/v1/ting/sagas/plan").mock(return_value=httpx.Response(500))
        result = await _plan_tool().execute({"action": "list"})
        assert result.is_error

    async def test_unknown_action(self):
        result = await _plan_tool().execute({"action": "dance"})
        assert result.is_error


class TestTingSpecTool:
    @respx.mock
    async def test_review_approve(self):
        route = respx.post(f"{_BASE}/api/v1/ting/specs/campaigns/spec-x/review").mock(
            return_value=httpx.Response(200, json={"slug": "spec-x"})
        )
        result = await _spec_tool().execute(
            {"action": "review", "slug": "spec-x", "decision": "approve"}
        )
        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["decision"] == "approve"

    @respx.mock
    async def test_review_changes_requested_carries_notes(self):
        route = respx.post(f"{_BASE}/api/v1/ting/specs/campaigns/spec-x/review").mock(
            return_value=httpx.Response(200, json={"slug": "spec-x"})
        )
        await _spec_tool().execute(
            {
                "action": "review",
                "slug": "spec-x",
                "decision": "changes_requested",
                "notes": "tighten the scope",
            }
        )
        body = json.loads(route.calls.last.request.content)
        assert body["decision"] == "changes_requested"
        assert body["notes"] == "tighten the scope"

    async def test_review_requires_slug(self):
        result = await _spec_tool().execute({"action": "review"})
        assert result.is_error

    @respx.mock
    async def test_status(self):
        respx.get(f"{_BASE}/api/v1/ting/specs/campaigns/spec-x").mock(
            return_value=httpx.Response(200, json={"slug": "spec-x", "gates": []})
        )
        result = await _spec_tool().execute({"action": "status", "slug": "spec-x"})
        assert json.loads(result.content)["slug"] == "spec-x"

    @respx.mock
    async def test_artifact_requires_path(self):
        result = await _spec_tool().execute({"action": "artifact", "slug": "spec-x"})
        assert result.is_error


class TestWorkflowLaunchGateOverride:
    @respx.mock
    async def test_gate_override_and_provenance_forwarded(self):
        tool = TingWorkflowTool(base_url=_BASE, workload_token_file="/nonexistent")
        route = respx.post(f"{_BASE}/api/v1/ting/workflows/wf-1/launch").mock(
            return_value=httpx.Response(200, json={"slug": "run-1"})
        )
        result = await tool.execute(
            {
                "action": "launch",
                "workflow_id": "wf-1",
                "prompt": "research X",
                "provenance": {"initiative": "x", "resident_peer_id": "flock-product-steward"},
                "gate_auto_forward_after": "",
            }
        )
        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["gateAutoForwardAfter"] == ""
        assert body["provenance"]["resident_peer_id"] == "flock-product-steward"

    @respx.mock
    async def test_gate_override_omitted_when_absent(self):
        tool = TingWorkflowTool(base_url=_BASE, workload_token_file="/nonexistent")
        route = respx.post(f"{_BASE}/api/v1/ting/workflows/wf-1/launch").mock(
            return_value=httpx.Response(200, json={"slug": "run-1"})
        )
        await tool.execute({"action": "launch", "workflow_id": "wf-1", "prompt": "go"})
        body = json.loads(route.calls.last.request.content)
        assert "gateAutoForwardAfter" not in body
