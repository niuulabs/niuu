"""Linear tracker adapter.

Implements TrackerPort using the Linear GraphQL API.
Maps: Project=Saga, Milestone=Phase, Issue=Raid.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import asyncpg

from niuu.adapters.linear import GraphQLError, LinearGraphQLClient
from niuu.domain.models import LINEAR_API_URL
from tyr.domain.models import (
    ConfidenceEvent,
    ConfidenceEventType,
    Phase,
    PhaseStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from tyr.ports.tracker import TrackerPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State mapping
# ---------------------------------------------------------------------------

_RAID_TO_LINEAR: dict[RaidStatus, str] = {
    RaidStatus.PENDING: "Todo",
    RaidStatus.QUEUED: "Todo",
    RaidStatus.RUNNING: "In Progress",
    RaidStatus.REVIEW: "In Review",
    RaidStatus.ESCALATED: "In Review",
    RaidStatus.MERGED: "Done",
    RaidStatus.FAILED: "Canceled",
}

_LINEAR_TO_RAID: dict[str, RaidStatus] = {
    "Backlog": RaidStatus.PENDING,
    "Todo": RaidStatus.PENDING,
    "In Progress": RaidStatus.RUNNING,
    "In Review": RaidStatus.REVIEW,
    "Done": RaidStatus.MERGED,
    "Canceled": RaidStatus.FAILED,
}

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

_PROJECT_FIELDS = """
      id
      name
      description
      state
      url
      startDate
      targetDate
      progress
      slugId
      projectMilestones { nodes { id progress } }
      issues { nodes { id } }
"""

_LIST_PROJECTS_QUERY = (
    """
query ListProjects($first: Int!) {
  projects(first: $first) {
    nodes {
"""
    + _PROJECT_FIELDS
    + """
    }
  }
}
"""
)

_GET_PROJECT_QUERY = (
    """
query GetProject($id: String!) {
  project(id: $id) {
"""
    + _PROJECT_FIELDS
    + """
  }
}
"""
)

_LIST_MILESTONES_QUERY = """
query ListMilestones($projectId: String!) {
  project(id: $projectId) {
    projectMilestones {
      nodes {
        id
        name
        description
        sortOrder
        progress
        targetDate
      }
    }
  }
}
"""

_GET_PROJECT_FULL_QUERY = """
query GetProjectFull($id: String!, $issueFirst: Int!) {
  project(id: $id) {
      id
      name
      description
      state
      url
      startDate
      targetDate
      progress
      projectMilestones {
        nodes {
          id
          name
          description
          sortOrder
          progress
          targetDate
        }
      }
      issueCount: issues { nodes { id } }
      issuesFull: issues(first: $issueFirst) {
        nodes {
          id
          identifier
          title
          description
          state { name type }
          assignee { name }
          labels { nodes { name } }
          priority
          priorityLabel
          estimate
          url
          projectMilestone { id }
        }
      }
  }
}
"""

_ISSUE_FIELDS = """
      id
      identifier
      title
      description
      state { name type }
      assignee { name }
      labels { nodes { name } }
      priority
      priorityLabel
      estimate
      url
      projectMilestone { id }
"""

_LIST_ISSUE_RELATIONS_QUERY = """
query ListIssueRelations($projectId: ID!, $first: Int!) {
  issues(
    filter: { project: { id: { eq: $projectId } } }
    first: $first
  ) {
    nodes {
      identifier
      state { type }
      relations {
        nodes {
          type
          relatedIssue { identifier }
        }
      }
    }
  }
}
"""

_LIST_ISSUES_QUERY = (
    """
query ListIssues($projectId: ID!, $first: Int!) {
  issues(
    filter: { project: { id: { eq: $projectId } } }
    first: $first
    orderBy: updatedAt
  ) {
    nodes {
"""
    + _ISSUE_FIELDS
    + """
    }
  }
}
"""
)

_LIST_ISSUES_BY_MILESTONE_QUERY = (
    """
query ListIssuesByMilestone($projectId: ID!, $milestoneId: ID!, $first: Int!) {
  issues(
    filter: {
      project: { id: { eq: $projectId } }
      projectMilestone: { id: { eq: $milestoneId } }
    }
    first: $first
    orderBy: updatedAt
  ) {
    nodes {
"""
    + _ISSUE_FIELDS
    + """
    }
  }
}
"""
)

_CREATE_PROJECT_QUERY = """
mutation CreateProject($name: String!, $description: String, $teamIds: [String!]!) {
  projectCreate(input: { name: $name, description: $description, teamIds: $teamIds }) {
    project { id }
    success
  }
}
"""

_CREATE_MILESTONE_QUERY = """
mutation CreateMilestone($name: String!, $projectId: String!, $sortOrder: Float!) {
  projectMilestoneCreate(input: { name: $name, projectId: $projectId, sortOrder: $sortOrder }) {
    projectMilestone { id }
    success
  }
}
"""

_CREATE_DOCUMENT_QUERY = """
mutation CreateDocument($title: String!, $content: String, $projectId: String) {
  documentCreate(input: { title: $title, content: $content, projectId: $projectId }) {
    document { id }
    success
  }
}
"""

_CREATE_ISSUE_DOCUMENT_QUERY = """
mutation CreateIssueDocument($title: String!, $content: String, $issueId: String!) {
  documentCreate(input: { title: $title, content: $content, issueId: $issueId }) {
    document { id }
    success
  }
}
"""

_CREATE_ISSUE_QUERY = """
mutation CreateIssue(
  $title: String!,
  $description: String,
  $projectId: String!,
  $projectMilestoneId: String,
  $teamId: String!,
  $estimate: Int
) {
  issueCreate(input: {
    title: $title,
    description: $description,
    projectId: $projectId,
    projectMilestoneId: $projectMilestoneId,
    teamId: $teamId,
    estimate: $estimate
  }) {
    issue { id identifier }
    success
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
    projectMilestone { id }
    createdAt
    updatedAt
  }
}
"""

_GET_MILESTONE_QUERY = """
query GetMilestone($id: String!) {
  projectMilestone(id: $id) {
    id
    name
    description
    sortOrder
    progress
    project { id }
  }
}
"""

_UPDATE_ISSUE_STATE_QUERY = """
mutation UpdateIssueState($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: { stateId: $stateId }) {
    issue { id state { name } }
    success
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

_TEAM_STATES_QUERY = """
query TeamStates($teamId: String!) {
  team(id: $teamId) {
    states {
      nodes { id name }
    }
  }
}
"""

_ADD_COMMENT_QUERY = """
mutation AddComment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
  }
}
"""


class LinearTrackerAdapter(TrackerPort):
    """Linear tracker adapter: Project=Saga, Milestone=Phase, Issue=Raid."""

    def __init__(
        self,
        api_key: str,
        team_id: str | None = None,
        api_url: str = LINEAR_API_URL,
        cache_ttl: float = 30.0,
        max_retries: int = 3,
        pool: asyncpg.Pool | None = None,
        **_extra: object,
    ) -> None:
        self._team_id = team_id
        self._pool = pool
        self._gql = LinearGraphQLClient(
            api_key=api_key,
            api_url=api_url,
            cache_ttl=cache_ttl,
            max_retries=max_retries,
        )

    async def _get_team_id(self) -> str:
        """Return the configured team ID, or discover the first available team."""
        if self._team_id:
            return self._team_id
        data = await self._gql.query("{ teams { nodes { id } } }")
        nodes = data.get("teams", {}).get("nodes", [])
        if not nodes:
            raise GraphQLError("No Linear teams accessible with this API key")
        self._team_id = nodes[0]["id"]
        return self._team_id

    # -- CRUD: create --

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        team_id = await self._get_team_id()
        project_desc = description or (
            f"Saga: {saga.slug}\nRepos: {', '.join(saga.repos)}\nBranch: {saga.feature_branch}"
        )
        data = await self._gql.query(
            _CREATE_PROJECT_QUERY,
            {
                "name": saga.name,
                "description": project_desc,
                "teamIds": [team_id],
            },
        )
        project = data.get("projectCreate", {}).get("project")
        if project is None:
            raise GraphQLError("Failed to create Linear project")
        self._gql.invalidate_cache("projects")
        return project["id"]

    async def attach_document(self, project_id: str, title: str, content: str) -> str:
        data = await self._gql.query(
            _CREATE_DOCUMENT_QUERY,
            {"title": title, "content": content, "projectId": project_id},
        )
        doc = data.get("documentCreate", {}).get("document")
        if doc is None:
            raise GraphQLError("Failed to create Linear document")
        return doc["id"]

    async def add_comment(self, issue_id: str, body: str) -> None:
        await self._gql.query(
            _ADD_COMMENT_QUERY,
            {"issueId": issue_id, "body": body},
        )

    async def attach_issue_document(self, issue_id: str, title: str, content: str) -> str:
        """Attach a document to an issue (shows as a resource)."""
        data = await self._gql.query(
            _CREATE_ISSUE_DOCUMENT_QUERY,
            {"title": title, "content": content, "issueId": issue_id},
        )
        doc = data.get("documentCreate", {}).get("document")
        if doc is None:
            raise GraphQLError("Failed to create issue document")
        return doc["id"]

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        parent_id = project_id or phase.tracker_id
        data = await self._gql.query(
            _CREATE_MILESTONE_QUERY,
            {
                "name": phase.name,
                "projectId": parent_id,
                "sortOrder": float(phase.number),
            },
        )
        milestone = data.get("projectMilestoneCreate", {}).get("projectMilestone")
        if milestone is None:
            raise GraphQLError("Failed to create Linear milestone")
        self._gql.invalidate_cache("milestones")
        return milestone["id"]

    async def create_raid(self, raid: Raid, *, project_id: str = "", milestone_id: str = "") -> str:
        description = raid.description
        if raid.acceptance_criteria:
            criteria = "\n".join(f"- [ ] {c}" for c in raid.acceptance_criteria)
            description += f"\n\n## Acceptance Criteria\n{criteria}"
        if raid.declared_files:
            files = "\n".join(f"- `{f}`" for f in raid.declared_files)
            description += f"\n\n## Declared Files\n{files}"
        if raid.confidence:
            description += f"\n\n**Confidence:** {raid.confidence:.0%}"

        # Linear estimate is an integer (story points); round hours to nearest int
        estimate = round(raid.estimate_hours) if raid.estimate_hours else None

        effective_project_id = project_id or raid.tracker_id
        effective_milestone_id = milestone_id or None

        data = await self._gql.query(
            _CREATE_ISSUE_QUERY,
            {
                "title": raid.name,
                "description": description,
                "projectId": effective_project_id,
                "projectMilestoneId": effective_milestone_id,
                "teamId": await self._get_team_id(),
                "estimate": estimate,
            },
        )
        issue = data.get("issueCreate", {}).get("issue")
        if issue is None:
            raise GraphQLError("Failed to create Linear issue")
        self._gql.invalidate_cache("issues")
        return issue["id"]

    # -- CRUD: update / close --

    async def update_raid_state(self, raid_id: str, state: RaidStatus) -> None:
        linear_state_name = _RAID_TO_LINEAR.get(state)
        if linear_state_name is None:
            raise ValueError(f"No Linear state mapping for {state}")

        logger.info("Setting Linear issue %s to '%s'", raid_id, linear_state_name)
        state_id = await self._resolve_state_id(raid_id, linear_state_name)
        await self._gql.query(
            _UPDATE_ISSUE_STATE_QUERY,
            {"issueId": raid_id, "stateId": state_id},
        )
        self._gql.invalidate_cache("issues")
        logger.info("Linear issue %s set to '%s'", raid_id, linear_state_name)

    async def close_raid(self, raid_id: str) -> None:
        logger.info("Closing Linear issue %s (setting to Done)", raid_id)
        state_id = await self._resolve_state_id(raid_id, "Done")
        await self._gql.query(
            _UPDATE_ISSUE_STATE_QUERY,
            {"issueId": raid_id, "stateId": state_id},
        )
        self._gql.invalidate_cache("issues")
        logger.info("Linear issue %s closed (Done)", raid_id)

    # -- Read: domain entities --

    async def get_saga(self, saga_id: str) -> Saga:
        data = await self._gql.query(_GET_PROJECT_QUERY, {"id": saga_id})
        project = data.get("project")
        if project is None:
            raise GraphQLError(f"Project not found: {saga_id}")
        return self._project_to_saga(project)

    async def get_phase(self, tracker_id: str) -> Phase:
        data = await self._gql.query(_GET_MILESTONE_QUERY, {"id": tracker_id})
        milestone = data.get("projectMilestone")
        if milestone is None:
            raise GraphQLError(f"Milestone not found: {tracker_id}")
        return self._milestone_to_phase(milestone)

    async def get_raid(self, tracker_id: str) -> Raid:
        data = await self._gql.query(_GET_ISSUE_QUERY, {"id": tracker_id})
        issue = data.get("issue")
        if issue is None:
            raise GraphQLError(f"Issue not found: {tracker_id}")
        progress = await self._fetch_progress(tracker_id)
        return self._issue_to_raid(issue, progress=progress)

    async def list_pending_raids(self, phase_id: str) -> list[Raid]:
        data = await self._gql.query(
            _LIST_ISSUES_BY_MILESTONE_QUERY,
            {"projectId": "", "milestoneId": phase_id, "first": 100},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        raids = [self._issue_to_raid(n) for n in nodes]
        return [r for r in raids if r.status in (RaidStatus.PENDING, RaidStatus.QUEUED)]

    # -- Browsing --

    async def list_projects(self) -> list[TrackerProject]:
        cache_key = "projects:all"
        cached = self._gql.get_cached(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        data = await self._gql.query(_LIST_PROJECTS_QUERY, {"first": 50})
        nodes = data.get("projects", {}).get("nodes", [])
        projects = [self._node_to_tracker_project(n) for n in nodes]
        self._gql.set_cached(cache_key, projects)
        return projects

    async def get_project(self, project_id: str) -> TrackerProject:
        data = await self._gql.query(_GET_PROJECT_QUERY, {"id": project_id})
        project = data.get("project")
        if project is None:
            raise GraphQLError(f"Project not found: {project_id}")
        return self._node_to_tracker_project(project)

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        cache_key = f"milestones:{project_id}"
        cached = self._gql.get_cached(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        data = await self._gql.query(_LIST_MILESTONES_QUERY, {"projectId": project_id})
        nodes = data.get("project", {}).get("projectMilestones", {}).get("nodes", [])
        milestones = [self._node_to_tracker_milestone(n, project_id) for n in nodes]
        milestones.sort(key=lambda m: m.sort_order)
        self._gql.set_cached(cache_key, milestones)
        return milestones

    async def list_issues(
        self,
        project_id: str,
        milestone_id: str | None = None,
    ) -> list[TrackerIssue]:
        cache_key = f"issues:{project_id}:{milestone_id}"
        cached = self._gql.get_cached(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        if milestone_id:
            data = await self._gql.query(
                _LIST_ISSUES_BY_MILESTONE_QUERY,
                {"projectId": project_id, "milestoneId": milestone_id, "first": 100},
            )
        else:
            data = await self._gql.query(
                _LIST_ISSUES_QUERY,
                {"projectId": project_id, "first": 100},
            )

        nodes = data.get("issues", {}).get("nodes", [])
        issues = [self._node_to_tracker_issue(n) for n in nodes]
        self._gql.set_cached(cache_key, issues)
        return issues

    async def get_project_full(
        self, project_id: str
    ) -> tuple[TrackerProject, list[TrackerMilestone], list[TrackerIssue]]:
        """Fetch project, milestones, and issues in a single GraphQL call."""
        data = await self._gql.query(_GET_PROJECT_FULL_QUERY, {"id": project_id, "issueFirst": 250})
        project_node = data.get("project")
        if project_node is None:
            raise GraphQLError(f"Project not found: {project_id}")

        # The full query uses aliased fields to avoid conflicts
        # Restore standard keys for _node_to_tracker_project
        project_for_counts = {
            **project_node,
            "issues": project_node.get("issueCount", {}),
        }
        project = self._node_to_tracker_project(project_for_counts)

        ms_nodes = project_node.get("projectMilestones", {}).get("nodes", [])
        milestones = [self._node_to_tracker_milestone(n, project_id) for n in ms_nodes]
        milestones.sort(key=lambda m: m.sort_order)

        issue_nodes = project_node.get("issuesFull", {}).get("nodes", [])
        issues = [self._node_to_tracker_issue(n) for n in issue_nodes]

        return project, milestones, issues

    async def get_blocked_identifiers(self, project_id: str) -> set[str]:
        """Fetch issue relations and return identifiers blocked by incomplete issues."""
        data = await self._gql.query(
            _LIST_ISSUE_RELATIONS_QUERY, {"projectId": project_id, "first": 250}
        )
        nodes = data.get("issues", {}).get("nodes", [])

        blocked: set[str] = set()
        for node in nodes:
            state_type = (node.get("state") or {}).get("type", "")
            if state_type == "completed":
                continue
            for rel in (node.get("relations") or {}).get("nodes", []):
                if rel.get("type") == "blocks":
                    target = (rel.get("relatedIssue") or {}).get("identifier", "")
                    if target:
                        blocked.add(target)
        return blocked

    # -- Raid progress --

    async def update_raid_progress(
        self,
        tracker_id: str,
        *,
        status: RaidStatus | None = None,
        session_id: str | None = None,
        confidence: float | None = None,
        pr_url: str | None = None,
        pr_id: str | None = None,
        retry_count: int | None = None,
        reason: str | None = None,
        owner_id: str | None = None,
        phase_tracker_id: str | None = None,
        saga_tracker_id: str | None = None,
        chronicle_summary: str | None = None,
        reviewer_session_id: str | None = None,
        review_round: int | None = None,
    ) -> Raid:
        if self._pool is None:
            raise RuntimeError("pool is required for update_raid_progress")
        await self._pool.execute(
            """
            INSERT INTO raid_progress
                (tracker_id, status, session_id, confidence, pr_url, pr_id,
                 retry_count, reason, owner_id, phase_tracker_id, saga_tracker_id,
                 chronicle_summary, reviewer_session_id, review_round)
            VALUES ($1, COALESCE($2, 'PENDING'), $3, $4, $5, $6,
                    COALESCE($7, 0), $8, $9, $10, $11, $12, $13,
                    COALESCE($14, 0))
            ON CONFLICT (tracker_id) DO UPDATE SET
                status              = COALESCE($2, raid_progress.status),
                session_id          = COALESCE($3, raid_progress.session_id),
                confidence          = COALESCE($4, raid_progress.confidence),
                pr_url              = COALESCE($5, raid_progress.pr_url),
                pr_id               = COALESCE($6, raid_progress.pr_id),
                retry_count         = COALESCE($7, raid_progress.retry_count),
                reason              = COALESCE($8, raid_progress.reason),
                owner_id            = COALESCE($9, raid_progress.owner_id),
                phase_tracker_id    = COALESCE($10, raid_progress.phase_tracker_id),
                saga_tracker_id     = COALESCE($11, raid_progress.saga_tracker_id),
                chronicle_summary   = COALESCE($12, raid_progress.chronicle_summary),
                reviewer_session_id = COALESCE($13, raid_progress.reviewer_session_id),
                review_round        = COALESCE($14, raid_progress.review_round),
                updated_at          = NOW()
            """,
            tracker_id,
            status.value if status is not None else None,
            session_id,
            confidence,
            pr_url,
            pr_id,
            retry_count,
            reason,
            owner_id,
            phase_tracker_id,
            saga_tracker_id,
            chronicle_summary,
            reviewer_session_id,
            review_round,
        )
        if status is not None:
            try:
                await self.update_raid_state(tracker_id, status)
            except Exception:
                logger.exception("Failed to sync status to Linear for %s", tracker_id)
        return await self.get_raid(tracker_id)

    async def _collect_raids(self, rows: list[asyncpg.Record]) -> list[Raid]:
        raids: list[Raid] = []
        stale_ids: list[str] = []
        for row in rows:
            try:
                raid = await self.get_raid(row["tracker_id"])
                raids.append(raid)
            except GraphQLError as exc:
                if "not found" in str(exc).lower() or "Could not find" in str(exc):
                    logger.warning(
                        "Raid %s references a deleted Linear issue — marking for cleanup",
                        row["tracker_id"],
                    )
                    stale_ids.append(row["tracker_id"])
                else:
                    logger.exception("Failed to fetch raid %s", row["tracker_id"])
            except Exception:
                logger.exception("Failed to fetch raid %s", row["tracker_id"])
        if stale_ids and self._pool is not None:
            await self._pool.execute(
                "DELETE FROM raid_progress WHERE tracker_id = ANY($1::text[])",
                stale_ids,
            )
            logger.info("Cleaned up %d stale raid_progress entries", len(stale_ids))
        return raids

    async def get_raid_progress_for_saga(self, saga_tracker_id: str) -> list[Raid]:
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            "SELECT tracker_id FROM raid_progress WHERE saga_tracker_id = $1",
            saga_tracker_id,
        )
        return await self._collect_raids(rows)

    async def get_raid_by_session(self, session_id: str) -> Raid | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT tracker_id FROM raid_progress WHERE session_id = $1",
            session_id,
        )
        if row is None:
            return None
        return await self.get_raid(row["tracker_id"])

    async def list_raids_by_status(self, status: RaidStatus) -> list[Raid]:
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            "SELECT tracker_id FROM raid_progress WHERE status = $1 ORDER BY updated_at",
            status.value,
        )
        return await self._collect_raids(rows)

    async def get_raid_by_id(self, raid_id: UUID) -> Raid | None:
        if self._pool is None:
            return None
        # Scan progress table for a tracker_id whose uuid5 matches raid_id
        rows = await self._pool.fetch("SELECT tracker_id FROM raid_progress")
        for row in rows:
            if uuid5(UUID(int=0), row["tracker_id"]) == raid_id:
                return await self.get_raid(row["tracker_id"])
        return None

    # -- Confidence events --

    async def add_confidence_event(self, tracker_id: str, event: ConfidenceEvent) -> None:
        if self._pool is None:
            raise RuntimeError("pool is required for add_confidence_event")
        await self._pool.execute(
            """
            INSERT INTO raid_confidence_events
                (id, raid_id, tracker_id, event_type, delta, score_after, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            event.id,
            event.raid_id,
            tracker_id,
            event.event_type.value,
            event.delta,
            event.score_after,
            event.created_at,
        )
        await self._pool.execute(
            "UPDATE raid_progress SET confidence = $2, updated_at = NOW() WHERE tracker_id = $1",
            tracker_id,
            event.score_after,
        )

    async def get_confidence_events(self, tracker_id: str) -> list[ConfidenceEvent]:
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            """
            SELECT ce.* FROM raid_confidence_events ce
            WHERE ce.tracker_id = $1
            ORDER BY ce.created_at
            """,
            tracker_id,
        )
        return [
            ConfidenceEvent(
                id=r["id"],
                raid_id=r["raid_id"],
                event_type=ConfidenceEventType(r["event_type"]),
                delta=r["delta"],
                score_after=r["score_after"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- Phase gate management --

    async def all_raids_merged(self, phase_tracker_id: str) -> bool:
        if self._pool is None:
            return False
        row = await self._pool.fetchrow(
            """
            SELECT count(*) FILTER (WHERE status != 'MERGED') AS remaining
            FROM raid_progress
            WHERE phase_tracker_id = $1
            """,
            phase_tracker_id,
        )
        return row is not None and row["remaining"] == 0

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        milestones = await self.list_milestones(saga_tracker_id)
        return [
            Phase(
                id=uuid4(),
                saga_id=UUID(int=0),
                tracker_id=m.id,
                number=m.sort_order,
                name=m.name,
                status=PhaseStatus.ACTIVE,
                confidence=0.0,
            )
            for m in milestones
        ]

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        # Linear does not have GATED/ACTIVE phase concepts — no-op
        return None

    # -- Cross-entity navigation --

    async def get_saga_for_raid(self, tracker_id: str) -> Saga | None:
        if self._pool is None:
            raise RuntimeError("Database pool not configured — cannot look up saga for raid")
        row = await self._pool.fetchrow(
            "SELECT saga_tracker_id FROM raid_progress WHERE tracker_id = $1",
            tracker_id,
        )
        if row is None or not row["saga_tracker_id"]:
            return None
        return await self.get_saga(row["saga_tracker_id"])

    async def get_phase_for_raid(self, tracker_id: str) -> Phase | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT phase_tracker_id FROM raid_progress WHERE tracker_id = $1",
            tracker_id,
        )
        if row is None or not row["phase_tracker_id"]:
            return None
        return await self.get_phase(row["phase_tracker_id"])

    async def get_owner_for_raid(self, tracker_id: str) -> str | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT owner_id FROM raid_progress WHERE tracker_id = $1",
            tracker_id,
        )
        if row is None:
            return None
        return row["owner_id"] or None

    # -- Session messages --

    async def save_session_message(self, message: SessionMessage) -> None:
        if self._pool is None:
            raise RuntimeError("pool is required for save_session_message")
        # Resolve tracker_id from raid UUID
        tracker_id = str(message.raid_id)
        rows = await self._pool.fetch("SELECT tracker_id FROM raid_progress")
        for row in rows:
            if uuid5(UUID(int=0), row["tracker_id"]) == message.raid_id:
                tracker_id = row["tracker_id"]
                break
        await self._pool.execute(
            """
            INSERT INTO raid_session_messages
                (id, raid_id, tracker_id, session_id, content, sender, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            message.id,
            message.raid_id,
            tracker_id,
            message.session_id,
            message.content,
            message.sender,
            message.created_at,
        )

    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            """
            SELECT * FROM raid_session_messages
            WHERE tracker_id = $1
            ORDER BY created_at
            """,
            tracker_id,
        )
        return [
            SessionMessage(
                id=r["id"],
                raid_id=r["raid_id"],
                session_id=r["session_id"],
                content=r["content"],
                sender=r["sender"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- Internal helpers --

    async def _fetch_progress(self, tracker_id: str) -> dict | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT * FROM raid_progress WHERE tracker_id = $1",
            tracker_id,
        )
        return dict(row) if row else None

    async def _resolve_state_id(self, issue_id: str, state_name: str) -> str:
        """Resolve a Linear workflow state ID by name for an issue's team."""
        team_data = await self._gql.query(_ISSUE_TEAM_QUERY, {"id": issue_id})
        issue_node = team_data.get("issue")
        if issue_node is None:
            raise GraphQLError(f"Issue not found: {issue_id}")
        team_id = issue_node["team"]["id"]

        states_data = await self._gql.query(_TEAM_STATES_QUERY, {"teamId": team_id})
        states = states_data.get("team", {}).get("states", {}).get("nodes", [])
        for s in states:
            if s["name"].lower() == state_name.lower():
                return s["id"]

        available = [s["name"] for s in states]
        raise GraphQLError(f"State '{state_name}' not found. Available: {', '.join(available)}")

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._gql.close()

    # -- Conversion helpers --

    @staticmethod
    def _node_to_tracker_project(node: dict) -> TrackerProject:
        ms_nodes = node.get("projectMilestones", {}).get("nodes", [])
        # Calculate progress as average of milestone progress
        if ms_nodes:
            ms_progress = [_parse_progress(m.get("progress")) for m in ms_nodes]
            progress = sum(ms_progress) / len(ms_progress)
        else:
            progress = _parse_progress(node.get("progress"))

        # Extract slug from URL: .../project/{slug}-{slugId}
        slug = ""
        url = node.get("url", "")
        slug_id = node.get("slugId", "")
        if url and slug_id:
            path_part = url.rsplit("/", 1)[-1]
            if path_part.endswith(f"-{slug_id}"):
                slug = path_part[: -(len(slug_id) + 1)]

        return TrackerProject(
            id=node["id"],
            name=node.get("name", ""),
            description=node.get("description") or "",
            status=node.get("state", ""),
            url=url,
            milestone_count=len(ms_nodes),
            issue_count=len(node.get("issues", {}).get("nodes", [])),
            slug=slug,
            progress=progress,
            start_date=node.get("startDate"),
            target_date=node.get("targetDate"),
        )

    @staticmethod
    def _node_to_tracker_milestone(node: dict, project_id: str) -> TrackerMilestone:
        return TrackerMilestone(
            id=node["id"],
            project_id=project_id,
            name=node.get("name", ""),
            description=node.get("description") or "",
            sort_order=int(node.get("sortOrder", 0)),
            progress=_parse_progress(node.get("progress")),
            target_date=node.get("targetDate"),
        )

    @staticmethod
    def _node_to_tracker_issue(node: dict) -> TrackerIssue:
        state = node.get("state") or {}
        return TrackerIssue(
            id=node["id"],
            identifier=node.get("identifier", ""),
            title=node.get("title", ""),
            description=node.get("description") or "",
            status=state.get("name", "Unknown"),
            status_type=state.get("type", ""),
            assignee=(node.get("assignee") or {}).get("name"),
            labels=[n["name"] for n in (node.get("labels") or {}).get("nodes", [])],
            priority=node.get("priority", 0),
            priority_label=node.get("priorityLabel", ""),
            estimate=node.get("estimate"),
            url=node.get("url", ""),
            milestone_id=(node.get("projectMilestone") or {}).get("id"),
        )

    @staticmethod
    def _project_to_saga(node: dict) -> Saga:
        now = datetime.now(UTC)
        return Saga(
            id=uuid4(),
            tracker_id=node["id"],
            tracker_type="linear",
            slug=node.get("name", "").lower().replace(" ", "-"),
            name=node.get("name", ""),
            repos=[],
            feature_branch="feat/test",
            status=SagaStatus.ACTIVE,
            confidence=0.0,
            created_at=now,
            base_branch="",
        )

    @staticmethod
    def _milestone_to_phase(node: dict) -> Phase:
        return Phase(
            id=uuid4(),
            saga_id=UUID(int=0),
            tracker_id=node["id"],
            number=int(node.get("sortOrder", 0)),
            name=node.get("name", ""),
            status=PhaseStatus.PENDING,
            confidence=0.0,
        )

    @staticmethod
    def _issue_to_raid(node: dict, *, progress: dict | None = None) -> Raid:
        state_name = node.get("state", {}).get("name", "Todo")
        now = datetime.now(UTC)
        raid_status = _LINEAR_TO_RAID.get(state_name, RaidStatus.PENDING)
        if progress and progress.get("status"):
            raid_status = RaidStatus(progress["status"])
        raid_id = uuid5(UUID(int=0), node["id"])
        return Raid(
            id=raid_id,
            phase_id=UUID(int=0),
            tracker_id=node["id"],
            identifier=node.get("identifier", ""),
            url=node.get("url", ""),
            name=node.get("title", ""),
            description=node.get("description") or "",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=None,
            status=raid_status,
            confidence=float(progress["confidence"])
            if progress and progress.get("confidence")
            else 0.0,
            session_id=progress.get("session_id") if progress else None,
            branch=None,
            chronicle_summary=None,
            pr_url=progress.get("pr_url") if progress else None,
            pr_id=progress.get("pr_id") if progress else None,
            retry_count=int(progress["retry_count"])
            if progress and progress.get("retry_count")
            else 0,
            created_at=now,
            updated_at=now,
            reviewer_session_id=progress.get("reviewer_session_id") if progress else None,
            review_round=int(progress["review_round"])
            if progress and progress.get("review_round")
            else 0,
        )


def _parse_progress(value: object) -> float:
    """Parse a Linear progress value and return a 0.0–1.0 float.

    Linear returns progress as a percentage float (e.g. 8.33 = 8.33%),
    or occasionally as a string like '100%'.
    """
    if value is None:
        return 0.0
    if isinstance(value, str):
        return float(value.rstrip("%")) / 100.0
    if isinstance(value, (int, float)):
        return float(value) / 100.0
    return 0.0
