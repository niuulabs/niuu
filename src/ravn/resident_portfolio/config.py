"""Configuration dataclasses and runtime boundary for resident portfolio management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ravn.domain.resident_expert import (
    ResidentDomainModel,
    ResidentWorkstream,
)
from ravn.domain.wakeful_resident import WakefulResidentRun


class WakefulRuntimePort(Protocol):
    """Boundary for advancing one selected objective through wakeful runtime."""

    async def run(self, mandate: str) -> WakefulResidentRun:
        """Advance bounded resident work for a mandate/objective."""


@dataclass(frozen=True)
class ResidentPortfolioConfig:
    """Bounds for one long-horizon portfolio management invocation."""

    max_objectives_selected: int = 1
    max_active_objectives: int = 3
    max_wake_cycles: int = 1
    max_workstream_turns: int = 1
    max_wall_clock_seconds: float = 1800.0
    max_tokens: int = 0
    bootstrap_when_empty: bool = True


@dataclass(frozen=True)
class ResidentPortfolioStewardConfig:
    """Bounds for resident portfolio stewardship."""

    max_passes: int = 3
    max_repairs_per_pass: int = 4
    max_follow_up_objectives: int = 3
    max_objectives_selected: int = 1
    max_active_objectives: int = 3
    max_advancements: int = 1
    repair_enabled: bool = True


@dataclass(frozen=True)
class ResidentCapabilityDiscoveryConfig:
    """Bounds for one resident capability discovery pass."""

    max_gaps: int = 1
    max_options: int = 4
    max_follow_up_objectives: int = 4


@dataclass(frozen=True)
class ResidentDelegationConfig:
    """Bounds for one resident delegation orchestration pass."""

    max_delegations: int = 2
    max_observations: int = 4
    max_follow_up_objectives: int = 4
    max_retry_follow_up_depth: int = 1
    approved_risk_objective_ids: tuple[str, ...] = ()
    abandon_after_seconds: float = 0.0
    reconcile_duplicate_delegations: bool = True


@dataclass(frozen=True)
class ResidentAutonomyLoopConfig:
    """Bounds for one resident autonomy loop invocation."""

    max_cycles: int = 2
    max_delegations_per_cycle: int = 1
    max_observations_per_cycle: int = 4
    max_review_attempts: int = 4
    max_retry_follow_up_depth: int = 1
    max_wall_clock_seconds: float = 1800.0
    sleep_between_cycles_seconds: float = 0.0
    approved_risk_objective_ids: tuple[str, ...] = ()
    abandon_after_seconds: float = 0.0
    reconcile_duplicate_delegations: bool = True


@dataclass(frozen=True)
class ResidentPortfolioEvidence:
    """Compact evidence used to discover/prioritize objectives."""

    domain_model: ResidentDomainModel | None = None
    workstreams: tuple[ResidentWorkstream, ...] = ()
    wake_records: tuple[Any, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    consolidation_refs: tuple[str, ...] = ()
