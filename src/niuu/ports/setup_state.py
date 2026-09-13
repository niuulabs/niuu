"""Port for persisting first-launch setup progress."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.setup import SetupState


class SetupStateStore(ABC):
    """Load and save the wizard's progress record."""

    @abstractmethod
    async def load(self) -> SetupState:
        """Return the persisted state, or an empty state when none exists yet.

        A missing record is the expected steady state before the first launch
        and is an answer, not a failure.
        """

    @abstractmethod
    async def save(self, state: SetupState) -> None:
        """Persist *state*; raises when it cannot be written."""
