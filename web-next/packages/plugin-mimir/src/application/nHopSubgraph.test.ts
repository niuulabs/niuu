import { describe, it, expect } from 'vitest';
import { nHopSubgraph } from './nHopSubgraph';
import type { MimirGraph } from '../domain/api-types';

/*
 * Test graph topology:
 *
 *   A -- B -- C
 *        |
 *        D -- E
 *
 *   F (isolated)
 */
const GRAPH: MimirGraph = {
  nodes: [
    { id: 'A', title: 'Node A', category: 'cat' },
    { id: 'B', title: 'Node B', category: 'cat' },
    { id: 'C', title: 'Node C', category: 'cat' },
    { id: 'D', title: 'Node D', category: 'cat' },
    { id: 'E', title: 'Node E', category: 'cat' },
    { id: 'F', title: 'Node F', category: 'cat' },
  ],
  edges: [
    { source: 'A', target: 'B' },
    { source: 'B', target: 'C' },
    { source: 'B', target: 'D' },
    { source: 'D', target: 'E' },
  ],
};

describe('nHopSubgraph', () => {
  it('returns empty graph when focusId is not in the graph', () => {
    const sub = nHopSubgraph(GRAPH, 'MISSING', 2);
    expect(sub.nodes).toHaveLength(0);
    expect(sub.edges).toHaveLength(0);
  });

  it('0-hop returns only the focus node with no edges', () => {
    const sub = nHopSubgraph(GRAPH, 'B', 0);
    expect(sub.nodes.map((n) => n.id)).toEqual(['B']);
    expect(sub.edges).toHaveLength(0);
  });

  it('1-hop from B includes A, C, D and edges A-B, B-C, B-D', () => {
    const sub = nHopSubgraph(GRAPH, 'B', 1);
    const ids = new Set(sub.nodes.map((n) => n.id));
    expect(ids).toContain('A');
    expect(ids).toContain('B');
    expect(ids).toContain('C');
    expect(ids).toContain('D');
    expect(ids).not.toContain('E');
    expect(ids).not.toContain('F');
    expect(sub.edges.length).toBe(3);
  });

  it('2-hops from B includes A, C, D, E', () => {
    const sub = nHopSubgraph(GRAPH, 'B', 2);
    const ids = new Set(sub.nodes.map((n) => n.id));
    expect(ids).toContain('E');
    expect(ids).not.toContain('F');
  });

  it('2-hops from A includes A, B, C, D but not E (3 hops away)', () => {
    const sub = nHopSubgraph(GRAPH, 'A', 2);
    const ids = new Set(sub.nodes.map((n) => n.id));
    expect(ids).toContain('A');
    expect(ids).toContain('B');
    expect(ids).toContain('C');
    expect(ids).toContain('D');
    expect(ids).not.toContain('E');
  });

  it('3-hops from A reaches everything connected (A,B,C,D,E)', () => {
    const sub = nHopSubgraph(GRAPH, 'A', 3);
    const ids = new Set(sub.nodes.map((n) => n.id));
    expect(ids).toContain('E');
    expect(ids).not.toContain('F'); // F is isolated
  });

  it('only includes edges where both endpoints are in the subgraph', () => {
    const sub = nHopSubgraph(GRAPH, 'A', 1);
    for (const edge of sub.edges) {
      const nodeIds = new Set(sub.nodes.map((n) => n.id));
      expect(nodeIds).toContain(edge.source);
      expect(nodeIds).toContain(edge.target);
    }
  });

  it('isolated node F with any hops returns only F with no edges', () => {
    const sub = nHopSubgraph(GRAPH, 'F', 5);
    expect(sub.nodes.map((n) => n.id)).toEqual(['F']);
    expect(sub.edges).toHaveLength(0);
  });

  it('handles graph with no edges gracefully', () => {
    const noEdges: MimirGraph = {
      nodes: [{ id: 'X', title: 'X', category: 'c' }],
      edges: [],
    };
    const sub = nHopSubgraph(noEdges, 'X', 3);
    expect(sub.nodes).toHaveLength(1);
    expect(sub.edges).toHaveLength(0);
  });
});
