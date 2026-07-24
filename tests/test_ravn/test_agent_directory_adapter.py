from __future__ import annotations

from typing import Any

import pytest

from niuu.domain.agent_directory import AgentDirectoryEntry
from ravn.adapters.agent_directory import GuildAgentDirectoryAdapter
from ravn.adapters.tool_build.http import HttpResponse


class _Client:
    def __init__(
        self,
        guild: dict[str, Any],
        card: dict[str, Any],
        *,
        guild_status: int = 200,
    ) -> None:
        self.guild = guild
        self.card = card
        self.guild_status = guild_status
        self.gets: list[tuple[str, dict[str, str]]] = []

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.gets.append((url, dict(headers or {})))
        if url.endswith("agent-card.json"):
            return HttpResponse(200, self.card)
        return HttpResponse(self.guild_status, self.guild)


def _guild_entry(card_url: str) -> AgentDirectoryEntry:
    return AgentDirectoryEntry(
        id="agent-guild",
        canonicalId="signed:hash:key",
        sourceAgentId="guild-source",
        sourceInstanceId="observatory",
        clusterId="eitri",
        topologyNodeId="runtime:guild-source",
        name="Guild peer",
        description="Already discovered.",
        kind="workflow-session",
        cardUrl=card_url,
        cardVersion="1.0",
        cardHash="hash",
        observedStatus="healthy",
        visibility="public",
    )


@pytest.mark.asyncio
async def test_configured_agent_card_skills_join_guild_directory() -> None:
    card_url = "https://platform.example/.well-known/agent-card.json"
    client = _Client(
        guild={"items": [], "warnings": [], "sources": [], "partial": False},
        card={
            "name": "Workflow peer",
            "description": "Publishes workflows through A2A.",
            "version": "1.0",
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["application/json"],
            "skills": [
                {
                    "id": "research",
                    "name": "Research Campaign",
                    "description": "Research a question.",
                    "tags": ["research"],
                }
            ],
            "supportedInterfaces": [
                {
                    "url": "https://platform.example/api/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
        },
    )
    adapter = GuildAgentDirectoryAdapter(
        base_url="https://platform.example",
        client=client,
        agent_card_urls=[card_url],
    )

    page = await adapter.list_agents()

    assert len(page.items) == 1
    agent = page.items[0]
    assert agent.card_url == card_url
    assert agent.skill_ids == ["research"]
    assert agent.skills[0].tags == ["research"]
    assert agent.supported_interfaces[0].url == "https://platform.example/api/a2a"
    assert client.gets[1] == (card_url, {"A2A-Version": "1.0"})


@pytest.mark.asyncio
async def test_configured_agent_card_is_not_duplicated_when_guild_has_it() -> None:
    card_url = "https://platform.example/.well-known/agent-card.json"
    entry = _guild_entry(card_url)
    client = _Client(
        guild={
            "items": [entry.model_dump(by_alias=True)],
            "warnings": [],
            "sources": [],
            "partial": False,
        },
        card={"name": "must not be fetched"},
    )
    adapter = GuildAgentDirectoryAdapter(
        base_url="https://platform.example",
        client=client,
        agent_card_urls=[card_url],
    )

    page = await adapter.list_agents()

    assert [agent.id for agent in page.items] == ["agent-guild"]
    assert len(client.gets) == 1


@pytest.mark.asyncio
async def test_configured_agent_card_remains_available_when_guild_is_down() -> None:
    card_url = "https://platform.example/.well-known/agent-card.json"
    client = _Client(
        guild={"detail": "unavailable"},
        guild_status=503,
        card={
            "name": "Workflow peer",
            "skills": [{"id": "research", "name": "Research"}],
            "supportedInterfaces": [
                {
                    "url": "https://platform.example/api/a2a",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
        },
    )
    adapter = GuildAgentDirectoryAdapter(
        base_url="https://platform.example",
        client=client,
        agent_card_urls=[card_url],
    )

    page = await adapter.list_agents()

    assert page.partial is True
    assert page.items[0].skill_ids == ["research"]
    assert page.warnings[0].code == "guild-directory-unavailable"
