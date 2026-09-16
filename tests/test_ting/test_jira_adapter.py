"""Tests for Ting's Jira tracker hierarchy adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ting.adapters.jira import JiraTrackerAdapter


def _response(request: httpx.Request, payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, request=request, json=payload)


def _adapter(handler) -> JiraTrackerAdapter:  # noqa: ANN001
    adapter = JiraTrackerAdapter(
        site_url="https://example.atlassian.net",
        email="person@example.com",
        api_token="secret",
        project_keys=["platform"],
        labels=["agent-work"],
    )
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return adapter


@pytest.mark.asyncio
async def test_list_projects_uses_project_and_label_scope() -> None:
    seen_count_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.casefold().endswith("/project/platform"):
            return _response(
                request,
                {"id": "10000", "key": "PLATFORM", "name": "Platform", "archived": False},
            )
        if request.url.path.endswith("/project/PLATFORM/versions"):
            return _response(request, [{"id": "200", "name": "Q4", "released": False}])
        if request.url.path.endswith("/search/approximate-count"):
            seen_count_body.update(json.loads(request.content))
            return _response(request, {"count": 7})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    adapter = _adapter(handler)
    try:
        projects = await adapter.list_projects()
    finally:
        await adapter.close()

    assert len(projects) == 1
    assert projects[0].id == "PLATFORM"
    assert projects[0].name == "Platform"
    assert projects[0].milestone_count == 1
    assert projects[0].issue_count == 7
    assert 'project = "PLATFORM"' in seen_count_body["jql"]
    assert 'labels in ("agent-work")' in seen_count_body["jql"]


@pytest.mark.asyncio
async def test_list_issues_applies_filters_and_maps_jira_fields() -> None:
    seen_jql = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_jql
        if request.url.path.casefold().endswith("/project/platform"):
            return _response(request, {"id": "10000", "key": "PLATFORM", "name": "Platform"})
        if request.url.path.endswith("/search/jql"):
            seen_jql = request.url.params["jql"]
            return _response(
                request,
                {
                    "isLast": True,
                    "issues": [
                        {
                            "id": "300",
                            "key": "PLATFORM-42",
                            "fields": {
                                "summary": "Ship the integration",
                                "description": {
                                    "type": "doc",
                                    "version": 1,
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [{"type": "text", "text": "Ready"}],
                                        }
                                    ],
                                },
                                "status": {
                                    "name": "In Progress",
                                    "statusCategory": {"key": "indeterminate"},
                                },
                                "assignee": {"displayName": "Ada"},
                                "labels": ["agent-work"],
                                "priority": {"name": "High"},
                                "fixVersions": [{"id": "200", "name": "Q4"}],
                                "timeoriginalestimate": 7200,
                                "project": {"key": "PLATFORM"},
                            },
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    adapter = _adapter(handler)
    try:
        issues = await adapter.list_issues("platform", milestone_id="200")
    finally:
        await adapter.close()

    assert 'project = "PLATFORM"' in seen_jql
    assert 'labels in ("agent-work")' in seen_jql
    assert 'fixVersion = "200"' in seen_jql
    assert issues[0].id == "PLATFORM-42"
    assert issues[0].description == "Ready"
    assert issues[0].status_type == "started"
    assert issues[0].assignee == "Ada"
    assert issues[0].priority == 2
    assert issues[0].estimate == 2.0
    assert issues[0].milestone_id == "200"


@pytest.mark.asyncio
async def test_project_outside_scope_is_rejected_after_id_resolution() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, {"id": "999", "key": "OTHER", "name": "Other"})

    adapter = _adapter(handler)
    try:
        with pytest.raises(Exception, match="outside this Jira connection's scope"):
            await adapter.get_project("999")
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    ("name", "category", "expected"),
    [
        ("To Do", "new", "unstarted"),
        ("Backlog", "new", "unstarted"),
        ("Blocked", "new", "blocked"),
        ("On Hold", "new", "blocked"),
        ("In Progress", "indeterminate", "started"),
        ("Done", "done", "completed"),
    ],
)
def test_status_type_uses_ting_vocabulary(name: str, category: str, expected: str) -> None:
    status = {"name": name, "statusCategory": {"key": category}}

    assert JiraTrackerAdapter._status_type(status) == expected
