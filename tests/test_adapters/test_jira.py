"""Jira Cloud tracker adapter contract tests."""

from __future__ import annotations

from base64 import b64encode

import httpx
import pytest
import respx

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
