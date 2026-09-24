"""Jira lifecycle and credential boundary regression tests."""

from dataclasses import asdict, replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4, uuid5

import httpx
import pytest

from ting.adapters.jira import JiraAPIError, JiraTrackerAdapter
from ting.domain.models import (
    PhaseStatus,
    RunStatus,
    SessionMessage,
)

PROJECT = {"id": "10", "key": "PLATFORM", "name": "Platform"}
VERSION = {"id": "20", "name": "Release", "released": True}
ISSUE = {
    "key": "PLATFORM-1",
    "fields": {
        "summary": "Work",
        "project": {"key": "PLATFORM"},
        "labels": ["agent-work"],
        "status": {"name": "Open"},
    },
}


@pytest.fixture
async def adapter():
    client = JiraTrackerAdapter(
        site_url="https://example.atlassian.net",
        email="a@example.com",
        api_token="test",
        project_keys=["PLATFORM"],
        labels=["agent-work"],
    )
    client.bind_connection(connection_id="connection-a", provider="jira", name="Jira")
    yield client
    await client.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"site_url": "http://example.atlassian.net"},
        {"access_token": "oauth", "email": "a", "api_token": "b"},
        {"cloud_id": "../bad", "access_token": "oauth"},
        {},
        {"access_token": "oauth", "project_keys": "PLATFORM"},
    ],
)
def test_invalid_credentials_and_scope_rejected(kwargs):
    with pytest.raises(ValueError):
        JiraTrackerAdapter(**{"site_url": "https://example.atlassian.net", **kwargs})


@pytest.mark.parametrize(
    "resources,expected",
    [
        ([{"url": "https://example.atlassian.net", "id": "cloud-1"}], "cloud-1"),
        ([], None),
        ({"invalid": True}, None),
    ],
)
async def test_oauth_discovers_only_authorized_site(resources, expected):
    adapter = JiraTrackerAdapter(site_url="https://example.atlassian.net", access_token="test")
    await adapter._client.aclose()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=resources)

    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        if expected:
            assert (
                await adapter._api_base_url()
                == f"https://api.atlassian.com/ex/jira/{expected}/rest/api/3"
            )
            await adapter._api_base_url()
            assert len(requests) == 1
        else:
            with pytest.raises(JiraAPIError):
                await adapter._api_base_url()
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "body",
    [{"errorMessages": ["denied"], "errors": {"project": "unknown"}}, ["invalid"], "bad gateway"],
)
async def test_http_errors_are_reported(adapter, body):
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, json=body))
    )
    with pytest.raises(JiraAPIError, match="403"):
        await adapter.get_run("PLATFORM-1")


async def test_create_hierarchy_and_comment(adapter):
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        payload = (
            PROJECT if path.startswith("/project/") else VERSION if path == "/version" else ISSUE
        )
        return httpx.Response(200, json=payload)

    adapter._request = request
    saga = adapter._node_to_saga(PROJECT)
    assert await adapter.create_saga(saga) == "PLATFORM"
    phase = adapter._version_to_phase(VERSION)
    assert await adapter.create_phase(phase, project_id="PLATFORM") == "20"
    run = replace(
        adapter._node_to_run(ISSUE), acceptance_criteria=["tests pass"], declared_files=["app.py"]
    )
    assert await adapter.create_run(run, project_id="PLATFORM", milestone_id="20") == "PLATFORM-1"
    fields = calls[-1][2]["json"]["fields"]
    assert fields["fixVersions"] == [{"id": "20"}]
    assert "tests pass" in adapter._plain_text(fields["description"])
    assert "app.py" in adapter._plain_text(fields["description"])
    await adapter.add_comment("PLATFORM-1", "reviewed")
    assert calls[-1][1].endswith("/comment")
    for method, entity in [(adapter.create_phase, phase), (adapter.create_run, run)]:
        with pytest.raises(JiraAPIError, match="project_id"):
            await method(entity)
    adapter._project_node = AsyncMock(side_effect=JiraAPIError("missing"))
    with pytest.raises(JiraAPIError, match="No Jira project"):
        await adapter.create_saga(replace(saga, tracker_id="", slug="PLATFORM"))


@pytest.mark.parametrize(
    "transitions,success",
    [
        ([{"name": "Done", "id": "1"}], True),
        ([{"name": "Resolved", "id": "2", "to": {"statusCategory": {"key": "done"}}}], True),
        ([], False),
    ],
)
async def test_close_run_selects_valid_transition(adapter, transitions, success):
    adapter._request = AsyncMock(
        return_value=httpx.Response(200, json={"transitions": transitions})
    )
    if success:
        await adapter.close_run("PLATFORM-1")
        assert adapter._request.call_args.kwargs["json"] == {
            "transition": {"id": transitions[0]["id"]}
        }
    else:
        with pytest.raises(JiraAPIError, match="No Jira transition"):
            await adapter.close_run("PLATFORM-1")


async def test_browse_pagination_versions_and_dependencies(adapter):
    adapter._project_keys = ()
    adapter._request = AsyncMock(
        side_effect=[
            httpx.Response(200, json={"values": [PROJECT], "total": 2}),
            httpx.Response(200, json={"values": [{**PROJECT, "key": "SECOND"}], "total": 2}),
        ]
    )
    adapter._node_to_project = AsyncMock(side_effect=lambda node: node["key"])
    assert await adapter.list_projects() == ["PLATFORM", "SECOND"]
    assert adapter._request.call_args.kwargs["params"]["startAt"] == 1
    adapter._request = AsyncMock(
        side_effect=[
            httpx.Response(200, json={"issues": [ISSUE], "nextPageToken": "next"}),
            httpx.Response(200, json={"issues": [{**ISSUE, "key": "PLATFORM-2"}], "isLast": True}),
        ]
    )
    assert len(await adapter.list_pending_runs("20")) == 2
    assert adapter._request.call_args.kwargs["params"]["nextPageToken"] == "next"
    adapter._project_node = AsyncMock(return_value=PROJECT)
    adapter._project_versions = AsyncMock(return_value=[VERSION])
    milestones = await adapter.list_milestones("PLATFORM")
    assert milestones[0].progress == 1.0
    assert (await adapter.list_phases_for_saga("PLATFORM"))[0].status == PhaseStatus.COMPLETE
    assert (await adapter.get_saga("PLATFORM")).tracker_connection_id == "connection-a"
    blocked = {
        **ISSUE,
        "fields": {
            **ISSUE["fields"],
            "issuelinks": [
                {"type": {"inward": "is blocked by"}, "inwardIssue": {"key": "PLATFORM-2"}}
            ],
        },
    }
    blocking = {
        **ISSUE,
        "key": "PLATFORM-2",
        "fields": {
            **ISSUE["fields"],
            "issuelinks": [{"type": {"outward": "blocks"}, "outwardIssue": {"key": "PLATFORM-3"}}],
        },
    }
    adapter._search_issues = AsyncMock(return_value=[blocked, blocking])
    assert await adapter.get_blocked_identifiers("PLATFORM") == {"PLATFORM-1", "PLATFORM-3"}


async def test_run_scope_and_progress(adapter):
    adapter._request = AsyncMock(return_value=httpx.Response(200, json=ISSUE))
    run = await adapter.get_run("PLATFORM-1")
    assert run.status == RunStatus.PENDING
    adapter._pool = AsyncMock()
    adapter._pool.fetchrow.return_value = {
        "status": "REVIEW",
        "session_id": "session",
        "confidence": 0.9,
    }
    assert (await adapter.get_run("PLATFORM-1")).status == RunStatus.REVIEW
    assert adapter._pool.fetchrow.call_args.args[-1] == "connection-a"
    adapter._request.return_value = httpx.Response(
        200, json={**ISSUE, "fields": {**ISSUE["fields"], "labels": []}}
    )
    with pytest.raises(JiraAPIError, match="outside"):
        await adapter.get_run("PLATFORM-1")


async def test_operational_state_queries_are_connection_scoped(adapter):
    pool = AsyncMock()
    adapter._pool = pool
    run = adapter._node_to_run(ISSUE)
    adapter.get_run = AsyncMock(return_value=run)
    adapter.update_run_state = AsyncMock()
    pool.fetch.return_value = [{"tracker_id": run.tracker_id}]
    pool.fetchrow.return_value = {
        "tracker_id": run.tracker_id,
        "saga_tracker_id": "PLATFORM",
        "phase_tracker_id": "20",
        "owner_id": "user-a",
    }
    assert (
        await adapter.update_run_progress(
            run.tracker_id, status=RunStatus.RUNNING, session_id="session"
        )
        == run
    )
    assert pool.execute.call_args.args[-1] == "connection-a"
    adapter.update_run_state.assert_awaited_once_with(run.tracker_id, RunStatus.RUNNING)
    assert await adapter.get_run_progress_for_saga("PLATFORM") == [run]
    assert await adapter.list_runs_by_status(RunStatus.RUNNING) == [run]
    assert await adapter.get_run_by_session("session") == run
    assert await adapter.get_run_by_id(run.id) == run
    assert await adapter.get_run_by_id(uuid4()) is None
    assert await adapter.get_owner_for_run(run.tracker_id) == "user-a"
    adapter.get_saga = AsyncMock(return_value="saga")
    adapter.get_phase = AsyncMock(return_value="phase")
    assert await adapter.get_saga_for_run(run.tracker_id) == "saga"
    assert await adapter.get_phase_for_run(run.tracker_id) == "phase"
    for call in pool.fetch.call_args_list + pool.fetchrow.call_args_list:
        assert "tracker_connection_id" in call.args[0]
        assert call.args[-1] == "connection-a"
    pool.fetchrow.return_value = None
    assert await adapter.get_run_by_session("missing") is None
    assert await adapter.get_owner_for_run("missing") is None
    assert await adapter.get_saga_for_run("missing") is None
    assert await adapter.get_phase_for_run("missing") is None


async def test_messages_and_phase_state(adapter):
    pool = AsyncMock()
    adapter._pool = pool
    now = datetime.now(UTC)
    run_id = uuid5(UUID(int=0), "PLATFORM-1")
    message = SessionMessage(uuid4(), run_id, "session", "continue", "user", now)
    pool.fetch.return_value = [{"tracker_id": "PLATFORM-1"}]
    await adapter.save_session_message(message)
    assert pool.execute.call_args.args[3] == "PLATFORM-1"
    assert pool.execute.call_args.args[-1] == "connection-a"
    pool.fetch.return_value = [asdict(message)]
    assert await adapter.get_session_messages("PLATFORM-1") == [message]
    for total, remaining, expected in [(0, 0, False), (2, 1, False), (2, 0, True)]:
        pool.fetchrow.return_value = {"total": total, "remaining": remaining}
        assert await adapter.all_runs_merged("20") is expected
    phase = adapter._version_to_phase(VERSION)
    pool.fetchrow.return_value = asdict(phase)
    assert await adapter.get_phase("20") == phase
    assert await adapter.update_phase_status("20", PhaseStatus.COMPLETE) == phase
    pool.fetch.return_value = [asdict(phase)]
    assert await adapter.list_phases_for_saga("PLATFORM") == [phase]
    pool.fetchrow.return_value = None
    adapter._request = AsyncMock(return_value=httpx.Response(200, json=VERSION))
    assert await adapter.update_phase_status("20", PhaseStatus.COMPLETE) == phase
    assert adapter._request.call_args_list[0].args == ("PUT", "/version/20")


async def test_optional_operational_reads_without_database(adapter):
    assert await adapter.get_run_progress_for_saga("x") == []
    assert await adapter.get_run_by_session("x") is None
    assert await adapter.list_runs_by_status(RunStatus.RUNNING) == []
    assert await adapter.get_run_by_id(uuid4()) is None
    assert await adapter.all_runs_merged("x") is False
    assert await adapter.get_saga_for_run("x") is None
    assert await adapter.get_phase_for_run("x") is None
    assert await adapter.get_owner_for_run("x") is None
    assert await adapter.get_session_messages("x") == []
    with pytest.raises(RuntimeError, match="pool"):
        await adapter.update_run_progress("x")
    with pytest.raises(RuntimeError, match="pool"):
        await adapter.save_session_message(None)


@pytest.mark.parametrize(
    "kind,name,expected",
    [
        ("completed", "Done", RunStatus.MERGED),
        ("completed", "Cancelled", RunStatus.FAILED),
        ("started", "In Review", RunStatus.REVIEW),
        ("started", "In Progress", RunStatus.RUNNING),
    ],
)
def test_status_conversion(kind, name, expected):
    assert JiraTrackerAdapter._run_status(kind, name) == expected
