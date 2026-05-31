"""Linear issue tracker adapter.

Implements the IssueTrackerProvider port using the Linear GraphQL API.
Composes the shared LinearGraphQLClient from niuu.
"""

from __future__ import annotations

import logging

from niuu.adapters.linear import GraphQLError, LinearGraphQLClient
from niuu.domain.models import LINEAR_API_URL
from niuu.domain.models import CacheEntry as _CacheEntry  # noqa: F401 — re-exported for tests
from volundr.domain.models import TrackerConnectionStatus, TrackerIssue
from volundr.domain.ports import IssueTrackerProvider

__all__ = ["LinearAdapter", "_CacheEntry"]

logger = logging.getLogger(__name__)

# --- GraphQL queries ---

_VIEWER_QUERY = """
query {
  viewer {
    id
    name
    email
  }
  organization {
    id
    name
  }
}
"""

_SEARCH_ISSUES_QUERY = """
query SearchIssues($term: String!, $first: Int!) {
  searchIssues(term: $term, first: $first) {
    nodes {
      id
      identifier
      title
      state { name }
      assignee { name }
      labels { nodes { name } }
      priority
      url
    }
  }
}
"""

_RECENT_ISSUES_QUERY = """
query RecentIssues($projectId: String!, $first: Int!) {
  issues(
    filter: { project: { id: { eq: $projectId } } }
    first: $first
    orderBy: updatedAt
  ) {
    nodes {
      id
      identifier
      title
      state { name }
      assignee { name }
      labels { nodes { name } }
      priority
      url
    }
  }
}
"""

_GET_ISSUE_QUERY = """
query GetIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    state { name }
    assignee { name }
    labels { nodes { name } }
    priority
    url
  }
}
"""

_UPDATE_ISSUE_STATUS_QUERY = """
mutation UpdateIssueStatus($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: { stateId: $stateId }) {
    issue {
      id
      identifier
      title
      state { name }
      assignee { name }
      labels { nodes { name } }
      priority
      url
    }
  }
}
"""

_TEAM_STATES_QUERY = """
query TeamStates($teamId: String!) {
  team(id: $teamId) {
    states {
      nodes {
        id
        name
      }
    }
  }
}
"""

_ISSUE_TEAM_QUERY = """
query IssueTeam($id: String!) {
  issue(id: $id) {
    team { id }
  }
}
"""


# Keep backward-compatible alias
LinearAPIError = GraphQLError

__all__ = ["LinearAdapter", "_CacheEntry", "LinearAPIError"]


class LinearAdapter(IssueTrackerProvider):
    """Linear issue tracker adapter using GraphQL API."""

    def __init__(self, api_key: str, api_url: str = LINEAR_API_URL, **_extra: object):
        self._gql = LinearGraphQLClient(
            api_key=api_key,
            api_url=api_url,
        )

    @property
    def provider_name(self) -> str:
        return "linear"

    # -- Delegate cache/query to shared client for test access --

    def _get_cached(self, key: str) -> object | None:
        return self._gql.get_cached(key)

    def _set_cached(self, key: str, value: object, ttl: float) -> None:
        self._gql.set_cached(key, value, ttl)

    async def _graphql(
        self,
        query: str,
        variables: dict | None = None,
    ) -> dict:
        return await self._gql.query(query, variables)

    @staticmethod
    def _node_to_issue(node: dict) -> TrackerIssue:
        """Convert a Linear issue GraphQL node to a TrackerIssue."""
        return TrackerIssue(
            id=node["id"],
            identifier=node["identifier"],
            title=node["title"],
            status=node.get("state", {}).get("name", "Unknown"),
            assignee=(node.get("assignee") or {}).get("name"),
            labels=[n["name"] for n in (node.get("labels") or {}).get("nodes", [])],
            priority=node.get("priority", 0),
            url=node.get("url", ""),
        )

    async def check_connection(self) -> TrackerConnectionStatus:
        """Check connection to Linear API."""
        cached = self._gql.get_cached("connection")
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = await self._gql.query(_VIEWER_QUERY)
            viewer = data.get("viewer", {})
            org = data.get("organization", {})
            result = TrackerConnectionStatus(
                connected=True,
                provider="linear",
                workspace=org.get("name"),
                user=viewer.get("name"),
            )
            self._gql.set_cached("connection", result, ttl=300.0)
            return result
        except Exception:
            logger.exception("Linear connection check failed")
            return TrackerConnectionStatus(
                connected=False,
                provider="linear",
            )

    async def search_issues(
        self,
        query: str,
        project_id: str | None = None,
    ) -> list[TrackerIssue]:
        """Search Linear issues."""
        cache_key = f"search:{query}:{project_id}"
        cached = self._gql.get_cached(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        search_term = query
        if project_id:
            search_term = f"project:{project_id} {query}"

        data = await self._gql.query(
            _SEARCH_ISSUES_QUERY,
            {"term": search_term, "first": 25},
        )
        nodes = data.get("searchIssues", {}).get("nodes", [])
        issues = [self._node_to_issue(n) for n in nodes]
        self._gql.set_cached(cache_key, issues, ttl=30.0)
        return issues

    async def get_recent_issues(
        self,
        project_id: str,
        limit: int = 10,
    ) -> list[TrackerIssue]:
        """Get recent Linear issues for a project."""
        cache_key = f"recent:{project_id}:{limit}"
        cached = self._gql.get_cached(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        data = await self._gql.query(
            _RECENT_ISSUES_QUERY,
            {"projectId": project_id, "first": limit},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        issues = [self._node_to_issue(n) for n in nodes]
        self._gql.set_cached(cache_key, issues, ttl=30.0)
        return issues

    async def get_issue(self, issue_id: str) -> TrackerIssue | None:
        """Get a single Linear issue."""
        try:
            data = await self._gql.query(
                _GET_ISSUE_QUERY,
                {"id": issue_id},
            )
            node = data.get("issue")
            if node is None:
                return None
            return self._node_to_issue(node)
        except GraphQLError:
            return None

    async def update_issue_status(
        self,
        issue_id: str,
        status: str,
    ) -> TrackerIssue:
        """Update an issue's status in Linear.

        Finds the target state by name within the issue's team, then
        updates the issue to that state.
        """
        # Get the issue's team
        team_data = await self._gql.query(
            _ISSUE_TEAM_QUERY,
            {"id": issue_id},
        )
        issue_node = team_data.get("issue")
        if issue_node is None:
            raise GraphQLError(f"Issue not found: {issue_id}")
        team_id = issue_node["team"]["id"]

        # Get team states and find the target
        states_data = await self._gql.query(
            _TEAM_STATES_QUERY,
            {"teamId": team_id},
        )
        states = states_data.get("team", {}).get("states", {}).get("nodes", [])
        target_state = None
        for s in states:
            if s["name"].lower() == status.lower():
                target_state = s
                break

        if target_state is None:
            available = [s["name"] for s in states]
            raise GraphQLError(f"Status '{status}' not found. Available: {', '.join(available)}")

        # Update the issue
        data = await self._gql.query(
            _UPDATE_ISSUE_STATUS_QUERY,
            {"issueId": issue_id, "stateId": target_state["id"]},
        )
        updated = data.get("issueUpdate", {}).get("issue")
        if updated is None:
            raise GraphQLError("Failed to update issue status")

        # Invalidate caches
        self._gql.invalidate_cache("search:")
        self._gql.invalidate_cache("recent:")

        return self._node_to_issue(updated)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._gql.close()
