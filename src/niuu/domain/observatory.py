"""Shared contracts for Observatory topology fragments and aggregation.

A *fragment* is one source's partial view of the topology. A Kubernetes scout, a
resident on a bare-metal host, a Docker container behind NAT and a static YAML
file all produce the same shape, so aggregation never needs to know what
produced one. That is what keeps the topology graph from being Kubernetes-shaped.

Lives in `niuu` rather than `observatory` because Guild aggregates fragments and
`niuu` must not import `observatory` — the dependency runs the other way.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

NodeStatus = Literal["healthy", "degraded", "failed", "idle", "observing", "unknown"]
EdgeKind = Literal["solid", "dashed-anim", "dashed-long", "soft", "run"]
EdgeRelationType = Literal[
    "contains",
    "manages",
    "uses",
    "reads",
    "writes",
    "routes_to",
    "exposes",
    "observes",
    "signals_to",
    "member_of",
]
EdgeConfidence = Literal["declared", "observed", "inferred"]
LayoutMode = Literal["manual", "orbit", "pack", "force", "hybrid"]
LayoutScope = Literal["world", "realm", "cluster", "group", "node"]

#: How a fragment reached the aggregator.
SourceTransport = Literal["pull", "push", "local", "static"]

#: `stale` is distinct from `failed`: the source was reachable and simply has
#: not reported within its TTL, which is a different operator situation from a
#: source that cannot be reached at all.
SourceStatus = Literal["healthy", "degraded", "stale", "failed"]


class _ObservatoryModel(BaseModel):
    """Observatory model with one consistent snake_case/camelCase boundary."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class LayoutAnchor(_ObservatoryModel):
    """Fixed or weighted placement hint for a node."""

    x: float = 0.0
    y: float = 0.0
    pinned: bool = False
    weight: float = 1.0


class LayoutHints(_ObservatoryModel):
    """Optional layout guidance emitted by a source."""

    mode: LayoutMode | None = None
    scope: LayoutScope | None = None
    anchor: LayoutAnchor | None = None
    order: int | None = None
    ring: int | None = None
    radius: float | None = None
    pack_group: str = ""
    cluster_role: str = ""
    axis_lock: list[Literal["x", "y"]] = Field(default_factory=list)
    note: str = ""


class TopologyNode(_ObservatoryModel):
    """One entity in the topology graph.

    Extra fields are preserved deliberately. Discovery adapters carry
    kind-specific detail (a Mímir's `pages`, a host's `gpu`, a resident's
    `persona`) in `DiscoveredEntity.metadata`, which is splatted onto the node.
    Rejecting unknown keys here would silently strip all of it.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )

    id: str
    type_id: str
    label: str
    parent_id: str | None = None
    status: NodeStatus = "unknown"

    # Placement. A node may be placed by any combination of these, or none —
    # a bare-metal host has no cluster and no namespace.
    realm_id: str = ""
    cluster_id: str = ""
    namespace: str = ""
    host_id: str = ""

    # Provenance — which source contributed this node.
    source_id: str = ""
    source_kind: str = ""

    # Visibility, applied by the aggregator after merge.
    owner_id: str | None = None
    tenant_id: str | None = None
    visibility: str = "system"

    layout_hints: LayoutHints | None = None


class TopologyEdge(_ObservatoryModel):
    """A directed relationship between two nodes.

    `confidence` is not decoration: `observed` means the relationship was read
    from real configuration, `declared` that an operator asserted it via
    labels, `inferred` that it was matched heuristically. The UI renders them
    differently, so guessing must never be recorded as observation.
    """

    id: str
    source_id: str
    target_id: str
    kind: EdgeKind = "solid"
    label: str = ""
    relation_type: EdgeRelationType | None = None
    confidence: EdgeConfidence = "declared"
    evidence: dict[str, str] = Field(default_factory=dict)


class TopologyEvent(_ObservatoryModel):
    """A discovery-time event surfaced alongside the graph."""

    id: str
    type: str = "info"
    level: str = "info"
    service: str = ""
    subject: str = ""
    body: str = ""
    message: str = ""
    timestamp: str = ""
    time: str = ""


class FragmentMeta(_ObservatoryModel):
    """Who produced a fragment, and where they sit."""

    source_id: str
    source_kind: str = ""
    source_name: str = ""
    realm_id: str = ""
    cluster_id: str = ""
    host_id: str = ""
    revision: str = ""


class ObservatoryFragment(_ObservatoryModel):
    """One source's partial view of the topology."""

    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)
    events: list[TopologyEvent] = Field(default_factory=list)
    layout_hints: LayoutHints | None = None
    meta: FragmentMeta | None = None


class TopologySourceHealth(_ObservatoryModel):
    """Reachability and freshness of one fragment source."""

    source_id: str
    source_kind: str = ""
    source_name: str = ""
    transport: SourceTransport = "pull"
    status: SourceStatus = "healthy"
    cluster_id: str = ""
    realm_id: str = ""
    revision: str = ""
    node_count: int = 0
    last_seen: str = ""
    message: str = ""


class TopologyWarning(_ObservatoryModel):
    """A degradation that did not stop the snapshot being served."""

    source_id: str = ""
    code: str = ""
    message: str = ""


class TopologySnapshot(_ObservatoryModel):
    """The merged graph plus honest reporting about how complete it is.

    A source that cannot be reached appears in `sources` with a failed status
    rather than vanishing, and `partial` says plainly that the graph is not the
    whole picture.
    """

    timestamp: str
    revision: str = ""
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)
    events: list[TopologyEvent] = Field(default_factory=list)
    layout_hints: LayoutHints | None = None
    sources: list[TopologySourceHealth] = Field(default_factory=list)
    warnings: list[TopologyWarning] = Field(default_factory=list)
    partial: bool = False
