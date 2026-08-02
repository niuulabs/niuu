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
                    logger.warning("Topology fragment unavailable from %s: %s", instance.name, exc)
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


def _merge(
    fragments: list[ObservatoryFragment],
) -> tuple[list[TopologyNode], list[TopologyEdge], list[TopologyEvent], list[TopologyWarning]]:
    """Combine fragments, keeping the first claim on any contested id.

    Two sources claiming the same node id is a real condition — an overlapping
    discovery scope, or two hosts announcing the same name — and it is reported
    rather than resolved silently, because whichever way it is resolved the
    graph is showing something an operator did not intend.
    """
    nodes: dict[str, TopologyNode] = {}
    edges: dict[str, TopologyEdge] = {}
    events: dict[str, TopologyEvent] = {}
    warnings: list[TopologyWarning] = []
    claimed_by: dict[str, str] = {}

    for fragment in fragments:
        source_id = fragment.meta.source_id if fragment.meta else ""
        for node in fragment.nodes:
            existing = nodes.get(node.id)
            if existing is None:
                nodes[node.id] = node
                claimed_by[node.id] = source_id
                continue
            if existing.model_dump() == node.model_dump():
                continue
            warnings.append(
                TopologyWarning(
                    source_id=source_id,
                    code="node_id_conflict",
                    message=(
                        f"Node '{node.id}' claimed by both '{claimed_by.get(node.id, 'unknown')}' "
                        f"and '{source_id}'; keeping the first"
                    ),
                )
            )
        for edge in fragment.edges:
            edges.setdefault(edge.id, edge)
        for event in fragment.events:
            events.setdefault(event.id, event)

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
