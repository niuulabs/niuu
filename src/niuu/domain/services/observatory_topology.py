"""Guild-backed aggregation of Observatory topology fragments.

Merges what every source knows into one graph. Sources arrive two ways —
polled over HTTP, or pushed into the inbox by hosts the aggregator cannot
reach — and the merge deliberately cannot tell which is which. That is what
keeps the topology from being Kubernetes-shaped: a resident on a bare-metal
Spark contributes exactly like a cluster scout does.

A source that fails or goes stale is reported, never hidden. An empty graph
and an unreachable estate look identical otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from niuu.domain.models import RegisteredInstance
from niuu.domain.observatory import (
    ObservatoryFragment,
    TopologyEdge,
    TopologyEvent,
    TopologyNode,
    TopologySnapshot,
    TopologySourceHealth,
    TopologyWarning,
)
from niuu.domain.services.observatory_fragments import ObservatoryFragmentInboxService
from niuu.domain.services.observatory_refs import resolve_node_ref
from niuu.ports.observatory_topology import ObservatoryTopologyClientPort

logger = logging.getLogger(__name__)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _instance_cluster(instance: RegisteredInstance) -> str:
    return str(instance.config.get("cluster") or instance.config.get("environment") or "")


class ObservatoryTopologyAggregationService:
    """Fan out to reachable sources, fold in pushed ones, merge the graph."""

    def __init__(
        self,
        *,
        client: ObservatoryTopologyClientPort,
        max_concurrency: int,
        fragment_inbox: ObservatoryFragmentInboxService | None = None,
    ) -> None:
        self._client = client
        self._max_concurrency = max_concurrency
        self._fragment_inbox = fragment_inbox

    async def get_snapshot(
        self,
        instances: list[RegisteredInstance],
        *,
        headers: Mapping[str, str],
    ) -> TopologySnapshot:
        fragments: list[ObservatoryFragment] = []
        sources: list[TopologySourceHealth] = []
        warnings: list[TopologyWarning] = []

        pulled = await self._pull(instances, headers=headers)
        for instance, fragment, error in pulled:
            if error is not None or fragment is None:
                # Several httpx transport errors stringify to "", which turned
                # every unreachable source into "Observatory Ymir: " — a report
                # that names the source and then says nothing about why. The
                # class name is the minimum that distinguishes a DNS failure
                # from a timeout from a refused connection.
                message = self._describe(error) if error else "Observatory returned no fragment"
                sources.append(
                    TopologySourceHealth(
                        source_id=instance.id,
                        source_kind=instance.kind.value,
                        source_name=instance.name,
                        transport="pull",
                        status="failed",
                        cluster_id=_instance_cluster(instance),
                        message=message,
                    )
                )
                warnings.append(
                    TopologyWarning(
                        source_id=instance.id,
                        code="source_unreachable",
                        message=f"{instance.name}: {message}",
                    )
                )
                continue

            fragments.append(fragment)
            meta = fragment.meta
            sources.append(
                TopologySourceHealth(
                    source_id=instance.id,
                    source_kind=instance.kind.value,
                    source_name=instance.name,
                    transport="pull",
                    status="healthy",
                    cluster_id=meta.cluster_id
                    if meta and meta.cluster_id
                    else _instance_cluster(instance),
                    realm_id=meta.realm_id if meta else "",
                    revision=meta.revision if meta else "",
                    node_count=len(fragment.nodes),
                )
            )

        for stored, health in await self._pushed():
            fragments.append(stored.fragment)
            sources.append(health)
            if health.status == "stale":
                warnings.append(
                    TopologyWarning(
                        source_id=health.source_id,
                        code="source_stale",
                        message=f"{health.source_name or health.source_id}: {health.message}",
                    )
                )

        nodes, edges, events, merge_warnings = _merge(fragments)
        warnings.extend(merge_warnings)

        return TopologySnapshot(
            timestamp=_iso(datetime.now(UTC)),
            revision=_revision(nodes, edges),
            nodes=nodes,
            edges=edges,
            events=events,
            sources=sources,
            warnings=warnings,
            partial=any(source.status in {"failed", "stale"} for source in sources),
        )

    @staticmethod
    def _describe(error: BaseException) -> str:
        return str(error) or type(error).__name__

    async def _pull(
        self,
        instances: list[RegisteredInstance],
        *,
        headers: Mapping[str, str],
    ) -> list[tuple[RegisteredInstance, ObservatoryFragment | None, Exception | None]]:
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def load(
            instance: RegisteredInstance,
        ) -> tuple[RegisteredInstance, ObservatoryFragment | None, Exception | None]:
            async with semaphore:
                try:
                    return (
                        instance,
                        await self._client.fetch_fragment(instance, headers=headers),
                        None,
                    )
                except Exception as exc:  # one bad source must not empty the graph
                    # Same reason the API field is described rather than
                    # stringified: several transport errors render as "", and a
                    # log line that names the source then stops is not a log.
                    logger.warning(
                        "Topology fragment unavailable from %s: %s",
                        instance.name,
                        self._describe(exc),
                    )
                    return instance, None, exc

        return list(await asyncio.gather(*(load(instance) for instance in instances)))

    async def _pushed(self):
        if self._fragment_inbox is None:
            return []
        try:
            return await self._fragment_inbox.current()
        except Exception:
            logger.warning("Topology fragment inbox unavailable", exc_info=True)
            return []


def _claim_authority(node: TopologyNode, fragment_cluster: str) -> int:
    """How much weight a source's claim on a node carries.

    2 — the source speaks for the cluster this node belongs to.
    1 — the claim at least places the node under a parent.
    0 — a bare reference, synthesised because some other node pointed at it.
    """
    # Discovery stamps placement as `clusterName`, which reaches the model as
    # an extra field rather than the declared `cluster_id`; read both so the
    # rule is not quietly dead.
    extra = node.model_extra or {}
    cluster = (node.cluster_id or str(extra.get("clusterName") or "")).strip()
    if fragment_cluster and cluster and fragment_cluster == cluster:
        return 2
    return 1 if node.parent_id else 0


def _is_empty(value: object) -> bool:
    """True when a claim carries nothing for this field."""
    return value is None or value == "" or value == [] or value == {}


def _is_given_name(node: TopologyNode) -> bool:
    """True when the label says something the node id does not already say.

    A source that has never met a thing still has to call it something, and
    what it reaches for is the identifier: Kubernetes calls the resident
    `valkyrie-eitri-k8s` because that is what the resource is named. The Ravn
    that runs it calls it Bryn. Both are claims on the same node; only one of
    them is a name.
    """
    label = node.label.strip()
    if not label:
        return False
    return label != node.id and label != node.id.rsplit(":", 1)[-1]


#: Fields two sources are expected to differ on, because they say who is
#: speaking rather than what the node is. `_merge_node` folds them, so
#: reporting them as a conflict warns about every node more than one source
#: has ever seen — which is most of them.
_PROVENANCE_FIELDS = frozenset({"sourceId", "sourceKind"})


def _contested_fields(left: TopologyNode, right: TopologyNode) -> list[str]:
    """Fields both claims fill in, and fill in differently.

    One source knowing more than another is not a conflict — only the fields
    where both looked and saw something different are. Nor is a difference the
    merge already resolves by rule: a label that merely echoes the node id
    loses to a real name deterministically, so it is not in dispute.
    """
    a = left.model_dump(by_alias=True)
    b = right.model_dump(by_alias=True)
    settled = set(_PROVENANCE_FIELDS)
    if _is_given_name(left) != _is_given_name(right):
        settled.add("label")
    return sorted(
        key
        for key in a.keys() & b.keys()
        if key not in settled
        and not _is_empty(a[key])
        and not _is_empty(b[key])
        and a[key] != b[key]
    )


def _merge_node(preferred: TopologyNode, other: TopologyNode) -> TopologyNode:
    """Fold a weaker claim into the stronger one without losing what it knew.

    Two sources describing the same node are usually describing different
    halves of it: the cluster it runs in knows where it is, the Ravn that runs
    it knows who it is and which flock it peers in. Replacing one claim
    wholesale with the other discarded the half that lost — residents came
    back named after their Kubernetes resource and with no flock, so the mesh
    they formed had nothing left to group on and stopped being drawn.
    """
    merged = preferred.model_dump(by_alias=True)
    for key, value in other.model_dump(by_alias=True).items():
        if key not in merged or _is_empty(merged[key]):
            merged[key] = value

    if not _is_given_name(preferred) and _is_given_name(other):
        merged["label"] = other.label

    kinds = {kind for claim in (preferred, other) for kind in claim.source_kind.split(",") if kind}
    if kinds:
        merged["sourceKind"] = ",".join(sorted(kinds))

    return TopologyNode.model_validate(merged)


def _resolve_pending_edge(
    edge: TopologyEdge,
    nodes: Mapping[str, TopologyNode],
) -> TopologyEdge | None:
    """Point a cross-source edge at the nodes it was always describing.

    Both endpoints have to land, and on different nodes. A reference that
    resolves to the same node as the other end is a source describing itself
    the long way round, not a relationship.
    """
    candidates = [node.model_dump(by_alias=True) for node in nodes.values()]
    source_id = resolve_node_ref(edge.source_id, candidates)
    target_id = resolve_node_ref(edge.target_id, candidates)
    if not source_id or not target_id or source_id == target_id:
        return None
    return edge.model_copy(update={"source_id": source_id, "target_id": target_id})


def _merge(
    fragments: list[ObservatoryFragment],
) -> tuple[list[TopologyNode], list[TopologyEdge], list[TopologyEvent], list[TopologyWarning]]:
    """Combine fragments, preferring the source that speaks for a node.

    Two sources claiming the same node id is usually not a conflict: an
    Observatory that merely *references* a peer's cluster synthesises a
    placeholder for it, carrying no placement, while the Observatory in that
    cluster emits the real thing. Keeping whichever arrived first let a
    placeholder win, and a cluster whose parent was lost that way took its
    realm off the canvas with it — a realm with no children has no rectangle
    to draw. Every realm but ymir's and vanaheim's disappeared this way.

    So a claim that places a node beats one that does not, and a source that
    declares itself the owner of that cluster beats both. Ranking decides who
    wins a contested field, never who is heard: claims are folded together, so
    the loser's knowledge survives. Only fields both sources filled in
    differently are a real disagreement, and those are reported rather than
    resolved silently.
    """
    nodes: dict[str, TopologyNode] = {}
    edges: dict[str, TopologyEdge] = {}
    pending: dict[str, tuple[str, TopologyEdge]] = {}
    events: dict[str, TopologyEvent] = {}
    warnings: list[TopologyWarning] = []
    claimed_by: dict[str, str] = {}

    authority: dict[str, int] = {}

    for fragment in fragments:
        source_id = fragment.meta.source_id if fragment.meta else ""
        fragment_cluster = fragment.meta.cluster_id if fragment.meta else ""
        for node in fragment.nodes:
            rank = _claim_authority(node, fragment_cluster)
            existing = nodes.get(node.id)
            if existing is None:
                nodes[node.id] = node
                claimed_by[node.id] = source_id
                authority[node.id] = rank
                continue
            if existing.model_dump() == node.model_dump():
                continue
            if rank > authority.get(node.id, 0):
                nodes[node.id] = _merge_node(node, existing)
                claimed_by[node.id] = source_id
                authority[node.id] = rank
                continue
            if rank < authority.get(node.id, 0):
                nodes[node.id] = _merge_node(existing, node)
                continue

            contested = _contested_fields(existing, node)
            nodes[node.id] = _merge_node(existing, node)
            if not contested:
                continue
            warnings.append(
                TopologyWarning(
                    source_id=source_id,
                    code="node_id_conflict",
                    message=(
                        f"Node '{node.id}' claimed by both '{claimed_by.get(node.id, 'unknown')}' "
                        f"and '{source_id}', which disagree on {', '.join(contested)}; "
                        "keeping the first"
                    ),
                )
            )
        for edge in fragment.edges:
            edges.setdefault(edge.id, edge)
        for edge in fragment.pending_edges:
            pending.setdefault(edge.id, (source_id, edge))
        for event in fragment.events:
            events.setdefault(event.id, event)

    for source_id, edge in pending.values():
        resolved = _resolve_pending_edge(edge, nodes)
        if resolved is None:
            warnings.append(
                TopologyWarning(
                    source_id=source_id,
                    code="edge_unresolved",
                    message=(
                        f"Edge '{edge.id}' names '{edge.source_id}' → '{edge.target_id}', "
                        "and no source in the estate reported a node matching one of them"
                    ),
                )
            )
            continue
        edges.setdefault(resolved.id, resolved)

    return list(nodes.values()), list(edges.values()), list(events.values()), warnings


def _revision(nodes: list[TopologyNode], edges: list[TopologyEdge]) -> str:
    """Digest of merged content, so a consumer can tell change from re-poll."""
    payload = json.dumps(
        {
            "nodes": sorted(
                (node.model_dump(by_alias=True, mode="json") for node in nodes), key=str
            ),
            "edges": sorted(
                (edge.model_dump(by_alias=True, mode="json") for edge in edges), key=str
            ),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
