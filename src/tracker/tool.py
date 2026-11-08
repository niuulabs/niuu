"""tracker_issue tool: search, read, create and update issues through the platform tracker API."""

from __future__ import annotations

import httpx

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort
from tracker.platform_client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    DEFAULT_WORKLOAD_TOKEN_FILE,
    PlatformAuthMixin,
    http_failure,
    tool_err,
    tool_ok,
)

_PERMISSION_PLATFORM = "platform:api"
TRACKER_ISSUES_PATH = "/api/v1/tracker/issues"


class TrackerIssueTool(PlatformAuthMixin, ToolPort):
    """Search, view, create, and update issues via Volundr's tracker integration.

    Actions:
    - ``search``        — search issues across connected trackers (requires ``query``);
      results omit issue bodies (``description`` is null).
    - ``get``           — get issue details including the ``description`` body with
      acceptance criteria (requires ``issue_id``).
    - ``create``        — create an issue (requires ``title``, ``description``,
      ``team``; optional ``labels``, ``priority``, ``connection_id``).
    - ``update``        — rewrite an issue's title and/or description (requires
      ``issue_id`` and at least one of ``title``, ``description``).
    - ``update_status`` — update issue status (requires ``issue_id``, ``status``).
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        access_token: str = "",
        workload_token_file: str = DEFAULT_WORKLOAD_TOKEN_FILE,
        exchange_url: str = "",
        audiences: list[str] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout=timeout,
            access_token=access_token,
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
            "Search, view, create, and update issues in connected trackers (Linear, Jira, "
            "etc.) via Volundr. Actions: search (query) — matching issues without their "
            "bodies; get (issue_id) — full issue including its description (the ticket body "
            "with acceptance criteria, as Markdown); create (title + description + team, "
            "optional labels, priority, connection_id) — files a new issue and returns it; "
            "labels must already exist in the tracker; update (issue_id + title and/or "
            "description) — rewrites the ticket, the description replacing the whole body; "
            "update_status (issue_id + status)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "get", "create", "update", "update_status"],
                    "description": "Operation to perform.",
                },
                "issue_id": {
                    "type": "string",
                    "description": "Issue identifier (required for get, update, update_status).",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (required for search).",
                },
                "status": {
                    "type": "string",
                    "description": "New status value (required for update_status).",
                },
                "title": {
                    "type": "string",
                    "description": "Issue title (required for create; optional for update).",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Issue body as Markdown, including acceptance criteria "
                        "(required for create; for update it replaces the whole body)."
                    ),
                },
                "team": {
                    "type": "string",
                    "description": (
                        "Team key (Linear, e.g. NIU) or Jira project key to create the "
                        "issue in (required for create)."
                    ),
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Existing label names to apply (optional, create).",
                },
                "priority": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 4,
                    "description": "Priority 0=none, 1=urgent … 4=low (optional, create).",
                },
                "connection_id": {
                    "type": "string",
                    "description": (
                        "Tracker connection to create in; required when more than one "
                        "issue tracker is connected (optional, create)."
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
                case "search":
                    return await self._search(client, input.get("query", ""))
                case "get":
                    return await self._get(client, input.get("issue_id", ""))
                case "create":
                    return await self._create(client, input)
                case "update":
                    return await self._update(client, input)
                case "update_status":
                    return await self._update_status(
                        client, input.get("issue_id", ""), input.get("status", "")
                    )
                case _:
                    return tool_err(f"Unknown action: {action!r}")
        raise AssertionError("Unreachable execute fallthrough")

    async def _search(self, client: httpx.AsyncClient, query: str) -> ToolResult:
        if not query:
            return tool_err("query is required for search action")
        try:
            resp = await client.get(TRACKER_ISSUES_PATH, params={"q": query})
            resp.raise_for_status()
            return tool_ok(resp.json())
        except Exception as exc:
            return tool_err(f"Failed to search issues: {http_failure(exc)}")

    async def _get(self, client: httpx.AsyncClient, issue_id: str) -> ToolResult:
        if not issue_id:
            return tool_err("issue_id is required for get action")
        try:
            resp = await client.get(f"{TRACKER_ISSUES_PATH}/{issue_id}")
            resp.raise_for_status()
            return tool_ok(resp.json())
        except Exception as exc:
            return tool_err(f"Failed to get issue {issue_id}: {http_failure(exc)}")

    async def _create(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        missing = [key for key in ("title", "description", "team") if not input.get(key)]
        if missing:
            return tool_err(f"{', '.join(missing)} required for create action")
        body: dict[str, object] = {
            "title": input["title"],
            "description": input["description"],
            "team": input["team"],
        }
        for key in ("labels", "priority", "connection_id"):
            if input.get(key) is not None:
                body[key] = input[key]
        try:
            resp = await client.post(TRACKER_ISSUES_PATH, json=body)
            resp.raise_for_status()
            return tool_ok(resp.json())
        except httpx.HTTPStatusError as exc:
            return tool_err(
                f"Failed to create issue: {exc.response.status_code} {exc.response.text}"
            )
        except Exception as exc:
            return tool_err(f"Failed to create issue: {http_failure(exc)}")

    async def _update(self, client: httpx.AsyncClient, input: dict) -> ToolResult:
        issue_id = input.get("issue_id", "")
        body = {key: input[key] for key in ("title", "description") if input.get(key)}
        if not issue_id or not body:
            return tool_err("issue_id and a title and/or description are required for update")
        try:
            resp = await client.patch(f"{TRACKER_ISSUES_PATH}/{issue_id}", json=body)
            resp.raise_for_status()
            return tool_ok(resp.json())
        except Exception as exc:
            return tool_err(f"Failed to update issue {issue_id}: {http_failure(exc)}")

    async def _update_status(
        self, client: httpx.AsyncClient, issue_id: str, status: str
    ) -> ToolResult:
        if not issue_id or not status:
            return tool_err("issue_id and status are required for update_status")
        try:
            resp = await client.patch(
                f"{TRACKER_ISSUES_PATH}/{issue_id}",
                json={"status": status},
            )
            resp.raise_for_status()
            return tool_ok(resp.json())
        except Exception as exc:
            return tool_err(f"Failed to update issue {issue_id}: {http_failure(exc)}")
