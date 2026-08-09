"""Resident state adapters."""

from __future__ import annotations

from ravn.domain.resident_state import ResidentStatePort
from ravn.memory_telemetry import record_resident_state_fallback


def _adapter_name(candidate: ResidentStatePort) -> str:
    return type(candidate).__name__


async def select_resident_state(
    *candidates: ResidentStatePort,
    environment_id: str = "",
) -> ResidentStatePort:
    """Return the first candidate whose backend is available, in preference order.

    Lets composition prefer GBrain and fall back to a local/Mimir store when
    GBrain is absent, without the caller branching on adapter type.

    Falling back is invisible from the outside — the resident keeps working
    against a store with different reach — so any selection past the first
    preference is counted.
    """
    if not candidates:
        raise RuntimeError("no resident-state adapter is available")

    preferred = _adapter_name(candidates[0])
    for index, candidate in enumerate(candidates):
        if not await candidate.available():
            continue
        if index > 0:
            record_resident_state_fallback(
                preferred=preferred,
                selected=_adapter_name(candidate),
                reason="preferred_unavailable",
                environment_id=environment_id,
            )
        return candidate
    raise RuntimeError("no resident-state adapter is available")


__all__ = ["select_resident_state"]
