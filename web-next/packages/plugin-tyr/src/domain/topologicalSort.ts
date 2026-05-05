/**
 * Topological sort and cycle detection for workflow DAGs.
 *
 * - `topologicalSort` uses Kahn's algorithm (BFS) for deterministic layer output.
 * - `detectCycle` uses DFS with three-colour marking.
 *
 * Both functions accept plain arrays of IDs and edge descriptors so they can
 * be used independently of the Zod schemas.
 *
 * Owner: plugin-tyr.
 */

export interface TopologicalLayer {
  /** Zero-based depth from entry nodes. */
  depth: number;
  /** Node IDs at this depth, sorted lexicographically for determinism. */
  nodeIds: string[];
}

type Edge = { source: string; target: string };

/**
 * Compute topological layers using Kahn's algorithm.
 *
 * Nodes within each layer are sorted lexicographically so the output is
 * deterministic regardless of insertion order.
 *
 * Nodes that form or depend on a cycle are silently omitted — call
 * `detectCycle()` first if you need to surface that error.
 */
export function topologicalSort(nodeIds: string[], edges: ReadonlyArray<Edge>): TopologicalLayer[] {
  const inDegree = new Map<string, number>(nodeIds.map((id) => [id, 0]));
  const adjacency = new Map<string, string[]>(nodeIds.map((id) => [id, []]));

  for (const edge of edges) {
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1);
    adjacency.get(edge.source)?.push(edge.target);
  }

  const layers: TopologicalLayer[] = [];
  let queue = nodeIds.filter((id) => (inDegree.get(id) ?? 0) === 0).sort();
  let depth = 0;

  while (queue.length > 0) {
    layers.push({ depth, nodeIds: [...queue] });
    const next: string[] = [];
    for (const nodeId of queue) {
      for (const neighbor of adjacency.get(nodeId) ?? []) {
        const deg = (inDegree.get(neighbor) ?? 0) - 1;
        inDegree.set(neighbor, deg);
        if (deg === 0) next.push(neighbor);
      }
    }
    queue = next.sort();
    depth++;
  }

  return layers;
}

/**
 * Detect all nodes that participate in at least one directed cycle.
 *
 * Uses DFS with three-colour marking (WHITE/GRAY/BLACK).
 * Returns an empty array when the graph is acyclic.
 */
export function detectCycle(nodeIds: string[], edges: ReadonlyArray<Edge>): string[] {
  const adjacency = new Map<string, string[]>(nodeIds.map((id) => [id, []]));
  for (const edge of edges) {
    adjacency.get(edge.source)?.push(edge.target);
  }

  const WHITE = 0 as const;
  const GRAY = 1 as const;
  const BLACK = 2 as const;
  type Color = typeof WHITE | typeof GRAY | typeof BLACK;

  const color = new Map<string, Color>(nodeIds.map((id) => [id, WHITE]));
  const cycleNodes = new Set<string>();

  function dfs(node: string): boolean {
    color.set(node, GRAY);
    for (const neighbor of adjacency.get(node) ?? []) {
      if (color.get(neighbor) === GRAY) {
        cycleNodes.add(neighbor);
        cycleNodes.add(node);
        return true;
      }
      if (color.get(neighbor) === WHITE && dfs(neighbor)) {
        cycleNodes.add(node);
        return true;
      }
    }
    color.set(node, BLACK);
    return false;
  }

  for (const id of nodeIds) {
    if (color.get(id) === WHITE) dfs(id);
  }

  return Array.from(cycleNodes).sort();
}
