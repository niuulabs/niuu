"""Outbound port for querying cluster-local Observatory Agent Directories."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from niuu.domain.agent_directory import AgentDirectoryFilters, AgentDirectoryPage
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
