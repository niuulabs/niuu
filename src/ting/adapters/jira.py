"""Jira Cloud tracker adapter for Ting.

Maps Jira projects to sagas, versions to phases, and issues to runs.  Jira
owns the visible work hierarchy while Ting's ``run_progress`` tables retain
execution-only state such as sessions and pull requests.
"""

from __future__ import annotations

import logging
import re
from base64 import b64encode
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid5

import asyncpg
import httpx

from ting.domain.models import (
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
    SessionMessage,
    TrackerIssue,
    TrackerMilestone,
    TrackerProject,
)
from ting.ports.tracker import TrackerPort

logger = logging.getLogger(__name__)

_ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
_ATLASSIAN_API_ROOT = "https://api.atlassian.com/ex/jira"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_ISSUE_FIELDS = (
    "summary,description,status,assignee,labels,priority,project,fixVersions,"
    "timeoriginalestimate,created,updated,issuelinks"
)

_PRIORITY_MAP: dict[str, int] = {
    "highest": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "lowest": 5,
}

_RUN_TO_JIRA_NAMES: dict[RunStatus, tuple[str, ...]] = {
    RunStatus.PENDING: ("To Do", "Open", "Backlog"),
    RunStatus.QUEUED: ("To Do", "Open", "Backlog"),
    RunStatus.RUNNING: ("In Progress", "Start Progress"),
    RunStatus.REVIEW: ("In Review", "Review"),
    RunStatus.ESCALATED: ("In Review", "Review"),
    RunStatus.MERGED: ("Done", "Closed", "Resolve Issue"),
    RunStatus.FAILED: ("Cancelled", "Canceled", "Won't Do"),
}

_RUN_TO_CATEGORY: dict[RunStatus, str] = {
    RunStatus.PENDING: "new",
    RunStatus.QUEUED: "new",
    RunStatus.RUNNING: "indeterminate",
    RunStatus.REVIEW: "indeterminate",
    RunStatus.ESCALATED: "indeterminate",
    RunStatus.MERGED: "done",
    RunStatus.FAILED: "done",
}


class JiraAPIError(RuntimeError):
    """Raised when Jira cannot fulfill a tracker operation."""


class JiraTrackerAdapter(TrackerPort):
    """Ting tracker implementation backed by Jira Cloud REST API v3."""

    def __init__(
        self,
        site_url: str,
        api_token: str = "",
        email: str = "",
        access_token: str = "",
        cloud_id: str = "",
        project_keys: list[str] | None = None,
        labels: list[str] | None = None,
        issue_type: str = "Task",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        pool: asyncpg.Pool | None = None,
        **_extra: object,
    ) -> None:
        self._site_url = site_url.rstrip("/")
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

        self._cloud_id = cloud_id.strip()
        if self._cloud_id and not re.fullmatch(r"[A-Za-z0-9-]+", self._cloud_id):
            raise ValueError("cloud_id may contain only letters, digits, and dashes")
        self._project_keys = self._clean_values(project_keys, upper=True)
        self._labels = self._clean_values(labels)
        self._issue_type = issue_type.strip() or "Task"
        self._pool = pool
        self._resource: dict[str, Any] | None = None
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=float(timeout),
        )

    # -- HTTP and scope helpers -------------------------------------------------

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
                raise JiraAPIError(f"The OAuth grant does not include Jira site {self._site_url}")
            self._resource = matches[0]
        return f"{_ATLASSIAN_API_ROOT}/{self._resource['id']}/rest/api/3"

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self._client.request(
            method,
            f"{await self._api_base_url()}{path}",
            **kwargs,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text
            try:
                payload = response.json()
                errors = [
                    *(payload.get("errorMessages") or []),
                    *map(str, (payload.get("errors") or {}).values()),
                ]
                detail = "; ".join(errors) or detail
            except (ValueError, AttributeError):
                pass
            raise JiraAPIError(
                f"Jira {method} {path} failed ({response.status_code}): {detail[:500]}"
            ) from exc
        return response

    @staticmethod
    def _clean_values(values: list[str] | None, *, upper: bool = False) -> tuple[str, ...]:
        if values is None:
            return ()
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError("Jira scope values must be a list of strings")
        cleaned = (value.strip() for value in values)
        return tuple(dict.fromkeys(value.upper() if upper else value for value in cleaned if value))

    @staticmethod
    def _jql_string(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _require_allowed_project(self, project_id: str) -> None:
        if self._project_keys and project_id.upper() not in self._project_keys:
            raise JiraAPIError(
                f"Project {project_id!r} is outside this Jira connection's scope "
                f"({', '.join(self._project_keys)})"
            )

    def _issue_jql(self, project_id: str, milestone_id: str | None = None) -> str:
        parts = [f"project = {self._jql_string(project_id)}"]
        if self._labels:
            labels = ", ".join(self._jql_string(label) for label in self._labels)
            parts.append(f"labels in ({labels})")
        if milestone_id:
            parts.append(f"fixVersion = {self._jql_string(milestone_id)}")
        return " AND ".join(parts)

    async def _project_node(self, project_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/project/{quote(project_id, safe='')}")
        node = response.json()
        self._require_allowed_project(str(node.get("key") or project_id))
        return node

    async def _project_versions(self, project_id: str) -> list[dict[str, Any]]:
        response = await self._request("GET", f"/project/{quote(project_id, safe='')}/versions")
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def _issue_count(self, project_id: str) -> int:
        response = await self._request(
            "POST",
            "/search/approximate-count",
            json={"jql": self._issue_jql(project_id)},
        )
        return int(response.json().get("count") or 0)

    async def _search_issues(self, jql: str) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {
                "jql": f"{jql} ORDER BY updated DESC",
                "maxResults": 100,
                "fields": _ISSUE_FIELDS,
            }
            if token:
                params["nextPageToken"] = token
            response = await self._request("GET", "/search/jql", params=params)
            payload = response.json()
            issues.extend(payload.get("issues") or [])
            token = payload.get("nextPageToken")
            if not token or payload.get("isLast") is True:
                break
        return issues

    # -- External hierarchy creation -------------------------------------------

    async def create_saga(self, saga: Saga, *, description: str = "") -> str:
        """Resolve a saga onto an existing scoped Jira project.

        Jira project creation is an administrative operation and is deliberately
        not part of a normal Ting run.  A scoped integration targets an existing
        project; versions and issues are created inside it.
        """
        candidates = [saga.tracker_id, saga.slug, *self._project_keys]
        seen: set[str] = set()
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate or candidate.casefold() in seen:
                continue
            seen.add(candidate.casefold())
            try:
                project = await self._project_node(candidate)
                return str(project.get("key") or project["id"])
            except JiraAPIError:
                continue
        raise JiraAPIError(
            "No Jira project matched this saga. Configure project_keys or import an existing "
            "Jira project before creating work."
        )

    async def create_phase(self, phase: Phase, *, project_id: str = "") -> str:
        if not project_id:
            raise JiraAPIError("project_id is required to create a Jira version")
        project = await self._project_node(project_id)
        response = await self._request(
            "POST",
            "/version",
            json={"name": phase.name, "project": int(project["id"])},
        )
        return str(response.json()["id"])

    async def create_run(self, run: Run, *, project_id: str = "", milestone_id: str = "") -> str:
        if not project_id:
            raise JiraAPIError("project_id is required to create a Jira issue")
        self._require_allowed_project(project_id)
        description = run.description
        if run.acceptance_criteria:
            criteria = "\n".join(f"- [ ] {item}" for item in run.acceptance_criteria)
            description += f"\n\nAcceptance criteria\n{criteria}"
        if run.declared_files:
            files = "\n".join(f"- {item}" for item in run.declared_files)
            description += f"\n\nDeclared files\n{files}"
        fields: dict[str, Any] = {
            "project": {"key": project_id},
            "summary": run.name,
            "description": self._adf(description),
            "issuetype": {"name": self._issue_type},
        }
        if milestone_id:
            fields["fixVersions"] = [{"id": milestone_id}]
        if self._labels:
            fields["labels"] = list(self._labels)
        response = await self._request("POST", "/issue", json={"fields": fields})
        node = response.json()
        return str(node.get("key") or node["id"])

    async def add_comment(self, issue_id: str, body: str) -> None:
        await self._request(
            "POST",
            f"/issue/{quote(issue_id, safe='')}/comment",
            json={"body": self._adf(body)},
        )

    # -- External updates -------------------------------------------------------

    async def update_run_state(self, run_id: str, state: RunStatus) -> None:
        response = await self._request(
            "GET",
            f"/issue/{quote(run_id, safe='')}/transitions",
            params={"expand": "transitions.fields"},
        )
        transitions = response.json().get("transitions") or []
        desired_names = {name.casefold() for name in _RUN_TO_JIRA_NAMES[state]}
        target = next(
            (
                item
                for item in transitions
                if str(item.get("name") or "").casefold() in desired_names
            ),
            None,
        )
        if target is None:
            desired_category = _RUN_TO_CATEGORY[state]
            target = next(
                (
                    item
                    for item in transitions
                    if str(
                        ((item.get("to") or {}).get("statusCategory") or {}).get("key") or ""
                    ).casefold()
                    == desired_category
                ),
                None,
            )
        if target is None:
            available = ", ".join(str(item.get("name") or "") for item in transitions)
            raise JiraAPIError(f"No Jira transition for {state.value}. Available: {available}")
        await self._request(
            "POST",
            f"/issue/{quote(run_id, safe='')}/transitions",
            json={"transition": {"id": target["id"]}},
        )

    async def close_run(self, run_id: str) -> None:
        await self.update_run_state(run_id, RunStatus.MERGED)

    # -- External reads and browsing -------------------------------------------

    async def list_projects(self) -> list[TrackerProject]:
        if self._project_keys:
            nodes = [await self._project_node(key) for key in self._project_keys]
        else:
            nodes = []
            start_at = 0
            while True:
                response = await self._request(
                    "GET",
                    "/project/search",
                    params={"startAt": start_at, "maxResults": 50, "status": "live"},
                )
                payload = response.json()
                values = payload.get("values") or []
                nodes.extend(values)
                start_at += len(values)
                if not values or start_at >= int(payload.get("total") or 0):
                    break
        return [await self._node_to_project(node) for node in nodes]

    async def get_project(self, project_id: str) -> TrackerProject:
        return await self._node_to_project(await self._project_node(project_id))

    async def list_milestones(self, project_id: str) -> list[TrackerMilestone]:
        project = await self._project_node(project_id)
        key = str(project.get("key") or project_id)
        versions = await self._project_versions(key)
        return [self._version_to_milestone(node, key, index) for index, node in enumerate(versions)]

    async def list_issues(
        self, project_id: str, milestone_id: str | None = None
    ) -> list[TrackerIssue]:
        project = await self._project_node(project_id)
        key = str(project.get("key") or project_id)
        nodes = await self._search_issues(self._issue_jql(key, milestone_id))
        return [self._node_to_issue(node) for node in nodes]

    async def get_saga(self, saga_id: str) -> Saga:
        project = await self._project_node(saga_id)
        return replace(
            self._node_to_saga(project),
            tracker_connection_id=self.connection_id,
        )

    async def get_phase(self, tracker_id: str) -> Phase:
        if self._pool is not None:
            row = await self._pool.fetchrow(
                "SELECT * FROM phases WHERE tracker_id = $1", tracker_id
            )
            if row is not None:
                return self._row_to_phase(row)
        response = await self._request("GET", f"/version/{quote(tracker_id, safe='')}")
        node = response.json()
        return self._version_to_phase(node)

    async def get_run(self, tracker_id: str) -> Run:
        response = await self._request(
            "GET",
            f"/issue/{quote(tracker_id, safe='')}",
            params={"fields": _ISSUE_FIELDS},
        )
        node = response.json()
        project_key = str(((node.get("fields") or {}).get("project") or {}).get("key") or "")
        self._require_allowed_project(project_key)
        if self._labels:
            issue_labels = {
                str(label).casefold() for label in ((node.get("fields") or {}).get("labels") or [])
            }
            if not issue_labels.intersection(label.casefold() for label in self._labels):
                raise JiraAPIError(f"Issue {tracker_id} is outside this Jira connection's scope")
        return self._node_to_run(node, progress=await self._fetch_progress(tracker_id))

    async def list_pending_runs(self, phase_id: str) -> list[Run]:
        nodes = await self._search_issues(f"fixVersion = {self._jql_string(phase_id)}")
        runs = [self._node_to_run(node) for node in nodes]
        return [run for run in runs if run.status in {RunStatus.PENDING, RunStatus.QUEUED}]

    async def get_blocked_identifiers(self, project_id: str) -> set[str]:
        nodes = await self._search_issues(self._issue_jql(project_id))
        incomplete = {
            str(node.get("key") or "")
            for node in nodes
            if self._status_type((node.get("fields") or {}).get("status") or {}) != "completed"
        }
        blocked: set[str] = set()
        for node in nodes:
            identifier = str(node.get("key") or "")
            for link in (node.get("fields") or {}).get("issuelinks") or []:
                inward = link.get("inwardIssue")
                outward = link.get("outwardIssue")
                link_type = link.get("type") or {}
                if inward and str(link_type.get("inward") or "").casefold() == "is blocked by":
                    if str(inward.get("key") or "") in incomplete:
                        blocked.add(identifier)
                if outward and str(link_type.get("outward") or "").casefold() == "blocks":
                    if identifier in incomplete:
                        blocked.add(str(outward.get("key") or ""))
        return blocked

    # -- Ting-owned operational state ------------------------------------------

    async def update_run_progress(
        self,
        tracker_id: str,
        *,
        status: RunStatus | None = None,
        session_id: str | None = None,
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
    ) -> Run:
        if self._pool is None:
            raise RuntimeError("pool is required for update_run_progress")
        await self._pool.execute(
            """
            INSERT INTO run_progress
                (tracker_id, status, session_id, pr_url, pr_id,
                 retry_count, reason, owner_id, phase_tracker_id, saga_tracker_id,
                 chronicle_summary, reviewer_session_id, review_round, tracker_connection_id)
            VALUES ($1, COALESCE($2, 'PENDING'), $3, $4, $5,
                    COALESCE($6, 0), $7, $8, $9, $10, $11, $12, COALESCE($13, 0), $14)
            ON CONFLICT (tracker_connection_id, tracker_id) DO UPDATE SET
                status = COALESCE($2, run_progress.status),
                session_id = COALESCE($3, run_progress.session_id),
                pr_url = COALESCE($4, run_progress.pr_url),
                pr_id = COALESCE($5, run_progress.pr_id),
                retry_count = COALESCE($6, run_progress.retry_count),
                reason = COALESCE($7, run_progress.reason),
                owner_id = COALESCE($8, run_progress.owner_id),
                phase_tracker_id = COALESCE($9, run_progress.phase_tracker_id),
                saga_tracker_id = COALESCE($10, run_progress.saga_tracker_id),
                chronicle_summary = COALESCE($11, run_progress.chronicle_summary),
                reviewer_session_id = COALESCE($12, run_progress.reviewer_session_id),
                review_round = COALESCE($13, run_progress.review_round),
                updated_at = NOW()
            """,
            tracker_id,
            status.value if status else None,
            session_id,
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
            self.connection_id,
        )
        if status is not None:
            await self.update_run_state(tracker_id, status)
        return await self.get_run(tracker_id)

    async def _runs_for_rows(self, rows: list[asyncpg.Record]) -> list[Run]:
        runs: list[Run] = []
        for row in rows:
            try:
                runs.append(await self.get_run(row["tracker_id"]))
            except JiraAPIError:
                logger.warning("Could not fetch Jira run %s", row["tracker_id"], exc_info=True)
        return runs

    async def get_run_progress_for_saga(self, saga_tracker_id: str) -> list[Run]:
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            "SELECT tracker_id FROM run_progress WHERE saga_tracker_id = $1 "
            "AND tracker_connection_id = $2",
            saga_tracker_id,
            self.connection_id,
        )
        return await self._runs_for_rows(rows)

    async def get_run_by_session(self, session_id: str) -> Run | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT tracker_id FROM run_progress WHERE session_id = $1 "
            "AND tracker_connection_id = $2",
            session_id,
            self.connection_id,
        )
        return await self.get_run(row["tracker_id"]) if row else None

    async def list_runs_by_status(self, status: RunStatus) -> list[Run]:
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            "SELECT tracker_id FROM run_progress WHERE status = $1 "
            "AND tracker_connection_id = $2 ORDER BY updated_at",
            status.value,
            self.connection_id,
        )
        return await self._runs_for_rows(rows)

    async def get_run_by_id(self, run_id: UUID) -> Run | None:
        if self._pool is None:
            return None
        rows = await self._pool.fetch(
            "SELECT tracker_id FROM run_progress WHERE tracker_connection_id = $1",
            self.connection_id,
        )
        for row in rows:
            if uuid5(UUID(int=0), row["tracker_id"]) == run_id:
                return await self.get_run(row["tracker_id"])
        return None

    async def all_runs_merged(self, phase_tracker_id: str) -> bool:
        if self._pool is None:
            return False
        row = await self._pool.fetchrow(
            """
            SELECT count(*) FILTER (WHERE status != 'MERGED') AS remaining,
                   count(*) AS total
            FROM run_progress WHERE phase_tracker_id = $1 AND tracker_connection_id = $2
            """,
            phase_tracker_id,
            self.connection_id,
        )
        return bool(row and row["total"] and row["remaining"] == 0)

    async def list_phases_for_saga(self, saga_tracker_id: str) -> list[Phase]:
        if self._pool is not None:
            rows = await self._pool.fetch(
                """
                SELECT p.* FROM phases p JOIN sagas s ON s.id = p.saga_id
                WHERE s.tracker_id = $1 AND s.tracker_connection_id = $2 ORDER BY p.number
                """,
                saga_tracker_id,
                self.connection_id,
            )
            if rows:
                return [self._row_to_phase(row) for row in rows]
        milestones = await self.list_milestones(saga_tracker_id)
        return [
            Phase(
                id=uuid5(UUID(int=0), f"jira-version:{item.id}"),
                saga_id=UUID(int=0),
                tracker_id=item.id,
                number=item.sort_order,
                name=item.name,
                status=PhaseStatus.COMPLETE if item.progress >= 1 else PhaseStatus.PENDING,
            )
            for item in milestones
        ]

    async def update_phase_status(self, phase_tracker_id: str, status: PhaseStatus) -> Phase | None:
        if self._pool is not None:
            row = await self._pool.fetchrow(
                "UPDATE phases SET status = $2 WHERE tracker_id = $1 RETURNING *",
                phase_tracker_id,
                status.value,
            )
            if row is not None:
                return self._row_to_phase(row)
        if status == PhaseStatus.COMPLETE:
            await self._request(
                "PUT", f"/version/{quote(phase_tracker_id, safe='')}", json={"released": True}
            )
        return await self.get_phase(phase_tracker_id)

    async def get_saga_for_run(self, tracker_id: str) -> Saga | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT saga_tracker_id FROM run_progress WHERE tracker_id = $1 "
            "AND tracker_connection_id = $2",
            tracker_id,
            self.connection_id,
        )
        if not row or not row["saga_tracker_id"]:
            return None
        return await self.get_saga(row["saga_tracker_id"])

    async def get_phase_for_run(self, tracker_id: str) -> Phase | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT phase_tracker_id FROM run_progress WHERE tracker_id = $1 "
            "AND tracker_connection_id = $2",
            tracker_id,
            self.connection_id,
        )
        if not row or not row["phase_tracker_id"]:
            return None
        return await self.get_phase(row["phase_tracker_id"])

    async def get_owner_for_run(self, tracker_id: str) -> str | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT owner_id FROM run_progress WHERE tracker_id = $1 "
            "AND tracker_connection_id = $2",
            tracker_id,
            self.connection_id,
        )
        return (row["owner_id"] or None) if row else None

    async def save_session_message(self, message: SessionMessage) -> None:
        if self._pool is None:
            raise RuntimeError("pool is required for save_session_message")
        tracker_id = str(message.run_id)
        rows = await self._pool.fetch(
            "SELECT tracker_id FROM run_progress WHERE tracker_connection_id = $1",
            self.connection_id,
        )
        for row in rows:
            if uuid5(UUID(int=0), row["tracker_id"]) == message.run_id:
                tracker_id = row["tracker_id"]
                break
        await self._pool.execute(
            """
            INSERT INTO run_session_messages
                (id, run_id, tracker_id, session_id, content, sender, created_at,
                 tracker_connection_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            message.id,
            message.run_id,
            tracker_id,
            message.session_id,
            message.content,
            message.sender,
            message.created_at,
            self.connection_id,
        )

    async def get_session_messages(self, tracker_id: str) -> list[SessionMessage]:
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            "SELECT * FROM run_session_messages WHERE tracker_id = $1 "
            "AND tracker_connection_id = $2 ORDER BY created_at",
            tracker_id,
            self.connection_id,
        )
        return [
            SessionMessage(
                id=row["id"],
                run_id=row["run_id"],
                session_id=row["session_id"],
                content=row["content"],
                sender=row["sender"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def _fetch_progress(self, tracker_id: str) -> dict[str, Any] | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow(
            "SELECT * FROM run_progress WHERE tracker_id = $1 AND tracker_connection_id = $2",
            tracker_id,
            self.connection_id,
        )
        return dict(row) if row else None

    async def close(self) -> None:
        await self._client.aclose()

    # -- Conversion helpers -----------------------------------------------------

    async def _node_to_project(self, node: dict[str, Any]) -> TrackerProject:
        key = str(node.get("key") or node.get("id") or "")
        versions = await self._project_versions(key)
        issue_count = await self._issue_count(key)
        released = sum(1 for version in versions if version.get("released"))
        return TrackerProject(
            id=key,
            name=str(node.get("name") or key),
            description=self._plain_text(node.get("description")),
            status="archived" if node.get("archived") else "active",
            url=f"{self._site_url}/plugins/servlet/project-config/{quote(key, safe='')}",
            milestone_count=len(versions),
            issue_count=issue_count,
            slug=key.lower(),
            progress=(released / len(versions)) if versions else 0.0,
        )

    @classmethod
    def _version_to_milestone(
        cls, node: dict[str, Any], project_id: str, index: int
    ) -> TrackerMilestone:
        return TrackerMilestone(
            id=str(node["id"]),
            project_id=project_id,
            name=str(node.get("name") or ""),
            description=cls._plain_text(node.get("description")),
            sort_order=index,
            progress=1.0 if node.get("released") else 0.0,
            target_date=node.get("releaseDate"),
        )

    def _node_to_issue(self, node: dict[str, Any]) -> TrackerIssue:
        fields = node.get("fields") or {}
        status = fields.get("status") or {}
        priority = fields.get("priority") or {}
        versions = fields.get("fixVersions") or []
        estimate_seconds = fields.get("timeoriginalestimate")
        key = str(node.get("key") or "")
        priority_name = str(priority.get("name") or "")
        return TrackerIssue(
            id=key or str(node.get("id") or ""),
            identifier=key,
            title=str(fields.get("summary") or ""),
            description=self._plain_text(fields.get("description")),
            status=str(status.get("name") or "Unknown"),
            status_type=self._status_type(status),
            assignee=(fields.get("assignee") or {}).get("displayName"),
            labels=[str(label) for label in fields.get("labels") or []],
            priority=_PRIORITY_MAP.get(priority_name.casefold(), 0),
            priority_label=priority_name,
            estimate=(float(estimate_seconds) / 3600.0) if estimate_seconds is not None else None,
            url=f"{self._site_url}/browse/{quote(key, safe='')}",
            milestone_id=str(versions[0]["id"]) if versions else None,
        )

    @classmethod
    def _node_to_saga(cls, node: dict[str, Any]) -> Saga:
        key = str(node.get("key") or node.get("id") or "")
        return Saga(
            id=uuid5(UUID(int=0), f"jira-project:{key}"),
            tracker_id=key,
            tracker_type="jira",
            slug=key.lower(),
            name=str(node.get("name") or key),
            repos=[],
            feature_branch=f"feat/{key.lower()}",
            status=SagaStatus.COMPLETE if node.get("archived") else SagaStatus.ACTIVE,
            created_at=datetime.now(UTC),
            base_branch="",
        )

    @classmethod
    def _version_to_phase(cls, node: dict[str, Any]) -> Phase:
        tracker_id = str(node["id"])
        return Phase(
            id=uuid5(UUID(int=0), f"jira-version:{tracker_id}"),
            saga_id=UUID(int=0),
            tracker_id=tracker_id,
            number=0,
            name=str(node.get("name") or ""),
            status=PhaseStatus.COMPLETE if node.get("released") else PhaseStatus.PENDING,
        )

    def _node_to_run(self, node: dict[str, Any], *, progress: dict[str, Any] | None = None) -> Run:
        issue = self._node_to_issue(node)
        now = datetime.now(UTC)
        run_status = self._run_status(issue.status_type, issue.status)
        if progress and progress.get("status"):
            run_status = RunStatus(progress["status"])
        return Run(
            id=uuid5(UUID(int=0), issue.id),
            phase_id=UUID(int=0),
            tracker_id=issue.id,
            identifier=issue.identifier,
            url=issue.url,
            name=issue.title,
            description=issue.description,
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=issue.estimate,
            status=run_status,
            session_id=progress.get("session_id") if progress else None,
            branch=None,
            chronicle_summary=progress.get("chronicle_summary") if progress else None,
            pr_url=progress.get("pr_url") if progress else None,
            pr_id=progress.get("pr_id") if progress else None,
            retry_count=int(progress.get("retry_count") or 0) if progress else 0,
            created_at=now,
            updated_at=now,
            reviewer_session_id=progress.get("reviewer_session_id") if progress else None,
            review_round=int(progress.get("review_round") or 0) if progress else 0,
        )

    @staticmethod
    def _row_to_phase(row: asyncpg.Record) -> Phase:
        return Phase(
            id=row["id"],
            saga_id=row["saga_id"],
            tracker_id=row["tracker_id"],
            number=row["number"],
            name=row["name"],
            status=PhaseStatus(row["status"]),
        )

    @staticmethod
    def _status_type(status: dict[str, Any]) -> str:
        category = status.get("statusCategory") or {}
        key = str(category.get("key") or "").casefold()
        name = str(status.get("name") or "").strip().casefold()
        if key == "done":
            return "completed"
        if key == "indeterminate":
            return "started"
        if name in {"blocked", "on hold", "impediment"}:
            return "blocked"
        return "unstarted"

    @staticmethod
    def _run_status(status_type: str, status_name: str) -> RunStatus:
        if status_type == "completed":
            if status_name.casefold() in {"cancelled", "canceled", "won't do"}:
                return RunStatus.FAILED
            return RunStatus.MERGED
        if status_type == "started":
            if "review" in status_name.casefold():
                return RunStatus.REVIEW
            return RunStatus.RUNNING
        return RunStatus.PENDING

    @staticmethod
    def _adf(text: str) -> dict[str, Any]:
        paragraphs = []
        for line in text.splitlines() or [""]:
            content = [{"type": "text", "text": line}] if line else []
            paragraphs.append({"type": "paragraph", "content": content})
        return {"version": 1, "type": "doc", "content": paragraphs}

    @classmethod
    def _plain_text(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(part for item in value if (part := cls._plain_text(item)))
        if isinstance(value, dict):
            if value.get("type") == "text":
                return str(value.get("text") or "")
            parts = [cls._plain_text(item) for item in value.get("content") or []]
            block_types = {"doc", "paragraph", "bulletList", "listItem"}
            separator = "\n" if value.get("type") in block_types else ""
            return separator.join(part for part in parts if part)
        return str(value)
