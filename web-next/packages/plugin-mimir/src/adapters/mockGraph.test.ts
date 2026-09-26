import { describe, it, expect } from 'vitest';
import {
  mulberry32,
  generateMockMemoryGraph,
  generateMockLiveActivity,
  CONTRADICTION_EDGE_KINDS,
  TYPED_EDGE_KINDS,
  MEMORY_KINDS,
} from './mockGraph';
import type { GraphNode } from '../domain/api-types';

const NOW = Date.parse('2026-04-19T12:00:00Z');
const MOUNTS = ['local', 'shared', 'platform', 'forge'];

function baseOptions(overrides: Partial<Parameters<typeof generateMockMemoryGraph>[0]> = {}) {
  return {
    seed: 42,
    count: 300,
    mounts: MOUNTS,
    seedNodes: [] as GraphNode[],
    now: NOW,
    ...overrides,
  };
}

function seedNode(overrides: Partial<GraphNode> & Pick<GraphNode, 'id' | 'title'>): GraphNode {
  return {
    category: 'arch',
    updatedAt: '2026-04-19T00:00:00Z',
    firstSeen: '2026-01-01T00:00:00Z',
    confidence: null,
    ...overrides,
  };
}

describe('mulberry32', () => {
  it('is deterministic for a given seed', () => {
    const a = mulberry32(1);
    const b = mulberry32(1);
    const seqA = Array.from({ length: 10 }, () => a());
    const seqB = Array.from({ length: 10 }, () => b());
    expect(seqA).toEqual(seqB);
  });

  it('produces values in [0, 1)', () => {
    const rng = mulberry32(7);
    for (let i = 0; i < 100; i += 1) {
      const v = rng();
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });

  it('differs across seeds', () => {
    const a = mulberry32(1)();
    const b = mulberry32(2)();
    expect(a).not.toBe(b);
  });
});

describe('generateMockMemoryGraph', () => {
  it('generates exactly `count` synthetic nodes', () => {
    const graph = generateMockMemoryGraph(baseOptions({ count: 250 }));
    expect(graph.nodes).toHaveLength(250);
  });

  it('is deterministic for the same seed and now', () => {
    const a = generateMockMemoryGraph(baseOptions());
    const b = generateMockMemoryGraph(baseOptions());
    expect(a).toEqual(b);
  });

  it('differs for a different seed', () => {
    const a = generateMockMemoryGraph(baseOptions({ seed: 1 }));
    const b = generateMockMemoryGraph(baseOptions({ seed: 2 }));
    expect(a.nodes.map((n) => n.title)).not.toEqual(b.nodes.map((n) => n.title));
  });

  it('spreads node kinds across the full memory-kind vocabulary', () => {
    const graph = generateMockMemoryGraph(baseOptions({ count: 600 }));
    const kinds = new Set(graph.nodes.map((n) => n.kind));
    for (const kind of MEMORY_KINDS) {
      expect(kinds.has(kind)).toBe(true);
    }
  });

  it('every node carries a mount field from one of the given mounts', () => {
    const graph = generateMockMemoryGraph(baseOptions());
    for (const node of graph.nodes) {
      expect(MOUNTS).toContain(node.mount);
    }
  });

  it('every node id matches its path', () => {
    const graph = generateMockMemoryGraph(baseOptions());
    for (const node of graph.nodes) {
      expect(node.id).toBe(node.path);
    }
  });

  it('every node has firstSeen <= updatedAt <= now, within the last ~60 days', () => {
    const graph = generateMockMemoryGraph(baseOptions());
    const sixtyOneDaysMs = 61 * 24 * 60 * 60 * 1000;
    for (const node of graph.nodes) {
      const firstSeenMs = Date.parse(node.firstSeen!);
      const updatedMs = Date.parse(node.updatedAt!);
      expect(firstSeenMs).toBeLessThanOrEqual(updatedMs);
      expect(updatedMs).toBeLessThanOrEqual(NOW);
      expect(NOW - firstSeenMs).toBeLessThanOrEqual(sixtyOneDaysMs);
    }
  });

  it('mixes confidence values including null', () => {
    const graph = generateMockMemoryGraph(baseOptions({ count: 600 }));
    const values = new Set(graph.nodes.map((n) => n.confidence));
    expect(values.has('high')).toBe(true);
    expect(values.has('medium')).toBe(true);
    expect(values.has('low')).toBe(true);
    expect(values.has(null)).toBe(true);
  });

  it('every edge type is a recognised typed or contradiction relation', () => {
    const graph = generateMockMemoryGraph(baseOptions({ count: 400 }));
    const recognised = new Set<string>([...TYPED_EDGE_KINDS, ...CONTRADICTION_EDGE_KINDS]);
    for (const edge of graph.edges) {
      expect(recognised.has(edge.type ?? '')).toBe(true);
    }
  });

  it('includes at least one contradiction edge', () => {
    const graph = generateMockMemoryGraph(baseOptions({ count: 400 }));
    const hasContradiction = graph.edges.some((edge) =>
      (CONTRADICTION_EDGE_KINDS as readonly string[]).includes(edge.type ?? ''),
    );
    expect(hasContradiction).toBe(true);
  });

  it('every edge references a real node id (synthetic or seed)', () => {
    const seedNodes: GraphNode[] = [seedNode({ id: 'local:/hero', title: 'Hero' })];
    const graph = generateMockMemoryGraph(baseOptions({ seedNodes, count: 300 }));
    const ids = new Set([...seedNodes.map((n) => n.id), ...graph.nodes.map((n) => n.id)]);
    for (const edge of graph.edges) {
      expect(ids.has(edge.source)).toBe(true);
      expect(ids.has(edge.target)).toBe(true);
    }
  });

  it('never emits a self-loop or an exact duplicate edge pair', () => {
    const graph = generateMockMemoryGraph(baseOptions({ count: 400 }));
    const seen = new Set<string>();
    for (const edge of graph.edges) {
      expect(edge.source).not.toBe(edge.target);
      const key = `${edge.source}->${edge.target}`;
      expect(seen.has(key)).toBe(false);
      seen.add(key);
    }
  });

  it('weaves seed nodes into the graph as edge endpoints', () => {
    const seedNodes: GraphNode[] = [
      seedNode({ id: 'local:/hero-a', title: 'Hero A', kind: 'topic' }),
      seedNode({ id: 'local:/hero-b', title: 'Hero B', kind: 'topic' }),
    ];
    const graph = generateMockMemoryGraph(baseOptions({ seedNodes, count: 500 }));
    const seedIds = new Set(seedNodes.map((n) => n.id));
    const touchesSeed = graph.edges.some(
      (edge) => seedIds.has(edge.source) || seedIds.has(edge.target),
    );
    expect(touchesSeed).toBe(true);
  });
});

describe('generateMockLiveActivity', () => {
  const targets = [
    { mount: 'local', path: '/arch/overview' },
    { mount: 'shared', path: '/api/overview' },
    { mount: 'platform', path: '/infra/k8s' },
  ];

  it('returns one event per target, newest first', () => {
    const events = generateMockLiveActivity(targets, NOW);
    expect(events).toHaveLength(targets.length);
    for (let i = 1; i < events.length; i += 1) {
      expect(events[i - 1]!.timestamp >= events[i]!.timestamp).toBe(true);
    }
  });

  it('every event is a read or write with a matching mount/path', () => {
    const events = generateMockLiveActivity(targets, NOW);
    for (const event of events) {
      expect(['read', 'write']).toContain(event.kind);
      expect(targets.some((t) => t.mount === event.mount && t.path === event.path)).toBe(true);
    }
  });

  it('includes a null actor for at least one seed (mixed resident/unknown actors)', () => {
    const manyTargets = Array.from({ length: 12 }, (_, i) => ({
      mount: 'local',
      path: `/p${i}`,
    }));
    const events = generateMockLiveActivity(manyTargets, NOW);
    expect(events.some((e) => e.actor === null)).toBe(true);
    expect(events.some((e) => typeof e.actor === 'string')).toBe(true);
  });

  it('excludes events older than `since`', () => {
    const all = generateMockLiveActivity(targets, NOW);
    const cutoff = all[Math.floor(all.length / 2)]!.timestamp;
    const filtered = generateMockLiveActivity(targets, NOW, cutoff);
    expect(filtered.every((e) => e.timestamp >= cutoff)).toBe(true);
    expect(filtered.length).toBeLessThanOrEqual(all.length);
  });

  it('returns an empty array for no targets', () => {
    expect(generateMockLiveActivity([], NOW)).toEqual([]);
  });
});
