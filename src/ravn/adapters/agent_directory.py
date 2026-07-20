"""Authenticated Ravn adapter for Guild's aggregated Agent Directory."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from niuu.domain.agent_directory import AgentDirectoryEntry, AgentDirectoryPage
from ravn.adapters.tool_build.http import AsyncJsonHttpClient


class GuildAgentDirectoryAdapter:
    """Read the existing Guild projection; never maintain a parallel inventory."""

    def __init__(self, *, base_url: str, client: AsyncJsonHttpClient) -> None:
        self._url = _agent_directory_url(base_url)
        self._client = client

    async def list_agents(self) -> AgentDirectoryPage:
        response = await self._client.get(self._url)
        if response.status_code != 200 or not isinstance(response.body, dict):
            raise RuntimeError(
                f"Guild Agent Directory returned HTTP {response.status_code}"
            )
        return AgentDirectoryPage.model_validate(response.body)

    async def get_agent(self, agent_id: str) -> AgentDirectoryEntry | None:
        normalized = str(agent_id or "").strip()
        if not normalized:
            return None
        page = await self.list_agents()
        return next((item for item in page.items if item.id == normalized), None)


def _agent_directory_url(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Guild base URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("Guild base URL must not embed credentials")
    path = parsed.path.rstrip("/")
    suffix = "/api/v1/niuu/observatory/agents"
    if not path.endswith(suffix):
        path = f"{path}{suffix}" if path else suffix
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
