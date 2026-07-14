"""Port for principal-aware A2A Agent Card resolution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from niuu.domain.agent_directory import AgentInterface


@dataclass(frozen=True)
class ResolvedAgentCard:
    """Validated, searchable Agent Card fields safe for directory indexing."""

    name: str
    description: str
    version: str
    skills: tuple[str, ...]
    tags: tuple[str, ...]
    default_input_modes: tuple[str, ...]
    default_output_modes: tuple[str, ...]
    supported_interfaces: tuple[AgentInterface, ...]
    capabilities: dict[str, Any] = field(default_factory=dict)
    card_hash: str = ""
    signature_verified: bool | None = None
    signature_key_ids: tuple[str, ...] = ()


class AgentCardResolutionError(RuntimeError):
    """Raised when a card is unreachable, invalid, or has an invalid signature."""


class AgentCardResolverPort(ABC):
    """Resolve and validate Agent Cards without coupling services to HTTP."""

    @abstractmethod
    async def resolve(
        self,
        card_url: str,
        *,
        principal_key: str,
        headers: Mapping[str, str],
    ) -> ResolvedAgentCard:
        """Resolve a card for one caller-isolated cache partition."""
