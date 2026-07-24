"""Authenticated Ravn adapter for Guild's aggregated Agent Directory."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryPage,
    AgentDirectoryWarning,
    AgentInterface,
    AgentProvenance,
    AgentSkill,
)
from ravn.adapters.tool_build.http import AsyncJsonHttpClient


class GuildAgentDirectoryAdapter:
    """Read Guild and any explicitly configured Agent Cards as one directory."""

    def __init__(
        self,
        *,
        base_url: str,
        client: AsyncJsonHttpClient,
        agent_card_urls: list[str] | None = None,
    ) -> None:
        self._url = _agent_directory_url(base_url)
        self._client = client
        self._agent_card_urls = list(dict.fromkeys(agent_card_urls or []))

    async def list_agents(self) -> AgentDirectoryPage:
        response = await self._client.get(self._url)
        if response.status_code != 200 or not isinstance(response.body, dict):
            if not self._agent_card_urls:
                raise RuntimeError(f"Guild Agent Directory returned HTTP {response.status_code}")
            page = AgentDirectoryPage(
                warnings=[
                    AgentDirectoryWarning(
                        sourceInstanceId="guild",
                        code="guild-directory-unavailable",
                        message=(f"Guild Agent Directory returned HTTP {response.status_code}"),
                    )
                ],
                partial=True,
            )
        else:
            page = AgentDirectoryPage.model_validate(response.body)
        known_cards = {entry.card_url for entry in page.items}
        configured: list[AgentDirectoryEntry] = []
        warnings = list(page.warnings)
        for index, card_url in enumerate(self._agent_card_urls):
            if card_url in known_cards:
                continue
            try:
                configured.append(await self._load_agent_card(card_url))
            except Exception:
                warnings.append(
                    AgentDirectoryWarning(
                        sourceInstanceId=f"configured-agent-card-{index}",
                        code="agent-card-unavailable",
                        message="Configured Agent Card request failed",
                    )
                )
        items = [*page.items, *configured]
        revision = hashlib.sha256(
            "|".join(f"{entry.id}:{entry.card_hash}" for entry in items).encode()
        ).hexdigest()
        return page.model_copy(
            update={
                "items": items,
                "warnings": warnings,
                "partial": page.partial or bool(warnings),
                "revision": revision,
            }
        )

    async def get_agent(self, agent_id: str) -> AgentDirectoryEntry | None:
        normalized = str(agent_id or "").strip()
        if not normalized:
            return None
        page = await self.list_agents()
        return next((item for item in page.items if item.id == normalized), None)

    async def _load_agent_card(self, card_url: str) -> AgentDirectoryEntry:
        response = await self._client.get(card_url, headers={"A2A-Version": "1.0"})
        if response.status_code != 200 or not isinstance(response.body, dict):
            raise RuntimeError(f"Configured Agent Card returned HTTP {response.status_code}")
        return _agent_from_card(card_url, response.body)


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


def _agent_from_card(card_url: str, card: dict[str, Any]) -> AgentDirectoryEntry:
    canonical = json.dumps(card, sort_keys=True, separators=(",", ":"), default=str)
    card_hash = hashlib.sha256(canonical.encode()).hexdigest()
    source_agent_id = hashlib.sha256(card_url.encode()).hexdigest()[:24]
    agent_id = f"agent-{source_agent_id}"
    topology_node_id = f"agent-card:{source_agent_id}"
    skills = [
        AgentSkill.model_validate(skill)
        for skill in card.get("skills") or []
        if isinstance(skill, dict)
    ]
    interfaces = [
        AgentInterface.model_validate(interface)
        for interface in card.get("supportedInterfaces") or []
        if isinstance(interface, dict)
    ]
    return AgentDirectoryEntry(
        id=agent_id,
        canonicalId=f"configured:{card_url}",
        sourceAgentId=source_agent_id,
        sourceInstanceId="configured-agent-card",
        clusterId="",
        topologyNodeId=topology_node_id,
        name=str(card.get("name") or agent_id),
        description=str(card.get("description") or ""),
        kind="workflow-session",
        cardUrl=card_url,
        cardVersion=str(card.get("version") or ""),
        cardHash=card_hash,
        signatureVerified=None,
        skillIds=[skill.id for skill in skills],
        skills=skills,
        defaultInputModes=_string_list(card.get("defaultInputModes")),
        defaultOutputModes=_string_list(card.get("defaultOutputModes")),
        supportedInterfaces=interfaces,
        capabilities=(
            dict(card["capabilities"]) if isinstance(card.get("capabilities"), dict) else {}
        ),
        securitySchemes=(
            dict(card["securitySchemes"]) if isinstance(card.get("securitySchemes"), dict) else {}
        ),
        securityRequirements=[
            dict(item) for item in card.get("securityRequirements") or [] if isinstance(item, dict)
        ],
        observedStatus="healthy",
        visibility="public",
        provenance=[
            AgentProvenance(
                sourceAgentId=source_agent_id,
                sourceInstanceId="configured-agent-card",
                clusterId="",
                topologyNodeId=topology_node_id,
            )
        ],
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]
