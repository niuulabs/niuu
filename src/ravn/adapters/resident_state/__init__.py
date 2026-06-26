"""Resident state adapters."""

from __future__ import annotations

from ravn.domain.resident_state import ResidentStatePort


async def select_resident_state(*candidates: ResidentStatePort) -> ResidentStatePort:
    """Return the first candidate whose backend is available, in preference order.

    Lets composition prefer GBrain and fall back to a local/Mimir store when
    GBrain is absent, without the caller branching on adapter type.
    """
    for candidate in candidates:
        if await candidate.available():
            return candidate
    raise RuntimeError("no resident-state adapter is available")


__all__ = ["select_resident_state"]
