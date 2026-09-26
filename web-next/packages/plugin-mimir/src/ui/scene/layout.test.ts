import { describe, it, expect } from 'vitest';
import {
  computeLayout,
  computeLayoutUncached,
  flattenTo2D,
  radiusForDegree,
  layoutPoints,
} from './layout';
import type { MimirGraph } from '../../domain/api-types';

function plainNode(id: string) {
  return {
    id,
    title: id,
    category: 'x',
    path: `/${id}`,
    mount: 'local',
    updatedAt: '2026-04-01T00:00:00Z',
    firstSeen: '2026-03-01T00:00:00Z',
    confidence: null,
  };
}

function syntheticGraph(nodeCount: number, edgeCount: number, mountCount = 4): MimirGraph {
  const mounts = Array.from({ length: mountCount }, (_, i) => `mount-${i}`);
  const nodes = Array.from({ length: nodeCount }, (_, i) => ({
    id: `${mounts[i % mountCount]}:/n${i}`,
    title: `Node ${i}`,
    category: 'topic',
    kind: 'topic',
    path: `/n${i}`,
    mount: mounts[i % mountCount]!,
    updatedAt: '2026-04-01T00:00:00Z',
    firstSeen: '2026-03-01T00:00:00Z',
    confidence: null,
  }));
  const edges = Array.from({ length: edgeCount }, (_, i) => ({
    source: nodes[i % nodeCount]!.id,
    target: nodes[(i * 7 + 1) % nodeCount]!.id,
    type: 'depends_on',
  }));
  return { nodes, edges };
}

describe('computeLayoutUncached', () => {
  it('produces one position per node', () => {
    const graph = syntheticGraph(50, 80);
    const layout = computeLayoutUncached(graph);
    expect(layout.nodes.size).toBe(50);
    for (const node of graph.nodes) {
      expect(layout.nodes.has(node.id)).toBe(true);
    }
  });

  it('produces finite, non-overlapping-by-construction positions', () => {
    const graph = syntheticGraph(30, 40);
    const layout = computeLayoutUncached(graph);
    for (const node of layout.nodes.values()) {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
      expect(Number.isFinite(node.position.z)).toBe(true);
    }
  });

  it('is deterministic for the same graph content', () => {
    const a = computeLayoutUncached(syntheticGraph(60, 100));
    const b = computeLayoutUncached(syntheticGraph(60, 100));
    for (const [id, nodeA] of a.nodes) {
      const nodeB = b.nodes.get(id)!;
      expect(nodeB.position.x).toBeCloseTo(nodeA.position.x, 6);
      expect(nodeB.position.y).toBeCloseTo(nodeA.position.y, 6);
      expect(nodeB.position.z).toBeCloseTo(nodeA.position.z, 6);
    }
  });

  it('computes one mount centre per distinct mount', () => {
    const graph = syntheticGraph(40, 40, 3);
    const layout = computeLayoutUncached(graph);
    expect(layout.mountCentres.size).toBe(3);
  });

  it('records degree from edge endpoints', () => {
    const graph: MimirGraph = {
      nodes: [{ ...plainNode('a') }, { ...plainNode('b') }, { ...plainNode('c') }],
      edges: [
        { source: 'a', target: 'b' },
        { source: 'a', target: 'c' },
      ],
    };
    const layout = computeLayoutUncached(graph);
    expect(layout.nodes.get('a')?.degree).toBe(2);
    expect(layout.nodes.get('b')?.degree).toBe(1);
    expect(layout.nodes.get('c')?.degree).toBe(1);
  });

  it('clusters nodes closer to their own mount centre than a distant mount centre, on average', () => {
    const graph = syntheticGraph(400, 600, 4);
    const layout = computeLayoutUncached(graph);
    let ownCloser = 0;
    let total = 0;
    for (const node of layout.nodes.values()) {
      const own = layout.mountCentres.get(node.mount)!;
      const distOwn = Math.hypot(
        node.position.x - own.x,
        node.position.y - own.y,
        node.position.z - own.z,
      );
      for (const [mount, centre] of layout.mountCentres) {
        if (mount === node.mount) continue;
        const distOther = Math.hypot(
          node.position.x - centre.x,
          node.position.y - centre.y,
          node.position.z - centre.z,
        );
        total += 1;
        if (distOwn < distOther) ownCloser += 1;
      }
    }
    expect(ownCloser / total).toBeGreaterThan(0.9);
  });

  it('ignores edges referencing unknown node ids rather than throwing', () => {
    const graph: MimirGraph = {
      nodes: [{ id: 'a', title: 'a', category: 'x' }],
      edges: [{ source: 'a', target: 'does-not-exist' }],
    };
    expect(() => computeLayoutUncached(graph)).not.toThrow();
  });

  it('handles an empty graph', () => {
    const layout = computeLayoutUncached({ nodes: [], edges: [] });
    expect(layout.nodes.size).toBe(0);
  });

  it('handles a single mount without dividing by a degenerate spread', () => {
    const layout = computeLayoutUncached(syntheticGraph(20, 10, 1));
    expect(layout.mountCentres.size).toBe(1);
    for (const node of layout.nodes.values()) {
      expect(Number.isFinite(node.position.x)).toBe(true);
    }
  });
});

describe('computeLayout (memoised)', () => {
  it('returns the identical object for the same graph reference', () => {
    const graph = syntheticGraph(20, 20);
    expect(computeLayout(graph)).toBe(computeLayout(graph));
  });

  it('recomputes for a different graph object even with identical content', () => {
    const a = syntheticGraph(20, 20);
    const b = syntheticGraph(20, 20);
    expect(computeLayout(a)).not.toBe(computeLayout(b));
  });
});

describe('flattenTo2D', () => {
  it('zeroes y on every node and mount centre while keeping x/z', () => {
    const layout = computeLayoutUncached(syntheticGraph(30, 30));
    const flat = flattenTo2D(layout);
    for (const [id, node] of flat.nodes) {
      const original = layout.nodes.get(id)!;
      expect(node.position.y).toBe(0);
      expect(node.position.x).toBe(original.position.x);
      expect(node.position.z).toBe(original.position.z);
    }
    for (const [mount, centre] of flat.mountCentres) {
      expect(centre.y).toBe(0);
      expect(centre.x).toBe(layout.mountCentres.get(mount)!.x);
    }
  });
});

describe('layoutPoints', () => {
  it('returns one point per node', () => {
    const layout = computeLayoutUncached(syntheticGraph(15, 10));
    expect(layoutPoints(layout)).toHaveLength(15);
  });
});

describe('radiusForDegree', () => {
  it('returns the minimum radius at degree 0', () => {
    expect(radiusForDegree(0, 1, 5, 20)).toBe(1);
  });

  it('returns the maximum radius at or above the max degree', () => {
    expect(radiusForDegree(20, 1, 5, 20)).toBe(5);
    expect(radiusForDegree(100, 1, 5, 20)).toBe(5);
  });

  it('interpolates linearly in between', () => {
    expect(radiusForDegree(10, 1, 5, 20)).toBeCloseTo(3);
  });

  it('returns the minimum radius when maxDegree is zero', () => {
    expect(radiusForDegree(0, 2, 8, 0)).toBe(2);
  });
});

describe('performance', () => {
  // Solo, this runs in ~0.8-1.2s on a dev machine. The bound here is set
  // much higher than that on purpose: run inside the full suite (dozens of
  // files, shared CPU across parallel workers), the same computation has
  // been observed up to ~1.9s from scheduling contention alone, not from
  // the algorithm getting slower. A "generous" bound has to absorb that
  // noise or it becomes a flaky test rather than a real perf regression
  // guard.
  it('lays out 5,000 nodes / 15,000 edges in well under a generous bound', () => {
    const graph = syntheticGraph(5000, 15000, 8);
    const start = performance.now();
    const layout = computeLayoutUncached(graph);
    const elapsedMs = performance.now() - start;
    expect(layout.nodes.size).toBe(5000);

    console.log(`[layout perf] 5000 nodes / 15000 edges: ${elapsedMs.toFixed(0)}ms`);
    expect(elapsedMs).toBeLessThan(4000);
  });
});
