"""HTTP client adapter for cluster-local Observatory Agent Directories."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from niuu.domain.agent_directory import AgentDirectoryFilters, AgentDirectoryPage
from niuu.domain.models import RegisteredInstance
from niuu.ports.agent_directory import AgentDirectoryClientPort


def _agents_url(base_url: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Observatory base URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("Observatory base URL must not embed credentials")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1/observatory/agents"):
        pass
    elif path.endswith("/api/v1/observatory"):
        path = f"{path}/agents"
    else:
        path = f"{path}/api/v1/observatory/agents"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _query_params(filters: AgentDirectoryFilters) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    for key, values in (
        ("skill", filters.skills),
        ("tag", filters.tags),
        ("kind", filters.kinds),
        ("status", filters.statuses),
        ("environmentId", filters.environment_ids),
        ("cluster", filters.cluster_ids),
        ("instance", filters.instance_ids),
    ):
        params.extend((key, value) for value in values)
    return params


class HttpAgentDirectoryClient(AgentDirectoryClientPort):
    """Call one local directory with bounded timeout and auth forwarding."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def list_agents(
        self,
        instance: RegisteredInstance,
        *,
        headers: Mapping[str, str],
        filters: AgentDirectoryFilters,
    ) -> AgentDirectoryPage:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            response = await client.get(
                _agents_url(instance.base_url),
                headers=dict(headers),
                params=_query_params(filters),
            )
            response.raise_for_status()
            return AgentDirectoryPage.model_validate(response.json())
