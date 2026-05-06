"""Executor port for persona-selectable agent runtimes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from ravn.domain.checkpoint import InterruptReason


@runtime_checkable
class ExecutionAgentPort(Protocol):
    """Minimal interface the CLI, gateway, and drive loop need from an agent."""

    session: object
    tools: list[object]
    max_iterations: int
    llm_adapter_name: str
    checkpoint_port: object | None
    task_id: str
    _tools: dict[str, object]
    _interrupt_reason: InterruptReason | None

    async def run_turn(self, user_input: str) -> object:
        """Execute one turn and return a turn result."""

    def interrupt(self, reason: InterruptReason) -> None:
        """Signal the agent to stop at the next safe interruption point."""


class ExecutorPort(ABC):
    """Factory for building an execution agent for a persona."""

    @abstractmethod
    def build(self, **kwargs: Any) -> ExecutionAgentPort:
        """Construct an execution agent."""
