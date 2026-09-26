/** Pure BFS shortest-path — shift-click path tracing in Focus mode. */
import type { MimirGraph } from './api-types';

/**
 * Shortest undirected path from `fromId` to `toId`, as an ordered list of
 * node ids including both endpoints. Returns [] when either endpoint is
 * missing, they are equal, or no path exists.
 */
export function shortestPath(graph: MimirGraph, fromId: string, toId: string): string[] {
  if (fromId === toId) return [];
  const hasFrom = graph.nodes.some((n) => n.id === fromId);
  const hasTo = graph.nodes.some((n) => n.id === toId);
  if (!hasFrom || !hasTo) return [];

  const adjacency = new Map<string, string[]>();
  for (const edge of graph.edges) {
    adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]);
    adjacency.set(edge.target, [...(adjacency.get(edge.target) ?? []), edge.source]);
  }

  const cameFrom = new Map<string, string>();
  const visited = new Set<string>([fromId]);
  const queue: string[] = [fromId];

  while (queue.length > 0) {
    const current = queue.shift()!;
    if (current === toId) break;
    for (const neighbor of adjacency.get(current) ?? []) {
      if (visited.has(neighbor)) continue;
      visited.add(neighbor);
      cameFrom.set(neighbor, current);
      queue.push(neighbor);
    }
  }

  if (!visited.has(toId)) return [];

  const path: string[] = [toId];
  let cursor = toId;
  while (cursor !== fromId) {
    const prev = cameFrom.get(cursor);
    if (!prev) return [];
    path.push(prev);
    cursor = prev;
  }
  return path.reverse();
}
