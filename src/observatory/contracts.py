"""Shared Observatory fragment contracts.

These are intentionally transport-neutral shape definitions for services that
want to contribute topology, events, and layout hints to the merged
Observatory snapshot.
"""

from __future__ import annotations

from typing import Literal, TypedDict

LayoutMode = Literal["manual", "orbit", "pack", "force", "hybrid"]
LayoutScope = Literal["world", "realm", "cluster", "namespace", "group", "node"]
LayoutAxis = Literal["x", "y"]
EdgeKind = Literal["solid", "dashed-anim", "dashed-long", "soft", "run"]


class ObservatoryAnchorHint(TypedDict, total=False):
    """Optional fixed or weighted anchor for a node or scope."""

    x: float
    y: float
    pinned: bool
    weight: float


class ObservatoryLayoutHints(TypedDict, total=False):
    """Optional layout guidance attached to a topology snapshot or node."""

    mode: LayoutMode
    scope: LayoutScope
    anchor: ObservatoryAnchorHint
    order: int
    ring: int
    radius: float
    packGroup: str
    clusterRole: str
    axisLock: list[LayoutAxis]
    note: str


class ObservatoryNode(TypedDict, total=False):
    """One topology node contributed by a fragment producer."""

    id: str
    typeId: str
    label: str
    parentId: str | None
    status: str
    activity: str
    zone: str
    cluster: str | None
    hostId: str | None
    flockId: str | None
    sourceId: str
    sourceKind: str
    layoutHints: ObservatoryLayoutHints


class ObservatoryEdge(TypedDict):
    """One topology edge contributed by a fragment producer."""

    id: str
    sourceId: str
    targetId: str
    kind: EdgeKind


class ObservatoryEvent(TypedDict, total=False):
    """One event row contributed by a fragment producer."""

    id: str
    type: str
    service: str
    subject: str
    body: str
    message: str
    timestamp: str
    time: str


class ObservatoryFragmentMeta(TypedDict, total=False):
    """Metadata about the fragment producer."""

    sourceId: str
    sourceKind: str
    sourceName: str
    realmId: str
    clusterId: str
    revision: str


class ObservatoryFragment(TypedDict, total=False):
    """Partial topology payload produced by one service or scout."""

    nodes: list[ObservatoryNode]
    edges: list[ObservatoryEdge]
    events: list[ObservatoryEvent]
    layoutHints: ObservatoryLayoutHints
    meta: ObservatoryFragmentMeta


class ObservatorySnapshot(ObservatoryFragment, total=False):
    """Merged observatory snapshot returned to the frontend."""

    timestamp: str
