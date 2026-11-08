"""Outbound port for querying cluster-local Observatory Agent Directories."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Protocol

from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryFilters,
    AgentDirectoryPage,
)
from niuu.domain.models import RegisteredInstance


class AgentDirectoryClientPort(ABC):
    """Query one registered Observatory while preserving caller identity."""

    @abstractmethod
    async def list_agents(
        self,
        instance: RegisteredInstance,
        *,
        headers: Mapping[str, str],
        filters: AgentDirectoryFilters,
    ) -> AgentDirectoryPage:
        """Return one source's principal-filtered directory page."""


class PeerAgentDirectoryPort(Protocol):
    """Read the principal-scoped, aggregated peer directory."""

    async def list_agents(self) -> AgentDirectoryPage:
        """Return agents already filtered by Guild for the current principal."""

    async def get_agent(self, agent_id: str) -> AgentDirectoryEntry | None:
        """Resolve an aggregate agent id without accepting a caller-supplied URL."""
