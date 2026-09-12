import { describe, expect, it } from 'vitest';
import { filterGraph } from './filterGraph';
import { layoutForceDirected, projectPosition } from './graphGeometry';

const graph = {
  nodes: [
    {
      id: 'a',
      path: 'research/a',
      title: 'Comparison',
      category: 'research',
      kind: 'observation',
      summary: 'Hybrid retrieval',
      mount: 'gbrain',
    },
    { id: 'b', title: 'Retrieval', category: 'concepts', kind: 'concept', mount: 'local' },
  ],
  edges: [{ source: 'a', target: 'b', type: 'wikilink' }],
};
describe('graph exploration', () => {
  it('intersects metadata search and facets without retaining dangling edges', () => {
    expect(filterGraph(graph, 'GBRAIN hybrid', new Set(), '').nodes.map((n) => n.id)).toEqual([
      'a',
    ]);
    expect(filterGraph(graph, '', new Set(['research']), '').nodes.map((n) => n.id)).toEqual(['b']);
    expect(filterGraph(graph, '', new Set(), 'concept').edges).toEqual([]);
    expect(filterGraph(graph, 'missing', new Set(), '').nodes).toEqual([]);
  });
  it('positions nodes in real depth and changes perspective when orbiting', () => {
    const positions = layoutForceDirected(graph.nodes, graph.edges);
    expect(positions.some((p) => p.z !== 0)).toBe(true);
    const front = projectPosition(positions[0]!, 0, 0);
    const rotated = projectPosition(positions[0]!, Math.PI / 2, 0);
    expect(rotated.x).not.toBeCloseTo(front.x);
    expect(Number.isFinite(rotated.scale)).toBe(true);
  });
});
