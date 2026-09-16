"""Resolve existing sessions independently of new-work placement."""

from collections.abc import Sequence

from ting.ports.volundr import VolundrPort


async def find_session_target(adapters: Sequence[VolundrPort], session_id: str) -> VolundrPort:
    """Find the owning visible target; only a missing session permits another lookup.

    Provider/authentication errors propagate. No mutation is attempted until
    the owner has been found, and mutations are never retried on another target.
    """
    for adapter in adapters:
        if await adapter.get_session(session_id) is not None:
            return adapter
    raise LookupError(f"No visible Volundr target owns session {session_id}")
