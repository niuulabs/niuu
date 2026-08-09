"""Resident state adapters."""

from __future__ import annotations

from ravn.domain.resident_state import ResidentStatePort


async def select_resident_state(adapter: ResidentStatePort) -> ResidentStatePort:
    """Return the configured adapter, or raise if its backend is unusable.

    There is deliberately no fallback. Silently demoting to a second store
    left every resident running against its local files while the configured
    preference was unreachable, and nothing said so — the resident kept
    working, against different data, for months.

    Choosing a different store is configuration, not a runtime rescue: name
    the adapter you want in ``resident_state.adapter``.
    """
    if not await adapter.available():
        raise RuntimeError(
            f"resident state adapter {type(adapter).__name__} is configured but its "
            f"backend is not available. Fix the backend, or configure a different "
            f"resident_state.adapter — the resident will not silently run against "
            f"another store."
        )
    return adapter


__all__ = ["select_resident_state"]
