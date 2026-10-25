"""Jira Cloud tracker adapter contract tests."""

from __future__ import annotations

import json
from base64 import b64encode

import httpx
import pytest
import respx

from tracker.ports import TrackerRequestError
from volundr.adapters.outbound.jira import JiraAdapter, JiraAPIError

SITE_URL = "https://example.atlassian.net"
DIRECT_API = f"{SITE_URL}/rest/api/3"
ACCESSIBLE_RESOURCES = "https://api.atlassian.com/oauth/token/accessible-resources"
CLOUD_ID = "cloud-123"
OAUTH_API = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/api/3"


def _issue() -> dict:
    return {
        "id": "10001",
        "key": "PROJ-42",
        "self": f"{OAUTH_API}/issue/10001",
        "fields": {
            "summary": "Fix the sign-in",
            "project": {"key": "PROJ"},
            "status": {"name": "In Progress"},
            "assignee": {"displayName": "Ada"},
            "labels": ["auth"],
            "priority": {"name": "High"},
        },
    }


@pytest.mark.asyncio
@respx.mock
async def test_api_token_search_uses_basic_auth_and_enhanced_jql_endpoint() -> None:
    route = respx.get(f"{DIRECT_API}/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [_issue()], "isLast": True})
    )
    adapter = JiraAdapter(site_url=SITE_URL, email="ada@example.com", api_token="secret")
    try:
        issues = await adapter.search_issues('login "failure"', project_id="PROJ")
    finally:
        await adapter.close()

    assert [issue.identifier for issue in issues] == ["PROJ-42"]
    assert issues[0].url == f"{SITE_URL}/browse/PROJ-42"
    request = route.calls.last.request
    expected = b64encode(b"ada@example.com:secret").decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert request.url.params["jql"] == 'project = "PROJ" AND text ~ "login \\"failure\\""'


@pytest.mark.asyncio
@respx.mock
async def test_scoped_api_token_uses_the_cloud_gateway() -> None:
    route = respx.get(f"{OAUTH_API}/myself").mock(
        return_value=httpx.Response(200, json={"displayName": "Service Account"})
    )
    respx.get(f"{OAUTH_API}/serverInfo").mock(return_value=httpx.Response(404))
    adapter = JiraAdapter(
        site_url=SITE_URL,
        email="bot@serviceaccount.atlassian.com",
        api_token="scoped-token",
        cloud_id=CLOUD_ID,
    )
    try:
        status = await adapter.check_connection()
    finally:
        await adapter.close()

    assert status.connected is True
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")


@pytest.mark.asyncio
@respx.mock
async def test_oauth_resolves_the_requested_site_and_uses_the_cloud_gateway() -> None:
    resources = respx.get(ACCESSIBLE_RESOURCES).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": CLOUD_ID,
                    "name": "Example Jira",
                    "url": SITE_URL,
                    "scopes": ["read:jira-work", "write:jira-work"],
                }
            ],
        )
    )
    myself = respx.get(f"{OAUTH_API}/myself").mock(
        return_value=httpx.Response(200, json={"displayName": "Ada"})
    )
    respx.get(f"{OAUTH_API}/serverInfo").mock(
        return_value=httpx.Response(200, json={"serverTitle": "Example Jira"})
    )
    adapter = JiraAdapter(site_url=SITE_URL, access_token="oauth-access")
    try:
        status = await adapter.check_connection()
    finally:
        await adapter.close()

    assert status.connected is True
    assert status.workspace == "Example Jira"
    assert status.user == "Ada"
    assert resources.call_count == 1
    assert myself.calls.last.request.headers["authorization"] == "Bearer oauth-access"


@pytest.mark.asyncio
@respx.mock
async def test_oauth_rejects_a_grant_that_does_not_include_the_configured_site() -> None:
    respx.get(ACCESSIBLE_RESOURCES).mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "other", "name": "Other", "url": "https://other.atlassian.net"}],
        )
    )
    adapter = JiraAdapter(site_url=SITE_URL, access_token="oauth-access")
    try:
        with pytest.raises(JiraAPIError, match="does not include Jira site"):
            await adapter._api_base_url()
    finally:
        await adapter.close()


@pytest.mark.asyncio
@respx.mock
async def test_transition_failure_is_not_hidden_by_a_follow_up_read() -> None:
    respx.get(f"{DIRECT_API}/issue/PROJ-42/transitions").mock(
        return_value=httpx.Response(
            200,
            json={"transitions": [{"id": "31", "name": "Done"}]},
        )
    )
    respx.post(f"{DIRECT_API}/issue/PROJ-42/transitions").mock(
        return_value=httpx.Response(403, json={"errorMessages": ["Forbidden"]})
    )
    adapter = JiraAdapter(site_url=SITE_URL, email="ada@example.com", api_token="secret")
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.update_issue_status("PROJ-42", "Done")
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"site_url": SITE_URL},
        {"site_url": SITE_URL, "api_token": "token"},
        {
            "site_url": SITE_URL,
            "email": "ada@example.com",
            "api_token": "token",
            "access_token": "oauth",
        },
        {
            "site_url": "https://attacker.example",
            "email": "ada@example.com",
            "api_token": "token",
        },
    ],
)
def test_constructor_rejects_incomplete_or_ambiguous_auth(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        JiraAdapter(**kwargs)


@pytest.mark.asyncio
@respx.mock
async def test_search_applies_configured_project_and_label_scope() -> None:
    route = respx.get(f"{DIRECT_API}/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [_issue()], "isLast": True})
    )
    adapter = JiraAdapter(
        site_url=SITE_URL,
        email="ada@example.com",
        api_token="secret",
        project_keys=["PROJ", "OPS", "PROJ"],
        labels=["auth", "security"],
    )
    try:
        await adapter.search_issues("failure")
    finally:
        await adapter.close()

    assert route.calls.last.request.url.params["jql"] == (
        'project in ("PROJ", "OPS") AND labels in ("auth", "security") AND text ~ "failure"'
    )


@pytest.mark.asyncio
async def test_explicit_project_must_be_inside_configured_scope() -> None:
    adapter = JiraAdapter(
        site_url=SITE_URL,
        email="ada@example.com",
        api_token="secret",
        project_keys=["PROJ"],
    )
    try:
        with pytest.raises(JiraAPIError, match="outside this Jira connection"):
            await adapter.get_recent_issues("OTHER")
    finally:
        await adapter.close()


@pytest.mark.asyncio
@respx.mock
async def test_direct_issue_reads_hide_issues_outside_label_scope() -> None:
    issue = _issue()
    issue["fields"]["labels"] = ["frontend"]
    respx.get(f"{DIRECT_API}/issue/PROJ-42").mock(return_value=httpx.Response(200, json=issue))
    adapter = JiraAdapter(
        site_url=SITE_URL,
        email="ada@example.com",
        api_token="secret",
        labels=["backend"],
    )
    try:
        assert await adapter.get_issue("PROJ-42") is None
    finally:
        await adapter.close()


@pytest.mark.asyncio
@respx.mock
async def test_get_issue_requests_and_renders_the_adf_description() -> None:
    issue = _issue()
    issue["fields"]["description"] = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "Acceptance criteria"}],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Sign-in succeeds"}],
                            }
                        ],
                    }
                ],
            },
        ],
    }
    route = respx.get(f"{DIRECT_API}/issue/PROJ-42").mock(
        return_value=httpx.Response(200, json=issue)
    )
    adapter = JiraAdapter(site_url=SITE_URL, email="ada@example.com", api_token="secret")
    try:
        result = await adapter.get_issue("PROJ-42")
    finally:
        await adapter.close()

    assert result is not None
    assert result.description == "## Acceptance criteria\n\n- Sign-in succeeds"
    assert "description" in route.calls.last.request.url.params["fields"].split(",")


@pytest.mark.asyncio
@respx.mock
async def test_search_omits_the_description_field() -> None:
    route = respx.get(f"{DIRECT_API}/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [_issue()], "isLast": True})
    )
    adapter = JiraAdapter(site_url=SITE_URL, email="ada@example.com", api_token="secret")
    try:
        issues = await adapter.search_issues("login")
    finally:
        await adapter.close()

    assert issues[0].description is None
    assert "description" not in route.calls.last.request.url.params["fields"].split(",")


def _scoped_adapter(**kwargs) -> JiraAdapter:
    return JiraAdapter(site_url=SITE_URL, email="ada@example.com", api_token="secret", **kwargs)


@pytest.mark.asyncio
@respx.mock
async def test_create_issue_posts_fields_and_reads_the_issue_back() -> None:
    create = respx.post(f"{DIRECT_API}/issue").mock(
        return_value=httpx.Response(201, json={"id": "10001", "key": "PROJ-42"})
    )
    created = _issue()
    created["fields"]["labels"] = ["defect", "auth"]
    created["fields"]["description"] = "Line one\n\nLine two"
    respx.get(f"{DIRECT_API}/issue/PROJ-42").mock(return_value=httpx.Response(200, json=created))
    adapter = _scoped_adapter(labels=["auth"], issue_type="Bug")
    try:
        issue = await adapter.create_issue(
            "Fix the sign-in",
            "Line one\n\nLine two",
            team="PROJ",
            labels=["defect", "auth"],
            priority=1,
        )
    finally:
        await adapter.close()

    assert issue.identifier == "PROJ-42"
    assert issue.description == "Line one\n\nLine two"
    fields = json.loads(create.calls.last.request.content)["fields"]
    assert fields == {
        "project": {"key": "PROJ"},
        "summary": "Fix the sign-in",
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Line one"}]},
                {"type": "paragraph", "content": []},
                {"type": "paragraph", "content": [{"type": "text", "text": "Line two"}]},
            ],
        },
        "issuetype": {"name": "Bug"},
        "labels": ["defect", "auth"],
        "priority": {"name": "Highest"},
    }


@pytest.mark.asyncio
@respx.mock
async def test_create_issue_defaults_to_task_without_labels_or_priority() -> None:
    create = respx.post(f"{DIRECT_API}/issue").mock(
        return_value=httpx.Response(201, json={"id": "10001", "key": "PROJ-42"})
    )
    respx.get(f"{DIRECT_API}/issue/PROJ-42").mock(return_value=httpx.Response(200, json=_issue()))
    adapter = _scoped_adapter(issue_type="  ")
    try:
        await adapter.create_issue("T", "B", team="PROJ", priority=0)
    finally:
        await adapter.close()

    fields = json.loads(create.calls.last.request.content)["fields"]
    assert fields["issuetype"] == {"name": "Task"}
    assert "labels" not in fields
    assert "priority" not in fields


@pytest.mark.asyncio
async def test_create_issue_rejects_projects_outside_scope_and_bad_priority() -> None:
    adapter = _scoped_adapter(project_keys=["PROJ"])
    try:
        with pytest.raises(TrackerRequestError, match="outside this Jira connection"):
            await adapter.create_issue("T", "B", team="OTHER")
        with pytest.raises(TrackerRequestError, match="between 0 and 4, got 5"):
            await adapter.create_issue("T", "B", team="PROJ", priority=5)
    finally:
        await adapter.close()


@pytest.mark.asyncio
@respx.mock
async def test_create_issue_surfaces_jira_validation_errors() -> None:
    respx.post(f"{DIRECT_API}/issue").mock(
        return_value=httpx.Response(400, json={"errors": {"project": "valid project is required"}})
    )
    adapter = _scoped_adapter()
    try:
        with pytest.raises(TrackerRequestError, match="valid project is required"):
            await adapter.create_issue("T", "B", team="NOPE")
    finally:
        await adapter.close()


@pytest.mark.asyncio
@respx.mock
async def test_create_issue_raises_when_jira_returns_no_key_or_issue_vanishes() -> None:
    route = respx.post(f"{DIRECT_API}/issue")
    route.mock(return_value=httpx.Response(201, json={"id": "10001"}))
    adapter = _scoped_adapter()
    try:
        with pytest.raises(JiraAPIError, match="did not include an issue key"):
            await adapter.create_issue("T", "B", team="PROJ")
        route.mock(return_value=httpx.Response(201, json={"id": "10001", "key": "PROJ-42"}))
        respx.get(f"{DIRECT_API}/issue/PROJ-42").mock(return_value=httpx.Response(404))
        with pytest.raises(JiraAPIError, match="PROJ-42 could not be read back"):
            await adapter.create_issue("T", "B", team="PROJ")
    finally:
        await adapter.close()


@pytest.mark.asyncio
@respx.mock
async def test_update_issue_puts_summary_and_adf_description() -> None:
    edit = respx.put(f"{DIRECT_API}/issue/PROJ-42").mock(return_value=httpx.Response(204))
    respx.get(f"{DIRECT_API}/issue/PROJ-42").mock(return_value=httpx.Response(200, json=_issue()))
    adapter = _scoped_adapter()
    try:
        issue = await adapter.update_issue("PROJ-42", title="Fix the sign-in", description="Body")
    finally:
        await adapter.close()

    assert issue.identifier == "PROJ-42"
    fields = json.loads(edit.calls.last.request.content)["fields"]
    assert fields == {
        "summary": "Fix the sign-in",
        "description": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Body"}]}],
        },
    }


@pytest.mark.asyncio
@respx.mock
async def test_update_issue_rejects_nothing_out_of_scope_and_jira_validation() -> None:
    respx.get(f"{DIRECT_API}/issue/OTHER-1").mock(return_value=httpx.Response(404))
    respx.get(f"{DIRECT_API}/issue/PROJ-42").mock(return_value=httpx.Response(200, json=_issue()))
    respx.put(f"{DIRECT_API}/issue/PROJ-42").mock(
        return_value=httpx.Response(400, text='{"errors":{"summary":"too long"}}')
    )
    adapter = _scoped_adapter(project_keys=["PROJ"])
    try:
        with pytest.raises(TrackerRequestError, match="Nothing to update"):
            await adapter.update_issue("PROJ-42")
        with pytest.raises(TrackerRequestError, match="outside this Jira connection"):
            await adapter.update_issue("OTHER-1", title="x")
        with pytest.raises(TrackerRequestError, match="too long"):
            await adapter.update_issue("PROJ-42", title="x" * 300)
    finally:
        await adapter.close()
