/**
 * N-hop subgraph extraction.
 *
 * Given a full knowledge graph and a focus node ID, returns the subgraph
 * containing only nodes reachable within `hops` traversal steps (treating
 * edges as undirected for exploration purposes).
 */

import type { MimirGraph } from '../domain/api-types';

/**
 * Extract the subgraph reachable within `hops` undirected steps from `focusId`.
 *
 * Returns an empty graph when focusId is not present in the graph.
 * With hops=0 the returned graph contains only the focus node (no edges).
 * With hops=1 the returned graph contains the focus node, its immediate
 * neighbours, and all edges among them.
 */
export function nHopSubgraph(graph: MimirGraph, focusId: string, hops: number): MimirGraph {
  const nodeExists = graph.nodes.some((n) => n.id === focusId);
  if (!nodeExists) {
    return { nodes: [], edges: [] };
  }

  const reachable = new Set<string>([focusId]);
  let frontier = new Set<string>([focusId]);

  for (let hop = 0; hop < hops; hop++) {
    const nextFrontier = new Set<string>();

    for (const edge of graph.edges) {
      if (frontier.has(edge.source) && !reachable.has(edge.target)) {
        nextFrontier.add(edge.target);
        reachable.add(edge.target);
      }
      if (frontier.has(edge.target) && !reachable.has(edge.source)) {
        nextFrontier.add(edge.source);
        reachable.add(edge.source);
      }
    }

    frontier = nextFrontier;
    if (frontier.size === 0) break;
  }

  const nodes = graph.nodes.filter((n) => reachable.has(n.id));
  const edges = graph.edges.filter((e) => reachable.has(e.source) && reachable.has(e.target));

  return { nodes, edges };
}
