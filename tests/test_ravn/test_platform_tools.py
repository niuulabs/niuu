"""Tests for Ravn platform tools (volundr_session, volundr_git, tyr_saga, tracker_issue)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ravn.adapters.tools.platform_tools import (
    TrackerIssueTool,
    TyrSagaTool,
    VolundrGitTool,
    VolundrSessionTool,
)

BASE_URL = "http://localhost:8080"
FORGE_SESSIONS_URL = f"{BASE_URL}/api/v1/forge/sessions"
FORGE_GIT_URL = f"{BASE_URL}/api/v1/forge/repos"
TRACKER_ISSUES_URL = f"{BASE_URL}/api/v1/tracker/issues"


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
# TyrSagaTool
# ===========================================================================


class TestTyrSagaTool:
    def setup_method(self):
        self.tool = TyrSagaTool(base_url=BASE_URL)

    def test_name(self):
        assert self.tool.name == "tyr_saga"

    def test_required_permission(self):
        assert self.tool.required_permission == "platform:api"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_sagas(self):
        respx.get(f"{BASE_URL}/api/v1/tyr/sagas").mock(return_value=httpx.Response(200, json=[]))
        result = await self.tool.execute({"action": "list"})
        assert not result.is_error

    @pytest.mark.asyncio
    @respx.mock
    async def test_commit_saga(self):
        respx.post(f"{BASE_URL}/api/v1/tyr/sagas/commit").mock(
            return_value=httpx.Response(200, json={"id": "saga-1"})
        )
        result = await self.tool.execute(
            {
                "action": "commit",
                "name": "my saga",
                "slug": "my-saga",
                "repos": ["org/repo"],
                "base_branch": "main",
                "phases": [{"name": "phase-1", "raids": [{"name": "raid-1"}]}],
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
        respx.post(f"{BASE_URL}/api/v1/tyr/dispatch/approve").mock(
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
        respx.get(f"{BASE_URL}/api/v1/tyr/sagas/saga-1").mock(
            return_value=httpx.Response(200, json={"id": "saga-1", "status": "ACTIVE"})
        )
        result = await self.tool.execute({"action": "get", "saga_id": "saga-1"})
        assert not result.is_error
        data = json.loads(result.content)
        assert data["id"] == "saga-1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_raids(self):
        respx.get(f"{BASE_URL}/api/v1/tyr/raids/active").mock(
            return_value=httpx.Response(200, json=[])
        )
        result = await self.tool.execute({"action": "raids"})
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
        respx.post(f"{TRACKER_ISSUES_URL}/NIU-1").mock(
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
