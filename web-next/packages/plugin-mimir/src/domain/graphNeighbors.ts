/**
 * Pure BFS over `MimirGraph.edges` for the Focus panel's "Links" list.
 *
 * `IPageStore.getRelated` takes no mount and 404s for a page kept on a
 * mount other than the serving instance's own — it cannot be used to walk
 * links for an arbitrary focused node. The graph already has every edge
 * (both directions), scoped correctly, so BFS over it is used instead.
 */
import type { GraphNode, MimirGraph } from './api-types';
import { relationLabel } from './relationLabel';

export interface NeighborLink {
  node: GraphNode;
  /** Humanised relation label, or null for an untyped/structural edge. */
  relation: string | null;
  /** Hop distance from the focus node (1, 2, or 3). */
  hops: number;
}

/**
 * Direct neighbours of `focusId` within `depth` undirected hops, nearest
 * first. Each neighbour keeps the relation label of the edge that first
 * reached it. Returns [] when the focus node is not present in the graph.
 */
export function neighborsWithinDepth(
  graph: MimirGraph,
  focusId: string,
  depth: 1 | 2 | 3,
): NeighborLink[] {
  if (!graph.nodes.some((n) => n.id === focusId)) return [];

  const nodesById = new Map(graph.nodes.map((n) => [n.id, n]));
  const visited = new Set<string>([focusId]);
  const results: NeighborLink[] = [];
  let frontier = [focusId];

  for (let hop = 1; hop <= depth; hop++) {
    const nextFrontier: string[] = [];
    for (const current of frontier) {
      for (const edge of graph.edges) {
        const neighborId =
          edge.source === current ? edge.target : edge.target === current ? edge.source : null;
        if (neighborId === null || visited.has(neighborId)) continue;
        const node = nodesById.get(neighborId);
        if (!node) continue;
        visited.add(neighborId);
        nextFrontier.push(neighborId);
        results.push({ node, relation: relationLabel(edge.type ?? null), hops: hop });
      }
    }
    frontier = nextFrontier;
    if (frontier.length === 0) break;
  }

  return results;
}

/**
 * The set of node ids reachable in exactly one hop from any of `nodeIds`,
 * excluding `nodeIds` themselves. Used for the Ask panel's "<k> linked
 * pages lit" line.
 */
export function linkedNeighborCount(graph: MimirGraph, nodeIds: string[]): number {
  const source = new Set(nodeIds);
  const linked = new Set<string>();
  for (const id of nodeIds) {
    for (const link of neighborsWithinDepth(graph, id, 1)) {
      if (!source.has(link.node.id)) linked.add(link.node.id);
    }
  }
  return linked.size;
}
