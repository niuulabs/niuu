"""Jira issue tracker adapter.

Implements the IssueTrackerProvider port using the Jira REST API v3 (Cloud).
"""

from __future__ import annotations

import logging
from base64 import b64encode

import httpx

from tracker.models import TrackerConnectionStatus, TrackerIssue
from tracker.ports import IssueTrackerProvider

logger = logging.getLogger(__name__)

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

    Constructor kwargs match the dynamic adapter pattern:
    ``cls(api_token=..., email=..., site_url=...)``
    """

    def __init__(
        self,
        api_token: str,
        email: str,
        site_url: str,
    ) -> None:
        self._site_url = site_url.rstrip("/")
        self._email = email
        basic = b64encode(f"{email}:{api_token}".encode()).decode()
        self._client = httpx.AsyncClient(
            base_url=f"{self._site_url}/rest/api/3",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15.0,
        )

    @property
    def provider_name(self) -> str:
        return "jira"

    async def check_connection(self) -> TrackerConnectionStatus:
        """Check connection via GET /myself."""
        try:
            response = await self._client.get("/myself")
            response.raise_for_status()
            data = response.json()

            # Fetch server info for workspace name
            server_response = await self._client.get("/serverInfo")
            server_data = {}
            if server_response.is_success:
                server_data = server_response.json()

            return TrackerConnectionStatus(
                connected=True,
                provider="jira",
                workspace=server_data.get("serverTitle", self._site_url),
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
        jql_parts = [f'text ~ "{query}"']
        if project_id:
            jql_parts.insert(0, f"project = {project_id}")

        jql = " AND ".join(jql_parts)
        params = {"jql": jql, "maxResults": 25, "fields": "summary,status,assignee,labels,priority"}
        response = await self._client.get("/search", params=params)
        response.raise_for_status()
        data = response.json()

        return [self._issue_to_tracker(issue) for issue in data.get("issues", [])]

    async def get_recent_issues(
        self,
        project_id: str,
        limit: int = 10,
    ) -> list[TrackerIssue]:
        """Get recent issues for a project via JQL."""
        jql = f"project = {project_id} ORDER BY updated DESC"
        fields = "summary,status,assignee,labels,priority"
        params = {"jql": jql, "maxResults": limit, "fields": fields}
        response = await self._client.get("/search", params=params)
        response.raise_for_status()
        data = response.json()

        return [self._issue_to_tracker(issue) for issue in data.get("issues", [])]

    async def get_issue(self, issue_id: str) -> TrackerIssue | None:
        """Get a single issue by key or ID."""
        try:
            response = await self._client.get(
                f"/issue/{issue_id}",
                params={"fields": "summary,status,assignee,labels,priority"},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return self._issue_to_tracker(response.json())
        except httpx.HTTPStatusError:
            return None

    async def update_issue_status(
        self,
        issue_id: str,
        status: str,
    ) -> TrackerIssue:
        """Update issue status via transitions API."""
        # Get available transitions
        trans_response = await self._client.get(f"/issue/{issue_id}/transitions")
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
        await self._client.post(
            f"/issue/{issue_id}/transitions",
            json={"transition": {"id": target["id"]}},
        )

        # Fetch the updated issue
        updated = await self.get_issue(issue_id)
        if updated is None:
            raise JiraAPIError(f"Issue not found after update: {issue_id}")
        return updated

    @staticmethod
    def _issue_to_tracker(issue: dict) -> TrackerIssue:
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
            url=f"{issue.get('self', '').split('/rest/')[0]}/browse/{issue.get('key', '')}",
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class JiraAPIError(Exception):
    """Raised when the Jira API returns an error."""
