"""Shared model-catalog domain types owned by Bifrost."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ManagedModelProvider(StrEnum):
    """High-level provider family for a managed model."""

    CLOUD = "cloud"
    LOCAL = "local"


class ManagedModelTier(StrEnum):
    """UI/runtime tier for a managed model."""

    FRONTIER = "frontier"
    BALANCED = "balanced"
    EXECUTION = "execution"
    REASONING = "reasoning"


@dataclass(frozen=True)
class ManagedModel:
    """Canonical model-catalog entry exposed by Bifrost."""

    id: str
    name: str
    vendor: str
    provider: ManagedModelProvider = ManagedModelProvider.CLOUD
    tier: ManagedModelTier = ManagedModelTier.BALANCED
    color: str = "#2563EB"
    description: str = ""
    cost_per_million_tokens: float | None = None
    vram_required: str | None = None
    session_definition: str | None = None
    supports_tools: bool = True
    supports_thinking: bool = True
    enabled: bool = True
    aliases: tuple[str, ...] = field(default_factory=tuple)
    provider_keys: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ManagedModelAlias:
    """Alias mapping published by Bifrost."""

    alias: str
    target: str


class ProviderHealthState(StrEnum):
    """Observed health for a configured provider entry."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ManagedProvider:
    """Configured provider entry plus derived routing health."""

    key: str
    vendor: str
    base_url: str
    model_ids: tuple[str, ...] = field(default_factory=tuple)
    timeout_seconds: float = 120.0
    cost_per_token: float = 0.0
    state: ProviderHealthState = ProviderHealthState.UNKNOWN
    detail: str = ""

