"""Host preparation contract between machine allocation and session runtime."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from volundr.domain.compute import ComputeLease, MachineBootstrap
from volundr.domain.execution_catalog import HostRecipe


class HostPreparationError(RuntimeError):
    """A pinned host recipe could not be verified on its allocated guest."""


class HostPreparationStageResult(BaseModel):
    """Durable outcome for one named preparation stage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage_id: str
    stage_digest: str
    status: Literal["verified"] = "verified"
    applied: bool = False
    facts: dict[str, str] = Field(default_factory=dict)


class HostPreparationResult(BaseModel):
    """Verified recipe state observed after a preparation attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recipe_digest: str
    stages: tuple[HostPreparationStageResult, ...]
    facts: dict[str, str] = Field(default_factory=dict)

    def verify_requirements(self, requirements: dict[str, str]) -> None:
        """Fail when measured facts do not exactly satisfy declared requirements."""
        missing = sorted(set(requirements) - set(self.facts))
        if missing:
            raise HostPreparationError(
                f"Prepared host did not report required facts: {', '.join(missing)}"
            )
        mismatched = sorted(
            key for key, expected in requirements.items() if self.facts[key] != expected
        )
        if mismatched:
            raise HostPreparationError(
                f"Prepared host facts do not satisfy requirements: {', '.join(mismatched)}"
            )


class HostPreparation(ABC):
    """Ensure a provider-independent, pinned host recipe on an allocated guest."""

    @abstractmethod
    def machine_bootstrap(self, recipe: HostRecipe, defaults: MachineBootstrap) -> MachineBootstrap:
        """Contribute minimum access bootstrap without executing recipe stages."""

    @abstractmethod
    async def ensure(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        recipe: HostRecipe,
    ) -> HostPreparationResult:
        """Idempotently check, apply, verify, and checkpoint every recipe stage."""

    @abstractmethod
    async def observe(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        recipe: HostRecipe,
    ) -> HostPreparationResult:
        """Verify the current recipe without applying changes."""

    @abstractmethod
    async def close(self) -> None:
        """Close controller-side resources without changing the guest."""
