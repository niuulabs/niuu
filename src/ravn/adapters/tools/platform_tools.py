"""Platform tools — Ravn tools for interacting with the Niuu platform.

These tools let the Ravn agent create/manage Forge sessions, perform git
operations, decompose work into Ting sagas, and track issues via the shared
tracker routes.

All tools use the mounted platform APIs rather than direct imports,
preserving module boundaries while allowing route ownership to evolve.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION_PLATFORM = "platform:api"

_DEFAULT_BASE_URL = "http://localhost:8080"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_WORKLOAD_TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_DEFAULT_WORKLOAD_AUDIENCES = ["volundr-api", "forge", "ting", "mimir", "guild"]
_FORGE_SESSIONS_PATH = "/api/v1/forge/sessions"
_FORGE_REPOS_PATH = "/api/v1/forge/repos"
_TRACKER_ISSUES_PATH = "/api/v1/tracker/issues"
_TING_WORKFLOWS_PATH = "/api/v1/ting/workflows"


async def _client(
    base_url: str,
    timeout: float,
    pat_token: str = "",
    *,
    workload_token_file: str = _DEFAULT_WORKLOAD_TOKEN_FILE,
    exchange_url: str = "",
    audiences: list[str] | None = None,
) -> httpx.AsyncClient:
    headers: dict[str, str] = {}
    if pat_token:
        headers["Authorization"] = f"Bearer {pat_token}"
    else:
        token = await _exchange_workload_token(
            base_url,
            timeout,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, headers=headers)


async def _exchange_workload_token(
    base_url: str,
    timeout: float,
    *,
    workload_token_file: str,
    exchange_url: str,
    audiences: list[str] | None,
) -> str:
    token_path = Path(workload_token_file).expanduser()
    if not token_path.exists():
        return ""
    proof = token_path.read_text(encoding="utf-8").strip()
    if not proof:
        return ""
    url = exchange_url.rstrip("/") or f"{base_url.rstrip('/')}/api/v1/tokens/workload/exchange"
    body = {"token": proof, "audiences": audiences or _DEFAULT_WORKLOAD_AUDIENCES}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=body)
        response.raise_for_status()
        payload = response.json()
    token = str(payload.get("token") or "")
    if not token:
        raise RuntimeError("workload token exchange response did not include token")
    return token


class _PlatformAuthMixin:
    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        pat_token: str = "",
        workload_token_file: str = _DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        # Every platform tool takes the same auth kwargs verbatim; the shared
        # constructor stops each new tool from re-typing the boilerplate.
        self._set_platform_auth(
            base_url=base_url,
            timeout=timeout,
            pat_token=pat_token,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )

    def _set_platform_auth(
        self,
        *,
        base_url: str,
        timeout: float,
        pat_token: str,
        workload_token_file: str = _DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._pat_token = pat_token
        self._workload_token_file = workload_token_file
        self._exchange_url = exchange_url
        self._audiences = audiences or _DEFAULT_WORKLOAD_AUDIENCES

    async def _client(self) -> httpx.AsyncClient:
        return await _client(
            self._base_url,
            self._timeout,
            self._pat_token,
            workload_token_file=self._workload_token_file,
            exchange_url=self._exchange_url,
            audiences=self._audiences,
        )


def _ok(data: object) -> ToolResult:
    import json

    return ToolResult(tool_call_id="", content=json.dumps(data, default=str))


def _err(message: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=message, is_error=True)


# ---------------------------------------------------------------------------
# volundr_session
# ---------------------------------------------------------------------------


class VolundrSessionTool(_PlatformAuthMixin, ToolPort):
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
        workload_token_file: str = _DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        self._set_platform_auth(
            base_url=base_url,
            timeout=timeout,
            pat_token=pat_token,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )

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
        async with await self._client() as client:
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
        raise AssertionError("Unreachable execute fallthrough")

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


class VolundrGitTool(_PlatformAuthMixin, ToolPort):
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
        workload_token_file: str = _DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        self._set_platform_auth(
            base_url=base_url,
            timeout=timeout,
            pat_token=pat_token,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )

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
        async with await self._client() as client:
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
        raise AssertionError("Unreachable execute fallthrough")

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
# ting_saga
# ---------------------------------------------------------------------------


class TingSagaTool(_PlatformAuthMixin, ToolPort):
    """Decompose specs and manage Ting sagas.

    Actions:
    - ``list``     — list active sagas.
    - ``get``      — get saga details (requires ``saga_id``).
    - ``commit``   — commit a fully structured saga (requires ``name``, ``slug``,
                     ``repos``, ``base_branch``, ``phases``).
    - ``dispatch`` — dispatch saga runs for execution (requires ``items`` array).
    - ``delete``   — delete a saga (requires ``saga_id``).
    - ``runs``    — list active runs across all sagas.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        pat_token: str = "",
        workload_token_file: str = _DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        self._set_platform_auth(
            base_url=base_url,
            timeout=timeout,
            pat_token=pat_token,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )

    @property
    def name(self) -> str:
        return "ting_saga"

    @property
    def description(self) -> str:
        return (
            "Manage Ting sagas and runs: list, get (saga_id), "
            "commit (name + slug + repos + base_branch + phases), "
            "dispatch (items array with saga_id + issue_id + repo), "
            "delete (saga_id), runs (list active runs)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "commit", "dispatch", "delete", "runs"],
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
                            "runs": {
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
                        "required": ["name", "runs"],
                    },
                    "description": "Phase/run structure (required for commit).",
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
        async with await self._client() as client:
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
                case "runs":
                    return await self._runs(client)
                case _:
                    return _err(f"Unknown action: {action!r}")
        raise AssertionError("Unreachable execute fallthrough")

    async def _list(self, client: httpx.AsyncClient) -> ToolResult:
        try:
            resp = await client.get("/api/v1/ting/sagas")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list sagas: {exc}")

    async def _get(self, client: httpx.AsyncClient, saga_id: str) -> ToolResult:
        if not saga_id:
            return _err("saga_id is required for get action")
        try:
            resp = await client.get(f"/api/v1/ting/sagas/{saga_id}")
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
            resp = await client.post("/api/v1/ting/sagas/commit", json=body)
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
            resp = await client.post("/api/v1/ting/dispatch/approve", json=body)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to dispatch: {exc}")

    async def _delete(self, client: httpx.AsyncClient, saga_id: str) -> ToolResult:
        if not saga_id:
            return _err("saga_id is required for delete action")
        try:
            resp = await client.delete(f"/api/v1/ting/sagas/{saga_id}")
            resp.raise_for_status()
            return _ok({"saga_id": saga_id, "status": "deleted"})
        except Exception as exc:
            return _err(f"Failed to delete saga {saga_id}: {exc}")

    async def _runs(self, client: httpx.AsyncClient) -> ToolResult:
        try:
            resp = await client.get("/api/v1/ting/runs/active")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list active runs: {exc}")


# ---------------------------------------------------------------------------
# ting_workflow
# ---------------------------------------------------------------------------


class TingWorkflowTool(_PlatformAuthMixin, ToolPort):
    """List, inspect, and launch Ting workflows.

    Actions:
    - ``list``   — list workflows visible to the current token.
    - ``get``    — get workflow details (requires ``workflow_id``).
    - ``launch`` — start a workflow-backed Volundr flock session.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        pat_token: str = "",
        workload_token_file: str = _DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
        workflow_aliases: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._set_platform_auth(
            base_url=base_url,
            timeout=timeout,
            pat_token=pat_token,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )
        self._workflow_aliases = {
            str(name).strip().lower(): dict(config or {})
            for name, config in (workflow_aliases or {}).items()
            if str(name).strip()
        }

    @property
    def name(self) -> str:
        return "ting_workflow"

    @property
    def description(self) -> str:
        return (
            "List, inspect, and launch Ting workflows. "
            "Use list to discover available workflows, get to inspect one in detail, "
            "and launch to start a workflow-backed Volundr flock session without "
            "creating a saga first."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "launch"],
                    "description": "Operation to perform.",
                },
                "workflow_id": {
                    "type": "string",
                    "description": (
                        "Workflow UUID (required for get; optional for launch with alias)."
                    ),
                },
                "workflow_alias": {
                    "type": "string",
                    "description": (
                        "Configured workflow alias for launch, such as research or planning. "
                        "The alias can provide workflow_id/name/tags and launch defaults."
                    ),
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "system", "user"],
                    "description": "Optional workflow scope filter for list (default: all).",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Launch prompt describing the work to do (required for launch). "
                        "For workflow_alias=research, pass the operator's actual "
                        "research question or brief, not a generic launch phrase."
                    ),
                },
                "slug": {
                    "type": "string",
                    "description": "Optional slug for the launched workflow run.",
                },
                "session_name": {
                    "type": "string",
                    "description": "Optional explicit Volundr session name for launch.",
                },
                "context": {
                    "type": "object",
                    "description": (
                        "Optional structured workflow launch context. "
                        "Use this for workflow-specific inputs without "
                        "changing the generic launch API."
                    ),
                },
                "repo": {
                    "type": "string",
                    "description": "Optional repo URL or org/repo to mount in the session.",
                },
                "branch": {
                    "type": "string",
                    "description": "Optional repo branch for the session (default: main).",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Optional base branch override for the session.",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override.",
                },
                "definition": {
                    "type": "string",
                    "description": "Optional Volundr session definition override.",
                },
                "launch_spec": {
                    "type": "string",
                    "description": "Optional launch spec name override for the launched session.",
                },
                "integration_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional integration IDs to attach to the session.",
                },
                "connection_id": {
                    "type": "string",
                    "description": "Optional explicit Volundr connection target.",
                },
                "mimir_path": {
                    "type": "string",
                    "description": "Optional Mimir path override for workflow artifacts.",
                },
                "provenance": {
                    "type": "object",
                    "description": (
                        "Correlation metadata carried through the launch "
                        "(e.g. initiative slug, resident_peer_id) so "
                        "completion events route back to you."
                    ),
                },
                "gate_auto_forward_after": {
                    "type": "string",
                    "description": (
                        "Override every gate's autoForwardAfter for this "
                        "launch. Pass an empty string to disable auto-forward "
                        "so approvals wait for the human."
                    ),
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_PLATFORM

    async def execute(self, input: dict) -> ToolResult:
        action = input.get("action", "")
        async with await self._client() as client:
            match action:
                case "list":
                    return await self._list(client, input)
                case "get":
                    return await self._get(client, input.get("workflow_id", ""))
                case "launch":
                    return await self._launch(client, input)
                case _:
                    return _err(f"Unknown action: {action!r}")
        raise AssertionError("Unreachable execute fallthrough")

    async def _list(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        params: dict[str, str] = {}
        if scope := str(input.get("scope", "") or "").strip():
            params["scope"] = scope
        try:
            resp = await client.get(_TING_WORKFLOWS_PATH, params=params or None)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list workflows: {exc}")

    async def _get(self, client: httpx.AsyncClient, workflow_id: str) -> ToolResult:
        if not workflow_id:
            return _err("workflow_id is required for get action")
        try:
            resp = await client.get(f"{_TING_WORKFLOWS_PATH}/{workflow_id}")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to get workflow {workflow_id}: {exc}")

    async def _launch(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        launch_input = await self._launch_input_with_alias(client, input)
        if isinstance(launch_input, ToolResult):
            return launch_input

        workflow_id = str(launch_input.get("workflow_id", "") or "").strip()
        prompt = str(launch_input.get("prompt", "") or "").strip()
        if not workflow_id:
            return _err("workflow_id or workflow_alias is required for launch action")
        if not prompt:
            return _err("prompt is required for launch action")

        body: dict[str, object] = {"prompt": prompt}
        scalar_keys = (
            "slug",
            "session_name",
            "repo",
            "branch",
            "base_branch",
            "model",
            "definition",
            "launch_spec",
            "connection_id",
            "mimir_path",
        )
        for key in scalar_keys:
            value = launch_input.get(key)
            if value not in (None, ""):
                body[key] = value

        if integration_ids := launch_input.get("integration_ids"):
            body["integration_ids"] = integration_ids
        if context := launch_input.get("context"):
            body["context"] = context
        if provenance := launch_input.get("provenance"):
            body["provenance"] = provenance
        # Distinct from the scalar loop: an empty string is meaningful here
        # (it disables gate auto-forward so approvals wait for the human).
        gate_auto_forward = launch_input.get("gate_auto_forward_after")
        if gate_auto_forward is not None:
            body["gateAutoForwardAfter"] = str(gate_auto_forward)

        try:
            resp = await client.post(f"{_TING_WORKFLOWS_PATH}/{workflow_id}/launch", json=body)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to launch workflow {workflow_id}: {exc}")

    async def _launch_input_with_alias(
        self, client: httpx.AsyncClient, input: dict
    ) -> dict[str, Any] | ToolResult:
        alias_name = str(input.get("workflow_alias") or input.get("alias") or "").strip()
        if not alias_name:
            return input

        alias = self._workflow_aliases.get(alias_name.lower())
        if alias is None:
            return _err(f"workflow_alias {alias_name!r} is not configured")

        defaults = alias.get("defaults") or {}
        if not isinstance(defaults, dict):
            return _err(f"workflow_alias {alias_name!r} defaults must be an object")

        launch_input = {**defaults, **input}
        self._apply_alias_input_conventions(alias_name, launch_input)
        if not str(launch_input.get("workflow_id", "") or "").strip():
            workflow_id, error = await self._resolve_workflow_alias(client, alias_name, alias)
            if error:
                return _err(error)
            launch_input["workflow_id"] = workflow_id
        return launch_input

    def _apply_alias_input_conventions(
        self,
        alias_name: str,
        launch_input: dict[str, Any],
    ) -> None:
        if alias_name.strip().lower() != "research":
            return

        prompt = str(launch_input.get("prompt", "") or "").strip()
        if not prompt:
            return

        context = launch_input.get("context")
        if not isinstance(context, dict):
            context = {}
        else:
            context = dict(context)
        context.setdefault("question", prompt)
        context.setdefault("mode", "exploratory")
        launch_input["context"] = context

    async def _resolve_workflow_alias(
        self, client: httpx.AsyncClient, alias_name: str, alias: dict[str, Any]
    ) -> tuple[str, str]:
        workflow_id = str(alias.get("workflow_id", "") or "").strip()
        if workflow_id:
            return workflow_id, ""

        name = str(alias.get("name", "") or "").strip()
        tags = [str(tag).strip() for tag in alias.get("tags") or [] if str(tag).strip()]
        if not name and not tags:
            return "", f"workflow_alias {alias_name!r} needs workflow_id, name, or tags"

        params: dict[str, str] = {}
        if scope := str(alias.get("scope", "") or "").strip():
            params["scope"] = scope
        try:
            resp = await client.get(_TING_WORKFLOWS_PATH, params=params or None)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            return "", f"Failed to resolve workflow_alias {alias_name!r}: {exc}"

        workflows = _workflow_list(payload)
        matches = [
            workflow
            for workflow in workflows
            if _workflow_matches_alias(workflow, name=name, tags=tags)
        ]
        if not matches:
            return "", f"workflow_alias {alias_name!r} did not match any workflows"
        if len(matches) > 1:
            names = ", ".join(
                str(item.get("name") or item.get("id") or "") for item in matches[:3]
            )
            return "", f"workflow_alias {alias_name!r} matched multiple workflows: {names}"

        resolved = str(matches[0].get("id") or matches[0].get("workflow_id") or "").strip()
        if not resolved:
            return "", f"workflow_alias {alias_name!r} matched a workflow without an id"
        return resolved, ""


def _workflow_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("workflows", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _workflow_matches_alias(workflow: dict[str, Any], *, name: str, tags: list[str]) -> bool:
    if name:
        workflow_names = {
            str(workflow.get("name") or "").strip().lower(),
            str(workflow.get("id") or "").strip().lower(),
            str(workflow.get("workflow_id") or "").strip().lower(),
        }
        if name.lower() not in workflow_names:
            return False
    if tags:
        workflow_tags = {tag.lower() for tag in _workflow_tags(workflow)}
        if not {tag.lower() for tag in tags}.issubset(workflow_tags):
            return False
    return True


def _workflow_tags(workflow: dict[str, Any]) -> list[str]:
    candidates: list[Any] = [workflow.get("tags")]
    for parent in ("graph", "metadata"):
        value = workflow.get(parent)
        if isinstance(value, dict):
            candidates.append(value.get("tags"))
    for value in candidates:
        if isinstance(value, list):
            return [str(tag) for tag in value if str(tag).strip()]
    return []


# ---------------------------------------------------------------------------
# tracker_issue
# ---------------------------------------------------------------------------


class TrackerIssueTool(_PlatformAuthMixin, ToolPort):
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
        workload_token_file: str = _DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        self._set_platform_auth(
            base_url=base_url,
            timeout=timeout,
            pat_token=pat_token,
            workload_token_file=workload_token_file,
            exchange_url=exchange_url,
            audiences=audiences,
        )

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
        async with await self._client() as client:
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
        raise AssertionError("Unreachable execute fallthrough")

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
            resp = await client.patch(
                f"{_TRACKER_ISSUES_PATH}/{issue_id}",
                json={"status": status},
            )
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to update issue {issue_id}: {exc}")


# ---------------------------------------------------------------------------
# ting_plan
# ---------------------------------------------------------------------------

_TING_PLAN_PATH = "/api/v1/ting/sagas/plan"


class TingPlanTool(_PlatformAuthMixin, ToolPort):
    """Drive Ting's planning flow: spec text → gated plan → saga breakdown.

    Actions:
    - ``spawn``    — start a planning session (requires ``spec``; optional
      ``repo``, ``base_branch``, ``model``).
    - ``list``     — list active planning campaigns.
    - ``status``   — get one planning session with pending gates
      (requires ``slug``).
    - ``draft``    — read the current structured breakdown
      (requires ``slug``). Feed the result to ting_saga commit after the
      operator approves it.
    - ``feedback`` — approve or request changes on the pending plan gate
      (requires ``slug`` and ``content``; ``decision`` approve|changes_requested).
    - ``cancel``   — cancel a planning session (requires ``slug``).
    """

    @property
    def name(self) -> str:
        return "ting_plan"

    @property
    def description(self) -> str:
        return (
            "Drive Ting's planning flow (spec → gated plan → structured "
            "breakdown). Actions: spawn (spec required), list, status (slug "
            "required), draft (slug required — returns the breakdown to "
            "commit via ting_saga), feedback (slug + content required, "
            "decision approve|changes_requested), cancel (slug required)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["spawn", "list", "status", "draft", "feedback", "cancel"],
                    "description": "Planning operation to perform.",
                },
                "spec": {
                    "type": "string",
                    "description": "Specification text to plan from (spawn).",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository in org/repo form (spawn, optional).",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Base branch for the planning session (spawn).",
                },
                "model": {
                    "type": "string",
                    "description": "Model override for the planning session (spawn).",
                },
                "slug": {
                    "type": "string",
                    "description": "Planning campaign slug (status/draft/feedback/cancel).",
                },
                "content": {
                    "type": "string",
                    "description": "Feedback text for the pending gate (feedback).",
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "changes_requested"],
                    "description": "Gate decision (feedback; default approve).",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_PLATFORM

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        action = input.get("action", "")
        async with await self._client() as client:
            match action:
                case "spawn":
                    return await self._spawn(client, input)
                case "list":
                    return await self._list(client)
                case "status":
                    return await self._get(client, input, "")
                case "draft":
                    return await self._get(client, input, "/draft")
                case "feedback":
                    return await self._feedback(client, input)
                case "cancel":
                    return await self._cancel(client, input)
                case _:
                    return _err(f"Unknown action: {action!r}")

    async def _spawn(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        spec = str(input.get("spec") or "").strip()
        if not spec:
            return _err("spec is required for spawn")
        body = {
            "spec": spec,
            "repo": str(input.get("repo") or ""),
            "base_branch": str(input.get("base_branch") or "main"),
            "model": str(input.get("model") or ""),
        }
        try:
            resp = await client.post(_TING_PLAN_PATH, json=body)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to spawn planning session: {exc}")

    async def _list(self, client: httpx.AsyncClient) -> ToolResult:
        try:
            resp = await client.get(_TING_PLAN_PATH)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list planning sessions: {exc}")

    async def _get(self, client: httpx.AsyncClient, input: dict, suffix: str) -> ToolResult:
        slug = str(input.get("slug") or "").strip()
        if not slug:
            return _err("slug is required")
        try:
            resp = await client.get(f"{_TING_PLAN_PATH}/{slug}{suffix}")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to fetch plan {slug}{suffix or ''}: {exc}")

    async def _feedback(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        slug = str(input.get("slug") or "").strip()
        content = str(input.get("content") or "").strip()
        if not slug or not content:
            return _err("slug and content are required for feedback")
        body = {
            "content": content,
            "decision": str(input.get("decision") or "approve"),
        }
        try:
            resp = await client.post(f"{_TING_PLAN_PATH}/{slug}/feedback", json=body)
            resp.raise_for_status()
            return _ok(resp.json() if resp.text else {"status": "accepted"})
        except Exception as exc:
            return _err(f"Failed to send plan feedback for {slug}: {exc}")

    async def _cancel(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        slug = str(input.get("slug") or "").strip()
        if not slug:
            return _err("slug is required for cancel")
        try:
            resp = await client.delete(f"{_TING_PLAN_PATH}/{slug}")
            resp.raise_for_status()
            return _ok({"cancelled": slug})
        except Exception as exc:
            return _err(f"Failed to cancel plan {slug}: {exc}")


# ---------------------------------------------------------------------------
# ting_spec
# ---------------------------------------------------------------------------

_TING_SPECS_PATH = "/api/v1/ting/specs/campaigns"


class TingSpecTool(_PlatformAuthMixin, ToolPort):
    """Follow and review Ting specification campaigns (PRD/SRD/SDD gates).

    Actions:
    - ``list``      — list spec campaigns.
    - ``status``    — one campaign with pending gates (requires ``slug``).
    - ``artifacts`` — list a campaign's document artifacts (requires ``slug``).
    - ``artifact``  — read one artifact (requires ``slug`` and ``path``).
    - ``review``    — approve or request changes on the pending gate
      (requires ``slug``; ``decision`` approve|changes_requested; ``notes``).
    """

    @property
    def name(self) -> str:
        return "ting_spec"

    @property
    def description(self) -> str:
        return (
            "Follow and review Ting specification campaigns (PRD → SRD → "
            "SDD with human gates). Actions: list, status (slug required), "
            "artifacts (slug required), artifact (slug + path required), "
            "review (slug required, decision approve|changes_requested, "
            "notes optional)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "status", "artifacts", "artifact", "review"],
                    "description": "Spec-campaign operation to perform.",
                },
                "slug": {
                    "type": "string",
                    "description": "Spec campaign slug.",
                },
                "path": {
                    "type": "string",
                    "description": "Artifact path to read (artifact).",
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "changes_requested"],
                    "description": "Gate decision (review).",
                },
                "notes": {
                    "type": "string",
                    "description": "Review notes fed back into drafting (review).",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_PLATFORM

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        action = input.get("action", "")
        async with await self._client() as client:
            match action:
                case "list":
                    return await self._list(client)
                case "status":
                    return await self._get(client, input, "")
                case "artifacts":
                    return await self._get(client, input, "/artifacts")
                case "artifact":
                    return await self._artifact(client, input)
                case "review":
                    return await self._review(client, input)
                case _:
                    return _err(f"Unknown action: {action!r}")

    async def _list(self, client: httpx.AsyncClient) -> ToolResult:
        try:
            resp = await client.get(_TING_SPECS_PATH)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to list spec campaigns: {exc}")

    async def _get(self, client: httpx.AsyncClient, input: dict, suffix: str) -> ToolResult:
        slug = str(input.get("slug") or "").strip()
        if not slug:
            return _err("slug is required")
        try:
            resp = await client.get(f"{_TING_SPECS_PATH}/{slug}{suffix}")
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to fetch spec campaign {slug}{suffix or ''}: {exc}")

    async def _artifact(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        slug = str(input.get("slug") or "").strip()
        path = str(input.get("path") or "").strip()
        if not slug or not path:
            return _err("slug and path are required for artifact")
        try:
            resp = await client.get(
                f"{_TING_SPECS_PATH}/{slug}/artifact",
                params={"path": path},
            )
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to read artifact {path} for {slug}: {exc}")

    async def _review(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        slug = str(input.get("slug") or "").strip()
        if not slug:
            return _err("slug is required for review")
        body = {
            "decision": str(input.get("decision") or "approve"),
            "notes": str(input.get("notes") or ""),
        }
        try:
            resp = await client.post(f"{_TING_SPECS_PATH}/{slug}/review", json=body)
            resp.raise_for_status()
            return _ok(resp.json())
        except Exception as exc:
            return _err(f"Failed to review spec campaign {slug}: {exc}")
