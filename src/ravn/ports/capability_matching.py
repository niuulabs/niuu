"""Port for deciding whether two tools provide the same capability.

Names fork: a resident that cannot find its tool asks for it again under a new
name (``list_pods_in_namespace``, ``_fixed``, ``_safe``, ``_incluster_v3``…), and
nothing that compares spelling links them. Deciding that two tools serve the
same need is a judgment about meaning, which Ravn owns; the adapter behind this
port makes it with the same embedding shortlist and model judge that match
recorded lessons.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CapabilityDescriptor:
    """What a tool does, in the terms a judge can compare."""

    #: Stable id of the thing described (an artifact id, or a request id).
    key: str
    name: str
    description: str
    #: Input parameter names, which often separate two tools on one topic.
    inputs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        """The text embedded for the similarity shortlist."""
        return f"{self.name}: {self.description}".strip()


@dataclass(frozen=True)
class CapabilityVerdict:
    """The judge's call on one shortlisted candidate."""

    candidate_key: str
    same: bool
    similarity: float
    reason: str


class CapabilityMatcherPort(Protocol):
    """Decide which candidates provide the same capability as a subject."""

    async def same_capability(
        self,
        subject: CapabilityDescriptor,
        candidates: Sequence[CapabilityDescriptor],
        *,
        as_revision: bool = False,
    ) -> list[CapabilityVerdict]:
        """Return a verdict for every candidate close enough to be judged.

        ``as_revision`` asks whether the subject is a *next version* of a
        candidate, so a request that extends a tool (an extra field, an extra
        option) belongs to that tool's lineage. Without it the question is
        whether the candidate can stand in for the subject, which is what
        retiring duplicates needs.

        Candidates below the adapter's similarity threshold are distinct
        without a model call and get no verdict. A judge that cannot answer
        raises; it never reads as "no match".
        """
        ...
