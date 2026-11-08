"""Tests for the tracker_issue tool."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from tracker.tool import TrackerIssueTool

BASE_URL = "http://localhost:8080"
TRACKER_ISSUES_URL = f"{BASE_URL}/api/v1/tracker/issues"


class TestTrackerIssueTool:
    def setup_method(self):
        self.tool = TrackerIssueTool(base_url=BASE_URL)

    def test_name(self):
        assert self.tool.name == "tracker_issue"

    def test_required_permission(self):
        assert self.tool.required_permission == "platform:api"

    def test_description_says_get_returns_the_issue_body(self):
        description = self.tool.description
        assert "get (issue_id) — full issue including its description" in description
        assert "acceptance criteria" in description

    def test_input_schema_offers_create(self):
        schema = self.tool.input_schema
        assert "create" in schema["properties"]["action"]["enum"]
        for field in ("title", "description", "team", "labels", "priority", "connection_id"):
            assert field in schema["properties"]
        assert "create (title + description + team" in self.tool.description

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_issue_posts_the_body(self):
        route = respx.post(TRACKER_ISSUES_URL).mock(
            return_value=httpx.Response(201, json={"identifier": "NIU-2000"})
        )
        result = await self.tool.execute(
            {
                "action": "create",
                "title": "Tracker API lacks create",
                "description": "## Acceptance criteria",
                "team": "NIU",
                "labels": ["platform-defect"],
                "priority": 2,
                "connection_id": None,
            }
        )
        assert not result.is_error
        assert json.loads(result.content)["identifier"] == "NIU-2000"
        assert json.loads(route.calls.last.request.content) == {
            "title": "Tracker API lacks create",
            "description": "## Acceptance criteria",
            "team": "NIU",
            "labels": ["platform-defect"],
            "priority": 2,
        }

    @pytest.mark.asyncio
    async def test_create_issue_requires_title_description_team(self):
        result = await self.tool.execute({"action": "create", "title": "T"})
        assert result.is_error
        assert "description, team required for create action" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_issue_error_includes_the_api_detail(self):
        respx.post(TRACKER_ISSUES_URL).mock(
            return_value=httpx.Response(400, json={"detail": "Linear team not found: NOPE"})
        )
        result = await self.tool.execute(
            {"action": "create", "title": "T", "description": "B", "team": "NOPE"}
        )
        assert result.is_error
        assert "400" in result.content
        assert "Linear team not found: NOPE" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_issue_transport_error(self):
        respx.post(TRACKER_ISSUES_URL).mock(side_effect=httpx.ConnectError("refused"))
        result = await self.tool.execute(
            {"action": "create", "title": "T", "description": "B", "team": "NIU"}
        )
        assert result.is_error
        assert "Failed to create issue: refused" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_issue_returns_description(self):
        respx.get(f"{TRACKER_ISSUES_URL}/NIU-1092").mock(
            return_value=httpx.Response(
                200,
                json={"id": "NIU-1092", "description": "## Acceptance criteria\n- [ ] done"},
            )
        )
        result = await self.tool.execute({"action": "get", "issue_id": "NIU-1092"})
        assert not result.is_error
        assert json.loads(result.content)["description"] == "## Acceptance criteria\n- [ ] done"

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
    async def test_update_rewrites_the_ticket(self):
        """Regin was told to change NIU-1116 from dev to regin and had no way to."""
        route = respx.patch(f"{TRACKER_ISSUES_URL}/NIU-1116").mock(
            return_value=httpx.Response(200, json={"id": "NIU-1116", "description": "regin"})
        )
        result = await self.tool.execute(
            {"action": "update", "issue_id": "NIU-1116", "description": "Branch from regin."}
        )
        assert not result.is_error
        assert json.loads(route.calls.last.request.content) == {"description": "Branch from regin."}

    @pytest.mark.asyncio
    async def test_update_needs_an_issue_and_a_change(self):
        result = await self.tool.execute({"action": "update", "issue_id": "NIU-1116"})
        assert result.is_error
        assert "title and/or description" in result.content

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
    @respx.mock
    async def test_update_status_failure_carries_the_server_detail(self):
        """A resident must see why a write failed, not just the status code."""
        detail = (
            "Issue tracker connection conn-1 cannot update issue NIU-1: "
            "Status 'Cancelled' not found. Available: Todo, Done, Canceled"
        )
        respx.patch(f"{TRACKER_ISSUES_URL}/NIU-1").mock(
            return_value=httpx.Response(400, json={"detail": detail})
        )
        result = await self.tool.execute(
            {"action": "update_status", "issue_id": "NIU-1", "status": "Cancelled"}
        )
        assert result.is_error
        assert "Available: Todo, Done, Canceled" in result.content

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
