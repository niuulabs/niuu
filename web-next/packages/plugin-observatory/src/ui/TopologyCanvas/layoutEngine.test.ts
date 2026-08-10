import { describe, it, expect } from 'vitest';
import {
  hashAngle,
  computeLayout,
  computeLayoutBounds,
  placeArcChildren,
  packGroupsForNode,
  sortedNodes,
  zoneRadius,
  HOST_HALF_W,
  HOST_HALF_H,
} from './layoutEngine';
import { LAYOUT, NODE_SIZE } from './config';
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
    { id: 'ting-0', typeId: 'ting', label: 'ting-0', parentId: 'cluster-vk', status: 'healthy' },
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
      id: 'run-0',
      typeId: 'run',
      label: 'run-omega',
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
    { id: 'e1', sourceId: 'ting-0', targetId: 'volundr-0', kind: 'solid' },
    { id: 'e2', sourceId: 'ting-0', targetId: 'run-0', kind: 'dashed-anim' },
    { id: 'e3', sourceId: 'ravn-huginn', targetId: 'mimir-0', kind: 'dashed-long' },
    { id: 'e4', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
    { id: 'e5', sourceId: 'run-0', targetId: 'ravn-huginn', kind: 'run' },
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

describe('sortedNodes', () => {
  it('prefers explicit layout order over type priority and label ordering', () => {
    const nodes = sortedNodes([
      {
        id: 'service-z',
        typeId: 'service',
        label: 'Zulu',
        parentId: null,
        status: 'healthy',
      },
      {
        id: 'realm-a',
        typeId: 'realm',
        label: 'Alpha',
        parentId: null,
        status: 'healthy',
        layoutHints: { order: 1 },
      },
      {
        id: 'cluster-a',
        typeId: 'cluster',
        label: 'Beta',
        parentId: null,
        status: 'healthy',
        layoutHints: { order: 0 },
      },
    ]);

    expect(nodes.map((node) => node.id)).toEqual(['cluster-a', 'realm-a', 'service-z']);
  });

  it('falls back to label and then id when type and order are tied', () => {
    const nodes = sortedNodes([
      { id: 'svc-b', typeId: 'service', label: 'Same', parentId: null, status: 'healthy' },
      { id: 'svc-a', typeId: 'service', label: 'same', parentId: null, status: 'healthy' },
      { id: 'svc-c', typeId: 'service', label: 'Alpha', parentId: null, status: 'healthy' },
    ]);

    expect(nodes.map((node) => node.id)).toEqual(['svc-c', 'svc-a', 'svc-b']);
  });
});

describe('placeArcChildren', () => {
  it('returns no placements for an empty child list', () => {
    expect(
      placeArcChildren([], undefined, {
        baseRadius: 96,
        radialStep: 24,
        minSpacing: 48,
      }),
    ).toEqual(new Map());
  });

  it('uses default anchor, span, and center when options omit them', () => {
    const placements = placeArcChildren(
      [
        { id: 'a', typeId: 'service', label: 'A', parentId: null, status: 'healthy' },
        { id: 'b', typeId: 'service', label: 'B', parentId: null, status: 'healthy' },
      ],
      undefined,
      {
        baseRadius: 100,
        radialStep: 20,
        minSpacing: 60,
      },
    );

    const a = placements.get('a')!;
    const b = placements.get('b')!;
    expect(Math.hypot(a.x, a.y)).toBeCloseTo(100, 6);
    expect(Math.hypot(b.x, b.y)).toBeCloseTo(100, 6);
    expect(a.x).toBeCloseTo(-100, 6);
    expect(b.x).toBeCloseTo(-100, 6);
    expect(a.y).toBeCloseTo(0, 6);
    expect(b.y).toBeCloseTo(0, 6);
  });

  it('spreads large sibling sets across multiple radial bands', () => {
    const children = Array.from({ length: 6 }, (_, index) => ({
      id: `child-${index}`,
      typeId: 'service' as const,
      label: `Child ${index}`,
      parentId: null,
      status: 'healthy' as const,
    }));

    const placements = placeArcChildren(
      children,
      { x: 10, y: -5 },
      {
        baseRadius: 40,
        radialStep: 30,
        minSpacing: 50,
        arcCenter: 0,
        arcSpan: Math.PI / 2,
      },
    );

    const radii = children.map((child) => {
      const pos = placements.get(child.id)!;
      return Math.round(Math.hypot(pos.x - 10, pos.y + 5));
    });

    expect(new Set(radii).size).toBeGreaterThan(1);
  });
});

describe('packGroupsForNode', () => {
  it('returns a single pack group for non-run nodes', () => {
    const host = {
      id: 'host-a',
      typeId: 'host' as const,
      label: 'Host',
      parentId: null,
      status: 'healthy' as const,
    };
    const groups = packGroupsForNode(host, [
      {
        id: 'service-a',
        typeId: 'service',
        label: 'Service A',
        parentId: 'host-a',
        status: 'healthy',
      },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0]?.id).toBe('host-a');
  });

  it('groups run children by packGroup and sorts those groups by first explicit order', () => {
    const run = {
      id: 'run-a',
      typeId: 'run' as const,
      label: 'Run A',
      parentId: null,
      status: 'observing' as const,
    };
    const groups = packGroupsForNode(run, [
      {
        id: 'main-1',
        typeId: 'stage',
        label: 'Main 1',
        parentId: 'run-a',
        status: 'healthy',
        layoutHints: { packGroup: 'main', order: 5 },
      },
      {
        id: 'decision-1',
        typeId: 'gate',
        label: 'Decision',
        parentId: 'run-a',
        status: 'healthy',
        layoutHints: { packGroup: 'decision', order: 2 },
      },
      {
        id: 'fallback-1',
        typeId: 'model',
        label: 'Fallback',
        parentId: 'run-a',
        status: 'healthy',
      },
    ]);

    expect(groups.map((group) => group?.id)).toEqual(['run-a:decision', 'run-a:run', 'run-a:main']);
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

  it('packs top-level containers without overlapping them', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    const mimir = positions.get('mimir-0')!;
    const asgard = positions.get('realm-asgard')!;
    const vanaheim = positions.get('realm-vanaheim')!;
    const asgardRadius = asgard.zoneRadius ?? LAYOUT.REALM_INNER_RADIUS;
    const vanaheimRadius = vanaheim.zoneRadius ?? LAYOUT.REALM_INNER_RADIUS;

    expect(Math.hypot(mimir.x - asgard.x, mimir.y - asgard.y)).toBeGreaterThanOrEqual(asgardRadius);
    expect(Math.hypot(mimir.x - vanaheim.x, mimir.y - vanaheim.y)).toBeGreaterThanOrEqual(
      vanaheimRadius,
    );
    expect(Math.hypot(asgard.x - vanaheim.x, asgard.y - vanaheim.y)).toBeGreaterThanOrEqual(
      asgardRadius + vanaheimRadius,
    );
  });

  it('keeps a single cluster close to the realm center even when packed with host siblings', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    const clusterPos = positions.get('cluster-vk')!;
    const hostPos = positions.get('host-mjolnir')!;
    const parentPos = positions.get('realm-asgard')!;
    const clusterDist = Math.hypot(clusterPos.x - parentPos.x, clusterPos.y - parentPos.y);
    const hostDist = Math.hypot(hostPos.x - parentPos.x, hostPos.y - parentPos.y);
    expect(clusterDist).toBeLessThan(hostDist);
  });

  it('keeps hosts contained within their parent realm radius', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    const hostPos = positions.get('host-mjolnir')!;
    const parentPos = positions.get('realm-asgard')!;
    const parentRadius = parentPos.zoneRadius ?? LAYOUT.REALM_INNER_RADIUS;
    const dist = Math.hypot(hostPos.x - parentPos.x, hostPos.y - parentPos.y);
    expect(dist + 48).toBeLessThanOrEqual(parentRadius);
  });

  it('treats hosts as packed containers and keeps host children inside the host hull', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-edge',
          typeId: 'cluster',
          label: 'edge',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'host-brokkr',
          typeId: 'host',
          label: 'brokkr',
          parentId: 'cluster-edge',
          status: 'healthy',
        },
        {
          id: 'valk-brokkr',
          typeId: 'valkyrie',
          label: 'valkyrie',
          parentId: 'host-brokkr',
          status: 'healthy',
        },
        {
          id: 'raven-brokkr',
          typeId: 'ravn_long',
          label: 'huginn',
          parentId: 'host-brokkr',
          status: 'idle',
        },
        {
          id: 'printer-brokkr',
          typeId: 'printer',
          label: 'forge',
          parentId: 'host-brokkr',
          status: 'healthy',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    const cluster = positions.get('cluster-edge')!;
    const host = positions.get('host-brokkr')!;
    const clusterRadius = cluster.zoneRadius ?? LAYOUT.CLUSTER_INNER_RADIUS;
    const halfW = (host.containerWidth ?? HOST_HALF_W * 2) / 2;
    const halfH = (host.containerHeight ?? HOST_HALF_H * 2) / 2;

    expect(host.containerWidth ?? 0).toBeGreaterThan(HOST_HALF_W * 2);
    expect(host.containerHeight ?? 0).toBeGreaterThan(HOST_HALF_H * 2);
    expect(
      Math.hypot(host.x - cluster.x, host.y - cluster.y) + Math.max(halfW, halfH),
    ).toBeLessThanOrEqual(clusterRadius + 8);

    for (const childId of ['valk-brokkr', 'raven-brokkr', 'printer-brokkr']) {
      const child = positions.get(childId)!;
      expect(Math.abs(child.x - host.x)).toBeLessThanOrEqual(halfW);
      expect(Math.abs(child.y - host.y)).toBeLessThanOrEqual(halfH);
    }
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

  it('treats nodes without a matching parent as world-level roots', () => {
    const orphan: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'orphan-svc', typeId: 'service', label: 'orphan', parentId: null, status: 'healthy' },
      ],
      edges: [],
    };
    const positions = computeLayout(orphan);
    const pos = positions.get('orphan-svc')!;
    expect(pos).toMatchObject({ x: 0, y: 0 });
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

  it('packs cluster containers using their real zone radii so sibling clusters do not overlap', () => {
    const dense: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-a', typeId: 'realm', label: 'realm-a', parentId: null, status: 'healthy' },
        {
          id: 'cluster-a',
          typeId: 'cluster',
          label: 'cluster-a',
          parentId: 'realm-a',
          status: 'healthy',
        },
        {
          id: 'cluster-b',
          typeId: 'cluster',
          label: 'cluster-b',
          parentId: 'realm-a',
          status: 'healthy',
        },
        {
          id: 'cluster-c',
          typeId: 'cluster',
          label: 'cluster-c',
          parentId: 'realm-a',
          status: 'healthy',
        },
        ...['cluster-a', 'cluster-b', 'cluster-c'].flatMap((clusterId, clusterIndex) =>
          Array.from({ length: 8 }, (_, index) => ({
            id: `${clusterId}-run-${index}`,
            typeId: 'run' as const,
            label: `run-${clusterIndex}-${index}`,
            parentId: clusterId,
            status: 'observing' as const,
          })),
        ),
      ],
      edges: [],
    };

    const positions = computeLayout(dense);
    const a = positions.get('cluster-a')!;
    const b = positions.get('cluster-b')!;
    const c = positions.get('cluster-c')!;
    const aRadius = a.zoneRadius ?? LAYOUT.CLUSTER_INNER_RADIUS;
    const bRadius = b.zoneRadius ?? LAYOUT.CLUSTER_INNER_RADIUS;
    const cRadius = c.zoneRadius ?? LAYOUT.CLUSTER_INNER_RADIUS;

    expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(aRadius + bRadius);
    expect(Math.hypot(a.x - c.x, a.y - c.y)).toBeGreaterThanOrEqual(aRadius + cRadius);
    expect(Math.hypot(b.x - c.x, b.y - c.y)).toBeGreaterThanOrEqual(bRadius + cRadius);
  });

  it('grows cluster and realm container radii as child counts increase', () => {
    const dense: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'mimir-0', typeId: 'mimir', label: 'm', parentId: null, status: 'healthy' },
        { id: 'realm-a', typeId: 'realm', label: 'a', parentId: null, status: 'healthy' },
        { id: 'cluster-a', typeId: 'cluster', label: 'c', parentId: 'realm-a', status: 'healthy' },
        ...Array.from({ length: 10 }, (_, index) => ({
          id: `svc-${index}`,
          typeId: 'service' as const,
          label: `svc-${index}`,
          parentId: 'cluster-a',
          status: 'healthy' as const,
        })),
      ],
      edges: [],
    };
    const positions = computeLayout(dense);
    expect(positions.get('cluster-a')?.zoneRadius ?? 0).toBeGreaterThan(
      LAYOUT.CLUSTER_INNER_RADIUS,
    );
    expect(positions.get('realm-a')?.zoneRadius ?? 0).toBeGreaterThanOrEqual(
      LAYOUT.REALM_INNER_RADIUS,
    );
  });

  it('computes bounds that fully enclose the rendered topology', () => {
    const positions = computeLayout(TEST_TOPOLOGY);
    const bounds = computeLayoutBounds(TEST_TOPOLOGY, positions);
    expect(bounds).not.toBeNull();
    expect(bounds!.minX).toBeLessThan(bounds!.maxX);
    expect(bounds!.minY).toBeLessThan(bounds!.maxY);
  });

  it('centers a single realm and its only cluster so local maps are not pushed to the edge', () => {
    const localTopology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-platform',
          typeId: 'cluster',
          label: 'platform',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'mimir-platform',
          typeId: 'mimir',
          label: 'Mimir',
          parentId: 'cluster-platform',
          status: 'healthy',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(localTopology);
    expect(positions.get('realm-local')).toMatchObject({ x: 0, y: 0 });
    expect(positions.get('cluster-platform')).toMatchObject({ x: 0, y: 0 });
    const mimir = positions.get('mimir-platform')!;
    expect(mimir).toMatchObject({ x: 0, y: 0 });
  });

  it('treats a cluster-local Mimir as a first-class cluster service instead of the global center', () => {
    const localTopology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-platform',
          typeId: 'cluster',
          label: 'platform',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'mimir-platform',
          typeId: 'mimir',
          label: 'Mimir',
          parentId: 'cluster-platform',
          status: 'healthy',
        },
        {
          id: 'service-bifrost',
          typeId: 'bifrost',
          label: 'Bifrost',
          parentId: 'cluster-platform',
          status: 'healthy',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(localTopology);
    const cluster = positions.get('cluster-platform')!;
    const mimir = positions.get('mimir-platform')!;
    const bifrost = positions.get('service-bifrost')!;

    const clusterRadius = cluster.zoneRadius ?? LAYOUT.CLUSTER_INNER_RADIUS;
    expect(
      Math.hypot(mimir.x - cluster.x, mimir.y - cluster.y) + NODE_SIZE.mimir!,
    ).toBeLessThanOrEqual(clusterRadius);
    expect(Math.hypot(bifrost.x - cluster.x, bifrost.y - cluster.y) + 32).toBeLessThanOrEqual(
      clusterRadius,
    );
    expect(Math.hypot(mimir.x - bifrost.x, mimir.y - bifrost.y)).toBeGreaterThan(0);
  });

  it('packs cluster children tightly while keeping them inside the cluster radius', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-platform',
          typeId: 'cluster',
          label: 'platform',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'service-ravn',
          typeId: 'service',
          label: 'Ravn',
          parentId: 'cluster-platform',
          status: 'healthy',
          svcType: 'ravn',
        },
        {
          id: 'service-ting',
          typeId: 'ting',
          label: 'Ting',
          parentId: 'cluster-platform',
          status: 'healthy',
        },
        {
          id: 'warden-a',
          typeId: 'ravn_long',
          label: 'Warden A',
          parentId: 'cluster-platform',
          status: 'idle',
        },
        {
          id: 'run-a',
          typeId: 'run',
          label: 'Run A',
          parentId: 'cluster-platform',
          status: 'observing',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    const cluster = positions.get('cluster-platform')!;
    const ravn = positions.get('service-ravn')!;
    const warden = positions.get('warden-a')!;
    const ting = positions.get('service-ting')!;
    const run = positions.get('run-a')!;
    const clusterRadius = cluster.zoneRadius ?? LAYOUT.CLUSTER_INNER_RADIUS;

    for (const child of [ravn, warden, ting, run]) {
      expect(Math.hypot(child.x - cluster.x, child.y - cluster.y)).toBeLessThanOrEqual(
        clusterRadius,
      );
    }
    expect(Math.hypot(ravn.x - warden.x, ravn.y - warden.y)).toBeGreaterThan(0);
    expect(Math.hypot(ting.x - run.x, ting.y - run.y)).toBeGreaterThan(0);
  });

  it('sizes cluster packs using nested run footprint so run children stay contained', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-platform',
          typeId: 'cluster',
          label: 'platform',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'run-a',
          typeId: 'run',
          label: 'Run A',
          parentId: 'cluster-platform',
          status: 'observing',
        },
        {
          id: 'coord-a',
          typeId: 'ravn_run',
          label: 'coord',
          parentId: 'run-a',
          status: 'healthy',
        },
        {
          id: 'reviewer-a',
          typeId: 'ravn_run',
          label: 'reviewer',
          parentId: 'run-a',
          status: 'healthy',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    const cluster = positions.get('cluster-platform')!;
    const clusterRadius = cluster.zoneRadius ?? LAYOUT.CLUSTER_INNER_RADIUS;
    for (const id of ['run-a', 'coord-a', 'reviewer-a']) {
      const child = positions.get(id)!;
      expect(Math.hypot(child.x - cluster.x, child.y - cluster.y)).toBeLessThanOrEqual(
        clusterRadius,
      );
    }
  });

  it('packs generic service children into a contained sibling group instead of fallback overlap scatter', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-platform',
          typeId: 'cluster',
          label: 'platform',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'service-bifrost',
          typeId: 'bifrost',
          label: 'Bifrost',
          parentId: 'cluster-platform',
          status: 'healthy',
        },
        ...Array.from({ length: 5 }, (_, index) => ({
          id: `model-${index}`,
          typeId: 'model' as const,
          label: `model-${index}`,
          parentId: 'service-bifrost',
          status: 'healthy' as const,
        })),
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    const bifrost = positions.get('service-bifrost')!;
    const modelPositions = Array.from({ length: 5 }, (_, index) =>
      positions.get(`model-${index}`)!,
    );
    for (let index = 0; index < 5; index += 1) {
      const model = positions.get(`model-${index}`)!;
      const dist = Math.hypot(model.x - bifrost.x, model.y - bifrost.y);
      expect(dist).toBeGreaterThanOrEqual(44);
      expect(dist).toBeLessThanOrEqual(120);
    }
    for (let index = 0; index < modelPositions.length; index += 1) {
      for (let other = index + 1; other < modelPositions.length; other += 1) {
        expect(
          Math.hypot(
            modelPositions[index]!.x - modelPositions[other]!.x,
            modelPositions[index]!.y - modelPositions[other]!.y,
          ),
        ).toBeGreaterThan(0);
      }
    }
  });

  it('assigns zone radii to root-level clusters with no parent realm', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        {
          id: 'cluster-root',
          typeId: 'cluster',
          label: 'cluster-root',
          parentId: null,
          status: 'healthy',
        },
        {
          id: 'service-root',
          typeId: 'service',
          label: 'service-root',
          parentId: 'cluster-root',
          status: 'healthy',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    expect(positions.get('cluster-root')?.zoneRadius).toBeGreaterThanOrEqual(
      LAYOUT.CLUSTER_INNER_RADIUS,
    );
    expect(positions.get('service-root')).toBeDefined();
  });

  it('sizes namespace containers around discovered workloads and wardens', () => {
    const topology: Topology = {
      timestamp: '2026-06-14T00:00:00Z',
      nodes: [
        {
          id: 'cluster-ymir',
          typeId: 'cluster',
          label: 'ymir',
          parentId: null,
          status: 'healthy',
        },
        {
          id: 'namespace-ymir-volundr',
          typeId: 'namespace',
          label: 'volundr',
          parentId: 'cluster-ymir',
          status: 'healthy',
        },
        {
          id: 'k8s:ymir:volundr:deployment:niuu-mimir-shared',
          typeId: 'mimir',
          label: 'niuu-mimir-shared',
          parentId: 'namespace-ymir-volundr',
          status: 'healthy',
        },
        {
          id: 'k8s:ymir:volundr:deployment:mimir-shared-warden-agent',
          typeId: 'ravn_long',
          label: 'mimir-shared-warden-agent',
          parentId: 'namespace-ymir-volundr',
          status: 'healthy',
        },
        {
          id: 'k8s:ymir:volundr:service:niuu-ravn',
          typeId: 'ravn_long',
          label: 'niuu-ravn',
          parentId: 'namespace-ymir-volundr',
          status: 'healthy',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    const namespace = positions.get('namespace-ymir-volundr')!;
    const namespaceRadius = Math.max(
      (namespace.containerWidth ?? 0) / 2,
      (namespace.containerHeight ?? 0) / 2,
    );
    const warden = positions.get('k8s:ymir:volundr:deployment:mimir-shared-warden-agent')!;
    const mimir = positions.get('k8s:ymir:volundr:deployment:niuu-mimir-shared')!;

    expect(namespace.containerWidth).toBeGreaterThanOrEqual(LAYOUT.NAMESPACE_INNER_RADIUS * 2);
    expect(Math.hypot(warden.x - namespace.x, warden.y - namespace.y)).toBeLessThanOrEqual(
      namespaceRadius,
    );
    expect(Math.hypot(mimir.x - namespace.x, mimir.y - namespace.y)).toBeLessThanOrEqual(
      namespaceRadius,
    );
    expect(Math.hypot(warden.x - mimir.x, warden.y - mimir.y)).toBeGreaterThan(0);
  });

  it('scatters fallback children around already-positioned non-container parents', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-platform',
          typeId: 'cluster',
          label: 'platform',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'mimir-local',
          typeId: 'mimir',
          label: 'mimir',
          parentId: 'cluster-platform',
          status: 'healthy',
        },
        {
          id: 'model-child',
          typeId: 'model',
          label: 'child',
          parentId: 'mimir-local',
          status: 'healthy',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    const mimir = positions.get('mimir-local')!;
    const child = positions.get('model-child')!;

    expect(Math.hypot(child.x - mimir.x, child.y - mimir.y)).toBeGreaterThan(0);
  });

  it('lays out run workflow groups in directional anchors and alternates dense main nodes', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-platform',
          typeId: 'cluster',
          label: 'platform',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'run-a',
          typeId: 'run',
          label: 'Run A',
          parentId: 'cluster-platform',
          status: 'observing',
        },
        {
          id: 'trigger-a',
          typeId: 'trigger',
          label: 'Trigger',
          parentId: 'run-a',
          status: 'healthy',
          layoutHints: { packGroup: 'entry', order: 1 },
        },
        {
          id: 'resource-a',
          typeId: 'resource',
          label: 'Resource',
          parentId: 'run-a',
          status: 'healthy',
          layoutHints: { packGroup: 'resource', order: 2 },
        },
        {
          id: 'stage-a',
          typeId: 'stage',
          label: 'Stage A',
          parentId: 'run-a',
          status: 'healthy',
          layoutHints: { packGroup: 'main', order: 3 },
        },
        {
          id: 'stage-b',
          typeId: 'stage',
          label: 'Stage B',
          parentId: 'run-a',
          status: 'healthy',
          layoutHints: { packGroup: 'main', order: 4 },
        },
        {
          id: 'stage-c',
          typeId: 'stage',
          label: 'Stage C',
          parentId: 'run-a',
          status: 'healthy',
          layoutHints: { packGroup: 'main', order: 5 },
        },
        {
          id: 'gate-a',
          typeId: 'gate',
          label: 'Gate',
          parentId: 'run-a',
          status: 'healthy',
          layoutHints: { packGroup: 'decision', order: 6 },
        },
        {
          id: 'end-a',
          typeId: 'end',
          label: 'End',
          parentId: 'run-a',
          status: 'healthy',
          layoutHints: { packGroup: 'exit', order: 7 },
        },
        {
          id: 'aux-a',
          typeId: 'model',
          label: 'Aux',
          parentId: 'run-a',
          status: 'healthy',
          layoutHints: { packGroup: 'aux', order: 8 },
        },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    const run = positions.get('run-a')!;
    const trigger = positions.get('trigger-a')!;
    const resource = positions.get('resource-a')!;
    const stageA = positions.get('stage-a')!;
    const stageB = positions.get('stage-b')!;
    const stageC = positions.get('stage-c')!;
    const gate = positions.get('gate-a')!;
    const end = positions.get('end-a')!;
    const aux = positions.get('aux-a')!;

    expect(trigger.y).toBeLessThan(run.y);
    expect(resource.x).toBeLessThan(run.x);
    expect(gate.x).toBeGreaterThan(run.x);
    expect(end.y).toBeGreaterThan(run.y);
    expect(aux.x).toBeCloseTo(run.x, 6);
    expect(new Set([stageA.x, stageB.x, stageC.x]).size).toBeGreaterThan(1);
  });

  it('gives childless hosts a fallback hull size inside their containing cluster', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-local', typeId: 'realm', label: 'local', parentId: null, status: 'healthy' },
        {
          id: 'cluster-platform',
          typeId: 'cluster',
          label: 'platform',
          parentId: 'realm-local',
          status: 'healthy',
        },
        {
          id: 'host-empty',
          typeId: 'host',
          label: 'empty',
          parentId: 'cluster-platform',
          status: 'healthy',
        },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);
    const cluster = positions.get('cluster-platform')!;
    const host = positions.get('host-empty')!;
    const clusterRadius = cluster.zoneRadius ?? LAYOUT.CLUSTER_INNER_RADIUS;

    expect(host.containerWidth).toBeGreaterThan(HOST_HALF_W * 2);
    expect(host.containerHeight).toBeGreaterThan(HOST_HALF_H * 2);
    expect(
      Math.hypot(host.x - cluster.x, host.y - cluster.y) +
        Math.max((host.containerWidth ?? 0) / 2, (host.containerHeight ?? 0) / 2),
    ).toBeLessThanOrEqual(clusterRadius + 8);
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

describe('computeLayoutBounds', () => {
  it('returns null when every topology node is missing from the positions map', () => {
    const topology: Topology = {
      timestamp: '2026-04-19T00:00:00Z',
      nodes: [
        { id: 'realm-a', typeId: 'realm', label: 'a', parentId: null, status: 'healthy' },
        { id: 'host-a', typeId: 'host', label: 'host', parentId: 'realm-a', status: 'healthy' },
      ],
      edges: [],
    };

    expect(computeLayoutBounds(topology, new Map())).toBeNull();
  });
});

describe('nested containment', () => {
  /**
   * Valhalla's Skuld namespace holds seven workflow sessions, each running a
   * flock of agents. Sizing containers by child count packed the namespace
   * first, while the sessions inside it still measured as bare dots — each
   * then grew to hold six agents and drove straight through its neighbours.
   */
  function nestedTopology(sessions: number, membersEach: number): Topology {
    const nodes: Topology['nodes'] = [
      {
        id: 'cluster-valhalla',
        typeId: 'cluster',
        label: 'valhalla',
        parentId: null,
        status: 'healthy',
      },
      {
        id: 'ns-skuld',
        typeId: 'namespace',
        label: 'skuld',
        parentId: 'cluster-valhalla',
        status: 'healthy',
      },
    ];
    for (let s = 0; s < sessions; s += 1) {
      nodes.push({
        id: `session-${s}`,
        typeId: 'skuld',
        label: `session-${s}`,
        parentId: 'ns-skuld',
        status: 'healthy',
      });
      for (let m = 0; m < membersEach; m += 1) {
        nodes.push({
          id: `session-${s}:member-${m}`,
          typeId: 'ravn_run',
          label: `member-${m}`,
          parentId: `session-${s}`,
          status: 'healthy',
          flockId: `session-${s}`,
        });
      }
    }
    return { timestamp: '2026-08-02T00:00:00Z', nodes, edges: [] };
  }

  it('keeps the agents of one session clear of the next', () => {
    const topology = nestedTopology(7, 6);
    const positions = computeLayout(topology);
    const members = topology.nodes.filter((node) => node.typeId === 'ravn_run');

    let closest = Number.POSITIVE_INFINITY;
    for (const a of members) {
      for (const b of members) {
        if (a.parentId === b.parentId || a.id >= b.id) continue;
        const pa = positions.get(a.id)!;
        const pb = positions.get(b.id)!;
        closest = Math.min(closest, Math.hypot(pa.x - pb.x, pa.y - pb.y));
      }
    }

    // The packer reserves a gutter around every glyph, so agents that belong
    // to different sessions should never land inside one node's extent of
    // each other. Sizing by child count put them 17 units apart.
    expect(closest).toBeGreaterThan(NODE_SIZE.ravn_run! * 4);
  });

  it('survives a parent cycle rather than recursing forever', () => {
    // Malformed data, but a source that reports it should not hang the canvas.
    const topology: Topology = {
      timestamp: '2026-08-02T00:00:00Z',
      nodes: [
        { id: 'a', typeId: 'service', label: 'a', parentId: 'b', status: 'healthy' },
        { id: 'b', typeId: 'service', label: 'b', parentId: 'a', status: 'healthy' },
      ],
      edges: [],
    };

    const positions = computeLayout(topology);

    expect(positions.has('a')).toBe(true);
    expect(positions.has('b')).toBe(true);
  });

  it('gives the namespace room for the sessions as they actually size', () => {
    const topology = nestedTopology(7, 6);
    const positions = computeLayout(topology);
    const namespace = positions.get('ns-skuld')!;
    const halfWidth = (namespace.containerWidth ?? 0) / 2;

    for (const node of topology.nodes) {
      if (node.typeId !== 'ravn_run') continue;
      const pos = positions.get(node.id)!;
      expect(Math.hypot(pos.x - namespace.x, pos.y - namespace.y)).toBeLessThanOrEqual(halfWidth);
    }
  });
});
