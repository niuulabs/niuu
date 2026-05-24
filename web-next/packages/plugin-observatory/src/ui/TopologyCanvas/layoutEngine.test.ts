import { describe, it, expect } from 'vitest';
import {
  hashAngle,
  computeLayout,
  computeLayoutBounds,
  zoneRadius,
  HOST_HALF_W,
  HOST_HALF_H,
} from './layoutEngine';
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

    expect(Math.hypot(mimir.x - asgard.x, mimir.y - asgard.y)).toBeGreaterThanOrEqual(
      LAYOUT.MIMIR_RADIUS + asgardRadius,
    );
    expect(Math.hypot(mimir.x - vanaheim.x, mimir.y - vanaheim.y)).toBeGreaterThanOrEqual(
      LAYOUT.MIMIR_RADIUS + vanaheimRadius,
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
    expect(Math.hypot(host.x - cluster.x, host.y - cluster.y) + Math.max(halfW, halfH)).toBeLessThanOrEqual(
      clusterRadius + 8,
    );

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
        { id: 'cluster-a', typeId: 'cluster', label: 'cluster-a', parentId: 'realm-a', status: 'healthy' },
        { id: 'cluster-b', typeId: 'cluster', label: 'cluster-b', parentId: 'realm-a', status: 'healthy' },
        { id: 'cluster-c', typeId: 'cluster', label: 'cluster-c', parentId: 'realm-a', status: 'healthy' },
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
    expect(Math.hypot(mimir.x - cluster.x, mimir.y - cluster.y) + LAYOUT.MIMIR_RADIUS).toBeLessThanOrEqual(
      clusterRadius,
    );
    expect(
      Math.hypot(bifrost.x - cluster.x, bifrost.y - cluster.y) + 32,
    ).toBeLessThanOrEqual(clusterRadius);
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
    const modelPositions = Array.from({ length: 5 }, (_, index) => positions.get(`model-${index}`)!);
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
