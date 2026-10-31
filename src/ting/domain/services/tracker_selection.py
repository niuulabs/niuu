"""Choose the tracker adapter a project reads from."""

from __future__ import annotations

from ting.domain.models import Saga
from ting.ports.tracker import TrackerPort


def tracker_for_saga(trackers: list[TrackerPort], saga: Saga) -> TrackerPort | None:
    if saga.tracker_connection_id:
        matches = [
            tracker for tracker in trackers if tracker.connection_id == saga.tracker_connection_id
        ]
        return matches[0] if len(matches) == 1 else None
    if len(trackers) == 1:
        return trackers[0]
    return None
