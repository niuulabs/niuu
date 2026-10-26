"""Executor ports and the canonical provider-neutral Ravn harness contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from ravn.domain.checkpoint import InterruptReason
from ravn.domain.harness import (
    HarnessCapabilityProfile,
    HarnessControl,
    HarnessTurnInput,
    HarnessTurnResult,
)


@runtime_checkable
class RavnHarnessPort(Protocol):
    """Canonical Ravn-owned contract for executing one agent turn.

    ``start`` establishes a reusable runtime and is idempotent. ``close``
    releases it and is idempotent. ``run_turn`` accepts one typed input only
    after startup and returns one typed result. ``control`` is meaningful only
    during an active turn and returns ``False`` when the declared profile does
    not support the requested control. Invalid profile/input combinations raise
    ``HarnessContractError``; lifecycle misuse raises ``HarnessLifecycleError``;
    provider and tool failures propagate rather than becoming successful turns.
    """

    @property
    def profile(self) -> HarnessCapabilityProfile:
        """Return the eagerly validated capability declaration for this harness."""

    async def start(self) -> None:
        """Start the harness runtime if it is not already started."""

    async def run_turn(self, turn: HarnessTurnInput) -> HarnessTurnResult:
        """Execute one provider-neutral turn."""

    async def control(self, control: HarnessControl) -> bool:
        """Apply an active-turn control when the profile declares support."""

    async def close(self) -> None:
        """Release runtime resources if they have not already been released."""


class HarnessExecutorPort(ABC):
    """Factory for canonical Ravn harnesses.

    Implementations belong to Ravn adapters.  The legacy ``ExecutorPort``
    remains below until callers migrate their existing string-turn API.
    """

    @abstractmethod
    def build_harness(self, **kwargs: Any) -> RavnHarnessPort:
        """Construct a canonical harness with its declared capability profile."""


@runtime_checkable
class ExecutionAgentPort(Protocol):
    """Legacy interface the CLI, gateway, and drive loop currently need.

    See ``docs/architecture/ravn-harness-contract.md`` for its mapping to
    ``RavnHarnessPort``.  Kept unchanged so the canonical contract introduces
    no public behavior change before the caller migration.
    """

    session: object
    tools: list[object]
    max_iterations: int
    llm_adapter_name: str
    checkpoint_port: object | None
    task_id: str
    _tools: dict[str, object]
    _interrupt_reason: InterruptReason | None
    supports_steering: bool
    steering_mode: str

    async def run_turn(self, user_input: str) -> object:
        """Execute one turn and return a turn result."""

    def interrupt(self, reason: InterruptReason) -> None:
        """Signal the agent to stop at the next safe interruption point."""

    async def steer(self, content: str) -> bool:
        """Attempt to steer an active turn and return whether it was applied."""


class ExecutorPort(ABC):
    """Factory for building an execution agent for a persona."""

    @abstractmethod
    def build(self, **kwargs: Any) -> ExecutionAgentPort:
        """Construct an execution agent."""
