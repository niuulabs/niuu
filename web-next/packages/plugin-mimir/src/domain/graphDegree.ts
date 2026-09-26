/** Degree (connection count) helpers for the Explore panel's "Most connected" list. */
import type { GraphNode, MimirGraph } from './api-types';

export interface DegreeEntry {
  node: GraphNode;
  degree: number;
}

/** Number of edges touching `nodeId` (undirected). */
export function degreeOf(graph: MimirGraph, nodeId: string): number {
  return graph.edges.filter((e) => e.source === nodeId || e.target === nodeId).length;
}

/** Top `n` nodes by degree, highest first; ties keep graph node order. */
export function topConnected(graph: MimirGraph, n: number): DegreeEntry[] {
  return graph.nodes
    .map((node) => ({ node, degree: degreeOf(graph, node.id) }))
    .filter((entry) => entry.degree > 0)
    .sort((a, b) => b.degree - a.degree)
    .slice(0, n);
}
