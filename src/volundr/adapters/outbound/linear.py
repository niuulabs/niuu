"""Linear issue tracker adapter.

Implements the IssueTrackerProvider port using the Linear GraphQL API.
Composes the shared LinearGraphQLClient from niuu.
"""

from __future__ import annotations

import logging

from niuu.adapters.linear import GraphQLError, LinearGraphQLClient
from niuu.domain.models import LINEAR_API_URL
from niuu.domain.models import CacheEntry as _CacheEntry  # noqa: F401 — re-exported for tests
from tracker.models import TrackerConnectionStatus, TrackerIssue
from tracker.ports import IssueTrackerProvider, TrackerRequestError

__all__ = ["LinearAdapter", "_CacheEntry"]

logger = logging.getLogger(__name__)

# --- GraphQL queries ---
#
# Only the single-issue queries (GetIssue, UpdateIssueStatus) request
# ``description``. Search and recent-issue lists return up to 25 issues and
# deliberately omit bodies to keep payloads small; callers that need the body
# fetch the issue by id.

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
    description
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
      description
      state { name }
      assignee { name }
      labels { nodes { name } }
      priority
      url
    }
  }
}
"""

_UPDATE_ISSUE_QUERY = """
mutation UpdateIssue($issueId: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $issueId, input: $input) {
    success
    issue {
      id
      identifier
      title
      description
      state { name }
      assignee { name }
      labels { nodes { name } }
      priority
      url
    }
  }
}
"""

_TEAM_BY_KEY_QUERY = """
query TeamByKey($key: String!) {
  teams(filter: { key: { eqIgnoreCase: $key } }) {
    nodes {
      id
      key
    }
  }
}
"""

_LABELS_BY_NAME_QUERY = """
query LabelsByName($filter: IssueLabelFilter!) {
  issueLabels(filter: $filter, first: 250) {
    nodes {
      id
      name
      team { id }
    }
  }
}
"""

_CREATE_ISSUE_QUERY = """
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue {
      id
      identifier
      title
      description
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
            description=node.get("description"),
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
        except GraphQLError as exc:
            # Linear reports an unknown issue id as a GraphQL "Entity not found"
            # error; that is the expected "no such issue" answer. Every other
            # GraphQL error is a real failure and propagates.
            if "not found" in str(exc).lower():
                return None
            raise
        node = data.get("issue")
        if node is None:
            return None
        return self._node_to_issue(node)

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
            raise TrackerRequestError(
                f"Status '{status}' not found. Available: {', '.join(available)}"
            )

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

    async def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> TrackerIssue:
        """Rewrite a Linear issue's title and/or description."""
        payload: dict[str, str] = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if not payload:
            raise TrackerRequestError("Nothing to update: give a title and/or a description")

        data = await self._gql.query(_UPDATE_ISSUE_QUERY, {"issueId": issue_id, "input": payload})
        result = data.get("issueUpdate") or {}
        updated = result.get("issue")
        if not result.get("success") or updated is None:
            raise GraphQLError(f"Linear did not update issue {issue_id}: {result}")

        self._gql.invalidate_cache("search:")
        self._gql.invalidate_cache("recent:")
        return self._node_to_issue(updated)

    async def create_issue(
        self,
        title: str,
        description: str,
        *,
        team: str,
        labels: list[str] | None = None,
        priority: int | None = None,
    ) -> TrackerIssue:
        """Create a Linear issue in the team with key ``team``.

        Labels resolve by name (case-insensitive) among the team's labels and
        workspace labels; an unknown team or label raises
        :class:`TrackerRequestError`.
        """
        team_id = await self._resolve_team_id(team)
        payload: dict[str, object] = {
            "teamId": team_id,
            "title": title,
            "description": description,
        }
        if labels:
            payload["labelIds"] = await self._resolve_label_ids(team, team_id, labels)
        if priority is not None:
            payload["priority"] = priority

        data = await self._gql.query(_CREATE_ISSUE_QUERY, {"input": payload})
        result = data.get("issueCreate") or {}
        created = result.get("issue")
        if not result.get("success") or created is None:
            raise GraphQLError(f"Linear did not create the issue in team {team}: {result}")

        self._gql.invalidate_cache("search:")
        self._gql.invalidate_cache("recent:")
        return self._node_to_issue(created)

    async def _resolve_team_id(self, team_key: str) -> str:
        data = await self._gql.query(_TEAM_BY_KEY_QUERY, {"key": team_key})
        teams = (data.get("teams") or {}).get("nodes") or []
        if not teams:
            raise TrackerRequestError(f"Linear team not found: {team_key}")
        return teams[0]["id"]

    async def _resolve_label_ids(self, team_key: str, team_id: str, names: list[str]) -> list[str]:
        # Label names are case-insensitive in Linear: "bug" and "Bug" are one label.
        wanted: dict[str, str] = {}
        for name in names:
            wanted.setdefault(name.casefold(), name)
        label_filter = {"or": [{"name": {"eqIgnoreCase": name}} for name in wanted.values()]}
        data = await self._gql.query(_LABELS_BY_NAME_QUERY, {"filter": label_filter})
        nodes = (data.get("issueLabels") or {}).get("nodes") or []
        # Workspace labels (no team) and the target team's labels apply.
        usable = [n for n in nodes if (n.get("team") or {}).get("id") in (None, team_id)]

        label_ids: list[str] = []
        missing: list[str] = []
        ambiguous: list[str] = []
        for folded, name in wanted.items():
            matches = [n for n in usable if n["name"].casefold() == folded]
            if not matches:
                missing.append(name)
                continue
            if len(matches) > 1:
                ambiguous.append(name)
                continue
            label_ids.append(matches[0]["id"])
        if missing:
            raise TrackerRequestError(
                f"Linear label(s) not found in team {team_key} or the workspace: "
                f"{', '.join(missing)}"
            )
        if ambiguous:
            raise TrackerRequestError(
                f"Linear label name(s) match more than one label in team {team_key}: "
                f"{', '.join(ambiguous)}"
            )
        return label_ids

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._gql.close()
