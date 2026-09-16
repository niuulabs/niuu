"""Connection-aware routing for external tracker adapters."""

from __future__ import annotations

from collections.abc import Sequence

from ting.domain.models import Saga
from ting.ports.tracker import TrackerPort


class TrackerRoutingError(RuntimeError):
    """Raised when a tracker operation cannot be routed unambiguously."""


def select_tracker(
    adapters: Sequence[TrackerPort],
    *,
    connection_id: str = "",
    provider: str = "",
) -> TrackerPort:
    """Select one adapter by stable integration identity.

    Provider matching exists only for legacy sagas that predate connection
    persistence. It is accepted when it identifies exactly one adapter.
    """
    if connection_id:
        matches = [
            adapter
            for adapter in adapters
            if str(getattr(adapter, "connection_id", "")) == connection_id
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise TrackerRoutingError(
                f"Tracker connection '{connection_id}' is unavailable or disabled"
            )
        raise TrackerRoutingError(
            f"Tracker connection '{connection_id}' is configured more than once"
        )

    normalized_provider = provider.strip().lower()
    if normalized_provider:
        matches = [
            adapter
            for adapter in adapters
            if str(getattr(adapter, "provider", "")).strip().lower() == normalized_provider
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise TrackerRoutingError(
                f"Multiple '{normalized_provider}' tracker connections are configured; "
                "re-import the project to bind it to one connection"
            )

    if len(adapters) == 1:
        return adapters[0]
    if not adapters:
        raise TrackerRoutingError("No tracker connection is configured")
    raise TrackerRoutingError(
        "Multiple tracker connections are configured; a tracker_connection_id is required"
    )


def select_tracker_for_saga(adapters: Sequence[TrackerPort], saga: Saga) -> TrackerPort:
    """Resolve the tracker connection that owns a saga."""
    return select_tracker(
        adapters,
        connection_id=saga.tracker_connection_id,
        provider=saga.tracker_type,
    )


async def select_tracker_for_run(
    adapters: Sequence[TrackerPort],
    tracker_id: str,
    *,
    owner_id: str = "",
) -> TrackerPort:
    """Resolve a run through connection-scoped operational state."""
    matches: list[TrackerPort] = []
    for adapter in adapters:
        try:
            run_owner = await adapter.get_owner_for_run(tracker_id)
        except Exception:
            continue
        if run_owner and (not owner_id or run_owner == owner_id):
            matches.append(adapter)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise TrackerRoutingError(
            f"Run '{tracker_id}' exists in multiple tracker connections; "
            "connection identity is required"
        )
    if len(adapters) == 1:
        return adapters[0]
    raise TrackerRoutingError(
        f"Run '{tracker_id}' is not associated with an available tracker connection"
    )
