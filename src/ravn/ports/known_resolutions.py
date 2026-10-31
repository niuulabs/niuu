"""Known-resolution port — what a resident already did about a wall it hits again.

The resident runtime consults this before it hands a question to the operator.
When the question is about friction the resident has already addressed (a
promoted procedure, a commissioned tool, a filed defect), asking again is the
failure the self-improvement loop exists to remove: the runtime wakes the
resident with the resolution instead, once per case. A second ask in the same
case reaches the operator and is recorded against the resolution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class KnownResolution:
    """A resolution the resident already has for one recorded friction."""

    ref: str
    instruction: str


class KnownResolutionPort(ABC):
    @abstractmethod
    async def resolution_for(self, question: str) -> KnownResolution | None:
        """Return the resolution for the friction *question* is about, if one exists."""

    @abstractmethod
    async def note_redirect(self, ref: str, case_id: str) -> bool:
        """Record a redirect for *case_id*; False when this case was already redirected."""

    @abstractmethod
    async def note_escalated_anyway(self, ref: str, case_id: str, question: str) -> None:
        """Record that the resolution did not stop an escalation in *case_id*."""
