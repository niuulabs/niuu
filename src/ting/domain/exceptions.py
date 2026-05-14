"""Domain exceptions for the Ting saga coordinator."""

from __future__ import annotations

from uuid import UUID


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted on a Run."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid state transition: {current} -> {target}")


class RunNotFoundError(Exception):
    """Raised when a run cannot be found by ID."""

    def __init__(self, run_id: UUID | str) -> None:
        self.run_id = run_id
        super().__init__(f"Run not found: {run_id}")
