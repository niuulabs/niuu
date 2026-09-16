"""Jira issue tracker adapter.

Implements the IssueTrackerProvider port using the Jira REST API v3 (Cloud).
"""

from __future__ import annotations

import logging
import re
from base64 import b64encode
from typing import Any
from urllib.parse import urlsplit

import httpx

from tracker.models import TrackerConnectionStatus, TrackerIssue
from tracker.ports import IssueTrackerProvider

logger = logging.getLogger(__name__)

_ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
_ATLASSIAN_API_ROOT = "https://api.atlassian.com/ex/jira"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_ISSUE_FIELDS = "summary,status,assignee,labels,priority,project"

# Priority mapping: Jira priority names to numeric values
_PRIORITY_MAP: dict[str, int] = {
    "highest": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "lowest": 5,
}


class JiraAdapter(IssueTrackerProvider):
    """Jira Cloud issue tracker adapter using REST API v3.

    Supports either an Atlassian email + API token or an OAuth 2.0 access
    token. Constructor kwargs match the dynamic adapter pattern and unknown
    catalog metadata is ignored through ``**_extra``.
    """

    def __init__(
        self,
        site_url: str,
        api_token: str = "",
        email: str = "",
        access_token: str = "",
        cloud_id: str = "",
        project_keys: list[str] | None = None,
        labels: list[str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        **_extra: object,
    ) -> None:
        self._site_url = site_url.rstrip("/")
        if not self._site_url:
            raise ValueError("site_url is required")
        parsed_site = urlsplit(self._site_url)
        if (
            parsed_site.scheme != "https"
            or not parsed_site.hostname
            or not parsed_site.hostname.casefold().endswith(".atlassian.net")
            or parsed_site.username is not None
            or parsed_site.password is not None
            or parsed_site.path not in {"", "/"}
            or parsed_site.query
            or parsed_site.fragment
        ):
            raise ValueError("site_url must be an HTTPS Jira Cloud origin under atlassian.net")
        if access_token and (api_token or email):
            raise ValueError("Configure Jira with either OAuth or an API token, not both")
        if access_token:
            authorization = f"Bearer {access_token}"
            self._uses_oauth = True
        elif api_token and email:
            basic = b64encode(f"{email}:{api_token}".encode()).decode()
            authorization = f"Basic {basic}"
            self._uses_oauth = False
        else:
            raise ValueError("OAuth access_token or both email and api_token are required")
        self._resource: dict[str, Any] | None = None
        self._cloud_id = cloud_id.strip()
        if self._cloud_id and not re.fullmatch(r"[A-Za-z0-9-]+", self._cloud_id):
            raise ValueError("cloud_id may contain only letters, digits, and dashes")
        self._project_keys = self._clean_scope_values(project_keys)
        self._labels = self._clean_scope_values(labels)
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=float(timeout),
        )

    @property
    def provider_name(self) -> str:
        return "jira"

    async def _api_base_url(self) -> str:
        if not self._uses_oauth:
            if self._cloud_id:
                return f"{_ATLASSIAN_API_ROOT}/{self._cloud_id}/rest/api/3"
            return f"{self._site_url}/rest/api/3"
        if self._resource is None:
            response = await self._client.get(_ACCESSIBLE_RESOURCES_URL)
            response.raise_for_status()
            resources = response.json()
            if not isinstance(resources, list):
                raise JiraAPIError("Atlassian returned an invalid accessible-resources response")
            site = self._site_url.casefold()
            matches = [
                item
                for item in resources
                if isinstance(item, dict)
                and str(item.get("url") or "").rstrip("/").casefold() == site
                and item.get("id")
            ]
            if not matches:
                available = ", ".join(
                    str(item.get("url"))
                    for item in resources
                    if isinstance(item, dict) and item.get("url")
                )
                suffix = f" Available sites: {available}" if available else ""
                raise JiraAPIError(
                    f"The OAuth grant does not include Jira site {self._site_url}.{suffix}"
                )
            self._resource = matches[0]
        return f"{_ATLASSIAN_API_ROOT}/{self._resource['id']}/rest/api/3"

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        base_url = await self._api_base_url()
        return await self._client.request(method, f"{base_url}{path}", **kwargs)

    async def check_connection(self) -> TrackerConnectionStatus:
        """Check connection via GET /myself."""
        try:
            response = await self._request("GET", "/myself")
            response.raise_for_status()
            data = response.json()

            # Fetch server info for workspace name
            server_response = await self._request("GET", "/serverInfo")
            server_data = {}
            if server_response.is_success:
                server_data = server_response.json()

            return TrackerConnectionStatus(
                connected=True,
                provider="jira",
                workspace=server_data.get("serverTitle")
                or (self._resource or {}).get("name")
                or self._site_url,
                user=data.get("displayName", data.get("emailAddress")),
            )
        except Exception:
            logger.exception("Jira connection check failed")
            return TrackerConnectionStatus(
                connected=False,
                provider="jira",
            )

    async def search_issues(
        self,
        query: str,
        project_id: str | None = None,
    ) -> list[TrackerIssue]:
        """Search Jira issues via JQL text search."""
        jql_parts = self._scope_jql_parts()
        jql_parts.append(f"text ~ {self._jql_string(query)}")
        if project_id:
            self._require_allowed_project(project_id)
            jql_parts.insert(0, f"project = {self._jql_string(project_id)}")

        jql = " AND ".join(jql_parts)
        params = {"jql": jql, "maxResults": 25, "fields": _ISSUE_FIELDS}
        response = await self._request("GET", "/search/jql", params=params)
        response.raise_for_status()
        data = response.json()

        return [self._issue_to_tracker(issue, self._site_url) for issue in data.get("issues", [])]

    async def get_recent_issues(
        self,
        project_id: str,
        limit: int = 10,
    ) -> list[TrackerIssue]:
        """Get recent issues for a project via JQL."""
        self._require_allowed_project(project_id)
        jql_parts = [f"project = {self._jql_string(project_id)}", *self._label_jql_parts()]
        jql = f"{' AND '.join(jql_parts)} ORDER BY updated DESC"
        params = {"jql": jql, "maxResults": limit, "fields": _ISSUE_FIELDS}
        response = await self._request("GET", "/search/jql", params=params)
        response.raise_for_status()
        data = response.json()

        return [self._issue_to_tracker(issue, self._site_url) for issue in data.get("issues", [])]

    async def get_issue(self, issue_id: str) -> TrackerIssue | None:
        """Get a single issue by key or ID."""
        try:
            response = await self._request(
                "GET",
                f"/issue/{issue_id}",
                params={"fields": _ISSUE_FIELDS},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            issue = response.json()
            if not self._issue_is_in_scope(issue):
                return None
            return self._issue_to_tracker(issue, self._site_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def update_issue_status(
        self,
        issue_id: str,
        status: str,
    ) -> TrackerIssue:
        """Update issue status via transitions API."""
        if self._project_keys or self._labels:
            scoped_issue = await self.get_issue(issue_id)
            if scoped_issue is None:
                raise JiraAPIError(
                    f"Issue is outside this Jira connection's configured scope: {issue_id}"
                )

        # Get available transitions
        trans_response = await self._request("GET", f"/issue/{issue_id}/transitions")
        trans_response.raise_for_status()
        transitions = trans_response.json().get("transitions", [])

        target = None
        for t in transitions:
            if t.get("name", "").lower() == status.lower():
                target = t
                break

        if target is None:
            available = [t.get("name", "") for t in transitions]
            raise JiraAPIError(
                f"Transition '{status}' not available. Available: {', '.join(available)}"
            )

        # Perform the transition
        transition_response = await self._request(
            "POST",
            f"/issue/{issue_id}/transitions",
            json={"transition": {"id": target["id"]}},
        )
        transition_response.raise_for_status()

        # Fetch the updated issue
        updated = await self.get_issue(issue_id)
        if updated is None:
            raise JiraAPIError(f"Issue not found after update: {issue_id}")
        return updated

    @staticmethod
    def _jql_string(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _clean_scope_values(values: list[str] | None) -> tuple[str, ...]:
        if values is None:
            return ()
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError("Jira scope values must be a list of strings")
        return tuple(dict.fromkeys(value.strip() for value in (values or []) if value.strip()))

    def _require_allowed_project(self, project_id: str) -> None:
        if self._project_keys and project_id.casefold() not in {
            key.casefold() for key in self._project_keys
        }:
            allowed = ", ".join(self._project_keys)
            raise JiraAPIError(
                f"Project {project_id!r} is outside this Jira connection's configured scope "
                f"({allowed})"
            )

    def _project_jql_parts(self) -> list[str]:
        if not self._project_keys:
            return []
        projects = ", ".join(self._jql_string(key) for key in self._project_keys)
        return [f"project in ({projects})"]

    def _label_jql_parts(self) -> list[str]:
        if not self._labels:
            return []
        labels = ", ".join(self._jql_string(label) for label in self._labels)
        return [f"labels in ({labels})"]

    def _scope_jql_parts(self) -> list[str]:
        return [*self._project_jql_parts(), *self._label_jql_parts()]

    def _issue_is_in_scope(self, issue: dict[str, Any]) -> bool:
        fields = issue.get("fields") or {}
        project = fields.get("project") or {}
        project_key = str(project.get("key") or "")
        if self._project_keys and project_key.casefold() not in {
            key.casefold() for key in self._project_keys
        }:
            return False
        issue_labels = {str(label).casefold() for label in (fields.get("labels") or [])}
        return not self._labels or bool(
            issue_labels.intersection(label.casefold() for label in self._labels)
        )

    @staticmethod
    def _issue_to_tracker(issue: dict, site_url: str = "") -> TrackerIssue:
        """Convert a Jira issue response to TrackerIssue."""
        fields = issue.get("fields", {})
        assignee = fields.get("assignee")
        priority = fields.get("priority")
        priority_name = (priority.get("name", "") if priority else "").lower()

        return TrackerIssue(
            id=issue["id"],
            identifier=issue.get("key", issue["id"]),
            title=fields.get("summary", ""),
            status=(fields.get("status") or {}).get("name", "Unknown"),
            assignee=assignee.get("displayName") if assignee else None,
            labels=fields.get("labels", []),
            priority=_PRIORITY_MAP.get(priority_name, 0),
            url=(
                f"{site_url.rstrip('/')}/browse/{issue.get('key', '')}"
                if site_url
                else f"{issue.get('self', '').split('/rest/')[0]}/browse/{issue.get('key', '')}"
            ),
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class JiraAPIError(Exception):
    """Raised when the Jira API returns an error."""
