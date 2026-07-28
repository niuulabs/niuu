"""Tests for Ravn platform tools."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ravn.adapters.tools.platform_tools import (
    TingPlanTool,
    TingResearchTool,
    TingSagaTool,
    TingSpecTool,
    TingWorkflowTool,
    TrackerIssueTool,
    VolundrGitTool,
    VolundrSessionTool,
)

BASE_URL = "http://localhost:8080"
FORGE_SESSIONS_URL = f"{BASE_URL}/api/v1/forge/sessions"
FORGE_GIT_URL = f"{BASE_URL}/api/v1/forge/repos"
TRACKER_ISSUES_URL = f"{BASE_URL}/api/v1/tracker/issues"
TING_WORKFLOWS_URL = f"{BASE_URL}/api/v1/ting/workflows"
TING_RESEARCH_URL = f"{BASE_URL}/api/v1/ting/research/campaigns"
TING_PLAN_URL = f"{BASE_URL}/api/v1/ting/sagas/plan"
TING_SPEC_URL = f"{BASE_URL}/api/v1/ting/specs/campaigns"


# ===========================================================================
# VolundrSessionTool
# ===========================================================================


class TestVolundrSessionTool:
    def setup_method(self):
        self.tool = VolundrSessionTool(base_url=BASE_URL)

    def test_name(self):
        assert self.tool.name == "volundr_session"

    def test_description_mentions_sessions(self):
        assert "session" in self.tool.description.lower()

    def test_input_schema_has_action(self):
        assert "action" in self.tool.input_schema["properties"]

    def test_required_permission(self):
        assert self.tool.required_permission == "platform:api"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_sessions(self):
        respx.get(FORGE_SESSIONS_URL).mock(return_value=httpx.Response(200, json=[{"id": "abc"}]))
        result = await self.tool.execute({"action": "list"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data[0]["id"] == "abc"

    @pytest.mark.asyncio
    @respx.mock
    async def test_uses_workload_exchange_when_no_pat(self, tmp_path):
        token_file = tmp_path / "workload.jwt"
        token_file.write_text("projected-token", encoding="utf-8")
        exchange = respx.post(f"{BASE_URL}/api/v1/tokens/workload/exchange").mock(
            return_value=httpx.Response(201, json={"token": "workload-bearer"})
        )
        sessions = respx.get(FORGE_SESSIONS_URL).mock(
            return_value=httpx.Response(200, json=[{"id": "abc"}])
        )
        tool = VolundrSessionTool(base_url=BASE_URL, workload_token_file=str(token_file))

        result = await tool.execute({"action": "list"})

        assert not result.is_error
        assert json.loads(exchange.calls.last.request.content)["audiences"] == [
            "volundr-api",
            "forge",
            "ting",
            "mimir",
            "guild",
        ]
        assert sessions.calls.last.request.headers["authorization"] == "Bearer workload-bearer"

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_session(self):
        respx.post(FORGE_SESSIONS_URL).mock(
            return_value=httpx.Response(200, json={"id": "new-session"})
        )
        result = await self.tool.execute({"action": "create", "name": "my-session"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "new-session"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_session(self):
        session_id = "sess-get"
        respx.get(f"{FORGE_SESSIONS_URL}/{session_id}").mock(
            return_value=httpx.Response(200, json={"id": session_id, "status": "running"})
        )

        result = await self.tool.execute({"action": "get", "session_id": session_id})

        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == session_id

    @pytest.mark.asyncio
    async def test_create_session_missing_name(self):
        result = await self.tool.execute({"action": "create"})
        assert result.is_error
        assert "name" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_stop_session(self):
        session_id = "sess-123"
        respx.post(f"{FORGE_SESSIONS_URL}/{session_id}/stop").mock(
            return_value=httpx.Response(200, json={"status": "stopped"})
        )
        result = await self.tool.execute({"action": "stop", "session_id": session_id})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "stopped"

    @pytest.mark.asyncio
    @respx.mock
    async def test_start_session(self):
        session_id = "sess-start"
        respx.post(f"{FORGE_SESSIONS_URL}/{session_id}/start").mock(
            return_value=httpx.Response(200, json={"status": "running"})
        )

        result = await self.tool.execute({"action": "start", "session_id": session_id})

        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_stop_session_missing_id(self):
        result = await self.tool.execute({"action": "stop"})
        assert result.is_error
        assert "session_id" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_delete_session(self):
        session_id = "sess-456"
        respx.delete(f"{FORGE_SESSIONS_URL}/{session_id}").mock(
            return_value=httpx.Response(200, json={})
        )
        result = await self.tool.execute({"action": "delete", "session_id": session_id})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await self.tool.execute({"action": "explode"})
        assert result.is_error
        assert "Unknown action" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_api_error_returns_error_result(self):
        respx.get(FORGE_SESSIONS_URL).mock(return_value=httpx.Response(500, text="internal error"))
        result = await self.tool.execute({"action": "list"})
        assert result.is_error


# ===========================================================================
# VolundrGitTool
# ===========================================================================


class TestVolundrGitTool:
    def setup_method(self):
        self.tool = VolundrGitTool(base_url=BASE_URL)

    def test_name(self):
        assert self.tool.name == "volundr_git"

    def test_required_permission(self):
        assert self.tool.required_permission == "platform:api"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos(self):
        respx.get(f"{BASE_URL}/api/v1/niuu/repos").mock(
            return_value=httpx.Response(
                200,
                json={
                    "github": [
                        {
                            "name": "laevateinn",
                            "url": "https://github.com/niuulabs/laevateinn",
                            "clone_url": "https://github.com/niuulabs/laevateinn.git",
                            "default_branch": "dev",
                        }
                    ]
                },
            )
        )

        result = await self.tool.execute({"action": "list_repos"})

        assert not result.is_error
        assert json.loads(result.content)["github"][0]["name"] == "laevateinn"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_branches(self):
        respx.get(f"{FORGE_GIT_URL}/branches").mock(
            return_value=httpx.Response(200, json=[{"name": "main"}])
        )
        result = await self.tool.execute(
            {"action": "list_branches", "repo_url": "https://github.com/org/repo"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data[0]["name"] == "main"

    @pytest.mark.asyncio
    async def test_list_branches_missing_repo_url(self):
        result = await self.tool.execute({"action": "list_branches"})
        assert result.is_error

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pr(self):
        respx.post(f"{FORGE_GIT_URL}/prs").mock(
            return_value=httpx.Response(200, json={"url": "https://github.com/pr/1"})
        )
        result = await self.tool.execute(
            {
                "action": "create_pr",
                "session_id": "sess-123",
                "title": "My PR",
            }
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["url"] == "https://github.com/pr/1"

    @pytest.mark.asyncio
    async def test_create_pr_missing_session_id(self):
        result = await self.tool.execute({"action": "create_pr", "title": "My PR"})
        assert result.is_error

    @pytest.mark.asyncio
    @respx.mock
    async def test_ci_status(self):
        respx.get(f"{FORGE_GIT_URL}/prs/42/ci").mock(
            return_value=httpx.Response(200, json={"status": "passing"})
        )
        result = await self.tool.execute(
            {
                "action": "ci_status",
                "pr_number": 42,
                "repo_url": "https://github.com/org/repo",
                "branch": "main",
            }
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "passing"

    @pytest.mark.asyncio
    async def test_ci_status_missing_fields(self):
        result = await self.tool.execute({"action": "ci_status", "pr_number": 42})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await self.tool.execute({"action": "unknown"})
        assert result.is_error


# ===========================================================================
# TingSagaTool
# ===========================================================================


class TestTingSagaTool:
    def setup_method(self):
        self.tool = TingSagaTool(base_url=BASE_URL)

    def test_name(self):
        assert self.tool.name == "ting_saga"

    def test_required_permission(self):
        assert self.tool.required_permission == "platform:api"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_sagas(self):
        respx.get(f"{BASE_URL}/api/v1/ting/sagas").mock(return_value=httpx.Response(200, json=[]))
        result = await self.tool.execute({"action": "list"})
        assert not result.is_error

    @pytest.mark.asyncio
    @respx.mock
    async def test_commit_saga(self):
        respx.post(f"{BASE_URL}/api/v1/ting/sagas/commit").mock(
            return_value=httpx.Response(200, json={"id": "saga-1"})
        )
        result = await self.tool.execute(
            {
                "action": "commit",
                "name": "my saga",
                "slug": "my-saga",
                "repos": ["org/repo"],
                "base_branch": "main",
                "phases": [{"name": "phase-1", "runs": [{"name": "run-1"}]}],
            }
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "saga-1"

    @pytest.mark.asyncio
    async def test_commit_saga_missing_fields(self):
        result = await self.tool.execute({"action": "commit"})
        assert result.is_error

    @pytest.mark.asyncio
    @respx.mock
    async def test_dispatch_saga(self):
        respx.post(f"{BASE_URL}/api/v1/ting/dispatch/approve").mock(
            return_value=httpx.Response(200, json={"dispatched": 1})
        )
        result = await self.tool.execute(
            {
                "action": "dispatch",
                "items": [{"saga_id": "saga-1", "issue_id": "NIU-1", "repo": "org/repo"}],
            }
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["dispatched"] == 1

    @pytest.mark.asyncio
    async def test_dispatch_missing_items(self):
        result = await self.tool.execute({"action": "dispatch"})
        assert result.is_error

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_saga(self):
        respx.get(f"{BASE_URL}/api/v1/ting/sagas/saga-1").mock(
            return_value=httpx.Response(200, json={"id": "saga-1", "status": "ACTIVE"})
        )
        result = await self.tool.execute({"action": "get", "saga_id": "saga-1"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "saga-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_runs(self):
        respx.get(f"{BASE_URL}/api/v1/ting/runs/active").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await self.tool.execute({"action": "runs"})
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await self.tool.execute({"action": "nope"})
        assert result.is_error


# ===========================================================================
# TrackerIssueTool
# ===========================================================================


class TestTrackerIssueTool:
    def setup_method(self):
        self.tool = TrackerIssueTool(base_url=BASE_URL)

    def test_name(self):
        assert self.tool.name == "tracker_issue"

    def test_required_permission(self):
        assert self.tool.required_permission == "platform:api"

    @pytest.mark.asyncio
    @respx.mock
    async def test_search_issues(self):
        respx.get(TRACKER_ISSUES_URL).mock(
            return_value=httpx.Response(200, json=[{"id": "NIU-1", "title": "Fix bug"}])
        )
        result = await self.tool.execute({"action": "search", "query": "Fix bug"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data[0]["id"] == "NIU-1"

    @pytest.mark.asyncio
    async def test_search_issues_missing_query(self):
        result = await self.tool.execute({"action": "search"})
        assert result.is_error
        assert "query" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_update_status(self):
        respx.patch(f"{TRACKER_ISSUES_URL}/NIU-1").mock(
            return_value=httpx.Response(200, json={"id": "NIU-1", "status": "Done"})
        )
        result = await self.tool.execute(
            {"action": "update_status", "issue_id": "NIU-1", "status": "Done"}
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["status"] == "Done"

    @pytest.mark.asyncio
    async def test_update_status_missing_fields(self):
        result = await self.tool.execute({"action": "update_status", "status": "Done"})
        assert result.is_error

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_issue(self):
        respx.get(f"{TRACKER_ISSUES_URL}/NIU-1").mock(
            return_value=httpx.Response(200, json={"id": "NIU-1"})
        )
        result = await self.tool.execute({"action": "get", "issue_id": "NIU-1"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "NIU-1"

    @pytest.mark.asyncio
    async def test_get_issue_missing_id(self):
        result = await self.tool.execute({"action": "get"})
        assert result.is_error

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await self.tool.execute({"action": "unknown"})
        assert result.is_error


# ===========================================================================
# TingWorkflowTool
# ===========================================================================


class TestTingWorkflowTool:
    def setup_method(self):
        self.tool = TingWorkflowTool(base_url=BASE_URL)

    def test_name(self):
        assert self.tool.name == "ting_workflow"

    def test_required_permission(self):
        assert self.tool.required_permission == "platform:api"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_workflows(self):
        respx.get(TING_WORKFLOWS_URL).mock(
            return_value=httpx.Response(200, json=[{"id": "wf-1", "name": "Research Campaign"}])
        )
        result = await self.tool.execute({"action": "list"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data[0]["id"] == "wf-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_workflow(self):
        workflow_id = "wf-1"
        respx.get(f"{TING_WORKFLOWS_URL}/{workflow_id}").mock(
            return_value=httpx.Response(200, json={"id": workflow_id, "name": "Research Campaign"})
        )
        result = await self.tool.execute({"action": "get", "workflow_id": workflow_id})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == workflow_id

    @pytest.mark.asyncio
    async def test_get_workflow_missing_id(self):
        result = await self.tool.execute({"action": "get"})
        assert result.is_error
        assert "workflow_id" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_workflow(self):
        workflow_id = "wf-1"
        respx.post(f"{TING_WORKFLOWS_URL}/{workflow_id}/launch").mock(
            return_value=httpx.Response(
                201,
                json={
                    "workflowId": workflow_id,
                    "workflowName": "Research Campaign",
                    "slug": "germany-market",
                    "sessionId": "sess-1",
                    "sessionName": "research-germany-market",
                    "status": "running",
                    "clusterName": "valhalla",
                },
            )
        )
        result = await self.tool.execute(
            {
                "action": "launch",
                "workflow_id": workflow_id,
                "prompt": "Research whether Germany is a good expansion market.",
                "slug": "germany-market",
                "context": {
                    "mode": "evaluative",
                    "seed_urls": ["https://example.com"],
                },
            }
        )
        assert not result.is_error
        data = json.loads(result.content)
        assert data["sessionId"] == "sess-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_workflow_joins_when_chat_endpoint_returned(self):
        class JoinManager:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def join(self, session_id: str, chat_endpoint: str) -> dict:
                self.calls.append((session_id, chat_endpoint))
                return {"session_id": session_id, "connected": True}

        manager = JoinManager()
        tool = TingWorkflowTool(base_url=BASE_URL, session_join_manager=manager)
        respx.post(f"{TING_WORKFLOWS_URL}/wf-1/launch").mock(
            return_value=httpx.Response(
                201,
                json={
                    "sessionId": "sess-1",
                    "chatEndpoint": "wss://sessions.example/s/sess-1/session",
                },
            )
        )

        result = await tool.execute(
            {"action": "launch", "workflow_id": "wf-1", "prompt": "Research it."}
        )

        assert not result.is_error
        data = json.loads(result.content)
        assert manager.calls == [("sess-1", "wss://sessions.example/s/sess-1/session")]
        assert data["observerJoin"]["connected"] is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_workflow_reports_join_skipped_without_chat_endpoint(self):
        class JoinManager:
            async def join(self, session_id: str, chat_endpoint: str) -> dict:
                raise AssertionError("should not join without chatEndpoint")

        tool = TingWorkflowTool(base_url=BASE_URL, session_join_manager=JoinManager())
        respx.post(f"{TING_WORKFLOWS_URL}/wf-1/launch").mock(
            return_value=httpx.Response(201, json={"sessionId": "sess-1"})
        )

        result = await tool.execute(
            {"action": "launch", "workflow_id": "wf-1", "prompt": "Research it."}
        )

        assert not result.is_error
        data = json.loads(result.content)
        assert data["observerJoin"]["status"] == "skipped"

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_workflow_by_configured_alias_id(self):
        tool = TingWorkflowTool(
            base_url=BASE_URL,
            workflow_aliases={
                "research": {
                    "workflow_id": "wf-research",
                    "defaults": {
                        "model": "claude-sonnet-4-6",
                        "gate_auto_forward_after": "",
                        "provenance": {"resident": "muninn"},
                    },
                }
            },
        )
        route = respx.post(f"{TING_WORKFLOWS_URL}/wf-research/launch").mock(
            return_value=httpx.Response(
                201,
                json={
                    "workflowId": "wf-research",
                    "slug": "expansion",
                    "sessionId": "sess-research",
                    "sessionName": "research-expansion",
                    "status": "running",
                },
            )
        )

        result = await tool.execute(
            {
                "action": "launch",
                "workflow_alias": "research",
                "prompt": "Research the expansion options.",
                "provenance": {"resident": "muninn", "initiative": "expansion"},
            }
        )

        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["prompt"] == "Research the expansion options."
        assert body["model"] == "claude-sonnet-4-6"
        assert body["gateAutoForwardAfter"] == ""
        assert body["provenance"] == {"resident": "muninn", "initiative": "expansion"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_workflow_alias_preserves_explicit_context(self):
        tool = TingWorkflowTool(
            base_url=BASE_URL,
            workflow_aliases={"research": {"workflow_id": "wf-research"}},
        )
        route = respx.post(f"{TING_WORKFLOWS_URL}/wf-research/launch").mock(
            return_value=httpx.Response(
                201,
                json={
                    "workflowId": "wf-research",
                    "slug": "germany-market",
                    "sessionId": "sess-research",
                    "sessionName": "research-germany-market",
                    "status": "running",
                },
            )
        )

        result = await tool.execute(
            {
                "action": "launch",
                "workflow_alias": "research",
                "prompt": "Research the market.",
                "context": {
                    "question": "Is Germany a good expansion market?",
                    "mode": "evaluative",
                },
            }
        )

        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["prompt"] == "Research the market."
        assert body["context"]["question"] == "Is Germany a good expansion market?"
        assert body["context"]["mode"] == "evaluative"

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_workflow_by_configured_alias_name(self):
        tool = TingWorkflowTool(
            base_url=BASE_URL,
            workflow_aliases={"research": {"name": "Research Campaign", "scope": ""}},
        )
        respx.get(TING_WORKFLOWS_URL).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": "wf-other", "name": "Other"},
                    {"id": "wf-research", "name": "Research Campaign"},
                ],
            )
        )
        respx.post(f"{TING_WORKFLOWS_URL}/wf-research/launch").mock(
            return_value=httpx.Response(
                201,
                json={
                    "workflowId": "wf-research",
                    "slug": "expansion",
                    "sessionId": "sess-research",
                    "sessionName": "research-expansion",
                    "status": "running",
                },
            )
        )

        result = await tool.execute(
            {
                "action": "launch",
                "workflow_alias": "research",
                "prompt": "Research the expansion options.",
            }
        )

        assert not result.is_error
        data = json.loads(result.content)
        assert data["sessionId"] == "sess-research"

    @pytest.mark.asyncio
    async def test_launch_workflow_unknown_alias(self):
        result = await self.tool.execute(
            {
                "action": "launch",
                "workflow_alias": "research",
                "prompt": "Research the expansion options.",
            }
        )
        assert result.is_error
        assert "workflow_alias 'research' is not configured" in result.content

    @pytest.mark.asyncio
    async def test_launch_workflow_missing_fields(self):
        result = await self.tool.execute({"action": "launch", "workflow_id": "wf-1"})
        assert result.is_error
        assert "prompt" in result.content

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        result = await self.tool.execute({"action": "boom"})
        assert result.is_error


# ===========================================================================
# TingResearchTool
# ===========================================================================


class TestTingResearchTool:
    def setup_method(self):
        self.tool = TingResearchTool(base_url=BASE_URL)

    def test_name(self):
        assert self.tool.name == "ting_research"

    def test_required_permission(self):
        assert self.tool.required_permission == "platform:api"

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_research_by_configured_alias_id(self):
        tool = TingResearchTool(
            base_url=BASE_URL,
            workflow_aliases={
                "research": {
                    "workflow_id": "wf-research",
                    "defaults": {
                        "model": "claude-sonnet-4-6",
                        "gate_auto_forward_after": "",
                        "connection_id": "Valhalla",
                    },
                }
            },
        )
        route = respx.post(TING_RESEARCH_URL).mock(
            return_value=httpx.Response(
                201,
                json={
                    "workflowId": "wf-research",
                    "slug": "expansion",
                    "sessionId": "sess-research",
                    "sessionName": "research-expansion",
                    "status": "running",
                },
            )
        )

        result = await tool.execute(
            {
                "action": "launch",
                "workflow_alias": "research",
                "prompt": "Research the expansion options.",
            }
        )

        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["question"] == "Research the expansion options."
        assert body["workflowId"] == "wf-research"
        assert body["mode"] == "exploratory"
        assert body["model"] == "claude-sonnet-4-6"
        assert body["connectionId"] == "Valhalla"
        assert body["gateAutoForwardAfter"] == ""

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_research_defaults_to_research_alias(self):
        tool = TingResearchTool(
            base_url=BASE_URL,
            workflow_aliases={
                "research": {
                    "workflow_id": "wf-research",
                    "defaults": {"connection_id": "Valhalla"},
                }
            },
        )
        route = respx.post(TING_RESEARCH_URL).mock(
            return_value=httpx.Response(
                201,
                json={
                    "workflowId": "wf-research",
                    "sessionId": "sess-research",
                    "status": "running",
                },
            )
        )

        result = await tool.execute(
            {"action": "launch", "prompt": "Look into agent memory solutions."}
        )

        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["workflowId"] == "wf-research"
        assert body["connectionId"] == "Valhalla"

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_research_preserves_explicit_question_context(self):
        tool = TingResearchTool(
            base_url=BASE_URL,
            workflow_aliases={"research": {"workflow_id": "wf-research"}},
        )
        route = respx.post(TING_RESEARCH_URL).mock(
            return_value=httpx.Response(
                201,
                json={
                    "workflowId": "wf-research",
                    "slug": "germany-market",
                    "sessionId": "sess-research",
                    "sessionName": "research-germany-market",
                    "status": "running",
                },
            )
        )

        result = await tool.execute(
            {
                "action": "launch",
                "workflow_alias": "research",
                "prompt": "Research the market.",
                "context": {
                    "question": "Is Germany a good expansion market?",
                    "mode": "evaluative",
                },
            }
        )

        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["question"] == "Is Germany a good expansion market?"
        assert body["mode"] == "evaluative"

    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_research_without_workflow_id_uses_platform_default(self):
        route = respx.post(TING_RESEARCH_URL).mock(
            return_value=httpx.Response(
                201,
                json={
                    "slug": "memory-research",
                    "sessionId": "sess-research",
                    "status": "running",
                },
            )
        )

        result = await self.tool.execute(
            {"action": "launch", "question": "Look into agent memory solutions."}
        )

        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["question"] == "Look into agent memory solutions."
        assert "workflowId" not in body


class TestTingPlanTool:
    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_plan_uses_configured_alias_defaults(self):
        tool = TingPlanTool(
            base_url=BASE_URL,
            workflow_aliases={
                "plan": {
                    "workflow_id": "wf-plan",
                    "defaults": {
                        "connection_id": "Valhalla",
                        "base_branch": "dev",
                    },
                }
            },
        )
        route = respx.post(TING_PLAN_URL).mock(
            return_value=httpx.Response(
                201,
                json={
                    "session_id": "sess-plan",
                    "chat_endpoint": "/api/v1/forge/sessions/sess-plan/messages",
                    "campaign_slug": "plan-build-it",
                },
            )
        )

        result = await tool.execute({"action": "launch", "prompt": "Plan building it."})

        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["spec"] == "Plan building it."
        assert body["workflowId"] == "wf-plan"
        assert body["connectionId"] == "Valhalla"
        assert body["base_branch"] == "dev"


class TestTingSpecTool:
    @pytest.mark.asyncio
    @respx.mock
    async def test_launch_spec_uses_configured_alias_defaults(self):
        tool = TingSpecTool(
            base_url=BASE_URL,
            workflow_aliases={
                "spec": {
                    "workflow_id": "wf-spec",
                    "defaults": {
                        "connection_id": "Valhalla",
                        "repos": ["niuulabs/volundr"],
                    },
                }
            },
        )
        route = respx.post(TING_SPEC_URL).mock(
            return_value=httpx.Response(
                201,
                json={
                    "sessionId": "sess-spec",
                    "chatEndpoint": "/api/v1/forge/sessions/sess-spec/messages",
                    "slug": "spec-build-it",
                },
            )
        )

        result = await tool.execute({"action": "launch", "prompt": "Specify building it."})

        assert not result.is_error
        body = json.loads(route.calls.last.request.content)
        assert body["prompt"] == "Specify building it."
        assert body["workflowId"] == "wf-spec"
        assert body["connectionId"] == "Valhalla"
        assert body["repos"] == ["niuulabs/volundr"]
