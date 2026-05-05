"""Platform tools — Ravn tools for interacting with the Niuu platform.

These tools let the Ravn agent create/manage Forge sessions, perform git
operations, decompose work into Tyr sagas, and track issues via the shared
tracker routes.

All tools use the mounted platform APIs rather than direct imports,
preserving module boundaries while allowing route ownership to evolve.
"""

from __future__ import annotations

import logging

import httpx

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION_PLATFORM = "platform:api"

_DEFAULT_BASE_URL = "http://localhost:8080"
_DEFAULT_TIMEOUT = 30.0
_FORGE_SESSIONS_PATH = "/api/v1/forge/sessions"
_FORGE_REPOS_PATH = "/api/v1/forge/repos"
_NIUU_REPOS_PATH = "/api/v1/niuu/repos"
_TRACKER_ISSUES_PATH = "/api/v1/tracker/issues"


def _client(base_url: str, timeout: float, pat_token: str = "") -> httpx.AsyncClient:
    headers: dict[str, str] = {}
    if pat_token:
        headers["Authorization"] = f"Bearer {pat_token}"
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, headers=headers)


def _ok(data: object) -> ToolResult:
    import json

    return ToolResult(tool_call_id="", content=json.dumps(data, default=str))


def _err(message: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=message, is_error=True)


# ---------------------------------------------------------------------------
# volundr_session
# ---------------------------------------------------------------------------


class VolundrSessionTool(ToolPort):
    """Create, list, and stop Volundr coding sessions.

    Actions:
    - ``list``   — return all sessions (optionally filtered by status).
    - ``create`` — start a new session (requires ``name``).
    - ``stop``   — stop a session (requires ``session_id``).
    - ``delete`` — delete a session (requires ``session_id``).
    - ``get``    — get session details (requires ``session_id``).
    - ``start``  — start a stopped session (requires ``session_id``).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        pat_token: str = "",
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._pat_token = pat_token

    @property
    def name(self) -> str:
        return "volundr_session"

    @property
    def description(self) -> str:
        return (
            "Manage Volundr coding sessions. "
            "Actions: list, create (name required), get (session_id required), "
            "start (session_id required), stop (session_id required), "
            "delete (session_id required)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "create", "get", "start", "stop", "delete"],
                    "description": "Operation to perform.",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Session name (required for create). "
                        "RFC 1123: lowercase alphanumeric and hyphens, 1-63 chars."
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": "Session UUID (required for get, start, stop, delete).",
                },
                "model": {
                    "type": "string",
                    "description": "LLM model ID for the session (optional, for create).",
                },
                "system_prompt": {
                    "type": "string",
                    "description": (
                        "System prompt appended to Claude's instructions (optional, for create)."
                    ),
                },
                "initial_prompt": {
                    "type": "string",
                    "description": "Initial user message to send (optional, for create).",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status (optional, for list).",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_PLATFORM

    async def execute(self, input: dict) -> ToolResult:
        action = input.get("action", "")
        async with _client(self._base_url, self._timeout, self._pat_token) as client:
            match action:
                case "list":
                    return await self._list(client, input)
                case "create":
                    return await self._create(client, input)
                case "get":
                    return await self._get(client, input.get("session_id", ""))
                case "start":
                    return await self._start(client, input.get("session_id", ""))
                case "stop":
                    return await self._stop(client, input.get("session_id", ""))
                case "delete":
                    return await self._delete(client, input.get("session_id", ""))
                case _:
                    return _err(f"Unknown action: {action!r}")

    async def _list(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        try:
            params: dict[str, str] = {}
            if status := input.get("status"):
                params["status"] = status
            resp = await client.get(_FORGE_SESSIONS_PATH, params=params or None)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list sessions: {exc}")

    async def _create(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        name = input.get("name", "")
        if not name:
            return _err("session name is required for create action")
        body: dict = {"name": name}
        for key in ("model", "system_prompt", "initial_prompt"):
            if value := input.get(key):
                body[key] = value
        try:
            resp = await client.post(_FORGE_SESSIONS_PATH, json=body)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to create session: {exc}")

    async def _get(self, client: httpx.AsyncClient, session_id: str) -> ToolResult:
        if not session_id:
            return _err("session_id is required for get action")
        try:
            resp = await client.get(f"{_FORGE_SESSIONS_PATH}/{session_id}")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to get session {session_id}: {exc}")

    async def _start(self, client: httpx.AsyncClient, session_id: str) -> ToolResult:
        if not session_id:
            return _err("session_id is required for start action")
        try:
            resp = await client.post(f"{_FORGE_SESSIONS_PATH}/{session_id}/start")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to start session {session_id}: {exc}")

    async def _stop(self, client: httpx.AsyncClient, session_id: str) -> ToolResult:
        if not session_id:
            return _err("session_id is required for stop action")
        try:
            resp = await client.post(f"{_FORGE_SESSIONS_PATH}/{session_id}/stop")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to stop session {session_id}: {exc}")

    async def _delete(self, client: httpx.AsyncClient, session_id: str) -> ToolResult:
        if not session_id:
            return _err("session_id is required for delete action")
        try:
            resp = await client.delete(f"{_FORGE_SESSIONS_PATH}/{session_id}")
            resp.raise_for_status()
            return _ok({"session_id": session_id, "status": "deleted"})
        except Exception as exc:
            return _err(f"Failed to delete session {session_id}: {exc}")


# ---------------------------------------------------------------------------
# volundr_git
# ---------------------------------------------------------------------------


class VolundrGitTool(ToolPort):
    """Perform git operations via the Volundr API.

    Actions:
    - ``list_branches`` — list branches for a repo (requires ``repo_url``).
    - ``create_pr``     — open a pull request (requires ``session_id``, ``title``).
    - ``list_prs``      — list pull requests (requires ``repo_url``).
    - ``get_pr``        — get PR details (requires ``pr_number``, ``repo_url``).
    - ``merge_pr``      — merge a pull request (requires ``pr_number``, ``repo_url``).
    - ``ci_status``     — get CI status (requires ``pr_number``, ``repo_url``, ``branch``).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        pat_token: str = "",
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._pat_token = pat_token

    @property
    def name(self) -> str:
        return "volundr_git"

    @property
    def description(self) -> str:
        return (
            "Git operations via Volundr: list_branches (repo_url), "
            "create_pr (session_id + title), list_prs (repo_url), "
            "get_pr (pr_number + repo_url), merge_pr (pr_number + repo_url), "
            "ci_status (pr_number + repo_url + branch)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_branches",
                        "create_pr",
                        "list_prs",
                        "get_pr",
                        "merge_pr",
                        "ci_status",
                    ],
                    "description": "Operation to perform.",
                },
                "repo_url": {
                    "type": "string",
                    "description": "Repository URL.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session UUID (required for create_pr).",
                },
                "pr_number": {
                    "type": "integer",
                    "description": (
                        "Pull request number (required for get_pr, merge_pr, ci_status)."
                    ),
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name (required for ci_status).",
                },
                "title": {
                    "type": "string",
                    "description": "Pull request title (for create_pr, auto-generated if omitted).",
                },
                "target_branch": {
                    "type": "string",
                    "description": "Target branch for PR (default: main).",
                },
                "merge_method": {
                    "type": "string",
                    "enum": ["merge", "squash", "rebase"],
                    "description": "Merge method (default: squash).",
                },
                "status": {
                    "type": "string",
                    "enum": ["open", "closed", "merged", "all"],
                    "description": "PR status filter for list_prs (default: open).",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_PLATFORM

    async def execute(self, input: dict) -> ToolResult:
        action = input.get("action", "")
        async with _client(self._base_url, self._timeout, self._pat_token) as client:
            match action:
                case "list_branches":
                    return await self._list_branches(client, input)
                case "create_pr":
                    return await self._create_pr(client, input)
                case "list_prs":
                    return await self._list_prs(client, input)
                case "get_pr":
                    return await self._get_pr(client, input)
                case "merge_pr":
                    return await self._merge_pr(client, input)
                case "ci_status":
                    return await self._ci_status(client, input)
                case _:
                    return _err(f"Unknown action: {action!r}")

    async def _list_branches(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        repo_url = input.get("repo_url", "")
        if not repo_url:
            return _err("repo_url is required for list_branches")
        try:
            resp = await client.get(f"{_FORGE_REPOS_PATH}/branches", params={"repo_url": repo_url})
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list branches: {exc}")

    async def _create_pr(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        session_id = input.get("session_id", "")
        if not session_id:
            return _err("session_id is required for create_pr")
        body: dict = {"session_id": session_id}
        if title := input.get("title"):
            body["title"] = title
        if target := input.get("target_branch"):
            body["target_branch"] = target
        try:
            resp = await client.post(f"{_FORGE_REPOS_PATH}/prs", json=body)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to create PR: {exc}")

    async def _list_prs(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        repo_url = input.get("repo_url", "")
        if not repo_url:
            return _err("repo_url is required for list_prs")
        params: dict[str, str] = {"repo_url": repo_url}
        if status := input.get("status"):
            params["status"] = status
        try:
            resp = await client.get(f"{_FORGE_REPOS_PATH}/prs", params=params)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list PRs: {exc}")

    async def _get_pr(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        pr_number = input.get("pr_number")
        repo_url = input.get("repo_url", "")
        if not pr_number or not repo_url:
            return _err("pr_number and repo_url are required for get_pr")
        try:
            resp = await client.get(
                f"{_FORGE_REPOS_PATH}/prs/{pr_number}",
                params={"repo_url": repo_url},
            )
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to get PR #{pr_number}: {exc}")

    async def _merge_pr(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        pr_number = input.get("pr_number")
        repo_url = input.get("repo_url", "")
        if not pr_number or not repo_url:
            return _err("pr_number and repo_url are required for merge_pr")
        body: dict = {}
        if method := input.get("merge_method"):
            body["merge_method"] = method
        try:
            resp = await client.post(
                f"{_FORGE_REPOS_PATH}/prs/{pr_number}/merge",
                params={"repo_url": repo_url},
                json=body,
            )
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to merge PR #{pr_number}: {exc}")

    async def _ci_status(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        pr_number = input.get("pr_number")
        repo_url = input.get("repo_url", "")
        branch = input.get("branch", "")
        if not pr_number or not repo_url or not branch:
            return _err("pr_number, repo_url, and branch are required for ci_status")
        try:
            resp = await client.get(
                f"{_FORGE_REPOS_PATH}/prs/{pr_number}/ci",
                params={"repo_url": repo_url, "branch": branch},
            )
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to get CI status: {exc}")


# ---------------------------------------------------------------------------
# tyr_saga
# ---------------------------------------------------------------------------


class TyrSagaTool(ToolPort):
    """Decompose specs and manage Tyr sagas.

    Actions:
    - ``list``     — list active sagas.
    - ``get``      — get saga details (requires ``saga_id``).
    - ``commit``   — commit a fully structured saga (requires ``name``, ``slug``,
                     ``repos``, ``base_branch``, ``phases``).
    - ``dispatch`` — dispatch saga raids for execution (requires ``items`` array).
    - ``delete``   — delete a saga (requires ``saga_id``).
    - ``raids``    — list active raids across all sagas.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        pat_token: str = "",
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._pat_token = pat_token

    @property
    def name(self) -> str:
        return "tyr_saga"

    @property
    def description(self) -> str:
        return (
            "Manage Tyr sagas and raids: list, get (saga_id), "
            "commit (name + slug + repos + base_branch + phases), "
            "dispatch (items array with saga_id + issue_id + repo), "
            "delete (saga_id), raids (list active raids)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "commit", "dispatch", "delete", "raids"],
                    "description": "Operation to perform.",
                },
                "saga_id": {
                    "type": "string",
                    "description": "Saga UUID (required for get, delete).",
                },
                "name": {
                    "type": "string",
                    "description": "Saga name (required for commit).",
                },
                "slug": {
                    "type": "string",
                    "description": "Unique saga slug (required for commit).",
                },
                "description": {
                    "type": "string",
                    "description": "Saga description (optional for commit).",
                },
                "repos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Repositories in org/repo format (required for commit).",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Branch to base feature branch on (required for commit).",
                },
                "phases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "raids": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "acceptance_criteria": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "declared_files": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                    "required": ["name"],
                                },
                            },
                        },
                        "required": ["name", "raids"],
                    },
                    "description": "Phase/raid structure (required for commit).",
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "saga_id": {"type": "string"},
                            "issue_id": {"type": "string"},
                            "repo": {"type": "string"},
                        },
                        "required": ["saga_id", "issue_id", "repo"],
                    },
                    "description": "Dispatch items (required for dispatch).",
                },
                "model": {
                    "type": "string",
                    "description": "LLM model override for dispatch (optional).",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_PLATFORM

    async def execute(self, input: dict) -> ToolResult:
        action = input.get("action", "")
        async with _client(self._base_url, self._timeout, self._pat_token) as client:
            match action:
                case "list":
                    return await self._list(client)
                case "get":
                    return await self._get(client, input.get("saga_id", ""))
                case "commit":
                    return await self._commit(client, input)
                case "dispatch":
                    return await self._dispatch(client, input)
                case "delete":
                    return await self._delete(client, input.get("saga_id", ""))
                case "raids":
                    return await self._raids(client)
                case _:
                    return _err(f"Unknown action: {action!r}")

    async def _list(self, client: httpx.AsyncClient) -> ToolResult:
        try:
            resp = await client.get("/api/v1/tyr/sagas")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list sagas: {exc}")

    async def _get(self, client: httpx.AsyncClient, saga_id: str) -> ToolResult:
        if not saga_id:
            return _err("saga_id is required for get action")
        try:
            resp = await client.get(f"/api/v1/tyr/sagas/{saga_id}")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to get saga {saga_id}: {exc}")

    async def _commit(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        name = input.get("name", "")
        slug = input.get("slug", "")
        repos = input.get("repos", [])
        base_branch = input.get("base_branch", "")
        phases = input.get("phases", [])
        if not name or not slug or not repos or not base_branch or not phases:
            return _err("name, slug, repos, base_branch, and phases are required for commit")
        body: dict = {
            "name": name,
            "slug": slug,
            "repos": repos,
            "base_branch": base_branch,
            "phases": phases,
        }
        if desc := input.get("description"):
            body["description"] = desc
        try:
            resp = await client.post("/api/v1/tyr/sagas/commit", json=body)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to commit saga: {exc}")

    async def _dispatch(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        items = input.get("items", [])
        if not items:
            return _err("items array is required for dispatch action")
        body: dict = {"items": items}
        if model := input.get("model"):
            body["model"] = model
        try:
            resp = await client.post("/api/v1/tyr/dispatch/approve", json=body)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to dispatch: {exc}")

    async def _delete(self, client: httpx.AsyncClient, saga_id: str) -> ToolResult:
        if not saga_id:
            return _err("saga_id is required for delete action")
        try:
            resp = await client.delete(f"/api/v1/tyr/sagas/{saga_id}")
            resp.raise_for_status()
            return _ok({"saga_id": saga_id, "status": "deleted"})
        except Exception as exc:
            return _err(f"Failed to delete saga {saga_id}: {exc}")

    async def _raids(self, client: httpx.AsyncClient) -> ToolResult:
        try:
            resp = await client.get("/api/v1/tyr/raids/active")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list active raids: {exc}")


# ---------------------------------------------------------------------------
# tracker_issue
# ---------------------------------------------------------------------------


class TrackerIssueTool(ToolPort):
    """Search, view, and update issues via Volundr's tracker integration.

    Actions:
    - ``search``        — search issues across connected trackers (requires ``query``).
    - ``get``           — get issue details (requires ``issue_id``).
    - ``update_status`` — update issue status (requires ``issue_id``, ``status``).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        pat_token: str = "",
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._pat_token = pat_token

    @property
    def name(self) -> str:
        return "tracker_issue"

    @property
    def description(self) -> str:
        return (
            "Search, view, and update issues in connected trackers (Linear, Jira, etc.) "
            "via Volundr. Actions: search (query), get (issue_id), "
            "update_status (issue_id + status)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "get", "update_status"],
                    "description": "Operation to perform.",
                },
                "issue_id": {
                    "type": "string",
                    "description": "Issue identifier (required for get, update_status).",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (required for search).",
                },
                "status": {
                    "type": "string",
                    "description": "New status value (required for update_status).",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_PLATFORM

    async def execute(self, input: dict) -> ToolResult:
        action = input.get("action", "")
        async with _client(self._base_url, self._timeout, self._pat_token) as client:
            match action:
                case "search":
                    return await self._search(client, input.get("query", ""))
                case "get":
                    return await self._get(client, input.get("issue_id", ""))
                case "update_status":
                    return await self._update_status(
                        client, input.get("issue_id", ""), input.get("status", "")
                    )
                case _:
                    return _err(f"Unknown action: {action!r}")

    async def _search(self, client: httpx.AsyncClient, query: str) -> ToolResult:
        if not query:
            return _err("query is required for search action")
        try:
            resp = await client.get(_TRACKER_ISSUES_PATH, params={"q": query})
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to search issues: {exc}")

    async def _get(self, client: httpx.AsyncClient, issue_id: str) -> ToolResult:
        if not issue_id:
            return _err("issue_id is required for get action")
        try:
            resp = await client.get(f"{_TRACKER_ISSUES_PATH}/{issue_id}")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to get issue {issue_id}: {exc}")

    async def _update_status(
        self, client: httpx.AsyncClient, issue_id: str, status: str
    ) -> ToolResult:
        if not issue_id or not status:
            return _err("issue_id and status are required for update_status")
        try:
            resp = await client.post(
                f"{_TRACKER_ISSUES_PATH}/{issue_id}",
                json={"status": status},
            )
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to update issue {issue_id}: {exc}")
