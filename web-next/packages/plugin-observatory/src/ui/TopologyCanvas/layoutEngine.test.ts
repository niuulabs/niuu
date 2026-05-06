import { describe, it, expect } from 'vitest';
import { hashAngle, computeLayout, zoneRadius } from './layoutEngine';
import { LAYOUT } from './config';
import type { Topology } from '../../domain';

// ── Shared test topology ──────────────────────────────────────────────────────

const TEST_TOPOLOGY: Topology = {
  timestamp: '2026-04-19T00:00:00Z',
  nodes: [
    { id: 'mimir-0', typeId: 'mimir', label: 'mímir-0', parentId: null, status: 'healthy' },
    { id: 'realm-asgard', typeId: 'realm', label: 'asgard', parentId: null, status: 'healthy' },
    { id: 'realm-vanaheim', typeId: 'realm', label: 'vanaheim', parentId: null, status: 'healthy' },
    {
      id: 'cluster-vk',
      typeId: 'cluster',
      label: 'valaskjálf',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    {
      id: 'host-mjolnir',
      typeId: 'host',
      label: 'mjölnir',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    { id: 'tyr-0', typeId: 'tyr', label: 'tyr-0', parentId: 'cluster-vk', status: 'healthy' },
    {
      id: 'bifrost-0',
      typeId: 'bifrost',
      label: 'bifröst-0',
      parentId: 'cluster-vk',
      status: 'healthy',
    },
    {
      id: 'volundr-0',
      typeId: 'volundr',
      label: 'völundr-0',
      parentId: 'cluster-vk',
      status: 'healthy',
    },
    {
      id: 'raid-0',
      typeId: 'raid',
      label: 'raid-omega',
      parentId: 'cluster-vk',
      status: 'observing',
    },
    {
      id: 'ravn-huginn',
      typeId: 'ravn_long',
      label: 'huginn',
      parentId: 'host-mjolnir',
      status: 'healthy',
    },
  ],
  edges: [
    { id: 'e1', sourceId: 'tyr-0', targetId: 'volundr-0', kind: 'solid' },
    { id: 'e2', sourceId: 'tyr-0', targetId: 'raid-0', kind: 'dashed-anim' },
    { id: 'e3', sourceId: 'ravn-huginn', targetId: 'mimir-0', kind: 'dashed-long' },
    { id: 'e4', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
    { id: 'e5', sourceId: 'raid-0', targetId: 'ravn-huginn', kind: 'raid' },
  ],
};

// ── hashAngle ─────────────────────────────────────────────────────────────────

describe('hashAngle', () => {
  it('returns a value in [0, 2π)', () => {
    for (const id of ['realm-asgard', 'realm-midgard', 'cluster-vk', 'host-a', 'x']) {
      const angle = hashAngle(id);
      expect(angle).toBeGreaterThanOrEqual(0);
      expect(angle).toBeLessThan(Math.PI * 2);
    }
  });

  it('is deterministic — same id always yields same angle', () => {
    const id = 'realm-asgard';
    expect(hashAngle(id)).toBe(hashAngle(id));
    expect(hashAngle(id)).toBe(hashAngle(id));
  });

  it('produces different angles for different ids', () => {
    const a = hashAngle('realm-asgard');
    const b = hashAngle('realm-midgard');
    const c = hashAngle('realm-vanaheim');
    expect(a).not.toBe(b);
    expect(b).not.toBe(c);
    expect(a).not.toBe(c);
  });

  it('handles single-character ids', () => {
    expect(() => hashAngle('a')).not.toThrow();
    const angle = hashAngle('a');
    expect(angle).toBeGreaterThanOrEqual(0);
  });

  it('handles empty string without throwing', () => {
    expect(() => hashAngle('')).not.toThrow();
  });
});

// ── computeLayout ─────────────────────────────────────────────────────────────

describe('computeLayout', () => {
  it('returns a position for every node in the topology', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    for (const node of TEST_TOPOLOGY.nodes) {
      expect(positions.has(node.id)).toBe(true);
    }
  });

  it('places Mímir at the origin (0, 0)', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    const mimiPos = positions.get('mimir-0');
    expect(mimiPos).toBeDefined();
    expect(mimiPos!.x).toBe(0);
    expect(mimiPos!.y).toBe(0);
  });

  it('places realms at exactly REALM_RING_RADIUS from origin', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    for (const node of TEST_TOPOLOGY.nodes) {
      if (node.typeId !== 'realm') continue;
      const pos = positions.get(node.id)!;
      const dist = Math.hypot(pos.x, pos.y);
      expect(dist).toBeCloseTo(LAYOUT.REALM_RING_RADIUS, 5);
    }
  });

  it('places clusters near their parent realm (within double CLUSTER_RING_DIST)', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    const clusterPos = positions.get('cluster-vk')!;
    const parentPos = positions.get('realm-asgard')!;
    const dist = Math.hypot(clusterPos.x - parentPos.x, clusterPos.y - parentPos.y);
    expect(dist).toBeCloseTo(LAYOUT.CLUSTER_RING_DIST, 5);
  });

  it('places hosts near their parent realm', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    const hostPos = positions.get('host-mjolnir')!;
    const parentPos = positions.get('realm-asgard')!;
    const dist = Math.hypot(hostPos.x - parentPos.x, hostPos.y - parentPos.y);
    expect(dist).toBeCloseTo(LAYOUT.HOST_RING_DIST, 5);
  });

  it('places different realms at different positions', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    const asgardPos = positions.get('realm-asgard')!;
    const vanaheimPos = positions.get('realm-vanaheim')!;
    const dist = Math.hypot(asgardPos.x - vanaheimPos.x, asgardPos.y - vanaheimPos.y);
    // Different realm IDs → different hash angles → different positions
    expect(dist).toBeGreaterThan(0);
  });

  it('is stable across multiple calls — same input yields same output', () => {
    const positions1 = computeLayout(TEST_TOPOLOGY);
    const positions2 = computeLayout(TEST_TOPOLOGY);

    for (const node of TEST_TOPOLOGY.nodes) {
      const p1 = positions1.get(node.id)!;
      const p2 = positions2.get(node.id)!;
      expect(p1.x).toBe(p2.x);
      expect(p1.y).toBe(p2.y);
    }
  });

  it('handles topology with no Mímir node', () => {
    const noMimir: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [{ id: 'realm-a', typeId: 'realm', label: 'a', parentId: null, status: 'healthy' }],
      edges: [],
    };
    expect(() => computeLayout(noMimir)).not.toThrow();
    const positions = computeLayout(noMimir);
    expect(positions.has('realm-a')).toBe(true);
  });

  it('handles empty topology', () => {
    const empty: Topology = { timestamp: '2026-04-19T00:00:00Z', nodes: [], edges: [] };
    const positions = computeLayout(empty);
    expect(positions.size).toBe(0);
  });

  it('places nodes without a matching parent near origin', () => {
    const orphan: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'orphan-svc', typeId: 'service', label: 'orphan', parentId: null, status: 'healthy' },
      ],
      edges: [],
    };
    const positions = computeLayout(orphan);
    const pos = positions.get('orphan-svc')!;
    // Falls back to anchor at (0,0), so should be at scatter distance from origin
    const dist = Math.hypot(pos.x, pos.y);
    expect(dist).toBeCloseTo(LAYOUT.NODE_SCATTER_DIST, 5);
  });

  it('places mimir_sub nodes at exactly SUB_MIMIR_RING from the primary Mímir', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'mimir-0', typeId: 'mimir', label: 'mímir-0', parentId: null, status: 'healthy' },
        {
          id: 'mimir-sub-0',
          typeId: 'mimir_sub',
          label: 'mímir/code',
          parentId: 'mimir-0',
          status: 'healthy',
        },
      ],
      edges: [],
    };
    const positions = computeLayout(topology);
    const mimiPos = positions.get('mimir-0')!;
    const subPos = positions.get('mimir-sub-0')!;
    expect(mimiPos).toBeDefined();
    expect(subPos).toBeDefined();
    const dist = Math.hypot(subPos.x - mimiPos.x, subPos.y - mimiPos.y);
    expect(dist).toBeCloseTo(LAYOUT.SUB_MIMIR_RING, 5);
  });

  it('realm positions do not depend on node array order', () => {
    const reversed: Topology = {
      ...TEST_TOPOLOGY,
      nodes: [...TEST_TOPOLOGY.nodes].reverse(),
    };
    const posForward = computeLayout(TEST_TOPOLOGY);
    const posReversed = computeLayout(reversed);

    // Realm positions are individually computed — order shouldn't matter
    const a1 = posForward.get('realm-asgard')!;
    const a2 = posReversed.get('realm-asgard')!;
    expect(a1.x).toBeCloseTo(a2.x);
    expect(a1.y).toBeCloseTo(a2.y);
  });
});

// ── zoneRadius ────────────────────────────────────────────────────────────────

describe('zoneRadius', () => {
  it('returns REALM_INNER_RADIUS for realms', () => {
    expect(zoneRadius('realm')).toBe(LAYOUT.REALM_INNER_RADIUS);
  });

  it('returns CLUSTER_INNER_RADIUS for clusters', () => {
    expect(zoneRadius('cluster')).toBe(LAYOUT.CLUSTER_INNER_RADIUS);
  });

  it('realm radius is larger than cluster radius', () => {
    expect(zoneRadius('realm')).toBeGreaterThan(zoneRadius('cluster'));
  });
});
