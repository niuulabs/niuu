"""LayerLifetimes — whether the sessions and runs behind ephemeral layers have ended (C7).

The retention sweep asks Forge, as the gateway's own service, which of the
sessions and runs it holds layers for are still active, which ended and when,
and which Forge no longer knows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LifeState(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"
    GONE = "gone"


@dataclass(frozen=True)
class Lifetime:
    state: LifeState
    ended_at: datetime | None = None


@dataclass(frozen=True)
class Lifetimes:
    sessions: dict[str, Lifetime]
    runs: dict[str, Lifetime]


class LifetimesUnavailableError(Exception):
    """Forge could not be asked, refused the gateway's credential, or answered something
    unusable. Nothing may be deleted on such an answer."""


class LayerLifetimes(ABC):
    @abstractmethod
    async def ended(self, sessions: list[str], runs: list[str]) -> Lifetimes:
        """The state of every session and run named; each one named is in the answer."""

    async def close(self) -> None:  # noqa: B027 — optional hook
        """Release connections."""
