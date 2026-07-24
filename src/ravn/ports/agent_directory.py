"""Ravn boundary for the existing Guild/Observatory Agent Directory."""

from __future__ import annotations

from typing import Protocol

from niuu.domain.agent_directory import AgentDirectoryEntry, AgentDirectoryPage


class PeerAgentDirectoryPort(Protocol):
    """Read the principal-scoped, aggregated peer directory."""

    async def list_agents(self) -> AgentDirectoryPage:
        """Return agents already filtered by Guild for the current principal."""

    async def get_agent(self, agent_id: str) -> AgentDirectoryEntry | None:
        """Resolve an aggregate agent id without accepting a caller-supplied URL."""
